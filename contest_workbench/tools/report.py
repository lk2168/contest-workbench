# -*- coding: utf-8 -*-
"""报告工具：写 Markdown，并顺手转一份 Word（复用本包内的 md2docx.py）。"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from ..config import OUT_DIR

# 转换器就放在本包内（tools/md2docx.py），随仓库走，不依赖外部路径
MD2DOCX = Path(__file__).resolve().parent / "md2docx.py"


def _to_docx(md_path: Path, docx_path: Path) -> tuple[bool, str]:
    """Markdown → Word。

    ★ 必须在**进程内**调用转换器。原来用 `[sys.executable, md2docx.py, ...]` 起子进程，
      打包成 exe 后 `sys.executable` 就是 **exe 自己** —— 会把整个程序再启动一遍（还会抢端口），
      于是"转 Word"变成打开第二个窗口。这是打包后才会暴露的坑。
    """
    first_err = ""
    try:
        from . import md2docx          # 直接 import：打包工具也能静态识别这个依赖
        md2docx.convert(str(md_path), str(docx_path))
        return True, ""
    except Exception as e:
        first_err = f"{type(e).__name__}: {e}"

    # 开发环境回退：只有确实在用真正的 Python 解释器时才起子进程
    is_python = Path(sys.executable).name.lower().startswith("python")
    if not getattr(sys, "frozen", False) and is_python and MD2DOCX.exists():
        try:
            subprocess.run([sys.executable, str(MD2DOCX), str(md_path), str(docx_path)],
                           check=True, capture_output=True, timeout=120)
            return True, ""
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
    return False, first_err


def write_report(filename: str, content: str) -> str:
    """把 Markdown 报告写入 out/，并尝试生成同名 .docx。"""
    safe = re.sub(r'[\\/:*?"<>|]', "_", (filename or "报告.md").strip())
    if not safe.endswith(".md"):
        safe += ".md"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    md_path = OUT_DIR / safe
    md_path.write_text(content, encoding="utf-8")

    docx_note = ""
    docx_path = md_path.with_suffix(".docx")
    ok, err = _to_docx(md_path, docx_path)
    if ok:
        docx_note = f"，Word 版：{docx_path}"
    else:
        docx_note = f"（Word 转换失败：{err or '未知原因'}，Markdown 已保存）"
    return f"已写入 {md_path}（{len(content)} 字符）{docx_note}"
