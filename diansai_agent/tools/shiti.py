# -*- coding: utf-8 -*-
"""题库工具：跨年份列出/检索赛题与历年规律。

题库目录（可多个，按顺序合并）：
  1. 仓库内置：<repo>/data/题库/            ← 随工作台走，开箱可用
  2. 环境变量 DIANSAI_KB 指定的目录
  3. 工作区外部真题库（本机默认路径，兼容旧用法）
"""
from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BUILTIN_DIR = REPO_ROOT / "data" / "题库"


def kb_dirs() -> list[Path]:
    """题库目录：内置优先；DIANSAI_KB 可追加外部题库（内容会按文件名去重）。"""
    dirs = [BUILTIN_DIR]
    env = os.environ.get("DIANSAI_KB")
    if env:
        dirs.append(Path(env))
    out, seen = [], set()
    for d in dirs:
        try:
            rp = d.resolve()
        except Exception:
            continue
        if rp in seen or not d.exists():
            continue
        seen.add(rp)
        out.append(d)
    return out


def _is_builtin(p: Path) -> bool:
    return "data" in p.parts and "题库" in p.parts


def _dedupe(files: list[Path]) -> list[Path]:
    """同一道题只留一份（内置题库优先）。

    去重键用「年份目录 + 文件名」：**不能只用文件名** —— 不同年份会有同名文件
    （例如每年都可能有《答疑汇总.md》），只用文件名会把别的年份误删。
    """
    best: dict[tuple[str, str], Path] = {}
    for p in files:
        key = (p.parent.name, p.stem)
        cur = best.get(key)
        if cur is None or (_is_builtin(p) and not _is_builtin(cur)):
            best[key] = p
    return sorted(best.values(), key=lambda p: (p.parent.name, p.stem), reverse=True)


def _is_shiti(p: Path) -> bool:
    """文件名是否像一道题（A题_xxx / A_xxx），排除索引、规律、答疑等辅助文件。"""
    return bool(re.match(r"^[A-Za-z]\s*题?[_．.\s]", p.stem)) or bool(re.fullmatch(r"[A-Za-z]\s*题?", p.stem))


def _all_md() -> list[Path]:
    """题目文件（供 list_shiti / read_shiti）。"""
    files: list[Path] = []
    for d in kb_dirs():
        files += [p for p in d.rglob("*.md") if _is_shiti(p)]
    return _dedupe(files)


def _all_text_files() -> list[Path]:
    """题库里所有可检索的文本（含历年题名与分类、答疑等），供 search_tiku。"""
    files: list[Path] = []
    for d in kb_dirs():
        files += [p for p in d.rglob("*.md") if not p.name.startswith("_")]
    return sorted(set(files))


def _all_qa() -> list[Path]:
    files: list[Path] = []
    for d in kb_dirs():
        files += [p for p in d.rglob("*.md") if p.name.startswith("答疑")]
    return _dedupe(files)


def list_shiti() -> str:
    """列出题库里的所有赛题（按年份分组）。"""
    files = _all_md()
    if not files:
        return f"[错误] 题库为空。已查找：{', '.join(str(d) for d in kb_dirs())}"
    groups: dict[str, list[str]] = {}
    for p in files:
        year = p.parent.name
        m = re.match(r"([A-Za-z])\s*题", p.stem)
        code = m.group(1).upper() if m else "?"
        title = re.sub(r"^[A-Za-z]\s*题[_ ]*", "", p.stem).replace("_", " ")
        groups.setdefault(year, []).append(f"  {code} 题 | {title} | {p.stat().st_size} 字节")
    lines = [f"题库（共 {len(files)} 道题，来自 {len(groups)} 个年份/批次）："]
    for year in sorted(groups, reverse=True):
        lines.append(f"\n【{year}】")
        lines += groups[year]
    return "\n".join(lines)


def _find(name: str) -> Path | None:
    key = (name or "").strip()
    if not key:
        return None
    files = _all_md()
    m = re.match(r"^([A-Za-z])\s*题?$", key)
    if m:  # 只给题号：优先取最新年份的那道（通常最相关），并列出全部命中
        code = m.group(1).upper()
        hits = [p for p in files if re.match(rf"^{code}\s*题", p.stem)]
        if hits:
            return sorted(hits, key=lambda p: p.parent.name, reverse=True)[0]
    for p in sorted(files, key=lambda p: p.parent.name, reverse=True):
        if key in p.stem or key in p.parent.name:
            return p
    return None


def read_shiti(name: str, max_chars: int = 8000) -> str:
    """读取某道赛题正文（多来源命中时，提示还有别的年份同名题）。"""
    p = _find(name)
    if p is None:
        return f"[错误] 题库里没找到「{name}」。先 list_shiti 看看有什么。"
    text = p.read_text(encoding="utf-8")
    total = len(text)
    if total > max_chars:
        text = text[:max_chars] + f"\n\n……（已截断，原文 {total} 字符）"
    # 提示其它年份的同题号题目（注意：文件名不一定以字母开头，这里要防御性判断）
    m = re.match(r"([A-Za-z])", p.stem)
    same: list[str] = []
    if m:
        code = m.group(1)
        for q in _all_md():
            if q != p and re.match(rf"^{code}\s*题", q.stem):
                same.append(q.parent.name)
    tip = f"\n（提示：其它年份也有 {code} 题：{', '.join(same)}）" if same else ""
    return f"【{p.parent.name} · {p.stem}】{tip}\n{text}"


def search_qa(keyword: str, max_chars: int = 4000) -> str:
    """在官方《问题解答（答疑）》里按关键词检索段落。"""
    files = _all_qa()
    if not files:
        return f"[提示] 没有答疑文件。已查找：{', '.join(str(d) for d in kb_dirs())}"
    hits: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        for seg in re.split(r"\n\s*\n", text):
            if keyword and keyword.lower() in seg.lower():
                hits.append(seg.strip())
    if not hits:
        return f"[结果] 答疑里没找到「{keyword}」。换个关键词（题目名/器件名/指标名）再试。"
    body = "\n\n---\n\n".join(hits)
    if len(body) > max_chars:
        body = body[:max_chars] + "\n\n……（命中较多，已截断）"
    return f"答疑中与「{keyword}」相关的 {len(hits)} 段：\n\n{body}"


def search_tiku(keyword: str, max_chars: int = 6000) -> str:
    """跨年份检索题库（赛题正文 + 答疑 + 历年规律文档）。

    用途：分析今年这道题时，先看看"往年考过什么类似的、当时怎么做的"。
    """
    kw = (keyword or "").strip()
    if not kw:
        return "[错误] 请给一个关键词（器件、指标、算法、题目关键词）。"

    files: list[Path] = _all_text_files()

    hits: list[str] = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        for seg in re.split(r"\n\s*\n", text):
            if kw.lower() in seg.lower():
                hits.append(f"[{p.parent.name}/{p.stem}] {seg.strip()[:500]}")

    if not hits:
        return (f"[结果] 题库里没找到和「{kw}」相关的内容。"
                f"（提示：可换更通用的词，如 '摄像头'、'PID'、'无线'、'测量'）")

    body = "\n\n".join(dict.fromkeys(hits))  # dict 去重且保序
    if len(body) > max_chars:
        body = body[:max_chars] + "\n\n……（已截断，换更具体的关键词可缩小范围）"
    return f"与「{kw}」相关的 {len(hits)} 处命中（{len(files)} 个文件里搜的）：\n\n{body}"
