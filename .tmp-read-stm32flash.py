# -*- coding: utf-8 -*-
"""读 stm32flash 的源码，找 Write Memory 的真正帧格式。"""
import base64
import json
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def fetch(path):
    r = subprocess.run(["gh", "api", f"repos/ARMinARM/stm32flash/contents/{path}", "-q", ".content"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return base64.b64decode(r.stdout.strip().replace("\n", "")).decode("utf-8", "replace")
    except Exception as e:
        return f"(解码失败 {e})"


# 先看仓库里有哪些文件
r = subprocess.run(["gh", "api", "repos/ARMinARM/stm32flash/contents",
                    "-q", ".[].name"], capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=90)
print("== 仓库文件 ==")
print("  " + (r.stdout or "").strip().replace("\n", "  "))

for name in ("stm32.c", "stm32.h"):
    src = fetch(name)
    if not src:
        print(f"\n  {name} 取不到")
        continue
    print(f"\n== {name} 里与「写内存」相关的片段（{len(src)} 字符）==")
    lines = src.splitlines()
    hits = [i for i, l in enumerate(lines)
            if re.search(r"write_memory|WRITE_MEMORY|0x31|Get|length|checksum", l, re.I)]
    shown = set()
    for i in hits:
        a, b = max(0, i - 2), min(len(lines), i + 6)
        key = (a, b)
        if key in shown or any(x in shown for x in [(a - 1, b), (a + 1, b)]):
            continue
        shown.add(key)
        for k in range(a, b):
            print(f"  {k + 1:5d}|{lines[k][:110]}")
        print("  ----")
        if len(shown) > 8:
            break
