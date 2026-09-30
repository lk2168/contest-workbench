# -*- coding: utf-8 -*-
"""Agent Loop —— 整个项目的心脏。

一句话：**让模型自己决定"下一步用哪个工具、传什么参数"，我们负责执行并把结果喂回去，
直到它说"我做完了"。**

循环长这样（每一步都在下面的代码里）：
    1) 把 messages 发给模型
    2) 模型要么直接回答（结束），要么返回 tool_calls（要用工具）
    3) 我们执行工具，把结果作为 role=tool 的消息追加进 messages
    4) 回到 1)，最多跑 max_steps 步（防止模型卡死烧钱）
"""
from __future__ import annotations

import json

from .llm import LLM
from .tools import TOOL_SCHEMAS, call_tool


class Agent:
    def __init__(self, llm: LLM, system_prompt: str, max_steps: int = 12,
                 verbose: bool = True, on_event=None) -> None:
        self.llm = llm
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.verbose = verbose
        # ★ 事件回调：网页端用它把"第 N 步 / 调用了哪个工具"实时推到浏览器（SSE）
        self.on_event = on_event
        self.usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _emit(self, kind: str, **data) -> None:
        """发一个结构化事件（给网页端），同时按需打印到终端。"""
        ev = {"kind": kind, **data}
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:
                pass  # 前端断了不能拖累 Agent 本体
        if self.verbose:
            msg = data.get("text") or ""
            if kind == "step":
                print(f"\n── 第 {data.get('n')} 步：问模型 ──", flush=True)
            elif kind == "tool":
                args = data.get("args") or {}
                print(f"   🔧 调用工具 {data.get('name')}（参数：{json.dumps(args, ensure_ascii=False)[:160]}）", flush=True)
            elif kind == "tool_result":
                print(f"   ↳ 结果 {data.get('chars')} 字符", flush=True)
            elif kind == "answer":
                print("── 模型给出最终回答，结束 ──", flush=True)
            elif msg:
                print(msg, flush=True)

    def _log(self, msg: str) -> None:
        self._emit("log", text=msg)

    def run(self, task: str = "", messages: list[dict] | None = None) -> str:
        """跑完一个任务，返回模型的最终回答（文本）。

        messages=None  → 开新对话（system + 本次 task）
        messages=[...] → **接着已有对话继续**（追问用）：调用方给出 system + 历史 + 新问题，
                         本方法只负责跑循环（这层不关心谁拼的消息，便于单独测试）。

        跑完把完整消息留在 `self.last_messages`，供上层保存上下文。
        """
        if messages is None:
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": task},
            ]
        self._emit("start", max_steps=self.max_steps)

        for step in range(1, self.max_steps + 1):
            self._emit("step", n=step)
            msg = self.llm.chat(messages, tools=TOOL_SCHEMAS)
            for k in self.usage_total:  # 累计 token，最后算钱
                self.usage_total[k] += (msg.get("_usage") or {}).get(k, 0)
            messages.append({k: v for k, v in msg.items() if not k.startswith("_")})

            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                # ★ 同时给 text 与 answer 两个键：text 供终端打印，answer 供网页端渲染。
                #   之前只发了 text，而前端读 ev.answer → 报告在网页端一直渲染不出来。
                text = msg.get("content") or ""
                self._emit("answer", text=text, answer=text)
                self.last_messages = messages
                return text

            for i, tc in enumerate(tool_calls):
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception:
                    args = {}
                self._emit("tool", name=name, args=args)
                result = call_tool(name, args)
                self._emit("tool_result", name=name, chars=len(str(result)))
                messages.append({
                    # 少数实现会漏掉 id，这里兜一个，否则整个请求会被 API 判为非法
                    "tool_call_id": tc.get("id") or f"call_{step}_{i}",
                    "role": "tool",
                    "content": str(result)[:12000],  # 工具结果别太长，省 token
                })

        self._emit("log", text="⚠️ 达到最大步数，强制收尾")
        messages.append({"role": "user", "content": "请基于已有信息直接给出最终报告，不要再调用工具。"})
        msg = self.llm.chat(messages)
        for k in self.usage_total:  # 收尾那一次调用也要计入
            self.usage_total[k] += (msg.get("_usage") or {}).get(k, 0)
        text = msg.get("content") or ""
        self._emit("answer", text=text, answer=text)
        self.last_messages = messages
        return text
