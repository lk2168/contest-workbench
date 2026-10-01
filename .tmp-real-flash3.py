# -*- coding: utf-8 -*-
"""真机烧录（写帧已按 stm32flash 修正）：备份 → 擦除 → 写入 → 读回校验。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\work\contest-workbench")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from contest_workbench.serial_assistant import SerialAssistant          # noqa: E402
from contest_workbench.stm32_isp import flash_via_assistant             # noqa: E402

HEX = Path(r"D:\work\stm32-serial-test\Output\atk_f407.hex")
RUN = "rts_high_dtr_high_boot"
BOOT = "dtr_low_rts_high_boot"

a = SerialAssistant()
print(a.open("COM3", 115200)[:52])
time.sleep(0.3)
print("① 暂停读取（独占串口）:", a.pause_reader())

print("② 烧录（自动进 BootLoader；握手失败自动重进）")
t0 = time.time()
rep = flash_via_assistant(a, HEX, verify=True, run=False, boot_preset=BOOT,
                          backup_dir=Path(r"D:\work\contest-workbench\out"))
print(f"   耗时 {time.time() - t0:.1f} 秒\n")
print(rep.text())

print("\n③ 复位回运行")
a.pulse_reset(RUN)
time.sleep(0.3)
a.resume_reader()
time.sleep(3.0)
evs = a.read_events(200)
text = "".join(str(e.get("text", "")) for e in evs if e.get("dir") != "tx")
print(f"   收到 {len(text)} 字符：" + (text.replace("\r\n", " ⏎ ").strip()[:90] or "(空)"))
ok_run = "请输入" in text
print("\n结论：" + ("✅✅ **一键烧录成功，且板子在跑新程序**" if (rep.ok and ok_run)
                  else ("⚠️ 烧录 ok=" + str(rep.ok) + "，但没看到打印" if rep.ok else "❌ 烧录仍失败")))
a.close()
