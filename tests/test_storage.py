# -*- coding: utf-8 -*-
"""持久化后端测试：三个后端语义等价 + 云端断点恢复真实成立。

- 用例 S1  等价性：Disk / Memory 两个后端跑同一业务场景，状态与事件序列一致
- 用例 S2  Makers 后端（FakeStore 模拟 context.store）完整流程到待审核
- 用例 S3  Makers 后端断点恢复：flush 后换新实例 load，从上次位置继续
- 用例 S4  Makers 后端交付：产物以 base64 收进 artifacts.files，可解码还原
- 用例 S5  事件增量 flush：重复 flush 不产生重复事件
"""
import base64
import json
import os

import pytest

from app.state import TaskRunner
from app.storage import DiskStorage, MemoryStorage, MakersStorage, EVENT_TAG


# ---------------- Fake EdgeOne context.store ----------------
class FakeStateStore:
    """官方语义：context.store.state 是子对象，方法 get/set/delete。"""

    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)


class FakeStore:
    """按官方文档语义模拟 context.store：state 子对象 + 平铺的消息方法。"""

    def __init__(self):
        self.state = FakeStateStore()
        self.messages = []
        self._seq = 0

    async def append_message(self, conversation_id=None, role=None, content=None,
                             metadata=None, **kw):
        self._seq += 1
        m = {"message_id": "msg_%04d" % self._seq, "role": role,
             "content": content, "metadata": dict(metadata or {})}
        self.messages.append(m)
        return m["message_id"]

    async def get_messages(self, conversation_id=None, limit=100, order="asc",
                           after=None, **kw):
        msgs = self.messages
        if after:
            for i, m in enumerate(msgs):
                if m["message_id"] == after:
                    msgs = msgs[i + 1:]
                    break
        return msgs[:limit]


class FakeContext:
    def __init__(self):
        self.store = FakeStore()
        self.conversation_id = "conv_test_001"
        self.env = {}


# ---------------- helpers ----------------
def _make_runner(cfg, data, task_id, storage=None):
    """构造 runner 并提交表单（不推进）；storage 为空时用 Disk 后端。"""
    if storage is not None:
        r = TaskRunner.from_storage(storage, cfg)
    else:
        r = TaskRunner(str(cfg["_ws"]), cfg, task_id)
    r.submit_form({"period": "2026 年第 44 周", "submitter": "张店长",
                   "sales_file": data["sales"], "notes_file": data["notes"]})
    return r


def _stages_of(runner):
    return [e["payload"].get("to") for e in runner.events.read_all()
            if e["type"] == "STATE_CHANGE"]


# ---------------- S1 等价性 ----------------
def test_s1_disk_and_memory_equivalent(ws, cfg, data):
    cfg["_ws"] = ws
    out = {}
    for backend in ("disk", "memory"):
        st = DiskStorage(ws, "s1_" + backend) if backend == "disk" \
            else MemoryStorage("s1_" + backend)
        r = _make_runner(cfg, data, "s1_" + backend, storage=st)
        r.resume()
        out[backend] = {
            "stage": r.state["stage"],
            "stages": _stages_of(r),
            "events": [e["type"] for e in r.events.read_all()],
            "qa": [q["name"] for q in r.state["qa_records"]],
        }
    assert out["disk"]["stage"] == out["memory"]["stage"] == "HUMAN_REVIEW"
    assert out["disk"]["stages"] == out["memory"]["stages"]
    assert out["disk"]["events"] == out["memory"]["events"]
    assert out["disk"]["qa"] == out["memory"]["qa"]


# ---------------- S2 Makers 后端完整流程 ----------------
def test_s2_makers_submit_to_review(cfg, data):
    import asyncio as _a
    ctx = FakeContext()
    st = MakersStorage(ctx, "s2")
    _a.run(st.load())

    r = TaskRunner.from_storage(st, cfg)
    r.submit_form({"period": "2026 年第 44 周", "submitter": "张店长",
                   "sales_file": data["sales"], "notes_file": data["notes"]})
    s = r.resume()
    assert s["stage"] == "HUMAN_REVIEW"
    _a.run(st.flush())

    # 数据确实写进了平台 store
    assert ctx.store.state.data["task:s2"]["stage"] == "HUMAN_REVIEW"
    types = [json.loads(m["content"])["type"] for m in ctx.store.messages
             if m["metadata"].get("record") == EVENT_TAG]
    assert "ADMIT_PASSED" in types and "AUTO_QA" in types


# ---------------- S3 Makers 断点恢复 ----------------
def test_s3_makers_resume_across_instances(cfg, data):
    import asyncio as _a

    ctx = FakeContext()
    st = MakersStorage(ctx, "s3")
    _a.run(st.load())
    r1 = TaskRunner.from_storage(st, cfg)
    r1.submit_form({"period": "2026 年第 44 周", "submitter": "张店长",
                    "sales_file": data["sales"], "notes_file": data["notes"]})
    _a.run(st.flush())

    # 模拟实例回收：新的请求、新的实例，从平台 store 重新拉起
    st2 = MakersStorage(ctx, "s3")
    _a.run(st2.load())
    r2 = TaskRunner.from_storage(st2, cfg)
    assert r2.state["stage"] == r1.state["stage"]
    s = r2.resume()
    assert s["stage"] == "HUMAN_REVIEW"
    # 事件没有丢：第二段事件接在第一段后面
    all_types = [e["type"] for e in st2.read_events()]
    assert all_types.count("STATE_CHANGE") >= 4
    _a.run(st2.flush())
    assert not any(e["type"] == "ILLEGAL_TRANSITION" for e in st2.read_events())


# ---------------- S4 Makers 交付产物可还原 ----------------
def test_s4_makers_deliver_inline_files(cfg, data):
    import asyncio as _a

    ctx = FakeContext()
    st = MakersStorage(ctx, "s4")
    _a.run(st.load())
    r = TaskRunner.from_storage(st, cfg)
    r.submit_form({"period": "2026 年第 44 周", "submitter": "张店长",
                   "sales_file": data["sales"], "notes_file": data["notes"]})
    r.resume()
    r.review("approve", "通过")
    assert r.state["stage"] == "DELIVERED"
    _a.run(st.flush())

    files = r.state["artifacts"]["files"]
    docx = [k for k in files if k.endswith("经营周报.docx")]
    assert docx, "交付清单应包含周报 docx"
    raw = base64.b64decode(files[docx[0]])
    assert raw[:2] == b"PK"          # docx 本质是 zip，魔数校验
    charts = [k for k in files if k.endswith(".png")]
    assert len(charts) >= 3
    for k in charts:
        assert base64.b64decode(files[k])[:8] == b"\x89PNG\r\n\x1a\n"


# ---------------- S5 增量 flush 幂等 ----------------
def test_s5_flush_idempotent(cfg, data):
    import asyncio as _a

    ctx = FakeContext()
    st = MakersStorage(ctx, "s5")
    _a.run(st.load())
    r = TaskRunner.from_storage(st, cfg)
    r.submit_form({"period": "2026 年第 44 周", "submitter": "张店长",
                   "sales_file": data["sales"], "notes_file": data["notes"]})
    r.resume()
    _a.run(st.flush())
    n1 = len(ctx.store.messages)
    _a.run(st.flush())
    _a.run(st.flush())
    assert len(ctx.store.messages) == n1, "重复 flush 不得产生重复事件"
