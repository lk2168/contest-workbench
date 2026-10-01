#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""串口探针：列端口 → 打开 → 听几秒 → 报告收到了什么（也可发一行指令）。

用途：
  1. **环境体检**：板子插上后先跑它，能立刻区分"没插好 / 驱动没装 / 端口被占用 / 数据正常"
  2. 调参前的快速确认：不打开网页端就能看数据到底长什么样

用法（在项目根目录）：
  python scripts/serial_probe.py                    # 只列端口
  python scripts/serial_probe.py COM5              # 打开 COM5，听 5 秒
  python scripts/serial_probe.py COM5 --listen 10  # 听 10 秒
  python scripts/serial_probe.py COM5 --send "START\\n"   # 打开后先发一行再听
  python scripts/serial_probe.py COM5 --baud 9600  # 指定波特率（默认 115200）
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from contest_workbench.serial_assistant import (SerialAssistant, list_ports,     # noqa: E402
                                               serial_available)


def _count(port: str, baud: int, seconds: float) -> tuple:
    """在某个波特率下听几秒，返回 (行数, 字节数, 前几行原文)。"""
    a = SerialAssistant()
    msg = a.open(port, baudrate=baud)
    if msg.startswith("[错误]"):
        return -1, 0, [msg]
    t0 = time.time()
    lines, sample = 0, []
    while time.time() - t0 < seconds:
        for ev in a.read_events(500):
            if ev.get("kind") == "error":
                continue
            lines += 1
            if len(sample) < 3:
                sample.append(str(ev.get("text", ""))[:80])
        time.sleep(0.05)
    snap = a.snapshot()
    a.close()
    return lines, snap.get("buffer_bytes", 0) or 0, sample


def scan_baud(port: str, seconds: float = 2.0) -> int:
    """逐个波特率试一遍 —— 不知道固件用多少波特率时最省事。

    ★ 注意：请**边扫边按板子的复位键**（很多固件只在上电/复位时打印一次）。
    """
    common = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
    print(f"扫描 {port} 的常见波特率（每个听 {seconds:g} 秒）")
    print("★ 请在这期间反复按板子的复位键 —— 只打印一次的固件必须靠复位才能看到\n")
    hits = []
    for b in common:
        lines, nbytes, sample = _count(port, b, seconds)
        if lines < 0:
            print(f"  {b:>7} : {sample[0][:60]}")
            continue
        mark = "✅ 有数据" if lines else "─"
        print(f"  {b:>7} : {lines:>4} 行 {mark}")
        for s in sample:
            print(f"            {s}")
        if lines:
            hits.append(b)
    print("\n" + "=" * 60)
    if hits:
        print(f"有效波特率：{', '.join(str(h) for h in hits)}")
        print(f"下一步：python scripts/serial_probe.py {port} --baud {hits[0]} --listen 8")
    else:
        print("所有常见波特率都没收到数据。可能原因：")
        print("  1) 固件根本不打印（换个已知会打印的例程，如正点原子的串口实验）")
        print("  2) 板子没在运行 / 没上电（看板子上的电源灯）")
        print("  3) 串口线接的是板子的另一个口（USB_232 与 ST-Link 口是两条独立通道）")
        print("  4) 波特率非标（如 1000000）；可以在代码里确认后 --baud 指定")
        print("  5) 板子的 TX/RX 与 CH340 的接线问题（自制板/杜邦线时常见）")
    return 0 if hits else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="串口探针（列端口 / 听数据）")
    ap.add_argument("port", nargs="?", help="端口名，如 COM5；不填则只列端口")
    ap.add_argument("--baud", type=int, default=115200, help="波特率，默认 115200")
    ap.add_argument("--listen", type=float, default=5.0, help="听多少秒，默认 5")
    ap.add_argument("--send", default="", help="打开后先发送的内容（如 \"START\\n\"）")
    ap.add_argument("--hex", action="store_true", help="发送内容按 HEX 解释")
    ap.add_argument("--scan", action="store_true",
                    help="扫描常见波特率（不知道固件波特率时用它，扫描时请按复位键）")
    args = ap.parse_args()

    print("=" * 60)
    print(f"pyserial 可用：{serial_available()}")
    ports = list_ports()
    print(f"发现 {len(ports)} 个串口：")
    for p in ports:
        print(f"  {p.get('device')}  |  {p.get('description')}  |  {p.get('hwid')}")
    if not ports:
        print("\n⚠️ 一个串口都没有。按这个顺序排查：")
        print("   1) 板子 USB 线是否插好（换一个 USB 口；有的线只能充电不能传数据）")
        print("   2) 板子是否上电、电源开关是否打开")
        print("   3) **USB 转串口驱动装了没**（CH340 / CP210x / FTDI / ST-Link）")
        print("      —— 本机侦察显示：目前没装任何串口驱动，这是最常见的原因")
        print("   4) 设备管理器里有没有带黄色感叹号的设备")
        print("   装完驱动后重新插拔一次板子，再跑本脚本")
        return 1

    if not args.port:
        print("\n（只列端口。要听数据请带上端口名，例如：python scripts/serial_probe.py COM5）")
        print("    不知道波特率就加 --scan：python scripts/serial_probe.py COM5 --scan")
        return 0

    dev = args.port
    if not any(p.get("device") == dev for p in ports):
        print(f"\n⚠️ 你给的 {dev} 不在上面的列表里 —— 端口名可能写错了")
        return 1

    if args.scan:
        return scan_baud(dev)

    a = SerialAssistant()
    print(f"\n打开 {dev} @ {args.baud} …")
    msg = a.open(dev, baudrate=args.baud)
    print("  " + msg)
    if msg.startswith("[错误]"):
        return 1

    if args.send:
        sent = a.send(args.send, hex_mode=args.hex)
        print("  发送：" + sent)

    print(f"听 {args.listen:g} 秒（Ctrl+C 可提前结束）…\n")
    t0 = time.time()
    lines = 0
    while time.time() - t0 < args.listen:
        evs = a.read_events(200)
        for ev in evs:
            if ev.get("kind") == "error":
                print(f"  [设备错误] {ev.get('text')}")
                continue
            lines += 1
            mark = "→" if ev.get("dir") == "tx" else "←"
            vals = ev.get("values")
            extra = f"   （解析为 t={vals[0]}, v={vals[1]}）" if vals else ""
            print(f"  {mark} {ev.get('time','')[-15:]}  {ev.get('text','')[:90]}{extra}")
        time.sleep(0.1)

    snap = a.snapshot()
    print("\n" + "=" * 60)
    print(f"共收到 {lines} 行 · 解析出 {snap['total']} 个数据点 · 坏行 {snap['bad_lines']} "
          f"· 丢弃 {snap['dropped']}")
    if snap["points"]:
        print(f"前 3 个点：{snap['points'][:3]}")
        print(f"后 3 个点：{snap['points'][-3:]}")
        print("\n★ 数据能被解析 → 直接可以送进调参助手：")
        print("   python -c \"import sys;sys.path.insert(0,'.');"
              "from contest_workbench.serial_assistant import SerialAssistant;"
              "print(SerialAssistant().analyze())\"")
    else:
        print("没有解析出数据点。若你的固件输出的是别的格式，把上面收到的原始行发我，我来适配。")
    a.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已中断")
