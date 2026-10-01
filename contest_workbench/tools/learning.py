# -*- coding: utf-8 -*-
"""知识点映射：把「这道题考什么」变成「你要会什么、怎么自学」。

★ 全部走**确定性匹配**（关键词命中），不调用模型：
  - 不花 token、结果可复现、可被测试（见 tests/test_learning.py）
  - **链接不由模型生成**（幻觉重灾区）：只给"搜索词"和**代码拼接**的搜索入口
  - 命中依据是题库真题的实际用词（数据在 prompts/knowledge/<分区>.yaml）

给谁用：
  - 模型：通过 suggest_learning 工具拿到清单，写进报告的"前置知识"部分
  - 网页端：通过 /api/learning 拿到结构化数据渲染成面板（含一键复制搜索词）
"""
from __future__ import annotations

import urllib.parse
from pathlib import Path

from ..domains import PROMPT_DIR, get_domain

_CACHE: dict[str, list[dict]] = {}


def knowledge_path(domain_id: str) -> Path:
    """知识点文件位置：prompts/knowledge/<分区>.yaml（随包发布）。"""
    return PROMPT_DIR / "knowledge" / f"{domain_id}.yaml"


def load_knowledge(domain_id: str) -> list[dict]:
    """读取并缓存某分区的知识点表；文件不存在/格式错 → 返回空表（不抛异常）。

    ★ 返回空表而不是报错：知识点是"锦上添花"，不能因为它挡住主流程。
    """
    if domain_id in _CACHE:
        return _CACHE[domain_id]
    p = knowledge_path(domain_id)
    items: list[dict] = []
    if p.exists():
        try:
            import yaml
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            raw = data.get("items") or []
            for i, it in enumerate(raw):
                if not isinstance(it, dict) or not it.get("知识点"):
                    continue
                items.append({
                    "id": str(it.get("id") or f"item-{i}"),
                    "知识点": str(it["知识点"]),
                    "命中关键词": [str(k) for k in (it.get("命中关键词") or [])],
                    "掌握判据": [str(k) for k in (it.get("掌握判据") or [])],
                    "搜索词": [str(k) for k in (it.get("搜索词") or [])],
                    "核验资源": [dict(r) for r in (it.get("核验资源") or [])],
                })
        except Exception:
            items = []
    _CACHE[domain_id] = items
    return items


def clear_cache() -> None:
    """测试/热更新用：下次读取重新走磁盘（改完 yaml 不必重启）。"""
    _CACHE.clear()


def match_knowledge(text: str, items: list[dict], top_k: int = 6) -> list[dict]:
    """按关键词命中给知识点打分排序（确定性）。

    打分规则：命中的关键词**按长度加权**（越长越具体，如"功率因数"优于"功率"），
    同分按条目原顺序（保证结果稳定，可测试）。
    """
    hay = (text or "").lower()
    scored: list[tuple[int, int, dict]] = []
    for idx, it in enumerate(items):
        hits = [k for k in it["命中关键词"] if k and k.lower() in hay]
        if not hits:
            continue
        score = sum(len(h) for h in hits)
        scored.append((score, idx, {**it, "命中": hits, "得分": score}))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [x[2] for x in scored[:top_k]]


def search_links(word: str) -> dict:
    """把搜索词变成**可点击的搜索入口**（代码拼接，不依赖模型生成链接）。

    为什么这样做：让模型直接给 URL 是幻觉重灾区（点开常年 404）。
    搜索页 URL 是"可构造"的 —— 只要引擎的搜索路径不变就永远有效。
    """
    q = urllib.parse.quote(word)
    return {
        "bilibili": f"https://search.bilibili.com/all?keyword={q}",
        "bing": f"https://www.bing.com/search?q={q}",
    }


def learning_for(domain_id: str = "diansai", text: str = "", top_k: int = 6) -> dict:
    """结构化结果（网页端直接用，不做二次解析）。"""
    items = load_knowledge(domain_id)
    matched = match_knowledge(text, items, top_k=top_k) if text.strip() else items[:top_k]
    from_keywords = bool(text.strip())
    out = []
    for it in matched:
        links = {}
        for w in it["搜索词"]:
            links[w] = search_links(w)
        out.append({**it, "搜索链接": links})
    return {
        "domain": domain_id,
        "total": len(items),
        "matched": len(out),
        "by_keywords": from_keywords,
        "has_knowledge_file": knowledge_path(domain_id).exists(),
        "items": out,
    }


def suggest_learning(file: str = "", name: str = "", text: str = "",
                     max_items: int = 6) -> str:
    """工具入口：给模型看的 Markdown 清单。

    file/name 二选一（题号或文件名），会自动去读题面正文再匹配；
    也可以直接传 text（例如某一段得分点描述）。
    """
    domain_id = "diansai"
    try:
        from .shiti import current_domain_id, read_shiti
        domain_id = current_domain_id()
    except Exception:
        pass

    body = text or ""
    source = "（直接给的关键词）"
    if not body and (file or name):
        try:
            from .shiti import read_shiti
            body = read_shiti(file=file, name=name, max_chars=12000)
            source = f"题面（{file or name}）"
        except Exception as e:
            return f"[错误] 读不到题面：{type(e).__name__}: {e}"

    data = learning_for(domain_id, text=body, top_k=max_items)
    if not data["items"]:
        p = knowledge_path(domain_id)
        return (f"[错误] 没有可用的知识点表：{p}\n"
                "（知识点是可选增强，不影响分析与调参；可在该文件里补充条目）")

    lines = [f"# 这道题要会什么（来源：{source}）", ""]
    if data["by_keywords"]:
        lines.append(f"按关键词命中 {data['matched']} / {data['total']} 个知识块，按命中度排序。")
    else:
        lines.append(f"（没给题面，列出知识表前 {data['matched']} / {data['total']} 个知识块）")
    lines.append("")
    for n, it in enumerate(data["items"], 1):
        hit = ("　命中：" + "、".join(it["命中"])) if it.get("命中") else ""
        lines.append(f"## {n}. {it['知识点']}{hit}")
        lines.append("")
        if it["掌握判据"]:
            lines.append("**怎么判断自己会了**（能自测才算会）：")
            lines += [f"- [ ] {c}" for c in it["掌握判据"]]
            lines.append("")
        if it["搜索词"]:
            lines.append("**自己搜这些词**（别只看视频，要动手做一遍）：")
            lines += [f"- `{w}`" for w in it["搜索词"]]
            lines.append("")
        if it["核验资源"]:
            lines.append("**核验过的资源**（不写网址，避免失效/编造）：")
            lines += [f"- {r.get('类型', '资源')}：{r.get('名称', '')}"
                      + (f"（{r['备注']}）" if r.get("备注") else "") for r in it["核验资源"]]
            lines.append("")
    lines.append("> 提示：搜索入口由界面拼好（B站/必应），不用自己记网址。")
    return "\n".join(lines)
