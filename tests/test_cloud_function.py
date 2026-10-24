# -*- coding: utf-8 -*-
"""云端网页工单台（cloud-functions/zhoubao-web）端到端测试。

验证 Serverless 无持久化前提下的**无状态同步模式**是否成立：
一次请求内跑完整条链路，状态随表单回传，产物内联进页面。
"""
import importlib.util
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = os.path.join(ROOT, "cloud-functions", "zhoubao-web", "index.py")

TID = "cf_test"


@pytest.fixture(scope="module")
def cf():
    spec = importlib.util.spec_from_file_location("cf_zhoubao_web", ENTRY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def client(cf):
    cf.app.config["TESTING"] = True
    return cf.app.test_client()


def _blob_of(html: str) -> str:
    m = re.search(r'name="blob"\s+value="([^"]*)"', html)
    assert m, "结果页未携带状态快照 blob"
    return m.group(1)


def _run(client, data, sales=None, notes=None):
    payload = dict(data)
    handles = []
    if sales:
        handles.append(open(sales, "rb"))
        payload["sales_file"] = (handles[-1], os.path.basename(sales))
    if notes:
        handles.append(open(notes, "rb"))
        payload["notes_file"] = (handles[-1], os.path.basename(notes))
    try:
        return client.post("/run", data=payload,
                           content_type="multipart/form-data")
    finally:
        for h in handles:
            h.close()


# ---------- C1：表单页可访问 ----------
def test_c1_form_page(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "发起工单" in html
    assert 'name="sales_file"' in html


# ---------- C2：两份材料齐全 → 执行 → 质检 → 待审核 ----------
def test_c2_full_flow_to_review(client, data):
    r = _run(client,
             {"period": "2026 年第 44 周", "submitter": "张店长", "task_id": TID},
             data["sales"], data["notes"])
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "HUMAN_REVIEW" in html
    assert "自动质检" in html


# ---------- C3：缺材料 → 暂停待补，且提供补充入口 ----------
def test_c3_missing_material_pauses(client, data):
    r = _run(client,
             {"period": "2026 年第 44 周", "submitter": "张店长",
              "task_id": TID + "_m"},
             data["sales"], None)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "PAUSED_MISSING" in html
    assert "/zhoubao-web/material" in html


# ---------- C4：退回必须附意见，否则被拒 ----------
def test_c4_reject_requires_comment(client, data):
    r = _run(client,
             {"period": "2026 年第 44 周", "submitter": "张店长",
              "task_id": TID + "_r"},
             data["sales"], data["notes"])
    blob = _blob_of(r.get_data(as_text=True))
    # 平台上表单提交到 /zhoubao-web/review，运行时会去掉前缀再交给 Flask，
    # 这里直接打内部路由等价模拟
    r2 = client.post("/review",
                     data={"blob": blob, "action": "reject", "comment": "   "})
    html = r2.get_data(as_text=True)
    assert "意见" in html or "必填" in html    # 退回不附意见应被拒绝并提示


# ---------- C5：审核通过 → 交付，产物内联可下载 ----------
def test_c5_approve_delivers(client, data):
    r = _run(client,
             {"period": "2026 年第 44 周", "submitter": "张店长",
              "task_id": TID + "_d"},
             data["sales"], data["notes"])
    blob = _blob_of(r.get_data(as_text=True))
    r2 = client.post("/review",
                     data={"blob": blob, "action": "approve", "comment": "通过"})
    html = r2.get_data(as_text=True)
    assert "DELIVERED" in html
    assert "data:application/vnd" in html      # 周报内联为 data URI
    assert "经营周报.docx" in html
