# -*- coding: utf-8 -*-
"""事件日志：记录全部执行事件（状态变化/模型调用/工具调用/质检/审核），支持断点恢复。"""
import json
import os
import time


class EventLog:
    def __init__(self, task_dir: str):
        self.path = os.path.join(task_dir, "events.jsonl")

    def log(self, task_id: str, etype: str, payload=None) -> dict:
        e = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "task_id": task_id,
            "type": etype,
            "payload": payload or {},
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        return e

    def read_all(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


def load_state(task_dir: str) -> dict:
    path = os.path.join(task_dir, "state.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(task_dir: str, state: dict) -> None:
    path = os.path.join(task_dir, "state.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 原子写，避免中断产生半截状态
