# -*- coding: utf-8 -*-
"""模型层：OpenAI 兼容接口 + 离线 Mock 兜底。

无 API Key 或调用失败时进入 Mock 模式（模板化叙事），保证全流程（含演示/自测）可离线跑通；
真实模式下所有输出都基于工具产出的真实数据，模型只负责“叙事”，不负责判定。
"""
import os


class LLMError(Exception):
    pass


class _MockLLM:
    mode = "mock"

    def generate_narrative(self, analysis, notes, employee, feedback=""):
        a = analysis
        top = a["by_store"][0] if a["by_store"] else {}
        overview = ("本周总销售额 %.0f 元，订单 %d 单，客单价 %.1f 元；"
                    "表现最好的门店是 %s，销售额 %.0f 元，占比 %.1f%%。"
                    % (a["total_amount"], a["total_orders"], a["avg_order_value"],
                       top.get("store", "-"), top.get("amount", 0),
                       top.get("share", 0) * 100))
        anomalies = []
        for item in a["anomalies"]:
            quote = _quote_from_notes(notes, item)
            anomalies.append(
                "%s %s 销售额 %.0f 元，较该店均值%s %.1f%%，原因是：%s"
                % (item["date"], item["store"], item["amount"],
                   "偏高" if item["kind"] == "spike" else "偏低",
                   item["deviation_pct"], quote))
        conclusions = [
            "整体经营%s，%s" % ("向好" if a["wow_change_pct"] >= 0 else "承压",
                              "环比%s %.1f%%" % ("增长" if a["wow_change_pct"] >= 0 else "下降",
                                                abs(a["wow_change_pct"]))),
            "异常主要集中在 %s，已定位到具体门店与日期。"
            % ("、".join(sorted({i["store"] for i in a["anomalies"]})) or "无明显异常"),
        ]
        suggestions = [
            "对异常门店核查库存与排班，避免重复缺货。",
            "把本周有效的促销动作固化为标准打法，推广到低增长门店。",
        ]
        if feedback:
            conclusions.append("已按审核/质检意见修正：" + feedback[:60])
        return {"overview": overview, "anomalies": anomalies,
                "conclusions": conclusions, "suggestions": suggestions,
                "mode": self.mode}


CAUSE_KEYWORDS = ["断货", "缺货", "促销", "活动", "延迟", "到货", "下滑", "暴涨",
                  "提升", "下降", "增长", "推广", "原因", "客流", "积压", "涨价"]


def _quote_from_notes(notes: str, item: dict) -> str:
    """从周会纪要里为异常找归因句子。要求同时命中「门店名」与「因果关键词」，
    找不到就如实说明，不编造。
    """
    sentences = [s.strip() for s in notes.replace("\n", "。").split("。") if s.strip()]
    store, date = item["store"], item["date"]
    scored = []
    for s in sentences:
        if store not in s:
            continue
        score = sum(1 for k in CAUSE_KEYWORDS if k in s)
        # 异常类型也要匹配方向：骤降优先找“断货/下滑”，暴涨优先找“促销/活动”
        if item["kind"] == "drop":
            score += sum(2 for k in ["断货", "缺货", "延迟", "下滑", "下降"] if k in s)
        else:
            score += sum(2 for k in ["促销", "活动", "暴涨", "提升", "增长"] if k in s)
        if score:
            scored.append((score, s))
    if scored:
        scored.sort(key=lambda x: -x[0])
        best = scored[0][1]
        return "纪要归因（%s，%s）：%s" % (date, store, best[:80])
    return "纪要中未提及 %s %s 的具体原因，建议人工核实" % (date, store)


class _RemoteLLM:
    mode = "remote"

    def __init__(self, cfg, api_key=None, base_url=None, model=None):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise LLMError("未安装 openai 库，请 pip install openai 或留空 Key 使用 Mock 模式")
        m = cfg["model"]
        api_key = api_key or os.environ.get(m.get("api_key_env", "EMPLOYEE_LLM_API_KEY"), "")
        if not api_key:
            raise LLMError("未配置 API Key")
        self.client = OpenAI(api_key=api_key, base_url=base_url or m.get("base_url"))
        self.model = model or m.get("model")
        self.temperature = m.get("temperature", 0.3)
        self.timeout = cfg["limits"]["max_llm_seconds"]

    def generate_narrative(self, analysis, notes, employee, feedback=""):
        prompt = (
            "你是%s，职责：%s。\n注意事项：%s\n\n"
            "以下是工具统计出来的真实数据（不得改数字）：\n%s\n\n"
            "周会纪要原文：\n%s\n\n%s"
            "请输出 JSON：{\"overview\":...,\"anomalies\":[...],\"conclusions\":[...],\"suggestions\":[...]}"
            "，全部使用中文，anomalies 每条一句话。"
            % (employee["name"], employee.get("responsibility", ""),
               "；".join(employee.get("notices", [])),
               _dump(analysis), notes[:3000],
               ("必须采纳的修改意见：" + feedback + "\n" if feedback else ""))
        )
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": "你是严谨的经营分析师，只基于给定数据输出。"},
                          {"role": "user", "content": prompt}],
                temperature=self.temperature,
                timeout=self.timeout,
                response_format={"type": "json_object"},
            )
            txt = resp.choices[0].message.content
            import json
            data = json.loads(txt)
        except Exception as e:
            raise LLMError("模型调用失败：%s" % e)
        data.setdefault("overview", "")
        data["anomalies"] = data.get("anomalies") or []
        data["conclusions"] = data.get("conclusions") or []
        data["suggestions"] = data.get("suggestions") or []
        data["mode"] = self.mode
        return data


def _dump(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)[:6000]


def makers_gateway_overrides():
    """部署在 EdgeOne Makers 时，平台会注入 AI Gateway 环境变量。

    检测到就用平台网关（内置免费模型额度、无需自备 Key、Key 不落盘），
    优先级高于配置文件里的 base_url / model。
    """
    base = os.environ.get("AI_GATEWAY_BASE_URL", "")
    key = (os.environ.get("AI_GATEWAY_API_KEY", "")
           or os.environ.get("MAKERS_MODELS_KEY", ""))
    if not (base and key):
        return None
    return {"base_url": base, "api_key": key,
            "model": os.environ.get("AI_GATEWAY_MODEL", "@makers/deepseek-v4-flash")}


def build_llm(cfg: dict):
    """优先级：Makers AI 网关 > 配置文件里的真实接口 > Mock 兜底。"""
    gw = makers_gateway_overrides()
    if gw:
        try:
            return _RemoteLLM(cfg, **gw)
        except Exception:
            pass  # 网关配置异常时按普通模式再试，仍失败落 Mock
    try:
        return _RemoteLLM(cfg)
    except Exception:
        return _MockLLM()
