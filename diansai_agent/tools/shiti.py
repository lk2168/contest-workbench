# -*- coding: utf-8 -*-
"""赛题工具：列出真题 / 读赛题正文 / 检索官方答疑。

真题库默认指向工作区里已建好的目录，可用环境变量 DIANSAI_KB 覆盖。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_KB = Path(
    os.environ.get(
        "DIANSAI_KB",
        r"D:\deepseek-harness\gongzuoqu\diansai\真题库\2026省赛",
    )
)


def _kb() -> Path:
    return Path(os.environ.get("DIANSAI_KB", str(DEFAULT_KB)))


def list_shiti() -> str:
    """列出真题库里的题（题号 + 标题 + 字数）。"""
    kb = _kb()
    if not kb.exists():
        return f"[错误] 真题库不存在：{kb}（可用环境变量 DIANSAI_KB 指定）"
    rows = []
    for p in sorted(kb.glob("*.md")):
        if p.name.startswith("答疑"):
            continue
        m = re.match(r"([A-Za-z])\s*题", p.stem)
        code = m.group(1).upper() if m else "?"
        title = re.sub(r"^[A-Za-z]\s*题[_ ]*", "", p.stem).replace("_", " ")
        rows.append(f"{code} 题 | {title} | {p.stat().st_size} 字节 | 文件：{p.name}")
    if not rows:
        return f"[错误] 真题库里没有赛题文件：{kb}"
    return "本地真题库（%s）：\n" % kb + "\n".join(rows)


def _find(name: str) -> Path | None:
    kb = _kb()
    key = (name or "").strip()
    files = [p for p in kb.glob("*.md") if not p.name.startswith("答疑")]
    if not key:
        return None
    # 1) 题号精确匹配（H / H题）
    m = re.match(r"^([A-Za-z])\s*题?$", key)
    if m:
        code = m.group(1).upper()
        for p in files:
            if re.match(rf"^{code}\s*题", p.stem):
                return p
    # 2) 文件名片段
    for p in files:
        if key in p.stem:
            return p
    return None


def read_shiti(name: str, max_chars: int = 8000) -> str:
    """读取赛题正文（已做 PDF 排版清洗）。"""
    p = _find(name)
    if p is None:
        return f"[错误] 没找到赛题「{name}」。先调用 list_shiti 看看有哪些题。"
    text = p.read_text(encoding="utf-8")
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n……（已截断，原文共 {len(p.read_text(encoding='utf-8'))} 字符）"
    return f"【{p.stem}】\n{text}"


def _qa_files() -> list[Path]:
    return sorted(_kb().glob("答疑*.md"))


def search_qa(keyword: str, max_chars: int = 4000) -> str:
    """在官方《问题解答》里按关键词检索（返回命中的段落）。"""
    files = _qa_files()
    if not files:
        return f"[提示] 真题库里没有答疑文件：{_kb()}"
    hits: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        # 按空行切段，找含关键词的段
        for seg in re.split(r"\n\s*\n", text):
            if keyword and keyword.lower() in seg.lower():
                hits.append(seg.strip())
    if not hits:
        return f"[结果] 答疑里没找到「{keyword}」。可以换个关键词（题目名、器件名、指标名）再试。"
    body = "\n\n---\n\n".join(hits)
    if len(body) > max_chars:
        body = body[:max_chars] + "\n\n……（命中较多，已截断）"
    return f"答疑中与「{keyword}」相关的 {len(hits)} 段：\n\n{body}"
