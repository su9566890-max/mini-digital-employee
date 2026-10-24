# -*- coding: utf-8 -*-
"""云端网页工单台（EdgeOne Makers cloud-functions / Flask WSGI）。

文件即路由：``cloud-functions/zhoubao-web/index.py`` → 线上路径 ``/zhoubao-web``。
平台会自动去掉文件系统路由前缀，所以下面 Flask 的路由按**相对路径**注册。

为什么是「无状态同步模式」
--------------------------
Makers 的 Python 运行时**没有官方持久化**（Blob 只提供 Node.js SDK、平台无托管数据库、
官方文档明确不建议 sqlite，因为 Serverless 冷启动会重置），而 ``context.store``
只在 ``agents/`` 的 Agent 运行时里可用 —— cloud-functions 用不了。

所以本工单台一次请求内跑完整条链路，任务状态序列化成 blob 随表单回传：

    GET  /            表单页（周期/发起人 + 上传两份材料）
    POST /run         准入校验 → 执行 → 自动质检 → 展示结果（含审核区）
    POST /review      通过则交付下载；退回则采纳意见重做并再次送审

产物（周报 docx、3 张图表）以 base64 data URI **内联进结果页**，点击即下载，
不依赖任何服务端存储 —— 换实例、冷启动都不影响。
"""
import base64
import html
import json
import os
import sys
import tempfile
import time
import uuid

# ---- 让入口能引用 _vendor 里的核心包（本地调试时回退仓库根）----
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = None
for _cand in (os.path.abspath(os.path.join(_HERE, "..", "_vendor")),
              os.path.abspath(os.path.join(_HERE, "..", ".."))):
    if os.path.isdir(os.path.join(_cand, "app")):
        _ROOT = _cand
        if _cand not in sys.path:
            sys.path.insert(0, _cand)
        break

import yaml  # noqa: E402
from flask import Flask, request  # noqa: E402

from app.qa import describe_checks  # noqa: E402
from app.state import (HUMAN_REVIEW, PAUSED_MISSING,  # noqa: E402
                       IllegalTransition, TaskRunner)
from app.storage import MemoryStorage  # noqa: E402
from app.tools import registry  # noqa: E402

CFG_PATH = os.path.join(_ROOT, "config", "employee_zhoubao.yaml")
_CACHE = {"cfg": None}

app = Flask(__name__)


def cfg():
    if _CACHE["cfg"] is None:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            c = yaml.safe_load(f)
        registry.set_policy(c["tools_allowed"], c["limits"]["max_tool_retry"])
        _CACHE["cfg"] = c
    return _CACHE["cfg"]


# ---------------- 状态快照：打包 → 随表单回传 → 解包 ----------------
def _pack(state: dict, events: list) -> str:
    """把状态、事件流与材料内容打包成可回传的字符串。

    材料内容必须内联：Serverless 下一次请求可能落到全新实例，
    原先写在临时目录里的上传文件就没了，退回重做时会读不到材料。
    """
    materials = {}
    for key in ("sales_file", "notes_file"):
        p = (state.get("form") or {}).get(key, "")
        if p and os.path.exists(p):
            with open(p, "rb") as f:
                materials[key] = {
                    "name": os.path.basename(p),
                    "b64": base64.b64encode(f.read()).decode("ascii"),
                }
    payload = {"state": state, "events": events, "materials": materials}
    return base64.b64encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")


def _unpack(blob: str):
    payload = json.loads(base64.b64decode(blob).decode("utf-8"))
    state = payload["state"]
    root = tempfile.mkdtemp(prefix="mde_")
    for key, m in (payload.get("materials") or {}).items():
        d = os.path.join(root, "materials")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, key + "_" + m["name"])
        with open(p, "wb") as f:
            f.write(base64.b64decode(m["b64"]))
        state.setdefault("form", {})[key] = p
    return state, payload.get("events") or [], root


def _storage_with(state: dict, events: list) -> MemoryStorage:
    """用已有状态与事件构造内存后端，等价于从断点处恢复继续跑。"""
    return MemoryStorage(state.get("task_id", "task"),
                         scratch_root=tempfile.mkdtemp(prefix="mde_"),
                         initial_state=state,
                         initial_events=events)


def _safe_name(name: str) -> str:
    """保留中文文件名，剥掉路径分隔符与非法字符，防目录穿越。"""
    raw = os.path.basename(name or "")
    safe = "".join(ch for ch in raw if ch not in '\\/:*?"<>|').strip()
    return safe


def _save_upload(file_storage, root: str, key: str) -> str:
    if not file_storage:
        return ""
    safe = _safe_name(file_storage.filename)
    if not safe:
        return ""
    d = os.path.join(root, "materials")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, key + "_" + safe)
    file_storage.save(p)
    return p


def _inline_files(state: dict) -> dict:
    """把报告与图表读成 base64，供结果页内联预览 / data URI 下载。"""
    out = {}
    arts = state.get("artifacts") or {}
    report = arts.get("report")
    if isinstance(report, str) and os.path.exists(report):
        with open(report, "rb") as f:
            period = (state.get("form") or {}).get("period", "周报")
            out["%s_经营周报.docx" % period] = base64.b64encode(
                f.read()).decode("ascii")
    for c in arts.get("charts") or []:
        if isinstance(c, str) and os.path.exists(c):
            with open(c, "rb") as f:
                out[os.path.basename(c)] = base64.b64encode(
                    f.read()).decode("ascii")
    return out


# ---------------- 页面 ----------------
_CSS = """
*{box-sizing:border-box}
body{margin:0;padding:24px;background:#f5f6f8;color:#1c2430;
 font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;line-height:1.6}
.wrap{max-width:920px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}
.sub{color:#6b7684;font-size:13px;margin-bottom:20px}
.card{background:#fff;border:1px solid #e3e7ee;border-radius:10px;padding:20px;margin-bottom:16px}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700;
 background:#eef1f6;color:#5b6675}
label{display:block;font-size:13px;font-weight:600;margin:12px 0 5px}
input[type=text],input[type=file],textarea,select{width:100%;padding:8px 10px;
 border:1px solid #d3dae5;border-radius:6px;font-size:14px;background:#fff}
textarea{min-height:70px}
button{margin-top:16px;padding:9px 20px;border:0;border-radius:6px;background:#1a56c4;
 color:#fff;font-size:14px;font-weight:600;cursor:pointer}
button.danger{background:#b3261e;margin-left:8px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #eef1f6}
th{background:#fafbfd;color:#5b6675;font-weight:600}
.ok{color:#12734a;font-weight:700}.bad{color:#b3261e;font-weight:700}
.msg{padding:10px 14px;border-radius:6px;margin-bottom:14px;font-size:13px}
.msg.err{background:#fdeaea;color:#b3261e}
.msg.ok{background:#e4f6ec;color:#12734a}
.charts{display:flex;flex-wrap:wrap;gap:12px}
.charts figure{margin:0;flex:1 1 260px;background:#fafbfd;border:1px solid #eef1f6;
 border-radius:8px;padding:10px}
.charts img{width:100%;display:block}
figcaption{font-size:12px;color:#6b7684;text-align:center;margin-top:6px}
pre{background:#f7f9fc;border:1px solid #e6ecf5;border-radius:6px;padding:10px;
 overflow:auto;font-size:12px}
.muted{color:#6b7684;font-size:12px}
"""


def _layout(title, body):
    out = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
    out += '<meta name="viewport" content="width=device-width,initial-scale=1">'
    out += "<title>" + html.escape(title) + "</title>"
    out += "<style>" + _CSS + "</style></head><body><div class=\"wrap\">"
    out += "<h1>" + html.escape(title) + "</h1>"
    out += ('<div class="sub">迷你数字员工 · 场景 A '
            '经营周报员工（云端版）</div>')
    out += body + "</div></body></html>"
    return out


def _form_page(default_id="", message=""):
    msg = ("<div class='msg err'>" + html.escape(message) + "</div>"
           if message else "")
    body = msg + """
<div class="card">
  <form method="post" action="/zhoubao-web/run" enctype="multipart/form-data">
    <label>统计周期 *</label>
    <input type="text" name="period" value="2026 年第 44 周" required>
    <label>发起人 *</label>
    <input type="text" name="submitter" value="张店长" required>
    <label>任务编号（留空自动生成）</label>
    <input type="text" name="task_id" value="__DEFID__">
    <label>门店销售明细（Excel/CSV）</label>
    <input type="file" name="sales_file" accept=".xlsx,.xls,.csv">
    <label>周会纪要（docx/txt）</label>
    <input type="file" name="notes_file" accept=".docx,.txt">
    <button type="submit">发起工单</button>
  </form>
</div>
<div class="card">
  <div class="muted">
    提交后系统在<b>一次请求内</b>跑完「准入校验 → 执行 → 自动质检」：<br>
    · 两份材料齐全 → 停在<b>待人工审核</b>，页面展示质检结果与异常归因<br>
    · 材料缺失 → 停在<b>缺材料暂停</b>，可在页面直接补充后继续<br>
    · 质检不通过 → 自动修正重试，仍不通过则<b>转人工</b><br><br>
    <b>云端版说明</b>：Serverless 无持久化，任务状态随表单回传，
    产物内联在页面里点击下载，因此不支持长时间挂起等待审核。
  </div>
</div>"""
    return _layout("发起工单", body.replace("__DEFID__", html.escape(default_id)))


_CSS_DONE = False


def _qa_rows(records):
    rows = []
    for r in records or []:
        cls = "ok" if r.get("passed") else "bad"
        txt = "通过" if r.get("passed") else "不通过"
        rows.append("<tr><td>" + html.escape(str(r.get("name", ""))) +
                    "</td><td class='" + cls + "'>" + txt +
                    "</td><td>" + html.escape(str(r.get("detail", ""))) +
                    "</td></tr>")
    if not rows:
        rows.append("<tr><td colspan='3' class='muted'>尚无质检记录</td></tr>")
    return "".join(rows)


def _charts_html(files: dict) -> str:
    figs = []
    for name, b64 in files.items():
        if name.lower().endswith(".png"):
            figs.append('<figure><img src="data:image/png;base64,' + b64 +
                        '" alt="' + html.escape(name) +
                        '"><figcaption>' + html.escape(name) +
                        "</figcaption></figure>")
    if not figs:
        return ""
    return ('<div class="card"><div class="muted" '
            'style="margin-bottom:8px">图表预览</div>'
            '<div class="charts">' + "".join(figs) + "</div></div>")


def _download_html(files: dict) -> str:
    for name, b64 in files.items():
        if name.lower().endswith(".docx"):
            return ('<div class="card"><a href="data:application/vnd.'
                    'openxmlformats-officedocument.wordprocessingml.document'
                    ';base64,' + b64 + '" download="' +
                    html.escape(name) +
                    '"><button type="button">下载经营周报（docx）'
                    "</button></a></div>")
    return ""


def _review_html(blob: str, stage: str) -> str:
    if stage == HUMAN_REVIEW:
        return """
<div class="card">
  <div style="font-size:16px;font-weight:700;margin-bottom:8px">人工审核</div>
  <form method="post" action="/zhoubao-web/review">
    <input type="hidden" name="blob" value="__BLOB__">
    <label>审核意见（退回时必填，将作为采纳意见写入重做提示词）</label>
    <textarea name="comment" placeholder="例如：补充促销活动的量化影响"></textarea>
    <div>
      <button type="submit" name="action" value="approve">通过并交付</button>
    </div>
    <div>
      <button type="submit" name="action" value="reject" class="danger">退回重做</button>
    </div>
  </form>
</div>""".replace("__BLOB__", html.escape(blob))
    if stage == PAUSED_MISSING:
        return """
<div class="card">
  <div style="font-size:16px;font-weight:700;margin-bottom:8px">补充缺失材料</div>
  <form method="post" action="/zhoubao-web/material" enctype="multipart/form-data">
    <input type="hidden" name="blob" value="__BLOB__">
    <label>材料类型</label>
    <select name="key">
      <option value="sales_file">门店销售明细</option>
      <option value="notes_file">周会纪要</option>
    </select>
    <label>选择文件</label>
    <input type="file" name="file" required>
    <button type="submit">补充并继续</button>
  </form>
</div>""".replace("__BLOB__", html.escape(blob))
    return ""


def _result_page(state: dict, events: list, message: str = "") -> str:
    """渲染结果页：状态徽章 + 质检表 + 图表预览 + 下载 + 审核区 + 事件流。"""
    stage = state.get("stage")
    qa = state.get("qa_records") or []
    passed = sum(1 for x in qa if x.get("passed"))
    files = _inline_files(state)
    period = str((state.get("form") or {}).get("period", ""))
    submitter = str((state.get("form") or {}).get("submitter", ""))
    blob = _pack(state, events)

    parts = []
    if message:
        parts.append("<div class='msg err'>" + html.escape(message) + "</div>")

    parts.append("<div class='card'>")
    parts.append("<span class='badge'>" + html.escape(str(stage)) + "</span>")
    parts.append("<div class='muted' style='margin-top:8px'>")
    parts.append("任务 " + html.escape(str(state.get("task_id", ""))))
    parts.append(" · 周期 " + html.escape(period))
    parts.append(" · 发起人 " + html.escape(submitter))
    parts.append(" · 模型调用 " + str(state.get("llm_rounds", 0)) + " 次")
    parts.append("</div>")
    if state.get("pause_reason"):
        parts.append("<div class='msg err'>" + html.escape(state["pause_reason"]) + "</div>")
    parts.append("</div>")

    parts.append("<div class='card'>")
    parts.append("<div style='font-size:16px;font-weight:700;margin-bottom:8px'>")
    parts.append("自动质检（%d 项，通过 %d 项）" % (len(qa), passed))
    parts.append("</div>")
    parts.append("<table><thead><tr><th>检查项</th><th>结果</th><th>说明</th></tr></thead><tbody>")
    parts.append(_qa_rows(qa))
    parts.append("</tbody></table></div>")

    parts.append(_charts_html(files))
    parts.append(_download_html(files))
    parts.append(_review_html(blob, stage))

    parts.append('<div class="card"><div class="muted">事件流（最近 20 条）</div><pre>')
    parts.append(html.escape("\n".join(
        "%s  %s" % (e.get("ts", ""), e.get("type", "")) for e in events[-20:]) or "(无)"))
    parts.append("</pre></div>")

    return _layout("任务详情", "".join(parts))


# ---------------- 路由（相对路径，平台会自动去掉 /zhoubao-web 前缀） ----------------
@app.route("/", methods=["GET"])
def index():
    return _form_page("zb_" + uuid.uuid4().hex[:8])


@app.route("/run", methods=["POST"])
def run():
    """一次请求内跑完：准入校验 → 执行 → 自动质检 → 展示结果。"""
    period = (request.form.get("period") or "").strip()
    submitter = (request.form.get("submitter") or "").strip()
    task_id = ((request.form.get("task_id") or "").strip()
               or ("zb_" + uuid.uuid4().hex[:8]))
    if not period or not submitter:
        return _form_page(task_id, "请填写统计周期与发起人")

    root = tempfile.mkdtemp(prefix="mde_")
    sales = _save_upload(request.files.get("sales_file"), root, "sales")
    notes = _save_upload(request.files.get("notes_file"), root, "notes")

    st = MemoryStorage(task_id, scratch_root=root)
    runner = TaskRunner.from_storage(st, cfg())
    runner.submit_form({"period": period, "submitter": submitter,
                        "sales_file": sales, "notes_file": notes})
    runner.resume()          # 停在 HUMAN_REVIEW / PAUSED_MISSING / SUSPENDED
    return _result_page(runner.state, st.read_events())


@app.route("/review", methods=["POST"])
def review():
    """人工审核：通过则交付；退回则采纳意见重做并再次送审。"""
    blob = request.form.get("blob") or ""
    if not blob:
        return _form_page("", "状态快照缺失，请重新发起工单")
    state, events, root = _unpack(blob)
    st = _storage_with(state, events)
    runner = TaskRunner.from_storage(st, cfg())
    msg = ""
    try:
        runner.review(request.form.get("action") or "",
                      request.form.get("comment") or "")
        if runner.state.get("stage") == "EXECUTING":
            runner.resume()  # 退回后自动重做并重新送审
    except (IllegalTransition, ValueError) as e:
        msg = str(e)
    return _result_page(runner.state, st.read_events(), msg)


@app.route("/material", methods=["POST"])
def material():
    """缺材料暂停后，在页面补充材料并继续。"""
    blob = request.form.get("blob") or ""
    key = request.form.get("key") or ""
    f = request.files.get("file")
    if not blob:
        return _form_page("", "状态快照缺失，请重新发起工单")
    state, events, root = _unpack(blob)
    st = _storage_with(state, events)
    runner = TaskRunner.from_storage(st, cfg())
    msg = ""
    try:
        path = _save_upload(f, root, "sales" if key == "sales_file" else "notes")
        if not path:
            raise ValueError("请选择要补充的文件")
        runner.add_material(key, path)
        runner.resume()
    except (IllegalTransition, ValueError) as e:
        msg = str(e)
    return _result_page(runner.state, st.read_events(), msg)


@app.route("/health")
def health():
    return {"ok": True, "service": "zhoubao-web",
            "checks": list(describe_checks())}
