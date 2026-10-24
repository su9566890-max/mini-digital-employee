# -*- coding: utf-8 -*-
"""持久化抽象层：同一套任务逻辑，既能跑在本地磁盘，也能跑在 EdgeOne Makers 云端。

为什么需要这一层
----------------
状态机原本把任务状态写成 ``tasks/<id>/state.json``、事件写成 ``events.jsonl``。
本地没问题，但部署到 EdgeOne Makers 后会失效：

* Makers 的 **Python 运行时没有官方持久化** —— Blob 对象存储目前只提供 Node.js SDK，
  KV 只面向 Edge Functions（JS 运行时），平台上也没有托管数据库，
  官方文档明确不建议用 sqlite（Serverless 冷启动会重置）；
* ``agents/`` 下的 Python Agent 能用 ``context.store``，数据落在平台 Blob 上，
  **跨实例、跨请求保留**，这是 Python 侧唯一官方支持的持久化手段。

于是把「写到哪里」抽出来：TaskRunner 只认本模块的**同步**接口，不感知运行环境。

三个实现
--------
==============  ==========================================================
DiskStorage     本地磁盘。命令行 / Web 工单台 / 单测都用它，行为与改造前完全一致。
MemoryStorage   纯内存。给「双后端等价性」验证用。
MakersStorage   EdgeOne Makers ``agents/`` 专用。状态落在 ``context.store.state``，
                事件流落在同一会话的消息历史里。
==============  ==========================================================

异步问题怎么化解
----------------
``context.store`` 的方法全是 async，而 TaskRunner 是同步的。MakersStorage 采用
「预加载 → 同步跑 → 统一回写」三段式::

    st = MakersStorage(context, task_id)
    await st.load()                          # 一次性把 state 与事件拉进内存
    runner = TaskRunner.from_storage(st, cfg)
    runner.submit_form(form); runner.resume()# 全程同步，读写都发生在内存缓冲
    await st.flush()                         # 增量回写：dirty 的 state + 新增事件

下一个请求带同一个 task_id 进来时再 load 一次，就能拿到上次停下的位置继续跑 ——
这就是 Serverless 环境下的「断点恢复」：状态不在进程里，也不在磁盘上，而在平台存储里。
"""
import abc
import base64
import json
import os
import shutil
import tempfile
import time

# 单个产物以内联（base64）方式存进云端 KV 的大小上限
MAX_INLINE_BYTES = 4 * 1024 * 1024

# 事件在消息历史里的标记，用于把任务事件和人与 Agent 的对话消息区分开
EVENT_TAG = "task-event"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _atomic_write_json(path: str, payload) -> None:
    """原子写：先写临时文件再替换，避免执行中断留下半截文件。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


class Storage(abc.ABC):
    """任务持久化后端。

    所有方法均为同步调用，便于被同步状态机直接使用。其中 ``scratch()`` 返回的是
    **真实文件系统路径** —— 因为 pandas / python-docx / matplotlib 这些工具只能
    操作真实文件，无法适配纯字节接口。云端时该目录是一次性临时目录，
    请求结束后的留存依靠 ``done()`` 把产物收进 artifacts。
    """

    #: 该后端是否提供可被外部长期访问的目录（本地磁盘为 True，云端临时目录为 False）
    persistent_dir: bool = False

    def __init__(self, task_id: str):
        self.task_id = task_id

    # ---------- 任务状态 ----------
    @abc.abstractmethod
    def load_state(self):
        """读取任务状态；任务不存在返回 None。"""

    @abc.abstractmethod
    def save_state(self, state: dict) -> None:
        """写入任务状态，必须是幂等的（重复写入结果一致）。"""

    # ---------- 事件流 ----------
    @abc.abstractmethod
    def append_event(self, etype: str, payload=None) -> dict:
        """追加一条事件并返回它。"""

    @abc.abstractmethod
    def read_events(self) -> list:
        """按发生顺序返回全部事件。"""

    # ---------- 工作目录与产物 ----------
    @abc.abstractmethod
    def scratch(self, *parts: str) -> str:
        """返回工作目录下的真实路径（工具读写文件用）。"""

    @abc.abstractmethod
    def deliver(self, state: dict) -> str:
        """把产物放到该环境下的最终交付位，返回可展示的交付位置描述。"""

    # ---------- 便捷别名 ----------
    def log(self, etype: str, payload=None) -> dict:
        return self.append_event(etype, payload)


class DiskStorage(Storage):
    """本地磁盘后端：``<workspace>/tasks/<task_id>/`` 下存放 state 与 events。

    与改造前的目录布局完全一致，命令行、Web 工单台、单元测试都依赖它。
    """

    persistent_dir = True

    def __init__(self, workspace: str, task_id: str):
        super().__init__(task_id)
        self.workspace = workspace
        self.task_dir = os.path.join(workspace, "tasks", task_id)
        os.makedirs(self.task_dir, exist_ok=True)
        os.makedirs(os.path.join(self.task_dir, "charts"), exist_ok=True)
        self.state_path = os.path.join(self.task_dir, "state.json")
        self.events_path = os.path.join(self.task_dir, "events.jsonl")

    def load_state(self):
        if not os.path.exists(self.state_path):
            return None
        with open(self.state_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_state(self, state: dict) -> None:
        _atomic_write_json(self.state_path, state)

    def append_event(self, etype: str, payload=None) -> dict:
        e = {"ts": _now(), "task_id": self.task_id, "type": etype,
             "payload": payload or {}}
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        return e

    def read_events(self) -> list:
        if not os.path.exists(self.events_path):
            return []
        with open(self.events_path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def scratch(self, *parts: str) -> str:
        return os.path.join(self.task_dir, *parts)

    def deliver(self, state: dict) -> str:
        from .qa import write_qa_record
        out_dir = os.path.join(self.workspace, "deliveries", self.task_id)
        os.makedirs(out_dir, exist_ok=True)
        report = state["artifacts"].get("report")
        if report and os.path.exists(report):
            shutil.copy(report, os.path.join(
                out_dir, "%s_经营周报.docx" % state["form"].get("period", self.task_id)))
        for src in state["artifacts"].get("charts", []):
            if os.path.exists(src):
                shutil.copy(src, out_dir)
        if os.path.exists(self.events_path):
            shutil.copy(self.events_path, os.path.join(out_dir, "events.jsonl"))
        qa_path = write_qa_record(out_dir, state)
        return {"delivery_dir": out_dir, "qa_record": qa_path}


class MemoryStorage(Storage):
    """纯内存后端：语义等价于 DiskStorage，用于验证两个后端行为一致。"""

    persistent_dir = False

    def __init__(self, task_id: str, scratch_root: str = None,
                 initial_state: dict = None, initial_events: list = None):
        super().__init__(task_id)
        # initial_state / initial_events 用于「从快照恢复」：云端一个请求内跑完，
        # 跨请求时把状态序列化回传，下一个实例据此还原到上次停下的位置。
        self._state = initial_state
        self._events = list(initial_events or [])
        self._scratch = os.path.join(scratch_root or tempfile.mkdtemp(prefix="mde_"),
                                     task_id)
        os.makedirs(self._scratch, exist_ok=True)
        os.makedirs(os.path.join(self._scratch, "charts"), exist_ok=True)

    def load_state(self):
        return self._state

    def save_state(self, state: dict) -> None:
        self._state = json.loads(json.dumps(state, ensure_ascii=False))

    def append_event(self, etype: str, payload=None) -> dict:
        e = {"ts": _now(), "task_id": self.task_id, "type": etype,
             "payload": payload or {}}
        self._events.append(e)
        return e

    def read_events(self) -> list:
        return list(self._events)

    def scratch(self, *parts: str) -> str:
        return os.path.join(self._scratch, *parts)

    def deliver(self, state: dict) -> dict:
        from .qa import build_qa_record
        qa_rec = build_qa_record(state)
        state.setdefault("artifacts", {})["qa_record"] = qa_rec
        return {"delivery_ref": "memory://%s" % self.task_id, "qa_record": qa_rec}


class MakersStorage(Storage):
    """EdgeOne Makers ``agents/`` 后端。

    状态落在 ``context.store.state``（按 conversation 隔离的 JSON KV），
    事件流落在同一会话的消息历史里。两者都由平台持久化到 Blob，
    因此**实例被回收、下次请求落到别的实例，数据依然在**。

    用法（在 Agent handler 内）::

        st = MakersStorage(context, task_id)
        await st.load()
        try:
            runner = TaskRunner.from_storage(st, cfg)
            ...
        finally:
            await st.flush()
    """

    persistent_dir = False

    def __init__(self, context, task_id: str, scratch_root: str = None):
        super().__init__(task_id)
        self.context = context
        self._state = None
        self._events = []
        self._flushed_events = 0
        self._dirty_state = False
        self._loaded = False
        self._scratch = os.path.join(scratch_root or tempfile.mkdtemp(prefix="mde_"),
                                     task_id)
        os.makedirs(self._scratch, exist_ok=True)
        os.makedirs(os.path.join(self._scratch, "charts"), exist_ok=True)

    # ---------- 生命周期（异步） ----------
    @property
    def state_key(self) -> str:
        return "task:%s" % self.task_id

    async def load(self) -> "MakersStorage":
        """把该任务的 state 与事件一次性拉进内存。之后所有读写都是同步的。"""
        try:
            raw = await self.context.store.state.get(self.state_key)
        except Exception:
            raw = None
        self._state = raw if isinstance(raw, dict) else None
        self._events = await self._read_events_from_store()
        self._flushed_events = len(self._events)
        self._loaded = True
        return self

    async def flush(self) -> None:
        """增量回写：只写 dirty 的 state 与本次新增的事件。"""
        if not self._loaded:
            return
        if self._dirty_state and self._state is not None:
            await self.context.store.state.set(self.state_key, self._state)
            self._dirty_state = False
        for e in self._events[self._flushed_events:]:
            try:
                await self.context.store.append_message(
                    conversation_id=self.context.conversation_id,
                    role="system",
                    content=json.dumps(e, ensure_ascii=False),
                    metadata={"record": EVENT_TAG, "task_id": self.task_id,
                              "type": e.get("type")},
                )
            except Exception:
                # 事件写入失败不影响主流程，但要在最后一次你能在链路追踪里看到
                break
        self._flushed_events = len(self._events)

    async def exists(self) -> bool:
        await self.load()
        return self._state is not None

    # ---------- 同步读写（跑在内存缓冲上） ----------
    def load_state(self):
        return self._state

    def save_state(self, state: dict) -> None:
        # state 的 value 必须能 JSON 序列化，这里先做一次净化，避免回写时才炸
        self._state = json.loads(json.dumps(state, ensure_ascii=False, default=str))
        self._dirty_state = True

    def append_event(self, etype: str, payload=None) -> dict:
        e = {"ts": _now(), "task_id": self.task_id, "type": etype,
             "payload": payload or {}}
        self._events.append(e)
        return e

    def read_events(self) -> list:
        return list(self._events)

    def scratch(self, *parts: str) -> str:
        return os.path.join(self._scratch, *parts)

    def deliver(self, state: dict) -> dict:
        """云端没有长期目录，把产物收进 artifacts.files（base64），供调用方下载。"""
        from .qa import build_qa_record
        arts = state.setdefault("artifacts", {})
        qa_rec = build_qa_record(state)
        arts["qa_record"] = qa_rec
        files = arts.setdefault("files", {})

        report = arts.get("report")
        period = state.get("form", {}).get("period", self.task_id)
        if isinstance(report, str) and os.path.exists(report):
            files.update(self._inline(report, "%s_经营周报.docx" % period))
        for c in arts.get("charts", []) or []:
            if isinstance(c, str) and os.path.exists(c):
                files.update(self._inline(c, os.path.basename(c)))

        conv = getattr(self.context, "conversation_id", "?")
        return {"delivery_ref": "makers://conversation/%s/%s" % (conv, self.task_id),
                "files": files}

    # ---------- 内部 ----------
    def _inline(self, path: str, name: str) -> dict:
        """把磁盘文件读成 base64 塞进 artifacts.files；过大则只登记元信息。"""
        if not os.path.exists(path):
            return {}
        size = os.path.getsize(path)
        if size > MAX_INLINE_BYTES:
            return {name: {"oversize": True, "bytes": size,
                           "note": "产物超过 %d 字节内联上限，未随响应返回"
                                   % MAX_INLINE_BYTES}}
        with open(path, "rb") as f:
            return {name: base64.b64encode(f.read()).decode("ascii")}

    async def _read_events_from_store(self) -> list:
        """分页拉取消息历史里属于本任务的事件。

        单次 get_messages 上限 100 条，所以循环翻页直到取完或达到安全上限。
        """
        events, cursor, guard = [], None, 0
        store = self.context.store
        cid = self.context.conversation_id
        while guard < 100:                      # 最多 10000 条，防止异常对话拖垮单次请求
            guard += 1
            kwargs = {"conversation_id": cid, "limit": 100, "order": "asc"}
            if cursor:
                kwargs["after"] = cursor
            try:
                msgs = await store.get_messages(**kwargs)
            except Exception:
                break
            if not msgs:
                break
            batch_ids = []
            for m in msgs:
                mid, content, meta = _unpack_message(m)
                batch_ids.append(mid)
                if (meta or {}).get("record") != EVENT_TAG:
                    continue                    # 人与 Agent 的对话消息，不是任务事件
                if (meta or {}).get("task_id") not in (None, self.task_id):
                    continue                    # 同一会话里的其它任务
                try:
                    e = json.loads(content) if isinstance(content, str) else content
                except Exception:
                    continue
                if isinstance(e, dict):
                    events.append(e)
            if len(msgs) < 100:
                break
            cursor = batch_ids[-1] if batch_ids else None
            if not cursor:
                break
        return events


def _unpack_message(m):
    """消息对象可能是平台返回的 dataclass，也可能是 dict，这里统一取值。"""
    if isinstance(m, dict):
        return m.get("message_id"), m.get("content"), m.get("metadata") or {}
    return (getattr(m, "message_id", None), getattr(m, "content", None),
            getattr(m, "metadata", None) or {})


def make_disk_storage(workspace: str, task_id: str) -> DiskStorage:
    return DiskStorage(workspace, task_id)
