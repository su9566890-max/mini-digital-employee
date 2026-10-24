# -*- coding: utf-8 -*-
"""工具注册表：白名单管控 + 失败重试 + 统一入参/出参 + 调用留痕。

员工只能调用白名单内的工具；越权调用被拒绝并记安全事件；
工具失败按上限重试，仍失败则抛出 ToolFailure（任务暂停转人工，不崩溃、不编造）。
"""
import time
import traceback

from . import implementations as impl

TOOLS = {
    "validate_form": {
        "desc": "校验任务表单字段与材料文件是否存在",
        "input": {"form": "dict: period, sales_file, notes_file, submitter"},
        "output": {"missing_fields": "list[str]", "missing_materials": "list[str]"},
        "func": impl.validate_form,
    },
    "read_sales_data": {
        "desc": "读取门店销售明细（Excel/CSV）为结构化记录",
        "input": {"path": "str: xlsx/csv 路径"},
        "output": {"columns": "list[str]", "rows": "list[dict]"},
        "func": impl.read_sales_data,
    },
    "read_meeting_notes": {
        "desc": "读取周会纪要（docx/txt）正文",
        "input": {"path": "str: docx/txt 路径"},
        "output": {"text": "str"},
        "func": impl.read_meeting_notes,
    },
    "analyze_sales": {
        "desc": "统计分析：汇总、环比、异常波动检测（偏离均值 ±40%）",
        "input": {"sales": "dict: read_sales_data 的输出"},
        "output": {"total_amount": "float", "anomalies": "list[dict]", "...": ""},
        "func": impl.analyze_sales,
    },
    "generate_weekly_report": {
        "desc": "生成带中文图表的经营周报（Word），幂等：同一任务同一路径覆盖写",
        "input": {"period": "str", "analysis": "dict", "narrative": "dict",
                  "out_path": "str", "charts_dir": "str"},
        "output": {"report": "str", "charts": "list[str]", "word_count": "int"},
        "func": impl.generate_weekly_report,
    },
}


class ToolFailure(Exception):
    pass


class ToolRegistry:
    def __init__(self):
        self.allowed = set(TOOLS.keys())
        self.max_retry = 2

    def set_policy(self, allowed, max_retry=2):
        self.allowed = set(allowed)
        self.max_retry = max_retry

    def call(self, name, args, task_id="-", events=None):
        if name not in TOOLS:
            self._sec(task_id, events, name, "未注册工具")
            raise ToolFailure("工具未注册：%s" % name)
        if name not in self.allowed:
            self._sec(task_id, events, name, "越权调用")
            raise ToolFailure("越权调用被拒绝：%s（仅允许 %s）" % (name, sorted(self.allowed)))
        spec = TOOLS[name]
        last_err = None
        for attempt in range(1, self.max_retry + 2):
            try:
                if events:
                    events.log(task_id, "TOOL_CALL",
                               {"tool": name, "attempt": attempt,
                                "args": {k: str(v)[:60] for k, v in args.items()}})
                result = spec["func"](**args)
                if events:
                    events.log(task_id, "TOOL_RESULT",
                               {"tool": name, "attempt": attempt,
                                "summary": str(result)[:200]})
                return result
            except Exception as e:
                last_err = e
                if events:
                    events.log(task_id, "TOOL_ERROR",
                               {"tool": name, "attempt": attempt, "error": str(e),
                                "trace": traceback.format_exc(limit=3)[-400:]})
                time.sleep(0.05)
        raise ToolFailure("工具 %s 重试 %d 次仍失败：%s"
                          % (name, self.max_retry + 1, last_err))

    def _sec(self, task_id, events, name, reason):
        if events:
            events.log(task_id, "SECURITY_DENY", {"tool": name, "reason": reason})

    def describe(self):
        return {n: {"desc": TOOLS[n]["desc"], "input": TOOLS[n]["input"],
                    "output": TOOLS[n]["output"]} for n in sorted(TOOLS)}


registry = ToolRegistry()
