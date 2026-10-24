# -*- coding: utf-8 -*-
"""事件日志：记录全部执行事件（状态流转 / 模型调用 / 工具调用 / 质检 / 审核）。

本模块现在是薄封装，**写到哪里由 app/storage.py 的 Storage 后端决定**：

- 本地 DiskStorage   → tasks/<id>/events.jsonl，便于直接用文本工具查看
- 云端 MakersStorage → 同一 conversation 的消息历史（metadata 打 task-event 标记）

事件只在云端做增量追加，flush 时只写本次新增的部分，重复 flush 不会产生重复事件。
"""


class EventLog:
    def __init__(self, storage):
        self.storage = storage

    def log(self, task_id: str, etype: str, payload=None) -> dict:
        return self.storage.append_event(etype, payload)

    def read_all(self) -> list:
        return self.storage.read_events()
