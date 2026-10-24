# -*- coding: utf-8 -*-
"""Web 工单台（Flask + 原生 Jinja 模板）。

启动： python main.py web [--port 5000]
页面： 任务列表 / 发起工单(含文件上传) / 任务详情(状态流转 + 事件流 + 审核 + 补材料 + 断点恢复)

所有操作最终仍然走 app.state.TaskRunner，网页只是壳：
关键控制依旧由状态机的流转表判定，网页上的按钮是否可用也由程序决定。
"""
import json
import os
import time

from flask import Flask, flash, redirect, render_template, request, send_file, url_for

import main as app_main
from app.qa import describe_checks
from app.state import TaskRunner


def create_app(workspace: str):
    uploads_dir = os.path.join(workspace, "uploads")
    os.makedirs(uploads_dir, exist_ok=True)

    app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), "templates"),
                static_folder=os.path.join(os.path.dirname(__file__), "static"))
    app.secret_key = "mini-digital-employee-local"

    def cfg():
        return app_main.load_cfg()

    def runner(task_id):
        return TaskRunner(workspace, cfg(), task_id)

    def tasks_dir():
        return os.path.join(workspace, "tasks")

    def list_tasks():
        root_dir = tasks_dir()
        items = []
        if not os.path.isdir(root_dir):
            return items
        for tid in os.listdir(root_dir):
            st_path = os.path.join(root_dir, tid, "state.json")
            if not os.path.exists(st_path):
                continue
            with open(st_path, "r", encoding="utf-8") as f:
                st = json.load(f)
            recs = st.get("qa_records", [])
            items.append({
                "task_id": tid,
                "stage": st["stage"],
                "period": st.get("form", {}).get("period"),
                "submitter": st.get("form", {}).get("submitter"),
                "qa_total": len(recs),
                "qa_passed": len([r for r in recs if r["passed"]]),
                "updated": time.strftime("%Y-%m-%d %H:%M:%S",
                                         time.localtime(os.path.getmtime(st_path))),
            })
        items.sort(key=lambda x: x["updated"], reverse=True)
        return items

    def save_upload(task_id, file_storage, key):
        """保留中文文件名的同时去掉路径分隔符，避免目录穿越。"""
        raw = os.path.basename(file_storage.filename or "upload.xlsx")
        safe = "".join(ch for ch in raw if ch not in '\\/:*?"<>|').strip() or "upload.xlsx"
        dest_dir = os.path.join(tasks_dir(), task_id, "uploads")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, key + "_" + safe)
        file_storage.save(dest)
        return dest

    def render_events(r, limit=40):
        out = []
        for e in reversed(r.events.read_all()[-limit:]):
            p = e["payload"]
            bits = []
            for k, v in p.items():
                if k in ("trace",):
                    continue
                bits.append("%s=%s" % (k, str(v)[:90]))
            out.append({"ts": e["ts"], "type": e["type"], "render": "，".join(bits) or "-"})
        return out

    # ---------------- 路由 ----------------
    @app.route("/")
    def index():
        return render_template("index.html", tasks=list_tasks())

    @app.route("/new", methods=["GET", "POST"])
    def new_task():
        if request.method == "GET":
            return render_template("new.html")
        tid = request.form.get("task_id", "").strip() or ("task_" + time.strftime("%m%d_%H%M%S"))
        sf, nf = request.files.get("sales_file"), request.files.get("notes_file")
        period = request.form.get("period", "").strip()
        submitter = request.form.get("submitter", "").strip()
        # 材料允许缺——缺材料时任务停在 PAUSED_MISSING，可在详情页补充后继续
        if not period or not submitter:
            flash("请填写统计周期与发起人", "err")
            return redirect(url_for("new_task"))
        # 注意：TaskRunner 构造时会写入初始 state.json，因此重复判断必须在构造前读取
        st_file = os.path.join(tasks_dir(), tid, "state.json")
        existing = None
        if os.path.exists(st_file):
            with open(st_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        r = runner(tid)
        if existing and existing.get("stage") not in ("FORM_SUBMITTED", "DELIVERED", "FAILED"):
            flash("该任务 ID 已存在且处于流转中（%s），请换一个 ID" % existing.get("stage"), "err")
            return redirect(url_for("new_task"))
        sales_path = save_upload(tid, sf, "sales") if sf and sf.filename else ""
        notes_path = save_upload(tid, nf, "notes") if nf and nf.filename else ""
        r.submit_form({"period": period, "submitter": submitter,
                       "sales_file": sales_path, "notes_file": notes_path})
        r.resume()
        flash("任务已发起并执行到：%s" % r.state["stage"], "ok")
        return redirect(url_for("task_detail", task_id=tid))

    @app.route("/task/<task_id>")
    def task_detail(task_id):
        r = runner(task_id)
        st = r.state
        report_url = None
        charts = []
        if st["artifacts"].get("report"):
            report_url = url_for("serve_file", task_id=task_id, name="weekly_report.docx")
        for c in st["artifacts"].get("charts", []):
            charts.append(os.path.basename(c))
        return render_template("task.html", task=st, events=render_events(r),
                               report_url=report_url, charts=charts)

    @app.route("/task/<task_id>/resume", methods=["POST"])
    def resume(task_id):
        r = runner(task_id)
        before = r.state["stage"]
        r.resume()
        flash("%s → %s" % (before, r.state["stage"]), "ok")
        return redirect(url_for("task_detail", task_id=task_id))

    @app.route("/task/<task_id>/material", methods=["POST"])
    def add_material(task_id):
        r = runner(task_id)
        key = request.form.get("key")
        f = request.files.get("file")
        if not f or key not in ("sales_file", "notes_file"):
            flash("请选择要补充的材料类型与文件", "err")
            return redirect(url_for("task_detail", task_id=task_id))
        path = save_upload(task_id, f, key)
        try:
            r.add_material(key, path)
            r.resume()
            flash("材料已补充，任务继续至：%s" % r.state["stage"], "ok")
        except Exception as e:
            flash("补充失败：%s" % e, "err")
        return redirect(url_for("task_detail", task_id=task_id))

    @app.route("/task/<task_id>/review", methods=["POST"])
    def review(task_id):
        r = runner(task_id)
        action = request.form.get("action")
        comment = request.form.get("comment", "").strip()
        try:
            r.review(action, comment)
            if r.state["stage"] == "EXECUTING":
                r.resume()  # 退回后自动重做并重新送审
            flash("已执行审核动作：%s（当前 %s）" % (action, r.state["stage"]), "ok")
        except Exception as e:
            flash("审核操作失败：%s" % e, "err")
        return redirect(url_for("task_detail", task_id=task_id))

    @app.route("/files/<task_id>/<path:name>")
    def serve_file(task_id, name):
        base = os.path.join(tasks_dir(), task_id)
        if name.startswith("__delivery__"):
            base = os.path.join(workspace, "deliveries", task_id)
            name = name.replace("__delivery__", "")
        # 目录穿越防护
        target = os.path.normpath(os.path.join(base, name))
        if not target.startswith(os.path.normpath(base)) or not os.path.exists(target):
            return "文件不存在", 404
        return send_file(target, as_attachment=False)

    @app.route("/health")
    def health():
        return {"ok": True, "checks": list(describe_checks())}

    return app


def run(workspace: str, port: int = 5000, host: str = "127.0.0.1"):
    app = create_app(workspace)
    print("Web 工单台已启动： http://%s:%d   （Ctrl+C 停止）" % (host, port))
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
