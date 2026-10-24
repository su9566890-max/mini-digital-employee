# -*- coding: utf-8 -*-
"""自测用例（≥10 条，覆盖正常 / 边界 / 异常）。

运行： python -m pytest tests -q
"""
import json
import os
import shutil

import pytest
import yaml

from app.state import TaskRunner, IllegalTransition
from app.tools import registry, ToolFailure
from app.tools import implementations as impl
from app.qa import run_qa
from app.llm import _MockLLM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_PATH = os.path.join(ROOT, "config", "employee_zhoubao.yaml")


def _form(data, **over):
    f = {"period": "2026 年第 44 周", "sales_file": data["sales"],
         "notes_file": data["notes"], "submitter": "张店长"}
    f.update(over)
    return f


# ---------- 用例 1：正常流程到人工审核 ----------
def test_01_normal_to_review(ws, cfg, data):
    r = TaskRunner(ws, cfg, "t01")
    r.submit_form(_form(data))
    st = r.resume()
    assert st["stage"] == "HUMAN_REVIEW"
    assert os.path.exists(st["artifacts"]["report"])
    assert len(st["artifacts"]["charts"]) >= 3
    assert all(rec["passed"] for rec in st["qa_records"])


# ---------- 用例 2：审核通过 → 交付，交付物齐全 ----------
def test_02_deliver_package(ws, cfg, data):
    r = TaskRunner(ws, cfg, "t02")
    r.submit_form(_form(data))
    r.resume()
    r.review("approve", "可用")
    d = r.state["artifacts"]["delivery_dir"]
    assert os.path.exists(os.path.join(d, "检验记录.json"))
    assert os.path.exists(os.path.join(d, "events.jsonl"))
    assert any(f.endswith("经营周报.docx") for f in os.listdir(d))
    assert r.state["stage"] == "DELIVERED"


# ---------- 用例 3：缺销售数据 → 暂停 → 补充后继续 ----------
def test_03_missing_sales_then_resume(ws, cfg, data):
    r = TaskRunner(ws, cfg, "t03")
    r.submit_form(_form(data, sales_file="不存在的文件.xlsx"))
    st = r.resume()
    assert st["stage"] == "PAUSED_MISSING"
    assert "材料缺失" in st["pause_reason"]
    r.add_material("sales_file", data["sales"])
    st = r.resume()
    assert st["stage"] == "HUMAN_REVIEW"


# ---------- 用例 4：表单必填字段缺失 → 直接判失败 ----------
def test_04_missing_form_field(ws, cfg, data):
    r = TaskRunner(ws, cfg, "t04")
    r.submit_form(_form(data, submitter=""))
    st = r.resume()
    assert st["stage"] == "FAILED"
    assert "表单字段缺失" in st["pause_reason"]


# ---------- 用例 5：审核退回 → 意见被采纳 → 重新送审 ----------
def test_05_reject_and_adopt(ws, cfg, data):
    r = TaskRunner(ws, cfg, "t05")
    r.submit_form(_form(data))
    r.resume()
    r.review("reject", "请针对低增长门店给出具体动作")
    st = r.resume()
    assert st["stage"] == "HUMAN_REVIEW"          # 重做后再送审
    assert "请针对低增长门店给出具体动作" in st["artifacts"]["narrative"]["conclusions"][-1]
    assert st["review"]["action"] == "reject"


# ---------- 用例 6：断点恢复（中途重建 Runner 继续执行） ----------
def test_06_resume_after_interrupt(ws, cfg, data):
    r = TaskRunner(ws, cfg, "t06")
    r.submit_form(_form(data))
    # 模拟执行中被中断：状态停留在 ADMIT_CHECK/EXECUTING 前，未跑到终态
    r.state["stage"] = "FORM_SUBMITTED"
    r._save()
    r2 = TaskRunner(ws, cfg, "t06")               # 重新加载，继续跑
    assert r2.state["stage"] == "FORM_SUBMITTED"
    st = r2.resume()
    assert st["stage"] == "HUMAN_REVIEW"
    types = [e["type"] for e in r2.events.read_all()]
    assert "STATE_CHANGE" in types and "ADMIT_PASSED" in types


# ---------- 用例 7：越权调用被拒绝并留痕 ----------
def test_07_unauthorized_tool(ws, cfg, data):
    events = []
    old = registry.allowed
    registry.allowed = {"read_sales_data"}
    try:
        with pytest.raises(ToolFailure):
            registry.call("generate_weekly_report", {}, task_id="t07")
    finally:
        registry.allowed = old
    # 越权记录
    r = TaskRunner(ws, cfg, "t07")
    r.events.log("t07", "SECURITY_DENY", {"tool": "generate_weekly_report"})
    assert any(e["type"] == "SECURITY_DENY" for e in r.events.read_all())


# ---------- 用例 8：工具失败重试后暂停，不崩溃不编造 ----------
def test_08_tool_failure_suspend(ws, cfg, data):
    r = TaskRunner(ws, cfg, "t08")
    r.submit_form(_form(data, notes_file="bad_notes.docx"))
    st = r.resume()
    assert st["stage"] == "PAUSED_MISSING"        # 文件不存在 → 准入阶段即拦住
    # 文件存在但格式非法 → 执行阶段工具失败 → 暂停
    bad = os.path.join(ws, "bad.xlsx")
    open(bad, "wb").write(b"not an xlsx")
    r2 = TaskRunner(ws, cfg, "t08b")
    r2.submit_form(_form(data, notes_file=data["notes"], sales_file=bad))
    st = r2.resume()
    assert st["stage"] == "SUSPENDED"
    assert "工具调用失败" in st["pause_reason"]
    assert len([e for e in r2.events.read_all() if e["type"] == "TOOL_ERROR"]) >= 3


# ---------- 用例 9：模型调用轮次超限 → 暂停转人工 ----------
def test_09_llm_round_limit(ws, cfg, data):
    r = TaskRunner(ws, cfg, "t09")
    r.submit_form(_form(data))
    r.state["llm_rounds"] = cfg["limits"]["max_llm_rounds"]
    r._save()
    st = r.resume()
    assert st["stage"] == "SUSPENDED"
    assert "轮次超限" in st["pause_reason"]


# ---------- 用例 10：非法流转被程序拒绝 ----------
def test_10_illegal_transition(ws, cfg, data):
    r = TaskRunner(ws, cfg, "t10")
    r.submit_form(_form(data))
    # 未到审核态就放行 → 程序拒绝并留痕
    with pytest.raises(IllegalTransition):
        r.review("approve", "提前放行")
    st = r.resume()
    assert st["stage"] == "HUMAN_REVIEW"
    r.review("approve", "可用")
    assert r.state["stage"] == "DELIVERED"
    with pytest.raises(IllegalTransition):      # 已交付终态不可再流转
        r.review("reject", "再改改")
    with pytest.raises(IllegalTransition):      # 非暂停态不可补材料
        r.add_material("sales_file", data["sales"])
    assert any(e["type"] == "ILLEGAL_TRANSITION" for e in r.events.read_all())


# ---------- 用例 11：质检——敏感词不通过并自动修正 ----------
def test_11_qa_sensitive_autofix(ws, cfg, data):
    r = TaskRunner(ws, cfg, "t11")
    r.submit_form(_form(data))
    r.resume()
    # 注入敏感词，让修复前的版本不通过：通过 mock LLM 输出敏感文案
    analysis = r.state["artifacts"]["analysis"]
    bad = _MockLLM().generate_narrative(analysis, "", cfg["employee"])
    bad["conclusions"] = ["本店濒临倒闭，属内幕信息"]
    impl.generate_weekly_report(period=r.state["form"]["period"], analysis=analysis,
                                narrative=bad, out_path=r.state["artifacts"]["report"],
                                charts_dir=os.path.join(ws, "charts11"))
    artifacts = {"report": r.state["artifacts"]["report"],
                 "analysis": analysis,
                 "charts": r.state["artifacts"]["charts"]}
    records, passed = run_qa(cfg, artifacts)
    assert not passed
    sensitive = [x for x in records if x["name"] == "sensitive_words"][0]
    assert not sensitive["passed"]
    # Mock LLM 修正后不再包含敏感词 → 通过
    good = _MockLLM().generate_narrative(analysis, "", cfg["employee"])
    assert "倒闭" not in json.dumps(good, ensure_ascii=False)


# ---------- 用例 12：数据一致性检查能抓到数字被改 ----------
def test_12_data_consistency(ws, cfg, data):
    analysis = impl.analyze_sales(impl.read_sales_data(data["sales"]))
    narrative = _MockLLM().generate_narrative(analysis, "", cfg["employee"])
    p = os.path.join(ws, "c12.docx")
    impl.generate_weekly_report("测试周期", analysis, narrative, p,
                                os.path.join(ws, "charts12"))
    # 正常：报告与分析结果一致 → 通过
    records, passed = run_qa(cfg, {"report": p, "analysis": analysis,
                                   "charts": r_state_charts(analysis, ws)})
    assert [x for x in records if x["name"] == "data_consistency"][0]["passed"]
    # 篡改：把分析结果的总额改掉再校验 → 一致性检查必须失败
    tampered = dict(analysis)
    tampered["total_amount"] = analysis["total_amount"] + 1
    records, passed = run_qa(cfg, {"report": p, "analysis": tampered,
                                   "charts": r_state_charts(analysis, ws)})
    assert not passed
    assert not [x for x in records if x["name"] == "data_consistency"][0]["passed"]


def r_state_charts(analysis, ws):
    """复用 generate_weekly_report 已产出的图表路径。"""
    d = os.path.join(ws, "charts12")
    impl._make_charts(analysis, d)
    return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".png")]


# ---------- 用例 13：工具入参/出参定义与白名单配置一致 ----------
def test_13_tool_definitions(cfg):
    desc = registry.describe()
    assert len(desc) >= 3
    for name in cfg["tools_allowed"]:
        assert name in desc
        assert desc[name]["input"] and desc[name]["output"]


# ---------- 用例 14：Mock 与真实模型接口返回结构一致 ----------
def test_14_mock_narrative_schema(ws, cfg, data):
    analysis = impl.analyze_sales(impl.read_sales_data(data["sales"]))
    notes = impl.read_meeting_notes(data["notes"])
    n = _MockLLM().generate_narrative(analysis, notes, cfg["employee"])
    for k in ["overview", "anomalies", "conclusions", "suggestions"]:
        assert k in n and isinstance(n[k], (str, list))
    assert len(n["anomalies"]) >= 1
    assert any("福田店" in a for a in n["anomalies"])   # 异常门店被定位
