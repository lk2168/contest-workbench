#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动器测试（离线、不开窗口）：端口自动避让 / 日志兜底 / 窗口能力探测。

不开真正的 GUI 窗口 —— 那会干扰用户；窗口能否出现由打包后的实测负责。
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["CONTEST_CONFIG_DIR"] = tempfile.mkdtemp(prefix="cw-launcher-")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from contest_workbench.launcher import find_free_port, has_window_support, log_path, say  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


def main() -> int:
    print("== 启动器 ==")
    p = find_free_port(8765)
    check("find_free_port 返回可绑定的端口", 8765 <= p < 8795, str(p))
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", p))
            bound = True
        except OSError:
            bound = False
    check("返回的端口确实空闲（能绑上）", bound, str(p))

    # 占用一个端口后，应当避开它
    with socket.socket() as blocker:
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", p))
        blocker.listen(1)
        p2 = find_free_port(p)
    check("端口被占用时会自动 +1 避让", p2 != p, f"{p} → {p2}")

    # say() 在"没有控制台"（打包 --windowed）时也必须能落盘日志
    say("启动器测试写入的一行日志")
    lp = log_path()
    check("say() 会写日志文件（打包后无控制台也能排障）",
          lp.exists() and "启动器测试" in lp.read_text(encoding="utf-8"), str(lp))

    check("has_window_support() 返回布尔", isinstance(has_window_support(), bool),
          f"当前 = {has_window_support()}（装了 pywebview 才为 True）")

    print("\n" + "=" * 52)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("全部通过 ✅（离线，不开窗口）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
