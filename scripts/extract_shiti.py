#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把赛题 PDF 批量抽成可读文本（修掉 PDF 的"一字一行"排版），存入题库目录。

用法：
    python scripts/extract_shiti.py                          # 用下面的默认路径
    python scripts/extract_shiti.py "D:\\我的赛题" "data\\题库\\2026-省赛"

要点（这两个坑很常见）：
  1) pypdf 默认 extract_text() 对带字距排版的 PDF 会**每字一行** → 用 extraction_mode="layout"
  2) 仍会有短行/中文间空格 → 再做短行合并 + 去中文间空格 + 压空行
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
# 默认源目录用「相对/中性」路径，别把某人机器的绝对路径写进开源仓库
# （原来写死了 C:\Users\<某人>\Desktop\... 既泄露用户名，对别人也完全没用）
DEFAULT_SRC = Path(os.environ.get("CONTEST_SRC", "真题库"))
DEFAULT_DST = ROOT / "data" / "题库" / "2026-省赛"


def clean(raw: str) -> str:
    """保守清洗：短行并回上一行、去掉中文之间的空格、压掉连续空行。"""
    out: list[str] = []
    for ln in raw.splitlines():
        t = ln.strip()
        if not t:
            out.append("")
            continue
        if out and len(t) <= 2 and out[-1] and not out[-1].endswith(("。", "：", "；", "）", ")", "】")):
            out[-1] = out[-1] + t
        else:
            out.append(t)
    text = "\n".join(out)
    text = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def main(argv: list[str]) -> int:
    src = Path(argv[0]) if len(argv) > 0 else DEFAULT_SRC
    dst = Path(argv[1]) if len(argv) > 1 else DEFAULT_DST
    if not src.exists():
        print(f"❌ 源目录不存在：{src}\n   用法：python scripts/extract_shiti.py <源目录> [目标目录]")
        return 2
    dst.mkdir(parents=True, exist_ok=True)

    rows = []
    for fn in sorted(os.listdir(src)):
        if not fn.lower().endswith(".pdf"):
            continue
        p = src / fn
        try:
            r = PdfReader(str(p))
            pages = []
            for i, pg in enumerate(r.pages, 1):
                try:
                    t = pg.extract_text(extraction_mode="layout") or ""
                except Exception:
                    t = pg.extract_text() or ""
                pages.append(f"\n===== 第 {i} 页 =====\n{clean(t)}")
            body = "\n".join(pages).strip()
            out = dst / (re.sub(r'[\\/:*?"<>|]', "_", fn[:-4]) + ".md")
            out.write_text(f"# {fn[:-4]}\n\n> 来源：{p}\n> 页数：{len(r.pages)}\n\n{body}\n", encoding="utf-8")
            rows.append((fn, len(r.pages), len(body), "✅"))
        except Exception as e:
            rows.append((fn, -1, 0, f"❌ {type(e).__name__}: {e}"))

    print(f"源：{src}\n目标：{dst}\n")
    for fn, pg, ln, flag in rows:
        print(f"{flag} {fn[:40]:<42} {pg:>3} 页 {ln:>6} 字符")
    print(f"\n共 {len(rows)} 个 PDF")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
