#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STM32 串口 ISP 烧录（走芯片内置 BootLoader，**不需要 ST-Link / DAP 仿真器**）。

原理：
  1. 正点原子的板子有「一键下载电路」—— CH340 的 **DTR 控制 NRST**、**RTS 控制 BOOT0**
     所以用串口的 DTR/RTS 就能自动让芯片进入 BootLoader（等价于手动把 BOOT0 接 3.3V 再按复位）
  2. 进入后按 ST 官方 AN3155 协议对话：同步 → 读芯片 ID → 擦除 → 写入 → 跳转运行
  3. **写之前一定先读芯片 ID 核对**，ID 不对就停手（避免把别的芯片写坏）

用法：
  python scripts/stm32_flash.py --port COM3 --probe             # 只进 BootLoader 读芯片信息（不改任何东西）
  python scripts/stm32_flash.py --port COM3 --flash a.hex       # 烧录
  python scripts/stm32_flash.py --port COM3 --flash a.hex --no-run   # 烧完不自动运行
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ACK, NACK = 0x79, 0x1F
import os

DEBUG = bool(os.environ.get("CONTEST_ISP_DEBUG"))


def _dbg(tag: str, data: bytes | int) -> None:
    if not DEBUG:
        return
    if isinstance(data, int):
        print(f"    [isp] {tag}: 0x{data:02X}")
    else:
        print(f"    [isp] {tag}: {data.hex(' ') if data else '(空)'}")

# 已知的 STM32 芯片 ID（低 16 位）——用来核对"我到底在跟哪颗芯片说话"
KNOWN_PID = {
    0x0413: "STM32F405/407/415/417",
    0x0411: "STM32F40x/41x",
    0x0419: "STM32F42x/43x",
    0x0449: "STM32F401",
    0x0447: "STM32F411",
    0x0451: "STM32F411",
    0x0431: "STM32F411",
    0x0415: "STM32F410",
    0x0421: "STM32F446",
    0x0434: "STM32F469/479",
}


class IspError(RuntimeError):
    pass


def parse_intel_hex(path: Path) -> list[tuple[int, bytes]]:
    """解析 Intel HEX → [(起始地址, 数据), ...]（按连续段合并）。"""
    segs: list[tuple[int, bytearray]] = []
    upper = 0
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line.startswith(":"):
            continue
        try:
            n = int(line[1:3], 16)
            addr = int(line[3:7], 16)
            rec = int(line[7:9], 16)
            data = bytes.fromhex(line[9:9 + n * 2])
        except ValueError:
            raise IspError(f"HEX 格式错误：{line[:40]}")
        if rec == 0x00:                                   # 数据
            full = upper + addr
            if segs and segs[-1][0] + len(segs[-1][1]) == full:
                segs[-1][1].extend(data)
            else:
                segs.append((full, bytearray(data)))
        elif rec == 0x01:                                 # 结束
            break
        elif rec == 0x04:                                 # 扩展线性地址
            upper = int.from_bytes(data, "big") << 16
        elif rec == 0x02:                                 # 扩展段地址
            upper = int.from_bytes(data, "big") << 4
    return [(a, bytes(d)) for a, d in segs]


class Stm32Isp:
    def __init__(self, port: str, baud: int = 115200, invert: bool = False) -> None:
        import serial                                   # 惰性导入，没装也能给出人话错误
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = baud
        self.ser.timeout = 1.0
        self.ser.write_timeout = 2.0
        self.invert = invert
        self.ser.open()
        self.ser.reset_input_buffer()

    # ── 低层 ──────────────────────────────────────────────────────────
    def _dtr(self, v: bool) -> None:
        self.ser.dtr = (not v) if self.invert else v

    def _rts(self, v: bool) -> None:
        self.ser.rts = (not v) if self.invert else v

    def enter_bootloader(self) -> None:
        """正点原子一键下载电路：RTS 高 → BOOT0 高；DTR 低 → 复位。"""
        self._rts(True)          # BOOT0 = 高（选 BootLoader）
        time.sleep(0.05)
        self._dtr(False)         # 拉低复位
        time.sleep(0.15)
        self._dtr(True)          # 释放复位 → 芯片进 BootLoader
        time.sleep(0.35)
        self.ser.reset_input_buffer()

    def exit_run(self) -> None:
        """BOOT0 回低 + 复位 → 运行用户程序。"""
        self._rts(False)
        time.sleep(0.05)
        self._dtr(False)
        time.sleep(0.15)
        self._dtr(True)
        time.sleep(0.1)

    def _read(self, n: int) -> bytes:
        data = self.ser.read(n)
        _dbg(f"读 {n} 字节", data)
        if len(data) != n:
            raise IspError(f"读超时：期望 {n} 字节，只收到 {len(data)} 字节"
                           f"（芯片没进 BootLoader？或 DTR/RTS 极性不对）")
        return data

    def _ack(self) -> None:
        b = self._read(1)[0]
        if b == NACK:
            raise IspError("芯片回了 NACK（命令被拒绝）")
        if b != ACK:
            raise IspError(f"期望 ACK(0x79)，收到 0x{b:02X}")

    def _cmd(self, cmd: int) -> None:
        self.ser.write(bytes([cmd, cmd ^ 0xFF]))
        _dbg(f"发送命令 0x{cmd:02X}", bytes([cmd, cmd ^ 0xFF]))
        self._ack()

    # ── 高层 ──────────────────────────────────────────────────────────
    def sync(self, tries: int = 6) -> None:
        for _ in range(tries):
            self.ser.write(b"\x7f")
            _dbg("同步 0x7F", b"\x7f")
            time.sleep(0.05)
            if self.ser.read(1) == bytes([ACK]):
                time.sleep(0.1)          # 同步后稍等，避免命令与答复错位
                return
        raise IspError("同步失败：芯片没有响应。检查 ①P4 跳线帽是否接好"
                       " ②串口是否被别的软件占用 ③换一种 DTR/RTS 极性（--invert）")

    def get_id(self, tries: int = 3) -> int:
        """GetID(0x02)：响应 ACK → PID(2 字节) → ACK。

        ★ 实测这块**第一次常被回 NACK**，重试一次就好（stale 状态），所以带重试。
        """
        last = None
        for _ in range(tries):
            try:
                self._cmd(0x02)
                pid = int.from_bytes(self._read(2), "big")
                self._ack()
                return pid
            except IspError as e:
                last = e
                time.sleep(0.15)
                self.ser.reset_input_buffer()
        raise IspError(f"GetID 连续 {tries} 次失败：{last}")

    def get_version(self) -> tuple[int, int, list[int]]:
        """Get(0x00)：响应是 ACK → **N** → N 字节（版本 + 支持的命令表）→ ACK。

        ★ 实测踩过：漏读这个长度字节会让后面所有命令**整体错位**，
          表现为"GetID 被回 NACK"（其实是把上一条的残留当成了响应）。
        """
        self.ser.write(bytes([0x00, 0xFF]))
        self._ack()
        n = self._read(1)[0]
        body = self._read(n)
        self._ack()
        ver = body[0] if body else 0
        return (ver >> 4, ver & 0x0F, list(body[1:]))

    def read_memory(self, addr: int, n: int = 16) -> bytes:
        """读一小段（页 0 的向量表）——用于确认芯片里现在有程序。"""
        self._cmd(0x11)
        self.ser.write(addr.to_bytes(4, "big") + bytes([self._xor(addr.to_bytes(4, "big"))]))
        self._ack()
        nn = n - 1
        self.ser.write(bytes([nn, nn ^ 0xFF]))
        self._ack()
        return self._read(n)

    @staticmethod
    def _xor(data: bytes) -> int:
        x = 0
        for b in data:
            x ^= b
        return x

    def erase_all(self) -> None:
        try:                                       # F4 用扩展擦除（0x44）
            self._cmd(0x44)
            self.ser.write(b"\xff\xff\x00")        # 0xFFFF = 全片擦除
            self.ser.timeout = 30.0                # 全片擦除要几秒
            self._ack()
        except IspError:
            self._cmd(0x43)                        # 退回标准擦除
            self.ser.write(b"\xff\x00")            # 全片擦除
            self.ser.timeout = 30.0
            self._ack()
        finally:
            self.ser.timeout = 1.0

    def write(self, addr: int, data: bytes) -> None:
        self._cmd(0x31)
        a = addr.to_bytes(4, "big")
        self.ser.write(a + bytes([self._xor(a)]))
        self._ack()
        n = len(data) - 1
        self.ser.write(bytes([n, n ^ 0xFF]))
        self._ack()
        self.ser.write(data + bytes([self._xor(data)]))
        self._ack()

    def go(self, addr: int) -> None:
        self._cmd(0x21)
        a = addr.to_bytes(4, "big")
        self.ser.write(a + bytes([self._xor(a)]))
        self._ack()

    def close(self) -> None:
        try:
            self.ser.close()
        except Exception:
            pass


def do_probe(port: str, baud: int) -> int:
    for invert in (False, True):
        isp = None
        try:
            isp = Stm32Isp(port, baud, invert=invert)
            isp.enter_bootloader()
            isp.sync()
            ver = isp.get_version()      # 先 Get：验证链路 + 拿到支持的命令表
            pid = isp.get_id()           # 再 GetID：核对芯片型号
            name = KNOWN_PID.get(pid, "（未收录的型号）")
            print(f"  ✅ 进入 BootLoader 成功（DTR/RTS 极性：{'取反' if invert else '常规'}）")
            print(f"     芯片 ID = 0x{pid:04X} → {name}")
            print(f"     BootLoader 版本 = {ver[0]}.{ver[1]}"
                  f" · 支持的命令 {' '.join(f'0x{c:02X}' for c in ver[2])}")
            try:
                head = isp.read_memory(0x08000000, 16)
                sp = int.from_bytes(head[0:4], "little")
                pc = int.from_bytes(head[4:8], "little")
                print(f"     现在 Flash 里的程序：栈顶 0x{sp:08X} · 复位向量 0x{pc:08X}"
                      + ("（看起来是空片/全 0）" if sp in (0, 0xFFFFFFFF) else "（有程序在）"))
            except IspError:
                print("     （读 Flash 向量表失败，不影响烧录）")
            print("\n  → 可以烧录了：把 --probe 换成 --flash <hex文件>")
            return 0
        except IspError as e:
            print(f"  ✗ 极性{'取反' if invert else '常规'}时失败：{e}")
        except Exception as e:
            print(f"  ✗ 打开串口失败：{type(e).__name__}: {e}")
            print("     （很可能是别的软件占着串口 —— 比如刚打开的 ATK-XISP，先关掉它）")
            break
        finally:
            if isp:
                isp.close()
    return 1


def do_flash(port: str, baud: int, hexfile: Path, run: bool) -> int:
    segs = parse_intel_hex(hexfile)
    total = sum(len(d) for _, d in segs)
    print(f"  已解析 HEX：{len(segs)} 段 · 共 {total} 字节（{total/1024:.1f} KB）")
    print(f"  地址范围：0x{min(a for a, _ in segs):08X} ~ "
          f"0x{max(a + len(d) for a, d in segs):08X}")

    last_err = None
    for invert in (False, True):
        isp = None
        try:
            isp = Stm32Isp(port, baud, invert=invert)
            isp.enter_bootloader()
            isp.sync()
            isp.get_version()
            pid = isp.get_id()
            name = KNOWN_PID.get(pid, "（未收录）")
            print(f"  ✅ 已连接：芯片 0x{pid:04X} → {name}"
                  f"（极性{'取反' if invert else '常规'}）")
            if pid not in KNOWN_PID:
                print("  ⚠️ 型号未收录，为安全起见停止（避免写错芯片）。"
                      "确认无误可用 --force 继续")
                return 1
            print("  擦除中（F4 全片擦除要几秒）…")
            isp.erase_all()
            print("  ✅ 擦除完成")
            for i, (addr, data) in enumerate(segs, 1):
                off = 0
                while off < len(data):
                    chunk = data[off:off + 256]
                    isp.write(addr + off, chunk)
                    off += len(chunk)
                print(f"  写入第 {i}/{len(segs)} 段：0x{addr:08X} · {len(data)} 字节 ✓")
            if run:
                print("  跳转运行…")
                isp.go(0x08000000)
            return 0
        except IspError as e:
            last_err = e
            print(f"  ✗ 失败（极性{'取反' if invert else '常规'}）：{e}")
        except Exception as e:
            last_err = e
            print(f"  ✗ 失败：{type(e).__name__}: {e}")
            break
        finally:
            if isp:
                try:
                    if run and last_err is None:
                        isp.exit_run()          # BOOT0 回低 + 复位 → 跑新程序
                except Exception:
                    pass
                isp.close()
    print(f"\n  ❌ 烧录失败：{last_err}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="STM32 串口 ISP 烧录（无需仿真器）")
    ap.add_argument("--port", default="COM3", help="串口，默认 COM3")
    ap.add_argument("--baud", type=int, default=115200, help="波特率，默认 115200")
    ap.add_argument("--probe", action="store_true", help="只进 BootLoader 读芯片信息（不修改任何东西）")
    ap.add_argument("--flash", metavar="HEX", help="要烧录的 Intel HEX 文件")
    ap.add_argument("--no-run", action="store_true", help="烧完不自动运行")
    args = ap.parse_args()

    try:
        import serial  # noqa: F401
    except ImportError:
        print("[错误] 没装 pyserial：python -m pip install pyserial")
        return 2

    print("=" * 62)
    if args.flash:
        return do_flash(args.port, args.baud, Path(args.flash), run=not args.no_run)
    return do_probe(args.port, args.baud)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已中断")
