#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动修掉「中文串里嵌了 ASCII 双引号」这个反复犯的错。

为什么需要它
------------
写补丁/测试脚本时，中文里想引用一个词（比如 说明"更冲"的方向），
很容易打成 ASCII 的双引号 `"`，于是和外层的字符串引号撞上 → SyntaxError。
这个坑我一天犯了 8 次，每次都要重来一轮。

做法：反复 `py_compile` → 解析报错行号 → 把该行**字符串内部**的 ASCII 双引号
按顺序换成中文引号「」（成对交替）→ 再编译，直到通过。
只动出问题的那一行，其它内容不碰。

用法：
    python scripts/fix_cn_quotes.py <文件> [<文件> ...]
"""
from __future__ import annotations

import py_compile
import re
import subprocess
import sys
import tempfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def compile_error_line(path: Path):
    """编译这个文件；出错就返回 (行号, 错误信息)，否则 (None, "")。"""
    # ★ 一定要用**绝对路径**：早先这里用相对路径 + cwd=临时目录 →
    #   文件根本没找到，报错里没有行号，被当成了"编译通过"（假阳性，害我多跑一轮）
    r = subprocess.run([sys.executable, "-m", "py_compile", str(path.resolve())],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 0:
        return None, ""
    err = (r.stderr or "").strip()
    m = re.search(r'line (\d+)', err)
    if not m:
        # 没有行号 ≠ 没问题：可能是文件不存在/权限等，必须如实报错
        return -1, err.splitlines()[-1] if err else "编译失败（原因不明）"
    return int(m.group(1)), (err.splitlines()[-1] if err else "")


def fix_line(line: str) -> tuple:
    """把一行里"字符串内部"的 ASCII 双引号换成「」；返回 (新行, 改了几处)。"""
    idx = [m.start() for m in re.finditer(r'"', line)]
    if len(idx) < 3:
        return line, 0                      # 少于 3 个引号 → 不是这个毛病
    inner = idx[1:-1]                       # 首尾是字符串定界符
    chars = list(line)
    for n, pos in enumerate(inner):
        chars[pos] = "「" if n % 2 == 0 else "」"
    return "".join(chars), len(inner)


def main() -> int:
    files = [Path(a) for a in sys.argv[1:]]
    if not files:
        print("用法：python scripts/fix_cn_quotes.py <文件> [<文件> ...]")
        return 2
    rc = 0
    for f in files:
        if not f.exists():
            print(f"  ❌ 找不到 {f}")
            rc = 1
            continue
        fixed_total = 0
        for _ in range(20):                 # 最多迭代 20 次（正常一两次就好）
            ln, err = compile_error_line(f)
            if ln is None:
                break
            if ln == -1:
                print(f"  ❌ {f.name}: 编译失败但没有行号（不是引号问题）：{err[:120]}")
                rc = 1
                break
            lines = f.read_text(encoding="utf-8").splitlines()
            if ln - 1 >= len(lines):
                print(f"  ❌ {f.name}: 报错行号 {ln} 超出文件范围")
                rc = 1
                break
            old = lines[ln - 1]
            new, n = fix_line(old)
            if n == 0:
                print(f"  ⚠️ {f.name} 第 {ln} 行不是这个毛病，需要手看：{old.strip()[:70]}")
                print(f"     {err[:110]}")
                rc = 1
                break
            lines[ln - 1] = new
            f.write_text("\n".join(lines) + "\n", encoding="utf-8")
            fixed_total += n
            print(f"  [ok] {f.name} 第 {ln} 行：{n} 个 ASCII 引号 → 「」")
        ln, err = compile_error_line(f)
        if ln is None:
            print(f"  ✅ {f.name} 编译通过（本次改了 {fixed_total} 处引号）")
        else:
            print(f"  ❌ {f.name} 仍有语法问题（第 {ln} 行）：{err[:110]}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
