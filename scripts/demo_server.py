#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演示模式启动器（源码方式运行用这个；exe 用「演示模式.bat」）。

真正的模拟设备在 `contest_workbench/demo.py` —— 放包里是为了**打包成 exe 也能用**
（试用包承诺"不用装 Python"，所以不能让演示模式依赖 scripts/ 下的脚本）。

用法：
    python scripts/demo_server.py                 # 起在 8765
    python scripts/demo_server.py --port 8899
"""
from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="演示模式：不用真板子看完整效果")
    ap.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    ap.add_argument("--no-browser", action="store_true", help="不要自动开浏览器")
    args = ap.parse_args()

    import os
    os.environ["CONTEST_DEMO"] = "1"          # ★ 和 exe 走同一条路（app 启动时识别）

    from contest_workbench import demo
    print("=" * 62)
    print(demo.安装())
    print(f"  界面地址：http://127.0.0.1:{args.port}")
    print("=" * 62)

    from contest_workbench.web import app as webapp
    import uvicorn
    if not args.no_browser:
        threading.Timer(1.5, lambda: __import__("webbrowser").open(
            f"http://127.0.0.1:{args.port}")).start()
    uvicorn.run(webapp.app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
