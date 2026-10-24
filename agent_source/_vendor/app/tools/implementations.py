# -*- coding: utf-8 -*-
"""工具实现：数据读取 / 分析 / 文档生成 / 表单校验。

同一批函数同时被「进程内工具注册表」和「MCP 服务端」复用，保证两条通道行为一致。
"""
import os
import json

REQUIRED_FORM_FIELDS = ["period", "submitter"]        # 空缺 → 表单不完整，任务直接判失败
REQUIRED_MATERIALS = ["sales_file", "notes_file"]     # 空缺或文件不存在 → 缺材料，暂停待补

ANOMALY_THRESHOLD = 0.40  # 偏离该店均值 ±40% 视为异常波动


# ---------------- 1. 表单校验 ----------------
def validate_form(form: dict) -> dict:
    missing_fields = [f for f in REQUIRED_FORM_FIELDS if not (form or {}).get(f)]
    missing_materials = []
    for key in REQUIRED_MATERIALS:
        p = (form or {}).get(key, "")
        if not p or not os.path.exists(p):
            missing_materials.append(key)
    return {"missing_fields": missing_fields, "missing_materials": missing_materials}


# ---------------- 2. 读取销售数据 ----------------
def read_sales_data(path: str) -> dict:
    if not path or not os.path.exists(path):
        raise FileNotFoundError("销售数据文件不存在：%s" % path)
    import pandas as pd
    if path.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path, encoding="utf-8-sig")
    need = {"日期", "门店", "品类", "销售额"}
    if not need.issubset(set(df.columns)):
        raise ValueError("销售数据缺少必需列：%s（应有 %s）" % (list(df.columns), sorted(need)))
    if df.empty:
        raise ValueError("销售数据为空")
    df["销售额"] = pd.to_numeric(df["销售额"], errors="coerce").fillna(0)
    if "订单数" in df.columns:
        df["订单数"] = pd.to_numeric(df["订单数"], errors="coerce").fillna(0)
    records = json.loads(df.to_json(orient="records", force_ascii=False, date_format="iso"))
    for r in records:
        if isinstance(r.get("日期"), str):
            r["日期"] = r["日期"][:10]
    return {"columns": list(df.columns), "rows": records}


# ---------------- 3. 读取周会纪要 ----------------
def read_meeting_notes(path: str) -> str:
    if not path or not os.path.exists(path):
        raise FileNotFoundError("周会纪要文件不存在：%s" % path)
    if path.lower().endswith(".docx"):
        try:
            from docx import Document
        except ImportError as e:
            raise RuntimeError("缺少 python-docx 依赖：%s" % e)
        doc = Document(path)
        return "\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------- 4. 统计分析 ----------------
def analyze_sales(sales: dict) -> dict:
    import pandas as pd
    df = pd.DataFrame(sales["rows"])
    if df.empty:
        raise ValueError("销售数据为空，无法分析")
    df["销售额"] = pd.to_numeric(df["销售额"], errors="coerce").fillna(0)
    total_amount = float(df["销售额"].sum())
    total_orders = int(df["订单数"].sum()) if "订单数" in df.columns else int(len(df))
    avg_order_value = round(total_amount / total_orders, 2) if total_orders else 0.0

    def _agg(by):
        g = df.groupby(by, as_index=False)["销售额"].sum().sort_values("销售额", ascending=False)
        return [g.iloc[i].to_dict() for i in range(len(g))]

    by_store = [{"store": r["门店"], "amount": round(float(r["销售额"]), 2),
                 "share": round(float(r["销售额"]) / total_amount, 4)}
                for r in _agg("门店")] if total_amount else []
    by_category = [{"category": r["品类"], "amount": round(float(r["销售额"]), 2)}
                   for r in _agg("品类")]
    daily = df.groupby("日期", as_index=False)["销售额"].sum().sort_values("日期")
    by_date = [{"date": str(r["日期"])[:10], "amount": round(float(r["销售额"]), 2)}
               for _, r in daily.iterrows()]

    # 环比：前半周期 vs 后半周期
    half = len(by_date) // 2
    first = sum(d["amount"] for d in by_date[:half]) or 0
    second = sum(d["amount"] for d in by_date[half:]) or 0
    wow_change_pct = round((second - first) / first * 100, 2) if first else 0.0

    # 异常检测：单店-单日偏离该店日均值超过阈值
    anomalies = []
    for store, grp in df.groupby("门店"):
        s = grp.groupby("日期", as_index=False)["销售额"].sum().sort_values("日期")
        mean = float(s["销售额"].mean())
        if mean <= 0:
            continue
        for _, r in s.iterrows():
            amount = float(r["销售额"])
            dev = (amount - mean) / mean
            if abs(dev) >= ANOMALY_THRESHOLD:
                anomalies.append({
                    "date": str(r["日期"])[:10], "store": str(store),
                    "amount": round(amount, 2), "store_daily_mean": round(mean, 2),
                    "deviation_pct": round(dev * 100, 2),
                    "kind": "spike" if dev > 0 else "drop",
                })
    anomalies.sort(key=lambda x: -abs(x["deviation_pct"]))
    return {
        "total_amount": round(total_amount, 2),
        "total_orders": total_orders,
        "avg_order_value": avg_order_value,
        "period_range": [by_date[0]["date"], by_date[-1]["date"]] if by_date else [],
        "by_store": by_store,
        "by_category": by_category,
        "by_date": by_date,
        "wow_change_pct": wow_change_pct,
        "anomalies": anomalies,
    }


# ---------------- 5. 生成经营周报（Word + 图表） ----------------
BUNDLED_FONT = "NotoSansSC-subset.otf"


def _find_bundled_font():
    """找仓库自带的开源中文字体（Noto Sans CJK SC 子集，OFL 许可）。

    为什么必须自带：Linux 容器 / EdgeOne Makers 云函数里通常没有中文字体，
    matplotlib 会静默回退到不含汉字的默认字体，图表里的中文标签变成方块。
    自带字体可以让本地和云端渲染效果完全一致。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    root = here
    for _ in range(5):
        root = os.path.dirname(root)
        if not root:
            break
        for base in (root, os.path.join(root, "_vendor")):
            p = os.path.join(base, "assets", "fonts", BUNDLED_FONT)
            if os.path.exists(p) and os.path.getsize(p) > 1024 * 512:
                return p
    return None


def _set_chinese_font():
    import matplotlib
    from matplotlib import font_manager

    bundled = _find_bundled_font()
    if bundled:
        # 自带字体优先：注册进 matplotlib 字体库，本地与云端表现一致
        try:
            font_manager.fontManager.addfont(bundled)
            name = font_manager.FontProperties(fname=bundled).get_name()
            fallback = list(matplotlib.rcParams["font.sans-serif"] or [])
            matplotlib.rcParams["font.family"] = "sans-serif"
            matplotlib.rcParams["font.sans-serif"] = [name] + fallback
            matplotlib.rcParams["axes.unicode_minus"] = False
            return name
        except Exception:
            # 不吞没原因：注册失败时继续尝试系统字体，但失败原因要能查到
            import warnings
            warnings.warn("自带中文字体注册失败，回退系统字体：%s" % bundled)

    for f in ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
              "Source Han Sans SC", "WenQuanYi Zen Hei", "Noto Sans SC",
              "Arial Unicode MS"]:
        try:
            font_manager.findfont(font_manager.FontProperties(family=f),
                                  fallback_to_default=False)
            matplotlib.rcParams["font.sans-serif"] = [f]
            break
        except Exception:
            continue
    matplotlib.rcParams["axes.unicode_minus"] = False
    return None


def _make_charts(analysis: dict, charts_dir: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _set_chinese_font()
    os.makedirs(charts_dir, exist_ok=True)
    paths = []

    if analysis["by_date"]:
        xs = [d["date"] for d in analysis["by_date"]]
        ys = [d["amount"] for d in analysis["by_date"]]
        fig, ax = plt.subplots(figsize=(6.4, 3.2), dpi=160)
        ax.plot(xs, ys, marker="o", color="#c0392b", linewidth=2, label="销售额")
        ax.set_title("每日销售额趋势", fontsize=12)
        ax.set_xlabel("日期"); ax.set_ylabel("销售额（元）")
        ax.legend(loc="best"); ax.grid(alpha=.3)
        for label in ax.get_xticklabels():
            label.set_rotation(30); label.set_horizontalalignment("right")
        fig.tight_layout()
        p = os.path.join(charts_dir, "每日销售额趋势.png")
        fig.savefig(p); plt.close(fig); paths.append(p)

    if analysis["by_store"]:
        xs = [s["store"] for s in analysis["by_store"]]
        ys = [s["amount"] for s in analysis["by_store"]]
        fig, ax = plt.subplots(figsize=(6.4, 3.2), dpi=160)
        ax.bar(xs, ys, color="#2980b9")
        ax.set_title("门店销售额对比", fontsize=12)
        ax.set_xlabel("门店"); ax.set_ylabel("销售额（元）")
        ax.grid(axis="y", alpha=.3)
        fig.tight_layout()
        p = os.path.join(charts_dir, "门店销售额对比.png")
        fig.savefig(p); plt.close(fig); paths.append(p)

    if analysis["by_category"]:
        labels = [c["category"] for c in analysis["by_category"]]
        vals = [c["amount"] for c in analysis["by_category"]]
        fig, ax = plt.subplots(figsize=(5.6, 3.6), dpi=160)
        ax.pie(vals, labels=labels, autopct="%1.1f%%", startangle=110)
        ax.set_title("品类销售占比", fontsize=12)
        fig.tight_layout()
        p = os.path.join(charts_dir, "品类销售占比.png")
        fig.savefig(p); plt.close(fig); paths.append(p)
    return paths


def generate_weekly_report(period: str, analysis: dict, narrative: dict,
                           out_path: str, charts_dir: str, feedback: str = "") -> dict:
    try:
        from docx import Document
        from docx.shared import Pt, Inches
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError as e:
        raise RuntimeError("缺少 python-docx 依赖：%s" % e)

    charts = _make_charts(analysis, charts_dir)
    doc = Document()
    title = doc.add_heading("本周经营周报", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub = doc.add_paragraph("统计周期：%s    数据源：门店销售明细 / 周会纪要" % period)
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # 一、本周经营概况
    doc.add_heading("一、本周经营概况", level=1)
    doc.add_paragraph(narrative.get("overview", ""))
    t = doc.add_table(rows=1, cols=4)
    t.style = "Light Grid Accent 1"
    hdr = t.rows[0].cells
    for i, h in enumerate(["指标", "数值", "环比", "说明"]):
        hdr[i].text = h
    rows = [
        ("总销售额（元）", "%.2f" % analysis["total_amount"]),
        ("订单数（单）", str(analysis["total_orders"])),
        ("客单价（元）", "%.2f" % analysis["avg_order_value"]),
        ("榜首门店", analysis["by_store"][0]["store"] if analysis["by_store"] else "-"),
    ]
    for name, val in rows:
        cells = t.add_row().cells
        cells[0].text = name
        cells[1].text = val
        cells[2].text = "%+.2f%%" % analysis["wow_change_pct"]
        cells[3].text = "工具统计结果"

    # 二、异常波动分析
    doc.add_heading("二、异常波动分析", level=1)
    if narrative.get("anomalies"):
        for line in narrative["anomalies"]:
            doc.add_paragraph(line, style="List Bullet")
    else:
        doc.add_paragraph("本周未检出显著异常波动（偏离均值 40% 以上）。")
    for p in charts:
        doc.add_picture(p, width=Inches(5.8))
        cap = doc.add_paragraph("图：%s" % os.path.basename(p).replace(".png", ""))
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if feedback:
        doc.add_paragraph("已按审核意见调整：" + feedback)

    # 三、结论与建议
    doc.add_heading("三、结论与建议", level=1)
    for c in narrative.get("conclusions", []):
        doc.add_paragraph(c, style="List Number")
    for s in narrative.get("suggestions", []):
        doc.add_paragraph(s, style="List Bullet")

    doc.add_paragraph("数据来源：门店销售明细（工具读取）与本周例会纪要（工具读取）；"
                      "所有数值均由工具统计得出。")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp.docx"
    doc.save(tmp)
    os.replace(tmp, out_path)  # 幂等：同一任务覆盖写，重试不产生重复文件

    word_count = sum(len(p.text) for p in doc.paragraphs)
    return {"report": out_path, "charts": charts, "word_count": word_count}
