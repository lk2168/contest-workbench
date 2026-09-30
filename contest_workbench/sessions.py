# -*- coding: utf-8 -*-
"""追问用的**对话记忆**（纯内存、本机、按会话 id 存）。

为什么需要它：Agent 跑完一次分析就把 messages 丢了 —— 用户想接着问"第 3 步的器件能不能换成 XX"
时，模型已经忘了刚才分析的是哪道题。这里把上下文留下来。

★ 只保留"人话"部分：`user` 的提问 与 `assistant` 的最终回答。
  中间的 tool_calls / tool 结果**不保留** —— 它们又长又碎，重放既费 token 又容易因
  配对被截断而变成非法请求（assistant.tool_calls 必须紧跟对应的 tool 结果）。
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

MAX_SESSIONS = 8           # 本机单用户，留最近几次分析足够
MAX_TURNS = 12             # 单会话最多保留多少轮 Q/A（防上下文无限膨胀）
TTL = 6 * 3600             # 6 小时不用就丢


class Sessions:
    """极简会话记忆（线程安全；超出容量按最久未用淘汰）。"""

    def __init__(self, max_sessions: int = MAX_SESSIONS, max_turns: int = MAX_TURNS,
                 ttl: int = TTL) -> None:
        self._d: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()
        self.max_sessions = max_sessions
        self.max_turns = max_turns
        self.ttl = ttl

    # ---------- 内部 ----------
    def _gc(self) -> None:
        now = time.time()
        for sid in [s for s, v in self._d.items() if now - v["ts"] > self.ttl]:
            self._d.pop(sid, None)

    # ---------- 对外 ----------
    def start(self, sid: str, domain: str, task: str, answer: str, meta: dict | None = None) -> None:
        """一次分析结束后，把"任务 + 报告"作为上下文起点存起来。"""
        with self._lock:
            self._d[sid] = {
                "domain": domain,
                "task": task,
                "label": (meta or {}).get("label", ""),
                "turns": [{"q": task, "a": answer}],
                "ts": time.time(),
            }
            self._d.move_to_end(sid)
            while len(self._d) > self.max_sessions:
                self._d.popitem(last=False)
            self._gc()

    def add_turn(self, sid: str, question: str, answer: str) -> None:
        with self._lock:
            v = self._d.get(sid)
            if not v:
                return
            v["turns"].append({"q": question, "a": answer})
            v["turns"] = v["turns"][-self.max_turns:]      # 只留最近几轮
            v["ts"] = time.time()
            self._d.move_to_end(sid)

    def get(self, sid: str) -> dict | None:
        with self._lock:
            self._gc()
            v = self._d.get(sid)
            if v:
                self._d.move_to_end(sid)
            return v

    def messages(self, sid: str, system_prompt: str, question: str) -> list[dict] | None:
        """拼出"接着聊"需要的 messages：system + 历史（人话部分）+ 本次问题。

        返回 None 表示这个会话不存在（前端应先做一次分析）。
        """
        v = self.get(sid)
        if not v:
            return None
        msgs: list[dict] = [{"role": "system", "content": system_prompt}]
        for t in v["turns"]:
            msgs.append({"role": "user", "content": t["q"]})
            msgs.append({"role": "assistant", "content": t["a"]})
        msgs.append({"role": "user", "content": question})
        return msgs

    def clear(self, sid: str) -> None:
        with self._lock:
            self._d.pop(sid, None)

    def count(self) -> int:
        with self._lock:
            self._gc()
            return len(self._d)


SESSIONS = Sessions()      # 进程级单例（本机单用户够用）
