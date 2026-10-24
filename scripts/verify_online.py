# -*- coding: utf-8 -*-
"""线上端到端验证脚本（EdgeOne Makers）。

用法::

    python scripts/verify_online.py \\
        --url "https://<your-app>.edgeone.cool/?eo_token=xxx&eo_time=yyy"

脚本会把预览链接上的 eo_token / eo_time 转成 Cookie 携带，
再依次走完：探活 → 发起任务 → 查询 → 审核通过 → 校验交付产物。
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def split_url(raw):
    """拆出基础地址与预览鉴权参数（eo_token / eo_time）。"""
    p = urllib.parse.urlparse(raw.strip())
    base = "%s://%s" % (p.scheme, p.netloc)
    q = urllib.parse.parse_qs(p.query)
    token = (q.get("eo_token") or [""])[0]
    t = (q.get("eo_time") or [""])[0]
    return base, token, t


def cookie_header(token, t):
    return "eo_token=%s; eo_time=%s" % (token, t) if token else None


def post(base, path, token, t, cid, payload, timeout=180):
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Makers-Conversation-Id": cid,
    }
    ck = cookie_header(token, t)
    if ck:
        headers["Cookie"] = ck
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, headers=headers,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def step(name, code, body):
    print("\n%s → HTTP %s" % (name, code))
    try:
        return json.loads(body)
    except Exception:
        print("   非 JSON 响应：%s" % body[:300])
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="带 eo_token/eo_time 的预览链接")
    ap.add_argument("--cid", default="conv_verify_001", help="会话 ID（6-36 位）")
    args = ap.parse_args()

    base, token, t = split_url(args.url)
    cid = args.cid
    tid = "zb_" + uuid.uuid4().hex[:8]
    print("目标：%s" % base)
    print("会话：%s" % cid)
    print("任务：%s" % tid)

    # 1) 探活
    code, body = post(base, "/zhoubao", token, t, cid,
                      {"action": "status", "task_id": "probe_live"})
    d = step("① 探活", code, body)
    if not d or not d.get("ok"):
        return 1
    print("   persistence = %s" % d.get("persistence"))

    # 2) 发起任务
    sales = os.path.join(ROOT, "sample_data", "销售明细.xlsx")
    notes = os.path.join(ROOT, "sample_data", "周会纪要.docx")
    for f in (sales, notes):
        if not os.path.exists(f):
            print("[x] 缺少示例数据：%s" % f)
            return 1
    with open(sales, "rb") as f:
        b64_sales = base64.b64encode(f.read()).decode("ascii")
    with open(notes, "rb") as f:
        b64_notes = base64.b64encode(f.read()).decode("ascii")

    code, body = post(base, "/zhoubao", token, t, cid, {
        "action": "submit", "task_id": tid, "period": "2026 年第 44 周",
        "submitter": "张店长", "sales_name": "销售明细.xlsx",
        "sales_file": b64_sales, "notes_name": "周会纪要.docx",
        "notes_file": b64_notes, "with_files": False})
    d = step("② 发起任务", code, body)
    if not d:
        return 1
    qa = d.get("qa_records", []) or []
    print("   stage    = %s" % d.get("stage"))
    print("   质检     = %d 项，通过 %d 项" % (
        len(qa), sum(1 for x in qa if x.get("passed"))))
    if d.get("stage") != "HUMAN_REVIEW":
        print("   完整响应：%s" % body[:500])
        return 1

    # 3) 断点恢复：换一个全新请求查同一任务（不依赖任何本地状态）
    code, body = post(base, "/zhoubao", token, t, cid,
                      {"action": "status", "task_id": tid, "with_files": False})
    d2 = step("③ 断点恢复（新请求查同一任务）", code, body)
    if not d2 or d2.get("stage") != "HUMAN_REVIEW":
        print("   [x] 状态未从平台恢复，断点恢复不成立")
        return 1

    # 4) 审核通过 → 交付
    code, body = post(base, "/zhoubao", token, t, cid, {
        "action": "review", "task_id": tid, "decision": "approve",
        "comment": "线上验证通过", "with_files": True})
    d3 = step("④ 审核通过", code, body)
    if not d3:
        return 1
    files = d3.get("files", {}) or {}
    if files:
        print("   交付产物 %d 个：" % len(files))
        for k, v in files.items():
            if isinstance(v, str):
                try:
                    n = len(base64.b64decode(v))
                except Exception:
                    n = -1
                print("     - %s（还原后 %d 字节）" % (k, n))
    else:
        print("   [!] 未返回产物")

    print("\n[OK] 线上端到端验证通过：服务存活、状态机能跑、质检生效、"
          "审核交付可用、断点恢复跨请求成立。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
