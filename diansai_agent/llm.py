# -*- coding: utf-8 -*-
"""LLM 调用层：只做一件事 —— 把 messages + tools 发给 DeepSeek，拿回结果。

用 requests 直接打 HTTP，不引第三方 SDK：
  - 依赖少（少一个出问题的环节）
  - 你能看清"一次模型调用"到底发了什么、回了什么（学 Agent 最重要的一步）
"""
from __future__ import annotations

import json
import time

import requests

from .config import Config


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.url = f"{cfg.base_url}/chat/completions"
        self.models_url = f"{cfg.base_url}/models"

    # ---------- 基础 ----------
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }

    def list_models(self) -> list[str]:
        """列出账号可用的模型名（第一次跑先做这个，确认模型名没写错）。"""
        r = requests.get(self.models_url, headers=self._headers(), timeout=30)
        if r.status_code != 200:
            raise LLMError(f"取模型列表失败 HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        return [m.get("id", "") for m in data.get("data", [])]

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float = 0.3, retries: int = 2) -> dict:
        """一次对话调用。返回 assistant 消息（可能带 tool_calls）。"""
        payload: dict = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        last_err = None
        for attempt in range(retries + 1):
            try:
                r = requests.post(self.url, headers=self._headers(),
                                  json=payload, timeout=180)
            except Exception as e:  # 网络抖一下很正常
                last_err = e
                time.sleep(2 * (attempt + 1))
                continue

            if r.status_code == 200:
                data = r.json()
                msg = data["choices"][0]["message"]
                msg["_usage"] = data.get("usage") or {}  # 供上层累计成本
                return msg

            # 429 / 5xx 重试；4xx 直接抛（参数或 Key 有问题，重试没意义）
            last_err = LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1))
                continue
            raise last_err

        raise LLMError(f"调用失败：{last_err}")

    @staticmethod
    def usage_of(resp: dict) -> dict:
        return resp.get("usage") or {}
