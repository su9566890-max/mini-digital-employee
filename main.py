# -*- coding: utf-8 -*-
"""迷你数字员工 · 命令行入口（一键运行）。

    python main.py init-data                      生成模拟业务数据
    python main.py submit  --form form.json       结构化表单发起任务
    python main.py resume  <task_id>              从断点/暂停继续推进
    python main.py add-material <task_id> <key> <path>   补充缺失材料
    python main.py review  <task_id> approve|reject --comment "意见"
    python main.py status  <task_id>              查看状态/质检记录/事件
    python main.py tools                          查看工具清单与入参出参
    python main.py demo                           端到端演示（正常 + 退回 + 交付）
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import yaml  # noqa: E402

from app.state import TaskRunner  # noqa: E402
from app.tools import registry  # noqa: E402
from app.qa import describe_checks  # noqa: E402

CFG_PATH = os.path.join(ROOT, "config", "employee_zhoubao.yaml")


def load_cfg(path=CFG_PATH):
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    registry.set_policy(cfg["tools_allowed"], cfg["limits"]["max_tool_retry"])
    return cfg


def get_runner(cfg, task_id):
    return TaskRunner(ROOT, cfg, task_id)


def cmd_submit(args):
    cfg = load_cfg()
    with open(args.form, "r", encoding="utf-8") as f:
        form = json.load(f)
    task_id = args.task_id or ("task_%s" % form.get("period", "x").replace(" ", "").replace("-", ""))
    r = get_runner(cfg, task_id)
    if args.reuse and r.state["form"]:
        r.state["form"] = form
        r._save()
    else:
        r.submit_form(form)
    print("[已发起] task_id=%s 表单=%s" % (task_id, json.dumps(form, ensure_ascii=False)))
    state = r.resume()
    _print_state(state)


def cmd_resume(args):
    cfg = load_cfg()
    r = get_runner(cfg, args.task_id)
    state = r.resume()
    _print_state(state)


def cmd_add_material(args):
    cfg = load_cfg()
    r = get_runner(cfg, args.task_id)
    r.add_material(args.key, args.path)
    state = r.resume()
    _print_state(state)


def cmd_review(args):
    cfg = load_cfg()
    r = get_runner(cfg, args.task_id)
    state = r.review(args.action, args.comment or "")
    if state["stage"] == "EXECUTING":
        state = r.resume()  # 退回后自动重做并重新送审
    _print_state(state)


def cmd_status(args):
    cfg = load_cfg()
    r = get_runner(cfg, args.task_id)
    state = r.state
    _print_state(state)
    if args.events:
        for e in r.events.read_all():
            print("  %s | %-20s | %s" % (e["ts"], e["type"], json.dumps(e["payload"], ensure_ascii=False)[:120]))


def cmd_tools(args):
    cfg = load_cfg()
    print("== 工具（仅白名单内可调用）==")
    for name, spec in registry.describe().items():
        print("- %s：%s" % (name, spec["desc"]))
        print("    in : %s\n    out: %s" % (spec["input"], spec["output"]))
    print("\n== 自动质检规则 ==")
    for k, v in describe_checks().items():
        mark = "√" if k in cfg["qa_rules"] else " "
        print("[%s] %s：%s" % (mark, k, v))


def cmd_init_data(args):
    from scripts.make_sample_data import build_all
    out = build_all(ROOT)
    print("示例数据已生成：")
    for k, v in out.items():
        print("  %s: %s" % (k, v))


def cmd_demo(args):
    """端到端演示：正常发起 → 质检 → 人工退回 → 采纳重做 → 通过 → 交付。"""
    cfg = load_cfg()
    data = os.path.join(ROOT, "sample_data")
    if not os.path.exists(os.path.join(data, "销售明细.xlsx")):
        cmd_init_data(args)
    import time
    task_id = "demo_%s" % time.strftime("%m%d_%H%M%S")  # 每次演示一个独立任务，交付物互不覆盖
    form = {
        "period": "2026 年第 44 周",
        "sales_file": os.path.join(data, "销售明细.xlsx"),
        "notes_file": os.path.join(data, "周会纪要.docx"),
        "submitter": "张店长",
    }
    r = get_runner(cfg, task_id)
    r.submit_form(form)
    print("① 已发起任务")
    r.resume()
    print("② 执行与质检完成，当前状态：%s" % r.state["stage"])
    print("③ 人工审核退回")
    r.review("reject", "结论段落偏少，请补充针对低增长门店的具体动作")
    r.resume()
    print("④ 已重做并重新送审，状态：%s" % r.state["stage"])
    r.review("approve", "可以使用")
    print("⑤ 交付完成")
    _print_state(r.state)


def _print_state(state):
    print("---- 任务 %s ----" % state["task_id"])
    print("当前状态 : %s" % state["stage"])
    if state["pause_reason"]:
        print("说明     : %s" % state["pause_reason"])
    print("模型轮次 : %s" % state["llm_rounds"])
    if state["artifacts"].get("report"):
        print("周报     : %s" % state["artifacts"]["report"])
    if state["artifacts"].get("delivery_dir"):
        print("交付目录 : %s" % state["artifacts"]["delivery_dir"])
    if state["qa_records"]:
        print("质检记录 :")
        for rec in state["qa_records"]:
            print("   [%s] %s - %s" % ("通过" if rec["passed"] else "不通", rec["name"], rec["detail"]))


def main():
    ap = argparse.ArgumentParser("迷你数字员工")
    sub = ap.add_subparsers()
    p = sub.add_parser("submit"); p.add_argument("--form", required=True)
    p.add_argument("--task-id"); p.add_argument("--reuse", action="store_true")
    p.set_defaults(func=cmd_submit)
    p = sub.add_parser("resume"); p.add_argument("task_id")
    p.set_defaults(func=cmd_resume)
    p = sub.add_parser("add-material")
    p.add_argument("task_id"); p.add_argument("key"); p.add_argument("path")
    p.set_defaults(func=cmd_add_material)
    p = sub.add_parser("review")
    p.add_argument("task_id"); p.add_argument("action", choices=["approve", "reject"])
    p.add_argument("--comment", default="")
    p.set_defaults(func=cmd_review)
    p = sub.add_parser("status"); p.add_argument("task_id")
    p.add_argument("--events", action="store_true"); p.set_defaults(func=cmd_status)
    p = sub.add_parser("tools"); p.set_defaults(func=cmd_tools)
    p = sub.add_parser("init-data"); p.set_defaults(func=cmd_init_data)
    p = sub.add_parser("demo"); p.set_defaults(func=cmd_demo)
    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return
    args.func(args)


if __name__ == "__main__":
    main()
