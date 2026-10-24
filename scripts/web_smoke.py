# -*- coding: utf-8 -*-
"""Web 工单台端到端冒烟脚本（不依赖 requests，用标准库拼 multipart）。

    python scripts/web_smoke.py [--port 5056]
覆盖：发起工单(含上传) → 状态推进 → 查看详情 → 退回 → 通过 → 交付下载
"""
import argparse
import io
import json
import os
import sys
import uuid

import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def multipart(fields: dict, files: dict):
    boundary = "----boundary_%s" % uuid.uuid4().hex
    buf = io.BytesIO()

    def w(b):
        buf.write(b if isinstance(b, bytes) else b.encode("utf-8"))

    for k, v in fields.items():
        w("--%s\r\n" % boundary)
        w('Content-Disposition: form-data; name="%s"\r\n\r\n' % k)
        w("%s\r\n" % v)
    for k, path in files.items():
        name = os.path.basename(path)
        data = open(path, "rb").read()
        w("--%s\r\n" % boundary)
        w('Content-Disposition: form-data; name="%s"; filename="%s"\r\n' % (k, name))
        w("Content-Type: application/octet-stream\r\n\r\n")
        buf.write(data)
        w("\r\n")
    w("--%s--\r\n" % boundary)
    return buf.getvalue(), boundary


def post(url, data=None, fields=None, files=None):
    if fields is not None:
        body, boundary = multipart(fields, files or {})
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "multipart/form-data; boundary=%s" % boundary)
    else:
        req = urllib.request.Request(url, data=(data or b""), method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.status, resp.read().decode("utf-8", "ignore")


def get(url):
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.status, resp.read().decode("utf-8", "ignore")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args()
    base = "http://127.0.0.1:%d" % args.port
    tid = "smoke_" + uuid.uuid4().hex[:6]
    sd = os.path.join(ROOT, "sample_data")

    st, body = get(base + "/")
    print("① 首页            → HTTP %s，包含任务列表：%s" % (st, "任务列表" in body))

    st, body = post(base + "/new", fields={
        "task_id": tid, "period": "2026 年第 44 周", "submitter": "冒烟测试"}, files={
        "sales_file": os.path.join(sd, "销售明细.xlsx"),
        "notes_file": os.path.join(sd, "周会纪要.docx")})
    print("② 发起工单+上传    → HTTP %s" % st)

    st, body = get("%s/task/%s" % (base, tid))
    stage = None
    for token in ["DELIVERED", "HUMAN_REVIEW", "PAUSED_MISSING", "SUSPENDED", "EXECUTING", "FAILED"]:
        if token in body:
            stage = token
            break
    print("③ 任务详情         → HTTP %s，状态徽章：%s" % (st, stage))
    print("   含事件流：%s；含质检记录：%s" % ("事件流" in body, "自动质检" in body))

    st, _ = post("%s/task/%s/review" % (base, tid),
                 fields={"action": "reject", "comment": "结论段落偏少，请补充低增长门店动作"})
    st, body = get("%s/task/%s" % (base, tid))
    print("④ 退回修改         → HTTP %s，退回后重做送审：%s" % (st, "HUMAN_REVIEW" in body))

    st, _ = post("%s/task/%s/review" % (base, tid),
                 fields={"action": "approve", "comment": "可以使用"})
    st, body = get("%s/task/%s" % (base, tid))
    print("⑤ 审核通过         → HTTP %s，已交付：%s" % (st, "DELIVERED" in body))

    st, body = get("%s/files/%s/weekly_report.docx" % (base, tid))
    size = body.encode("utf-8", "ignore") if isinstance(body, str) else b""
    print("⑥ 下载周报         → HTTP %s" % st)

    # 缺材料场景：只上传销售数据 → 应停在 PAUSED_MISSING，补材料后自动继续
    tid2 = "missing_" + uuid.uuid4().hex[:5]
    post(base + "/new", fields={"task_id": tid2, "period": "测试周期", "submitter": "冒烟"}, files={
        "sales_file": os.path.join(sd, "销售明细.xlsx")})
    st, body = get("%s/task/%s" % (base, tid2))
    paused = "PAUSED_MISSING" in body
    print("⑦ 缺材料暂停       → HTTP %s，暂停态：%s，页面给出补件入口：%s"
          % (st, paused, "上传并继续" in body))

    st, body = post("%s/task/%s/material" % (base, tid2), fields={"key": "notes_file"}, files={
        "file": os.path.join(sd, "周会纪要.docx")})
    st, body = get("%s/task/%s" % (base, tid2))
    print("⑧ 网页补充材料     → HTTP %s，恢复后送审：%s" % (st, "HUMAN_REVIEW" in body))

    print("\n冒烟完成，打开浏览器查看：%s" % base)


if __name__ == "__main__":
    main()
