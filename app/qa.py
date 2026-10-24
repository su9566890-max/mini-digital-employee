# -*- coding: utf-8 -*-
"""自动质检：不少于 3 类检查 + 检验记录随成果交付。

类别：structure_complete（结构完整）、data_consistency（数据一致性）、
sensitive_words（敏感词）、format_check（格式合规）。
不通过时由状态机自动修正重试，达到重试上限转人工。
"""
import json
import os
import time

from .tools import implementations as impl

CHECKS = {}


def check(name, desc_cn):
    def deco(fn):
        CHECKS[name] = {"fn": fn, "desc": desc_cn}
        return fn
    return deco


@check("structure_complete", "结构完整：周报须包含经营概况、异常波动分析、结论与建议三大节")
def _structure(artifacts, cfg):
    from docx import Document
    doc = Document(artifacts["report"])
    text = "\n".join(p.text for p in doc.paragraphs)
    need = ["一、本周经营概况", "二、异常波动分析", "三、结论与建议"]
    missing = [n for n in need if n not in text]
    return not missing, ("结构完整" if not missing else "缺少章节：" + "、".join(missing))


@check("data_consistency", "数据一致：周报中的总销售额必须与工具分析结果完全一致")
def _consistency(artifacts, cfg):
    from docx import Document
    doc = Document(artifacts["report"])
    text = "".join(p.text for p in doc.paragraphs) + "".join(
        c.text for t in doc.tables for r in t.rows for c in r.cells)
    total = artifacts["analysis"]["total_amount"]
    ok = ("%.2f" % total) in text
    return ok, ("总销售额 %.2f 与工具结果一致" % total if ok
                else "周报未找到工具统计的总销售额 %.2f" % total)


@check("sensitive_words", "敏感词：交付文档不得出现敏感词")
def _sensitive(artifacts, cfg):
    from docx import Document
    doc = Document(artifacts["report"])
    text = "\n".join(p.text for p in doc.paragraphs)
    hits = [w for w in cfg.get("sensitive_words", []) if w in text]
    return not hits, ("未命中敏感词" if not hits else "命中敏感词：" + "、".join(hits))


@check("format_check", "格式合规：须含至少 1 张图表且为中文标签，正文篇幅 200-5000 字")
def _format(artifacts, cfg):
    from docx import Document
    doc = Document(artifacts["report"])
    text = "\n".join(p.text for p in doc.paragraphs)
    has_chart = bool(artifacts.get("charts")) and all(os.path.exists(c) for c in artifacts["charts"])
    n = len(text)
    ok_chart = has_chart
    ok_len = 200 <= n <= 5000
    detail = []
    if not ok_chart:
        detail.append("缺少图表文件")
    if not ok_len:
        detail.append("正文 %d 字超出 200-5000 区间" % n)
    return ok_chart and ok_len, ("格式合规（含 %d 张图，%d 字）" % (len(artifacts.get("charts", [])), n)
                                 if not detail else "；".join(detail))


class QARecord:
    def __init__(self, name, desc, passed, detail):
        self.name = name
        self.desc = desc
        self.passed = passed
        self.detail = detail
        self.time = time.strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self):
        return {"name": self.name, "desc": self.desc, "passed": self.passed,
                "detail": self.detail, "time": self.time}


def run_qa(cfg: dict, artifacts: dict):
    """按配置文件中的 qa_rules 执行检查，返回 (records, passed)。"""
    records = []
    for name in cfg["qa_rules"]:
        meta = CHECKS.get(name)
        if not meta:
            records.append(QARecord(name, "未实现", False, "质检规则未实现").to_dict())
            continue
        try:
            passed, detail = meta["fn"](artifacts, cfg)
        except Exception as e:
            passed, detail = False, "检查执行失败：%s" % e
        records.append(QARecord(name, meta["desc"], bool(passed), detail).to_dict())
    return records, all(r["passed"] for r in records)


def build_qa_record(state: dict) -> dict:
    """构造检验记录正文（不含写盘，落盘交由 Storage 决定）。"""
    return {
        "task_id": state["task_id"],
        "period": state["form"].get("period"),
        "qa_records": state["qa_records"],
        "review": state["review"],
        "llm_rounds": state["llm_rounds"],
        "delivered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def write_qa_record(out_dir: str, state: dict) -> str:
    path = os.path.join(out_dir, "检验记录.json")
    os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(build_qa_record(state), f, ensure_ascii=False, indent=2)
    return path


def describe_checks():
    return {k: v["desc"] for k, v in CHECKS.items()}
