# -*- coding: utf-8 -*-
"""生成场景 A 的模拟业务数据（不含任何隐私与未公开信息）。

    销售明细.xlsx：3 家门店 × 7 天 × 4 品类，含一处“断货导致的骤降”异常
    周会纪要.docx：会议原文，包含异常归因线索（可作为 Llama 归因引用的来源）
"""
import datetime
import os

STORES = ["南山店", "福田店", "宝安店"]
CATEGORIES = ["咖啡", "烘焙", "轻食", "周边商品"]

# 门店-日期层面的“真实故事线”：福田店周三因断货骤降，南山店周末因促销暴涨
STORY = {
    ("福田店", "2026-11-04"): -0.62,   # 断货
    ("南山店", "2026-11-07"): 0.75,    # 周末促销
    ("南山店", "2026-11-08"): 0.80,
}

BASE = {
    "南山店": 26000,
    "福田店": 21000,
    "宝安店": 15000,
}
CAT_WEIGHT = {"咖啡": 0.45, "烘焙": 0.25, "轻食": 0.20, "周边商品": 0.10}


def _week_dates():
    start = datetime.date(2026, 11, 2)  # 周一
    return [start + datetime.timedelta(days=i) for i in range(7)]


def build_sales(seed=20261102):
    import random
    random.seed(seed)
    dates = _week_dates()
    days = [d.strftime("%Y-%m-%d") for d in dates]
    rows = []
    for store in STORES:
        for d in days:
            factor = 1.0
            # 双休日自然客流抬升
            if datetime.date.fromisoformat(d).weekday() >= 5:
                factor *= 1.18
            factor *= 1.0 + STORY.get((store, d), 0.0)
            day_total = BASE[store] * factor * random.uniform(0.95, 1.05)
            for cat, w in CAT_WEIGHT.items():
                amount = round(day_total * w * random.uniform(0.9, 1.1), 2)
                orders = max(1, int(amount / random.uniform(28, 42)))
                rows.append({"日期": d, "门店": store, "品类": cat,
                             "销售额": amount, "订单数": orders})
    return rows


def build_all(root):
    import pandas as pd
    from docx import Document

    out_dir = os.path.join(root, "sample_data")
    os.makedirs(out_dir, exist_ok=True)
    xlsx = os.path.join(out_dir, "销售明细.xlsx")
    rows = build_sales()
    df = pd.DataFrame(rows)[["日期", "门店", "品类", "销售额", "订单数"]]
    df.to_excel(xlsx, index=False)

    doc = Document()
    doc.add_heading("2026 年第 44 周经营周会纪要", level=1)
    doc.add_paragraph("时间：2026 年 11 月 2 日  地点：总部三楼会议室  主持人：张店长")
    doc.add_paragraph("参会：南山店、福田店、宝安店店长，运营部、供应链部代表")
    for line in [
        "本周南山店在周末推出「第二杯半价」活动，南山店客流与销售额明显抬升，建议评估是否固化为常规打法。",
        "福田店本周三因冷萃原料到货延迟，当日出现部分商品断货，销售额出现明显下滑，供应链已承诺下周起改为一周两次配送。",
        "宝安店整体平稳，客单价略有提升，主要来自轻食品类的套餐组合。",
        "周边商品品类销售额占比较低，本周各店均未超过 12%，运营部将重新评估陈列位。",
        "下周重点：福田店补货节奏复盘、南山店促销复盘、宝安店轻食套餐推广。",
    ]:
        doc.add_paragraph(line, style="List Bullet")
    docx = os.path.join(out_dir, "周会纪要.docx")
    doc.save(docx)

    form_example = {
        "period": "2026 年第 44 周",
        "sales_file": xlsx,
        "notes_file": docx,
        "submitter": "张店长",
    }
    import json
    with open(os.path.join(out_dir, "任务表单样例.json"), "w", encoding="utf-8") as f:
        json.dump(form_example, f, ensure_ascii=False, indent=2)
    return {"销售明细": xlsx, "周会纪要": docx,
            "任务表单样例": os.path.join(out_dir, "任务表单样例.json")}


if __name__ == "__main__":
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for k, v in build_all(here).items():
        print(k, v)
