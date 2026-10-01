# -*- coding: utf-8 -*-
"""读 stm32flash 的 protocol.txt 与 stm32_write_memory 实现，确认 F4 的写法。"""
import base64
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
    except Exception:
        return None


txt = fetch("protocol.txt")
if txt:
    print("== protocol.txt 里与写内存有关的段落 ==")
    lines = txt.splitlines()
    for i, l in enumerate(lines):
        if re.search(r"write memory|0x31|0x32|no.?stretch|N-1|number of bytes", l, re.I):
            for k in range(max(0, i - 3), min(len(lines), i + 8)):
                print(f"  {k + 1:4d}|{lines[k][:110]}")
            print("  ----")
            break

src = fetch("stm32.c")
if src:
    lines = src.splitlines()
    # 找 stm32_write_memory 函数体
    start = next((i for i, l in enumerate(lines) if "stm32_write_memory" in l and "(" in l), None)
    if start is not None:
        print(f"\n== stm32.c: stm32_write_memory（从第 {start + 1} 行）==")
        depth, started = 0, False
        for k in range(start, min(len(lines), start + 200)):
            line = lines[k]
            if "{" in line:
                depth += line.count("{")
                started = True
            if started:
                print(f"  {k + 1:5d}|{line[:110]}")
            if "}" in line:
                depth -= line.count("}")
                if started and depth <= 0:
                    break

# 再找"发命令"的底层函数，看它怎么组帧
for fname in ("stm32_send_command", "stm32_send_wm", "stm32_write_memory"):
    idx = next((i for i, l in enumerate(lines) if fname in l and "(" in l and ";" not in l), None)
    if idx is not None:
        print(f"\n（{fname} 在第 {idx + 1} 行）")
