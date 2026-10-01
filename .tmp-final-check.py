# -*- coding: utf-8 -*-
"""复位后验证：听周期性打印 / 发一行看回显。"""
import sys
import time

sys.path.insert(0, r"D:\work\contest-workbench")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from contest_workbench.serial_assistant import SerialAssistant      # noqa: E402

a = SerialAssistant()
print("  " + a.open("COM3", 115200)[:46])
time.sleep(0.5)
a.set_lines(dtr=True, rts=True)
time.sleep(0.3)
a.read_events(999)

print("① 静听 4 秒（程序每约 2 秒打印一次提示）")
t0, text = time.time(), ""
while time.time() - t0 < 4.0:
    time.sleep(0.3)
    evs = a.read_events(99)
    text += "".join(str(e.get("text", "")) for e in evs if e.get("dir") != "tx")
print("   收到：" + (text.replace("\r\n", " ⏎ ").strip()[:120] or "(空)"))

if "请输入" not in text:
    print("② 发一行 ATK 看回显")
    for i in range(2):
        a.send("ATK\r\n")
        time.sleep(1.2)
        evs = a.read_events(99)
        t2 = "".join(str(e.get("text", "")) for e in evs if e.get("dir") != "tx")
        if t2.strip():
            text += t2
            print("   回显：" + t2.replace("\r\n", " ⏎ ").strip()[:120])
            break
        print(f"   第 {i + 1} 次无回显")

print("\n结论：" + ("✅ 程序在跑，板子完全正常" if "请输入" in text or "ATK" in text or "您发送" in text
                  else "⚠️ 仍然没有打印（那就要查 P4 跳线/供电了）"))
a.close()
