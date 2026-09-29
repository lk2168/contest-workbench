# -*- coding: utf-8 -*-
"""报告工具：写 Markdown，并顺手转一份 Word（复用工作区里的 md2docx.py）。"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from ..config import OUT_DIR

# 转换器就放在本包内（tools/md2docx.py），随仓库走，不依赖外部路径
MD2DOCX = Path(__file__).resolve().parent / "md2docx.py"


def write_report(filename: str, content: str) -> str:
    """把 Markdown 报告写入 out/，并尝试生成同名 .docx。"""
    safe = re.sub(r'[\\/:*?"<>|]', "_", (filename or "报告.md").strip())
    if not safe.endswith(".md"):
        safe += ".md"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md_path = OUT_DIR / safe
    md_path.write_text(content, encoding="utf-8")

    docx_note = ""
    if MD2DOCX.exists():
        docx_path = md_path.with_suffix(".docx")
        try:
            subprocess.run(
                [sys.executable, str(MD2DOCX), str(md_path), str(docx_path)],
                check=True, capture_output=True, timeout=120,
            )
            docx_note = f"，Word 版：{docx_path}"
        except Exception as e:
            docx_note = f"（Word 转换失败：{type(e).__name__}，Markdown 已保存）"
    return f"已写入 {md_path}（{len(content)} 字符）{docx_note}"
