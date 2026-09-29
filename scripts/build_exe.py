#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打包成 Windows 单文件 exe（PyInstaller）。

用法：
    python scripts/build_exe.py              # 直接构建
    python scripts/build_exe.py --clean       # 先删掉 build/dist 再构建

产物：dist/contest-workbench.exe（双击即用，不需要用户装 Python）

★ 打包时要处理的三件事（都是踩过才知道的）：
  1. **随包资源**：prompts / web 静态文件 / 示例数据 必须显式 --add-data，否则运行时找不到
  2. **隐藏依赖**：uvicorn 动态 import 协议实现，PyInstaller 静态分析看不到 → 要 --hidden-import
  3. **子进程陷阱**：报告里的 Word 转换在源码里是起子进程的，打包后 sys.executable 就是 exe 本身
     → 已在 `tools/report.py` 改成**进程内调用**转换函数（见该文件注释）
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Windows 用分号分隔 --add-data，其它平台用冒号
SEP = ";" if sys.platform.startswith("win") else ":"

DATA = [
    ("contest_workbench/prompts", "contest_workbench/prompts"),
    ("contest_workbench/web/static", "contest_workbench/web/static"),
    ("samples", "samples"),
]

# uvicorn 靠字符串动态 import 协议实现，静态分析发现不了
HIDDEN = [
    "uvicorn.logging",
    "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    "contest_workbench.tools.md2docx",   # 报告转换器（惰性 import，显式声明更稳）
    "docx",                          # python-docx
]


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 contest-workbench 为单文件 exe")
    ap.add_argument("--clean", action="store_true", help="构建前清掉 build/ 与 dist/")
    ap.add_argument("--name", default="contest-workbench", help="产物名，默认 contest-workbench")
    ap.add_argument("--onedir", action="store_true", help="打成目录（启动快，但不是一个文件）")
    args = ap.parse_args()

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("❌ 没装 PyInstaller。请先：")
        print("   pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple")
        return 2

    if args.clean:
        for d in ("build", "dist"):
            p = ROOT / d
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)
                print(f"已删除 {p}")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir" if args.onedir else "--onefile",
        "--name", args.name,
        # 打包后不需要控制台？留着：出问题时用户能看到提示，且我们要打印访问地址
        "--console",
    ]
    for src, dst in DATA:
        cmd += ["--add-data", f"{src}{SEP}{dst}"]
    for h in HIDDEN:
        cmd += ["--hidden-import", h]
    cmd += ["launcher.py"]

    print("执行：", " ".join(cmd[:8]), "…")
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        print("❌ 打包失败，看上面的报错")
        return r.returncode

    out = ROOT / "dist" / (f"{args.name}.exe" if sys.platform.startswith("win") else args.name)
    if out.exists():
        print("\n✅ 打包完成：" + str(out))
        print(f"   大小：{out.stat().st_size / 1024 / 1024:.1f} MB")
        print("   双击即可运行（会自动打开浏览器）；不需要用户装 Python。")
        print("   提示：把题库放在 exe 同目录的 data/题库/<分区>/ 下即可被读到。")
    else:
        print("⚠️ 打包命令成功但没找到产物，检查 dist/ 目录")
    return 0


if __name__ == "__main__":
    sys.exit(main())
