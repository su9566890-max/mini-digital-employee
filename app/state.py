# -*- coding: utf-8 -*-
"""状态机：流程编排与流转控制。

关键控制（能否进入审核、能否交付、非法流转拒绝、超限暂停）全部由程序判定，
不交给模型决定；每个阶段结束后原子落盘 state.json，程序中断后可从断点恢复。
"""
import os
import json
import shutil
import time

from .events import EventLog, load_state, save_state
from .tools import registry, ToolFailure
from .llm import LLMError
from .qa import run_qa

# ---------- 状态定义 ----------
FORM_SUBMITTED = "FORM_SUBMITTED"    # 任务发起（结构化表单）
ADMIT_CHECK = "ADMIT_CHECK"          # 准入校验
PAUSED_MISSING = "PAUSED_MISSING"    # 缺材料，暂停等待补充
EXECUTING = "EXECUTING"              # 执行（工具调用 + 模型产出）
AUTO_QA = "AUTO_QA"                  # 自动质检
SUSPENDED = "SUSPENDED"              # 异常暂停，转人工处理
HUMAN_REVIEW = "HUMAN_REVIEW"        # 人工审核（等待审核人）
DELIVERED = "DELIVERED"              # 已交付
FAILED = "FAILED"                    # 终态：不合规任务被拒绝

# 合法流转表：表外的流转一律拒绝并留痕
TRANSITIONS = {
    FORM_SUBMITTED: [ADMIT_CHECK],
    ADMIT_CHECK: [PAUSED_MISSING, EXECUTING, FAILED],
    PAUSED_MISSING: [ADMIT_CHECK],
    EXECUTING: [AUTO_QA, SUSPENDED],
    AUTO_QA: [HUMAN_REVIEW, EXECUTING, SUSPENDED],
    HUMAN_REVIEW: [DELIVERED, EXECUTING],
    SUSPENDED: [EXECUTING],
    DELIVERED: [],
    FAILED: [],
}


class IllegalTransition(Exception):
    pass


class TaskRunner:
    def __init__(self, workspace: str, cfg: dict, task_id: str):
        self.workspace = workspace
        self.cfg = cfg
        self.task_id = task_id
        self.task_dir = os.path.join(workspace, "tasks", task_id)
        os.makedirs(self.task_dir, exist_ok=True)
        os.makedirs(os.path.join(self.task_dir, "charts"), exist_ok=True)
        self.events = EventLog(self.task_dir)
        self.state = load_state(self.task_dir)
        if self.state is None:
            self.state = {
                "task_id": task_id,
                "stage": FORM_SUBMITTED,
                "form": {},
                "llm_rounds": 0,
                "qa_retry": 0,
                "qa_records": [],
                "review": None,
                "feedback": "",
                "artifacts": {},
                "pause_reason": "",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_state(self.task_dir, self.state)

    # ---------- 基础 ----------
    def _save(self):
        save_state(self.task_dir, self.state)

    def _transition(self, new_stage: str, reason: str = ""):
        cur = self.state["stage"]
        if cur == new_stage:
            self.state["pause_reason"] = reason
            self._save()
            return
        if new_stage not in TRANSITIONS[cur]:
            self.events.log(self.task_id, "ILLEGAL_TRANSITION",
                            {"from": cur, "to": new_stage, "reason": reason})
            raise IllegalTransition("非法流转: %s -> %s" % (cur, new_stage))
        self.state["stage"] = new_stage
        self.state["pause_reason"] = reason
        self.events.log(self.task_id, "STATE_CHANGE",
                        {"from": cur, "to": new_stage, "reason": reason})
        self._save()

    # ---------- 对外动作 ----------
    def submit_form(self, form: dict) -> dict:
        self.state["form"] = form
        self.events.log(self.task_id, "FORM_SUBMITTED", {"form": form})
        self._transition(ADMIT_CHECK, "表单提交")
        return self.state

    def add_material(self, key: str, path: str) -> dict:
        if self.state["stage"] != PAUSED_MISSING:
            self.events.log(self.task_id, "ILLEGAL_TRANSITION",
                            {"from": self.state["stage"], "to": ADMIT_CHECK,
                             "reason": "非暂停态补充材料"})
            raise IllegalTransition("仅缺材料暂停态可补充材料")
        self.state["form"][key] = path
        self.events.log(self.task_id, "MATERIAL_ADDED", {"key": key, "path": path})
        self._transition(ADMIT_CHECK, "材料已补充，重新准入校验")
        return self.state

    def resume(self) -> dict:
        """从断点/暂停继续推进，直到需要外部输入或到达终态。"""
        guard = 0
        while True:
            guard += 1
            if guard > 50:
                raise RuntimeError("状态机异常：阶段推进超过 50 次")
            stage = self.state["stage"]
            if stage in (PAUSED_MISSING, HUMAN_REVIEW, DELIVERED, FAILED, SUSPENDED):
                return self.state
            if stage == FORM_SUBMITTED:
                self._transition(ADMIT_CHECK)
            elif stage == ADMIT_CHECK:
                self._admit_check()
            elif stage == EXECUTING:
                self._execute()
            elif stage == AUTO_QA:
                self._auto_qa()
            elif stage == DELIVERED:
                return self.state
            else:
                return self.state

    def review(self, action: str, comment: str = "") -> dict:
        """人工审核：approve / reject，退回意见记录在案并在重做时被采纳。"""
        if self.state["stage"] != HUMAN_REVIEW:
            self.events.log(self.task_id, "ILLEGAL_TRANSITION",
                            {"from": self.state["stage"], "to": EXECUTING,
                             "reason": "非待审状态执行审核"})
            raise IllegalTransition("当前状态 %s 不可审核" % self.state["stage"])
        if action == "approve":
            self.state["review"] = {"action": "approve", "comment": comment,
                                    "time": time.strftime("%Y-%m-%d %H:%M:%S")}
            self.events.log(self.task_id, "HUMAN_REVIEW", {"action": "approve"})
            self._transition(DELIVERED, "人工审核通过")
            self._deliver()
        elif action == "reject":
            if not comment.strip():
                raise ValueError("退回必须附审核意见")
            self.state["review"] = {"action": "reject", "comment": comment,
                                    "time": time.strftime("%Y-%m-%d %H:%M:%S")}
            self.state["feedback"] = comment  # 重做时写入提示词 = 采纳退回意见
            self.state["qa_retry"] = 0
            self.events.log(self.task_id, "HUMAN_REVIEW",
                            {"action": "reject", "comment": comment})
            self._transition(EXECUTING, "审核退回：" + comment)
        else:
            raise ValueError("action 须为 approve / reject")
        return self.state

    # ---------- 阶段实现 ----------
    def _admit_check(self):
        form = self.state["form"]
        res = registry.call("validate_form", {"form": form},
                            task_id=self.task_id, events=self.events)
        if res["missing_fields"]:
            self.events.log(self.task_id, "ADMIT_REJECTED",
                            {"missing_fields": res["missing_fields"]})
            self._transition(FAILED, "表单字段缺失: %s" % res["missing_fields"])
            return
        if res["missing_materials"]:
            self._transition(PAUSED_MISSING,
                             "材料缺失: %s，请发起人补充后继续" % res["missing_materials"])
            return
        self.events.log(self.task_id, "ADMIT_PASSED", {})
        self._transition(EXECUTING)

    def _check_limits(self) -> bool:
        lim = self.cfg["limits"]
        if self.state["llm_rounds"] >= lim["max_llm_rounds"]:
            self.events.log(self.task_id, "LIMIT_EXCEEDED",
                            {"kind": "llm_rounds", "value": self.state["llm_rounds"]})
            self._transition(SUSPENDED, "模型调用轮次超限，转人工处理")
            return True
        return False

    def _execute(self):
        from .llm import build_llm
        form = self.state["form"]
        if self._check_limits():
            return
        llm = build_llm(self.cfg)
        try:
            sales = registry.call("read_sales_data", {"path": form["sales_file"]},
                                  task_id=self.task_id, events=self.events)
            notes = registry.call("read_meeting_notes", {"path": form["notes_file"]},
                                  task_id=self.task_id, events=self.events)
            analysis = registry.call("analyze_sales", {"sales": sales},
                                     task_id=self.task_id, events=self.events)
            try:
                narrative = llm.generate_narrative(
                    analysis=analysis, notes=notes,
                    employee=self.cfg["employee"],
                    feedback=self.state.get("feedback", ""))
                self.state["llm_rounds"] += 1
                self.events.log(self.task_id, "LLM_CALL",
                                {"round": self.state["llm_rounds"], "mode": llm.mode})
            except (LLMError, TimeoutError) as e:
                self.events.log(self.task_id, "LLM_ERROR", {"error": str(e)})
                self._transition(SUSPENDED, "模型调用失败/超时，转人工处理")
                return
            out_path = os.path.join(self.task_dir, "weekly_report.docx")
            art = registry.call("generate_weekly_report", {
                "period": form["period"],
                "analysis": analysis,
                "narrative": narrative,
                "feedback": self.state.get("feedback", ""),
                "out_path": out_path,
                "charts_dir": os.path.join(self.task_dir, "charts"),
            }, task_id=self.task_id, events=self.events)
            self.state["artifacts"]["report"] = art["report"]
            self.state["artifacts"]["charts"] = art["charts"]
            self.state["artifacts"]["analysis"] = analysis
            self.state["artifacts"]["narrative"] = narrative
            self._save()
        except ToolFailure as e:
            # 工具多次失败：任务暂停并说明原因，不崩溃、不编造结果
            self.events.log(self.task_id, "TASK_SUSPENDED", {"reason": str(e)})
            self._transition(SUSPENDED, "工具调用失败且重试无效：" + str(e))
            return
        self._transition(AUTO_QA, "执行完成")

    def _auto_qa(self):
        cfg = self.cfg
        records, passed = run_qa(cfg, self.state["artifacts"])
        self.state["qa_records"] = [r.__dict__ if hasattr(r, "__dict__") else r for r in records]
        self.events.log(self.task_id, "AUTO_QA",
                        {"passed": passed, "records": self.state["qa_records"]})
        self._save()
        if passed:
            self._transition(HUMAN_REVIEW, "自动质检通过，等待人工审核")
            return
        limit = cfg["limits"]["max_qa_retry"]
        if self.state["qa_retry"] < limit:
            self.state["qa_retry"] += 1
            self.state["feedback"] = "质检未通过项：" + "；".join(
                "%s(%s)" % (r["name"], r["detail"]) for r in self.state["qa_records"]
                if not r["passed"])
            self.events.log(self.task_id, "QA_AUTO_FIX",
                            {"retry": self.state["qa_retry"],
                             "advice": self.state["feedback"]})
            self._transition(EXECUTING, "质检未通过，自动修正重试 %s/%s"
                             % (self.state["qa_retry"], limit))
            return
        self._transition(SUSPENDED, "质检修正超出重试上限，转人工处理")

    def _deliver(self):
        from .qa import write_qa_record
        out_dir = os.path.join(self.workspace, "deliveries", self.task_id)
        os.makedirs(out_dir, exist_ok=True)
        report = self.state["artifacts"].get("report")
        if report and os.path.exists(report):
            shutil.copy(report, os.path.join(
                out_dir, "%s_经营周报.docx" % self.state["form"].get("period", self.task_id)))
        for src in self.state["artifacts"].get("charts", []):
            if os.path.exists(src):
                shutil.copy(src, out_dir)
        self.state["artifacts"]["delivery_dir"] = out_dir
        record_path = write_qa_record(out_dir, self.state)
        self.state["artifacts"]["qa_record"] = record_path
        shutil.copy(os.path.join(self.task_dir, "events.jsonl"),
                    os.path.join(out_dir, "events.jsonl"))
        self.events.log(self.task_id, "DELIVERED", {"dir": out_dir})
        self._save()
