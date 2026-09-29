#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端运行时测试：用无头浏览器**真跑一遍首页 JS**，抓"元素不存在让 init 中断"这类 bug。

为什么需要它（真实教训）：
  设置抽屉改版时删掉了 `#modelName` / `#keySource` 两个元素，但 init() 里还在给它们赋值
  → 抛 `Cannot set properties of null` → **整个首屏初始化中断**，统计条与分区卡全都不渲染。
  而当时三套测试全绿（它们只断言**静态 HTML 字符串**，从不执行 JS）。
  这个 bug 是用户打开首页才发现的 —— 那正是这个测试要覆盖的。

用法：python tests/test_frontend_runtime.py       （退出码 0 = 通过 / 跳过）
没有 Edge/Chrome 的环境（如 ubuntu CI）会自动**跳过**，不算失败。
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PASS, FAIL, SKIP = [], [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


def find_browser() -> str | None:
    """找一个能无头渲染 Chromium 的浏览器。"""
    cands = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/microsoft-edge", "/usr/bin/google-chrome", "/usr/bin/chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for c in cands:
        if Path(c).exists():
            return c
    return None


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    browser = find_browser()
    if not browser:
        print("⏭️  跳过：本机没找到 Edge/Chrome（无法无头渲染）")
        print("\n" + "=" * 52)
        print("通过 0 项，失败 0 项，跳过 1 项（无浏览器）")
        return 0
    print(f"使用浏览器：{browser}")

    from contest_workbench.web.app import app
    import uvicorn

    os.environ.setdefault("CONTEST_CONFIG_DIR", tempfile.mkdtemp(prefix="cw-rt-"))
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    url = f"http://127.0.0.1:{port}/"
    for _ in range(60):                     # 等它起来
        if getattr(server, "started", False):
            break
        time.sleep(0.25)
    else:
        print("❌ 本地服务没起来，无法测试")
        return 1

    print(f"渲染 {url} …")
    cache = tempfile.mkdtemp(prefix="cw-browser-")
    try:
        r = subprocess.run(
            [browser, "--headless=new", "--disable-gpu", "--no-sandbox",
             f"--user-data-dir={cache}", "--virtual-time-budget=9000", "--dump-dom", url],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        dom = r.stdout or ""
    except Exception as e:
        print(f"❌ 无头渲染失败：{type(e).__name__}: {e}")
        return 1
    finally:
        server.should_exit = True

    if len(dom) < 2000:
        print(f"❌ 拿到的 DOM 太短（{len(dom)} 字符），渲染可能失败")
        return 1

    # ★ 关键：--dump-dom 会把内联 <script>/<style> 的**源码**也吐出来，
    #   直接在其中搜索会匹配到 JS 模板字符串与注释（假通过/假失败）。
    #   必须先剔掉脚本与样式，只留真正的渲染结果。
    rendered = re.sub(r"<script\b.*?</script>", "", dom, flags=re.S | re.I)
    rendered = re.sub(r"<style\b.*?</style>", "", rendered, flags=re.S | re.I)

    # ★ 核心断言：凡是"JS 中断"都会在渲染结果里留下这条错误提示
    check("首屏没有出现「加载失败」错误条", "加载失败" not in rendered,
          (re.search(r"加载失败[^<]{0,120}", rendered).group(0) if "加载失败" in rendered else ""))
    check("统计条已由 JS 填充（题库真题）", "题库真题" in rendered)
    check("分区焦点卡已渲染（含「进入分析」）",
          "进入分析" in rendered or "现在可用" in rendered)
    check("规划中分区也在列表里（数学建模）", "数学建模" in rendered)
    check("设置抽屉内容存在（运行自检按钮）", "运行自检" in rendered)
    check("没有 JS 报错字样", "Cannot set properties" not in rendered
          and "undefined is not" not in rendered and "null (setting" not in rendered)

    print("\n" + "=" * 52)
    tail = f"，跳过 {len(SKIP)} 项" if SKIP else ""
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项{tail}")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("全部通过 ✅（前端真实运行时检查）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
