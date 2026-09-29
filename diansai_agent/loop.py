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
                 verbose: bool = True) -> None:
        self.llm = llm
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.verbose = verbose
        self.usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)

    def run(self, task: str) -> str:
        """跑完一个任务，返回模型的最终回答（文本）。"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": task},
        ]

        for step in range(1, self.max_steps + 1):
            self._log(f"\n── 第 {step} 步：问模型 ──")
            msg = self.llm.chat(messages, tools=TOOL_SCHEMAS)
            for k in self.usage_total:  # 累计 token，最后算钱
                self.usage_total[k] += (msg.get("_usage") or {}).get(k, 0)
            messages.append({k: v for k, v in msg.items() if not k.startswith("_")})

            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                self._log("── 模型给出最终回答，结束 ──")
                return msg.get("content") or ""

            for i, tc in enumerate(tool_calls):
                fn = tc.get("function") or {}
                name = fn.get("name") or ""
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception:
                    args = {}
                self._log(f"   🔧 调用工具 {name}（参数：{json.dumps(args, ensure_ascii=False)[:160]}）")
                result = call_tool(name, args)
                self._log(f"   ↳ 结果 {len(str(result))} 字符")
                messages.append({
                    # 少数实现会漏掉 id，这里兜一个，否则整个请求会被 API 判为非法
                    "tool_call_id": tc.get("id") or f"call_{step}_{i}",
                    "role": "tool",
                    "content": str(result)[:12000],  # 工具结果别太长，省 token
                })

        self._log("⚠️ 达到最大步数，强制收尾")
        messages.append({"role": "user", "content": "请基于已有信息直接给出最终报告，不要再调用工具。"})
        msg = self.llm.chat(messages)
        for k in self.usage_total:  # 收尾那一次调用也要计入
            self.usage_total[k] += (msg.get("_usage") or {}).get(k, 0)
        return msg.get("content") or ""
