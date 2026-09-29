# -*- coding: utf-8 -*-
"""工具注册表：Agent 能用的"手"。

每个工具 = ①给模型看的说明（JSON Schema）②真正干活的 Python 函数。
模型只看得见说明，看不见代码 —— 所以说明写清楚很重要（什么时候用、参数是什么）。
"""
from __future__ import annotations

import json

from .shiti import list_shiti, read_shiti, search_qa
from .report import write_report

# 工具名 -> 执行函数
_FUNCS = {
    "list_shiti": lambda **kw: list_shiti(**kw),
    "read_shiti": lambda **kw: read_shiti(**kw),
    "search_qa": lambda **kw: search_qa(**kw),
    "write_report": lambda **kw: write_report(**kw),
}

# 给模型看的说明书
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_shiti",
            "description": "列出本地真题库里的所有赛题（题号、标题、文件）。开始分析前先调用它，确认题号对应哪道题。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_shiti",
            "description": "读取某道赛题的正文（已做过 PDF 排版清洗）。参数 name 可以是题号（如 H）或文件名片段。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "题号或文件名片段，例如 'H' 或 '滚球'"},
                    "max_chars": {"type": "integer", "description": "最多返回多少字符，默认 8000"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_qa",
            "description": "在赛区官方《问题解答（答疑）》里检索关键词。答疑常给出指标口径、器材限制、评分细节，写方案前务必查一次。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "关键词，如 '摆杆' 或 'H题'"},
                    "max_chars": {"type": "integer", "description": "最多返回多少字符，默认 4000"},
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_report",
            "description": "把最终分析报告写入 out/ 目录（同时生成 Word 版）。报告内容应为完整 Markdown。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "文件名（不含路径），如 'H题-分析报告.md'"},
                    "content": {"type": "string", "description": "完整 Markdown 正文"},
                },
                "required": ["filename", "content"],
            },
        },
    },
]


def call_tool(name: str, args: dict) -> str:
    """按名字执行工具，永远返回字符串（出错也返回错误说明，让模型自己纠错）。"""
    fn = _FUNCS.get(name)
    if fn is None:
        return f"[错误] 没有这个工具：{name}。可用工具：{', '.join(_FUNCS)}"
    try:
        out = fn(**(args or {}))
        if isinstance(out, (dict, list)):
            return json.dumps(out, ensure_ascii=False)[:20000]
        return str(out)
    except TypeError as e:
        return f"[错误] 参数不对：{e}"
    except Exception as e:
        return f"[错误] 工具执行失败：{type(e).__name__}: {e}"
