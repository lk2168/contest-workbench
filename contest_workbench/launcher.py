# -*- coding: utf-8 -*-
"""打包成 exe 后的启动入口：起网页端服务 + 自动打开浏览器。

为什么不直接 `uvicorn contest_workbench.web.app:app`：
  打包（PyInstaller）后没有"当前目录"这个前提，模块也不是从磁盘 import 的字符串路径，
  所以必须传 **app 对象**；同时要自己处理端口占用与浏览器打开。

命令行参数（给愿意折腾的人）：
  --port N      指定端口（默认从 8765 起找第一个空闲的）
  --no-browser  不自动开浏览器（服务器/远程用）
"""
from __future__ import annotations

import socket
import sys
import threading
import webbrowser

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def find_free_port(start: int = 8765, tries: int = 30) -> int:
    """从 start 起找一个能绑上的端口（被占用就 +1）。"""
    for p in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    no_browser = "--no-browser" in argv
    port = find_free_port()
    if "--port" in argv:
        try:
            port = int(argv[argv.index("--port") + 1])
        except Exception:
            pass

    try:
        import uvicorn
    except ImportError:
        print("缺少依赖：uvicorn。开发环境下请先 `pip install -r requirements.txt`。")
        return 2

    from .web.app import app

    url = f"http://127.0.0.1:{port}"
    print("=" * 56)
    print("  大学生竞赛工作台 已启动")
    print(f"  浏览器打开：{url}")
    print("  关闭这个窗口即停止服务（数据与报告只存在本机）")
    print("=" * 56)

    if not no_browser:
        # 等 uvicorn 起来再开浏览器，否则会看到"无法访问"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
