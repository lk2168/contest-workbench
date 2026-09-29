# -*- coding: utf-8 -*-
"""启动入口：**默认开原生桌面窗口**（pywebview + 系统自带 WebView2），浏览器只作回退。

为什么这样设计：
  - 打包成 exe 后如果只是"起服务 + 开浏览器"，用户看到的是**浏览器窗口**（有地址栏、混着别的标签页），
    不像一个"程序"。用 pywebview 包一层原生窗口，界面代码**一行都不用改**，
    却能拿到：自己的窗口标题与任务栏图标、无地址栏、关窗即退出。
  - pywebview 用 Windows 自带的 **WebView2 运行时**（Win11 预装），不额外装东西、体积增加很小。
  - 万一环境没有 WebView2（老系统）或没装 pywebview（只装了基础依赖）→ **自动回退到浏览器**，
    功能不受影响。

命令行参数：
  （默认）        开原生窗口
  --browser       改用默认浏览器打开（回退方式，也是没装 pywebview 时的行为）
  --server-only   只起服务、不开界面（局域网共享给队友；等价于旧的 --no-browser）
  --port N        指定端口（默认从 8765 起找第一个空闲的）
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import traceback
import webbrowser
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

APP_TITLE = "大学生竞赛工作台"
_STDOUT_IS_LOG = False      # ensure_streams() 把 stdout 接到日志文件后置 True，避免同一行写两遍


def log_path() -> Path:
    from .config import user_dir
    return user_dir() / "launcher.log"


def say(msg: str) -> None:
    """打印 + 写日志。

    ★ 打包成 --windowed（无控制台）后 stdout 可能是 None，print 会失败 —— 必须兜住，
      否则"双击没反应"时用户和我都拿不到任何线索。日志落在用户目录里。
    """
    try:
        print(msg, flush=True)
    except Exception:
        pass
    try:
        # stdout 已经指向日志文件时不再重复写（否则同一行会出现两次）
        if getattr(sys.stdout, "name", "") == str(log_path()):
            return
    except Exception:
        pass
    try:
        with log_path().open("a", encoding="utf-8") as f:
            f.write(time.strftime("[%H:%M:%S] ") + msg + "\n")
    except Exception:
        pass


def port_in_use(p: int) -> bool:
    """端口是否已被占用。

    ★ 不要用"设了 SO_REUSEADDR 再 bind 看成不成功"来判断 —— 在 **Windows** 上
      SO_REUSEADDR 允许绑定到别人正在监听的端口（端口劫持），于是会把"被占用"
      误判成"空闲"，程序选了个被占的端口，启动直接失败。
      正确做法：先 connect 试（能连上=有人监听），再不带 SO_REUSEADDR 试 bind。
    """
    with socket.socket() as s:
        s.settimeout(0.3)
        try:
            if s.connect_ex(("127.0.0.1", p)) == 0:
                return True
        except Exception:
            pass
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", p))
            return False
        except OSError:
            return True


def find_free_port(start: int = 8765, tries: int = 30) -> int:
    """从 start 起找一个**确实空闲**的端口（被占用就 +1）。"""
    for p in range(start, start + tries):
        if not port_in_use(p):
            return p
    return start


def ensure_streams() -> None:
    """保证 sys.stdout/stderr 不是 None。

    ★ 打包成 --windowed（无控制台）时，PyInstaller 会把它们设成 **None**，
      而 uvicorn 等库写日志时会因此抛异常 → 服务线程**静默死掉**，
      用户只看到窗口里"无法访问"，日志里只有一句"服务启动超时"。
      给它们接一个真实文件（就是我们的 launcher.log），既修问题又留线索。
    """
    import io
    global _STDOUT_IS_LOG
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                p = log_path()
                p.parent.mkdir(parents=True, exist_ok=True)
                setattr(sys, name, p.open("a", encoding="utf-8", buffering=1))
                _STDOUT_IS_LOG = True
            except Exception:
                setattr(sys, name, io.StringIO())


def _serve(app, port: int) -> None:
    """在后台线程里跑 uvicorn（webview.start() 会阻塞主线程，所以服务必须放后台）。

    ★ 必须把异常写进日志：打包后没有控制台，线程里抛异常是完全不可见的。
    """
    try:
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    except Exception:
        say("服务线程异常退出：\n" + traceback.format_exc())


def _wait_port(port: int, timeout: float = 20.0) -> bool:
    """等服务真的能连上（否则窗口会先显示"无法访问"）。"""
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def has_window_support() -> bool:
    try:
        import webview  # noqa: F401
        return True
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ensure_streams()          # ★ 必须在任何库写日志之前
    mode = "window"
    if "--browser" in argv:
        mode = "browser"
    if "--server-only" in argv or "--no-browser" in argv:
        mode = "server"
    port = find_free_port()
    if "--port" in argv:
        try:
            port = int(argv[argv.index("--port") + 1])
        except Exception:
            pass

    try:
        import uvicorn  # noqa: F401
    except ImportError:
        say("缺少依赖：uvicorn。开发环境下请先 `pip install -r requirements.txt`。")
        return 2

    from .web.app import app

    url = f"http://127.0.0.1:{port}"
    threading.Thread(target=_serve, args=(app, port), daemon=True).start()

    if not _wait_port(port):
        say(f"服务启动超时（{url} 连不上）—— 可能是端口被占用或防火墙拦截。")

    say("=" * 56)
    say(f"  {APP_TITLE} 已启动 → {url}")
    say(f"  运行方式：{'打包 exe' if getattr(sys, 'frozen', False) else '源码'} · 模式：{mode}")
    say("=" * 56)

    if mode == "server":
        say("（只起服务模式）把这个地址发给队友即可访问（需网络互达）。")
        return _idle()

    if mode == "browser" or not has_window_support():
        if mode == "window" and not has_window_support():
            say("没装 pywebview，回退到浏览器打开"
                "（想用原生窗口：pip install -r requirements-desktop.txt）")
        webbrowser.open(url)
        return _idle()

    # ── 原生窗口模式 ──
    import webview
    say("正在打开桌面窗口…（关掉窗口即退出程序）")
    webview.create_window(APP_TITLE, url, width=1280, height=860, min_size=(900, 620))
    webview.start()          # 阻塞直到窗口关闭
    say("窗口已关闭，程序退出。")
    return 0


def _idle() -> int:
    """没开窗口时（浏览器/只起服务），主线程要继续活着，否则服务会随进程退出。"""
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        say("启动失败：\n" + traceback.format_exc())
        raise
