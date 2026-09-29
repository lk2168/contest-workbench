# -*- coding: utf-8 -*-
"""题库工具：按**竞赛分区**列出/检索赛题、答疑与历年规律。

题库布局（多分区）：
    data/题库/<分区>/<年份批次>/<题号>题_<题名>.md
例如：data/题库/diansai/2026-省赛/H题_车载平衡滚球运动控制系统.md

分区由环境变量 DIANSAI_DOMAIN 指定（CLI 的 --domain 会设置它），默认 diansai。
另外支持环境变量 DIANSAI_KB 追加一个外部题库目录（内容按「年份目录+文件名」去重）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from ..domains import KB_ROOT, get_domain


def current_domain_id() -> str:
    return (os.environ.get("DIANSAI_DOMAIN") or "diansai").strip().lower()


def kb_dirs() -> list[Path]:
    """当前分区的题库目录（可多个，按顺序合并、按「年份目录+文件名」去重）。

    顺序：① 分区目录 ② 环境变量追加的外部目录 ③ 旧的"题库根目录平铺"布局（兼容老用户）
    """
    dirs: list[Path] = []
    try:
        dirs.append(get_domain(current_domain_id()).kb_dir())
    except KeyError:
        dirs.append(KB_ROOT / current_domain_id())
    env = os.environ.get("DIANSAI_KB")
    if env:
        dirs.append(Path(env))
    # 兼容：若题库根目录下直接躺着年份目录（老布局），也当一份题库
    if KB_ROOT.exists() and any(re.fullmatch(r"\d{4}.*", p.name) for p in KB_ROOT.iterdir() if p.is_dir()):
        dirs.append(KB_ROOT)

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
    for item in list_shiti_structured():
        groups.setdefault(item["year"], []).append(
            f"  {item['code']} 题 | {item['title']} | {item['chars']} 字符")
    lines = [f"题库（共 {len(files)} 道题，来自 {len(groups)} 个年份/批次）："]
    for year in sorted(groups, reverse=True):
        lines.append(f"\n【{year}】")
        lines += groups[year]
    return "\n".join(lines)


def list_shiti_structured() -> list[dict]:
    """结构化题目清单（给网页端做"年份+题目"两级选择用）。

    为什么需要：题库里 2021/2023/2024/2025/2026 **都有 H 题**，
    只按题号找会有歧义 —— 必须带上「年份/批次」和文件名才能唯一定位。
    """
    items = []
    for p in _all_md():
        m = re.match(r"([A-Za-z])\s*题?[_．.\s]?", p.stem)
        code = (m.group(1).upper() if m else "?")
        title = re.sub(r"^[A-Za-z]\s*题?[_．.\s]*", "", p.stem).replace("_", " ").strip()
        items.append({
            "year": p.parent.name,
            "code": code,
            "title": title,
            "file": p.stem,          # 唯一定位用（去重后的口径与 _all_md 一致）
            "chars": p.stat().st_size,
            "label": f"{p.parent.name} · {code} 题 · {title}",
        })
    items.sort(key=lambda x: (x["year"], x["code"]), reverse=True)
    return items


def _find_exact(file_or_stem: str) -> Path | None:
    """按文件名/文件名去扩展名精确查找（网页端传的就是它，避免歧义）。"""
    key = Path(file_or_stem or "").name
    key_ns = key[:-3] if key.lower().endswith(".md") else key
    for p in _all_md():
        if p.name == key or p.stem == key_ns:
            return p
    return None


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


def read_shiti(name: str = "", max_chars: int = 8000, file: str = "") -> str:
    """读取某道赛题正文。

    优先用 `file`（网页端传来的精确文件名，能唯一定位年份）；否则按 `name`（题号或关键词）找，
    并在多命中时提示其它年份的同题号题目。
    """
    p = _find_exact(file) if file else None
    if p is None:
        p = _find(name)
    if p is None:
        which = file or name
        return (f"[错误] 题库里没找到「{which}」。先 list_shiti 看看有什么；"
                f"若有多个年份同题号，请用 file 参数精确指定（如 '2025-国赛/H题_野生动物巡查系统'）。")
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
