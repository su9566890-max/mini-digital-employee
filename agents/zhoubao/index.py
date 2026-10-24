# -*- coding: utf-8 -*-
"""经营周报数字员工 —— EdgeOne Makers Agent 入口。

文件即路由：部署后对应线上路径 ``/zhoubao``。
请求头必须携带 ``Makers-Conversation-Id``（平台约定，缺失返回 400）。

与本地同一套状态机（app/state.py）与质检（app/qa.py），差别只在持久化后端：
本地走 tasks/<id>/state.json，云端走 context.store —— 数据由平台持久到 Blob，
实例回收或请求落到别的实例都不丢，断点恢复在 Serverless 上依然成立。

请求体（JSON）
--------------
==============  ==========================================================
action          submit / add_material / resume / review / status（默认 status）
task_id         任务编号，如 ``zb_2026W44_001``
period          统计周期，如「2026 年第 44 周」（submit 必填）
submitter       发起人（submit 必填）
sales_file      销售明细内容，base64（submit 必填）；sales_name 为原始文件名
notes_file      周会纪要内容，base64（submit 必填）；notes_name 为原始文件名
key             add_material 时补哪份材料（sales_file / notes_file）
content         add_material 时的 base64 内容；name 为原始文件名
decision        review 时 approve / reject
comment         review 意见（reject 必填，会被记入重做提示词）
with_files      交付后是否随响应返回产物 base64（默认 true）
==============  ==========================================================

示例
----
::

    curl -X POST https://<your-app>.edgeone.app/zhoubao \\
      -H "Makers-Conversation-Id: conv_demo_001" -H "Content-Type: application/json" \\
      -d '{"action":"submit","task_id":"zb_001","period":"2026 年第 44 周",
           "submitter":"张店长","sales_name":"销售明细.xlsx","sales_file":"<b64>",
           "notes_name":"周会纪要.docx","notes_file":"<b64>"}'
"""
import base64
import json
import os
import sys

# ---- 让入口能引用 _vendor 里的核心包（本地调试时回退仓库根）----
_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.abspath(os.path.join(_HERE, "..", "_vendor")),
              os.path.abspath(os.path.join(_HERE, "..", ".."))):
    if os.path.isdir(os.path.join(_cand, "app")) and _cand not in sys.path:
        sys.path.insert(0, _cand)
        _ROOT = _cand
        break

import yaml  # noqa: E402

from app.state import TaskRunner, IllegalTransition  # noqa: E402
from app.storage import MakersStorage  # noqa: E402
from app.tools import registry  # noqa: E402

CFG_PATH = os.path.join(_ROOT, "config", "employee_zhoubao.yaml")
_CACHE = {"cfg": None}


def _cfg():
    """加载员工能力配置；白名单/重试上限按配置初始化工具注册表。"""
    if _CACHE["cfg"] is None:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            c = yaml.safe_load(f)
        registry.set_policy(c["tools_allowed"], c["limits"]["max_tool_retry"])
        _CACHE["cfg"] = c
    return _CACHE["cfg"]


# ---------------- 请求解析（防御式：平台 context 形态可能有差异） ----------------
def _body(context) -> dict:
    req = getattr(context, "request", None)
    candidates = []
    if req is not None:
        candidates += [lambda: getattr(req, "body", None),
                       lambda: req.json() if hasattr(req, "json") else None]
    candidates += [lambda: getattr(context, "body", None)]
    for get in candidates:
        try:
            v = get()
        except Exception:
            continue
        if isinstance(v, (dict, list)):
            return v if isinstance(v, dict) else {}
        if isinstance(v, (bytes, bytearray)) and v.strip():
            try:
                return json.loads(bytes(v).decode("utf-8"))
            except Exception:
                continue
        if isinstance(v, str) and v.strip():
            try:
                return json.loads(v)
            except Exception:
                continue
    return {}


def _save_material(st: MakersStorage, key: str, b64: str, name: str) -> str:
    """把 base64 材料落成真实文件（pandas / python-docx 只认真实路径）。"""
    raw = base64.b64decode(b64)
    safe = os.path.basename(name or key)
    p = st.scratch("materials", key, safe)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(raw)
    return p


def _resp(payload: dict, status: int = 200):
    """与 EdgeOne Agent 运行时对齐的响应信封（与官方模板一致）：
    200 直接返回 dict；非 200 用 {"status_code", "body"} 包装。
    """
    if status != 200:
        body = dict(payload)
        body["ok"] = False
        return {"status_code": status, "body": body}
    out = {"ok": True}
    out.update(payload)
    return out


async def handler(context):
    # 平台注入的环境变量优先（AI Gateway 的 Key/URL/模型名都在这里）
    env = getattr(context, "env", None)
    if isinstance(env, dict):
        for k, v in env.items():
            os.environ.setdefault(str(k), str(v))

    body = _body(context)
    action = (body.get("action") or "status").strip().lower()
    task_id = (body.get("task_id") or "").strip()
    if not task_id:
        return _resp({"error": "缺少 task_id"})

    cfg = _cfg()
    st = MakersStorage(context, task_id)
    await st.load()
    # 平台 store 可用性如实上报：不可用时降级为“本次请求内有效”，不假装能断点恢复
    store_state = getattr(getattr(context, "store", None), "state", None)
    persistence_ok = bool(store_state) and hasattr(store_state, "get") \
        and hasattr(store_state, "set")
    runner = TaskRunner.from_storage(st, cfg)
    current = runner.state.get("stage")

    try:
        if action == "submit":
            if current not in (None, "FORM_SUBMITTED") :
                return _resp({"error": "任务 %s 已存在（%s），换一个 task_id 或直接 action=status"
                                       % (task_id, current)}, 409)
            missing = [k for k in ("period", "submitter", "sales_file", "notes_file")
                       if not body.get(k)]
            if missing:
                return _resp({"error": "submit 缺少字段: %s" % missing}, 400)
            form = {
                "period": body["period"], "submitter": body["submitter"],
                "sales_file": _save_material(st, "sales_file", body["sales_file"],
                                             body.get("sales_name", "销售明细.xlsx")),
                "notes_file": _save_material(st, "notes_file", body["notes_file"],
                                             body.get("notes_name", "周会纪要.docx")),
            }
            runner.submit_form(form)
            runner.resume()

        elif action == "add_material":
            key = body.get("key", "")
            if key not in ("sales_file", "notes_file") or not body.get("content"):
                return _resp({"error": "add_material 需要 key=sales_file/notes_file 与 content(base64)"}, 400)
            path = _save_material(st, key, body["content"], body.get("name", key))
            runner.add_material(key, path)
            runner.resume()

        elif action == "resume":
            runner.resume()

        elif action == "review":
            decision = body.get("decision", "")
            if decision not in ("approve", "reject"):
                return _resp({"error": "decision 须为 approve / reject"}, 400)
            runner.review(decision, body.get("comment", ""))

        elif action == "status":
            pass

        else:
            return _resp({"error": "未知 action: %s" % action}, 400)
    except IllegalTransition as e:
        # 非法流转已被事件流留痕；这里只把拒绝原因如实返回
        await st.flush()
        return _resp({"error": str(e), "stage": runner.state.get("stage")}, 409)

    await st.flush()

    s = runner.state
    with_files = bool(body.get("with_files", True))
    payload = {
        "task_id": task_id,
        "stage": s.get("stage"),
        "pause_reason": s.get("pause_reason", ""),
        "llm_rounds": s.get("llm_rounds", 0),
        "qa_records": s.get("qa_records", []),
        "review": s.get("review"),
        "delivery": s.get("artifacts", {}).get("delivery_ref")
                    or s.get("artifacts", {}).get("delivery_dir"),
        "persistence": "ok" if persistence_ok else "degraded(无 store，仅本次请求内有效)",
        "events_tail": [e.get("type") for e in st.read_events()[-8:]],
    }
    if s.get("stage") == "DELIVERED" and with_files:
        payload["files"] = s.get("artifacts", {}).get("files", {})
    return _resp(payload)
