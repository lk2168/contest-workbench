#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开发用：重启「源码版」网页端，并确认在跑的确实是源码（不是打包的 exe）。

为什么需要它（今天踩了三次的坑）：
  - 打包后的 exe 会**占着 8765 端口**；此时再起源码服务会静默失败（没绑上端口），
    浏览器访问到的其实是**旧 exe**里的静态文件 → 改了前端却"看不到变化"，
    还会以为是自己改错了地方。
  - 只按进程名（`cli.py web`）杀不干净 —— exe 进程名是 `contest-workbench`。

这个脚本按**端口**精确杀，再起源码服务，最后用 `/api/health` 的 `frozen` 字段自证身份。

用法：
    python scripts/dev_server.py                # 默认 8765
    python scripts/dev_server.py --port 8790
    python scripts/dev_server.py --keep         # 不清库（默认也不清）
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def kill_port(port: int) -> list:
    """杀掉占用该端口的进程（源码服务与打包 exe 都覆盖）。返回被杀掉的 PID 列表。"""
    ps = (
        f"$pids = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty OwningProcess -Unique; "
        "foreach ($p in $pids) { "
        "  $pr = Get-Process -Id $p -ErrorAction SilentlyContinue; "
        "  Write-Output \"$p $($pr.ProcessName)\"; "
        "  Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-c", ps], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    killed = [line.strip() for line in (r.stdout or "").splitlines() if line.strip()]
    if killed:
        time.sleep(3)
    return killed


def main() -> int:
    ap = argparse.ArgumentParser(description="重启源码版网页端并自证身份")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--wait", type=float, default=12.0, help="最多等多少秒")
    args = ap.parse_args()

    import requests

    print(f"① 清理端口 {args.port} 上的旧服务（含打包 exe）…")
    for line in kill_port(args.port):
        print(f"   杀掉 {line}")
    if not kill_port(args.port):
        print("   端口是空的")

    print("② 启动源码服务…")
    subprocess.Popen([sys.executable, "cli.py", "web", "--port", str(args.port)], cwd=ROOT,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    print("③ 等它就绪并确认身份…")
    t0 = time.time()
    while time.time() - t0 < args.wait:
        try:
            h = requests.get(f"http://127.0.0.1:{args.port}/api/health", timeout=3).json()
            frozen = h.get("frozen")
            url = f"http://127.0.0.1:{args.port}"
            if frozen:
                print(f"   ❌ 在跑的是**打包 exe**（frozen=True）—— 改了前端看不到变化！")
                print(f"      先关掉那个窗口，或再执行一次本脚本")
                return 1
            print(f"   ✅ 源码服务已就绪：{url}（frozen=False）")
            print(f"      • 电赛 → 串口采数：{url}/#diansai:serial")
            print(f"      • 改静态文件后刷新页面即可（Ctrl+F5）")
            return 0
        except Exception:
            time.sleep(0.8)
    print("   ❌ 等超时了，服务没起来（看看控制台报错？）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
