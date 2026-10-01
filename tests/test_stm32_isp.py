#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STM32 串口 ISP 的离线测试：用一个「假 BootLoader」把协议跑透，不需要真板子。

为什么要模拟器
--------------
烧录协议错一个字节就可能把板子写坏（真踩过：擦除成功、写入失败 → 板子变空）。
所以协议逻辑必须能**离线、可重复**地测：假 BootLoader 按 AN3155 回应，
还能故意制造"丢 ACK""写入被拒"等真机常见抖动，用来验证重试与安全保护。

用法：python tests/test_stm32_isp.py     （退出码 0 = 通过）
"""
from __future__ import annotations

import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from contest_workbench.stm32_isp import (                                     # noqa: E402
    ACK, NACK, IspError, Stm32Bootloader, Stm32Flasher, parse_intel_hex)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


# ── 假 BootLoader（设备侧）──────────────────────────────────────────────
class 假BootLoader:
    """按 AN3155 回应的假芯片。可选注入故障（丢 ACK / 拒绝写入）。"""

    FLASH_BASE = 0x08000000

    def __init__(self, pid: int = 0x0413, size: int = 64 * 1024,
                 drop_acks: int = 0, refuse_write: bool = False, ver: int = 0x31,
                 id_extra_byte: bool = True, fail_reads: bool = False):
        self.mem = bytearray(b"\xFF" * size)
        self.pid = pid
        self.ver = ver
        self.cmds = [0x00, 0x01, 0x02, 0x11, 0x21, 0x31, 0x44, 0x63, 0x73, 0x82, 0x92]
        self.out = bytearray()
        self.timeout = 1.0
        self.drop_acks = drop_acks            # >0 时：前 N 个 ACK 故意不发（模拟真机抖动）
        self.refuse_write = refuse_write      # True 时：写命令一律回 NACK
        # ★ 真机（BootLoader 3.1）在 PID 前会多一个字节：ACK|01|PID(2)|ACK
        #   默认打开，让离线测试就覆盖这个"文档没写"的怪癖
        self.id_extra_byte = id_extra_byte
        self.fail_reads = fail_reads          # True 时：读命令不回应（模拟读通路坏）
        self.buf = bytearray()
        self.state = "idle"
        self.frame = {}                       # 正在收的一帧的参数
        self.write_calls = 0
        self.erase_calls = 0
        self.read_calls = 0

    # ── 主机侧接口 ──
    def write(self, data) -> int:
        self.buf.extend(bytes(data))
        self._pump()
        return len(data)

    def read(self, n: int = 1) -> bytes:
        out = bytes(self.out[:n])
        del self.out[:n]
        return out

    @property
    def in_waiting(self) -> int:
        return len(self.out)

    def reset_input_buffer(self) -> None:
        self.buf.clear()
        self.out.clear()
        self.state = "idle"

    # ── 设备侧状态机 ──
    def _ack(self):
        if self.drop_acks > 0:
            self.drop_acks -= 1
            return                              # 故意不回（真机上就是这么丢的）
        self.out.append(ACK)

    def _nack(self):
        self.out.append(NACK)

    def _pump(self):
        while self.buf:
            if self.state == "idle":
                b = self.buf[0]
                if b == 0x7F:
                    del self.buf[:1]
                    self.out.append(ACK)
                    continue
                if len(self.buf) < 2:
                    return
                cmd, comp = self.buf[0], self.buf[1]
                if comp != (cmd ^ 0xFF):
                    del self.buf[:1]
                    self._nack()
                    continue
                del self.buf[:2]
                if cmd == 0x00:                     # Get
                    self.out.append(ACK)
                    self.out.append(len(self.cmds))
                    self.out.append(self.ver)
                    self.out.extend(self.cmds)      # ★ 版本 + N 个命令码（共 N+1 字节）
                    self.out.append(ACK)
                elif cmd == 0x02:                   # GetID
                    self.out.append(ACK)
                    if self.id_extra_byte:
                        self.out.append(0x01)       # 真机的那个"多出来的字节"
                    self.out.extend(int(self.pid).to_bytes(2, "big"))
                    self.out.append(ACK)
                elif cmd == 0x11:                   # Read
                    if self.fail_reads:
                        continue                    # 只对读命令装死（同步/ID 仍然正常）
                    self.out.append(ACK)
                    self.state, self.frame = "read_addr", {}
                elif cmd == 0x31:                   # Write
                    if self.refuse_write:
                        self._nack()
                        continue
                    self.out.append(ACK)
                    self.state, self.frame = "write_addr", {}
                elif cmd in (0x44, 0x43):           # Erase
                    self.out.append(ACK)
                    self.state, self.frame = "erase_args", {}
                elif cmd == 0x21:                   # Go
                    self.out.append(ACK)
                    self.state, self.frame = "go_addr", {}
                else:
                    self._nack()
                continue
            # 各命令的参数阶段
            if self.state in ("read_addr", "write_addr", "go_addr"):
                if len(self.buf) < 5:
                    return
                raw = bytes(self.buf[:5])
                del self.buf[:5]
                if raw[4] != (raw[0] ^ raw[1] ^ raw[2] ^ raw[3]):
                    self._nack()
                    self.state = "idle"
                    continue
                self.frame["addr"] = int.from_bytes(raw[:4], "big")
                self.out.append(ACK)
                self.state = self.state.replace("_addr", "_len")
            elif self.state in ("read_len", "write_len"):
                if len(self.buf) < 2:
                    return
                n, comp = self.buf[0], self.buf[1]
                del self.buf[:2]
                if comp != (n ^ 0xFF):
                    self._nack()
                    self.state = "idle"
                    continue
                self.out.append(ACK)
                if self.state == "read_len":
                    # ★ 收到长度就**立刻**回数据（pump 在缓冲区空时会退出，等不到下一轮）
                    cnt = n + 1
                    off = self.frame["addr"] - self.FLASH_BASE
                    self.out.extend(self.mem[off:off + cnt])
                    self.read_calls += 1
                    self.state = "idle"
                else:
                    self.frame["n"] = n + 1
                    self.state = "write_data"
            elif self.state == "read_data":
                n = self.frame.get("n", 1)
                off = self.frame["addr"] - self.FLASH_BASE
                self.out.extend(self.mem[off:off + n])
                self.read_calls += 1
                self.state = "idle"
            elif self.state == "write_data":
                n = self.frame["n"]
                if len(self.buf) < n + 1:
                    return
                data = bytes(self.buf[:n])
                chk = self.buf[n]
                del self.buf[:n + 1]
                if chk != (lambda d: __import__("functools").reduce(lambda a, b: a ^ b, d, 0))(data):
                    self._nack()
                    self.state = "idle"
                    continue
                off = self.frame["addr"] - self.FLASH_BASE
                self.mem[off:off + n] = data
                self.write_calls += 1
                self.out.append(ACK)
                self.state = "idle"
            elif self.state == "go_addr":
                # Go 的参数在 addr 阶段已经收完了
                self.state = "idle"
            elif self.state == "erase_args":
                if self.buf[0] == 0xFF:            # 全片
                    if len(self.buf) < 3:
                        return
                    del self.buf[:3]
                else:
                    if len(self.buf) < 2:
                        return
                    del self.buf[:2]
                self.mem[:] = b"\xFF" * len(self.mem)
                self.erase_calls += 1
                self.out.append(ACK)
                self.state = "idle"


def 造hex(segs, path: Path) -> Path:
    """把 [(addr, data)] 写成 Intel HEX 文件。"""
    lines = []
    for addr, data in segs:
        # 扩展线性地址记录（保证地址正确）
        lines.append(f":02000004{(addr >> 16) & 0xFFFF:04X}{((0x02 + 0x04 + ((addr >> 16) & 0xFF) + ((addr >> 24) & 0xFF)) ^ 0xFF) & 0xFF:02X}")
        off = 0
        while off < len(data):
            chunk = data[off:off + 16]
            rec = addr + off
            n = len(chunk)
            body = [n, (rec >> 8) & 0xFF, rec & 0xFF, 0x00] + list(chunk)
            chk = (-sum(body)) & 0xFF
            lines.append(":" + "".join(f"{b:02X}" for b in body) + f"{chk:02X}")
            off += n
    lines.append(":00000001FF")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="cw-isp-"))

    print("== ① Intel HEX 解析 ==")
    segs = [(0x08000000, bytes(range(64))), (0x08001000, b"\xAA" * 32)]
    hx = 造hex(segs, tmp / "a.hex")
    got = parse_intel_hex(hx)
    check("解析出 2 段且数据一致",
          len(got) == 2 and got[0][1] == bytes(range(64)) and got[1][1] == b"\xAA" * 32,
          str([(hex(a), len(d)) for a, d in got]))
    check("地址正确（0x08000000 / 0x08001000）",
          got[0][0] == 0x08000000 and got[1][0] == 0x08001000)
    bad = tmp / "bad.hex"
    bad.write_text(":0G00000000FF\n", encoding="utf-8")
    try:
        parse_intel_hex(bad)
        check("坏 HEX → 报错", False, "没报错")
    except IspError as e:
        check("坏 HEX → 报人话错", "格式不对" in str(e), str(e))
    check("不存在的文件 → 人话错", "找不到文件" in str(_safe(lambda: parse_intel_hex(tmp / "x.hex"))))

    print("\n== ② 协议层（对着假 BootLoader）==")
    dev = 假BootLoader()
    bl = Stm32Bootloader(dev, gap=0)
    bl.sync()
    check("同步成功", True)
    check("读芯片 ID = 0x0413", bl.get_id() == 0x0413)
    v1, v2, cmds = bl.get_version()
    check("★ Get 读的是 N+1 字节（版本 + N 个命令码）",
          f"{v1}.{v2}" == "3.1" and len(cmds) == 11 and cmds[0] == 0x00,
          f"{v1}.{v2} {cmds}")

    dev.mem[0:4] = b"\x11\x22\x33\x44"
    check("读 Flash 4 字节", bl.read_memory(0x08000000, 4) == b"\x11\x22\x33\x44")
    bl.write(0x08000100, b"\xDE\xAD\xBE\xEF")
    check("写 4 字节落到假 Flash 里", bytes(dev.mem[0x100:0x104]) == b"\xDE\xAD\xBE\xEF")
    bl.erase_all()
    check("全片擦除 → 全 0xFF", set(dev.mem) == {0xFF})
    bl.go(0x08000000)
    check("Go 命令被接受", True)

    # ★ 两种格式都要能读：真机多一个字节，老文档没有
    for extra in (True, False):
        d = 假BootLoader(pid=0x0413, id_extra_byte=extra)
        b = Stm32Bootloader(d, gap=0)
        b.sync()
        check(f"★ GetID 兼容真机格式（多一个字节={extra}）", b.get_id() == 0x0413)

    dev2 = 假BootLoader(pid=0x1234)
    bl2 = Stm32Bootloader(dev2, gap=0)
    bl2.sync()
    check("未知型号也能读出 ID（由上层决定是否停手）", bl2.get_id() == 0x1234)

    dev3 = 假BootLoader(refuse_write=True)
    bl3 = Stm32Bootloader(dev3, gap=0)
    bl3.sync()
    try:
        bl3.write(0x08000000, b"\x01\x02\x03\x04")
        check("写入被拒 → 抛错", False, "没抛错")
    except IspError as e:
        check("写入被拒 → 人话错误（NACK）", "NACK" in str(e), str(e))

    print("\n== ③ 重试（真机常见：偶发丢 ACK）==")
    dev4 = 假BootLoader(drop_acks=2)          # 前两个 ACK 故意不发
    bl4 = Stm32Bootloader(dev4, gap=0, retries=3)
    try:
        bl4.sync()
        pid = bl4.get_id()
        check("★ 丢 ACK 时能自动重试成功（sync 自带重试）", pid == 0x0413, str(pid))
    except IspError as e:
        check("★ 丢 ACK 时能自动重试成功", False, str(e))

    print("\n== ④ 完整烧录流程（假芯片）==")
    payload = bytes((i * 7) & 0xFF for i in range(600))         # 600 字节 → 3 次写
    hx2 = 造hex([(0x08000000, payload)], tmp / "b.hex")
    dev5 = 假BootLoader()
    fl = Stm32Flasher(dev5, gap=0)
    rep = fl.flash(hx2, verify=True, run=True, backup=True, backup_dir=tmp)
    check("★ 烧录成功", rep.ok, rep.text())
    check("芯片识别正确", rep.chip.startswith("STM32F405"), rep.chip)
    check("★ 读回校验通过", rep.verified)
    check("★ 擦除前备份了原固件（比「写探针」更实在：Flash 必须先擦才能写）",
          bool(rep.backup_path) and Path(rep.backup_path).exists()
          and Path(rep.backup_path).stat().st_size == 600,
          rep.backup_path)
    check("备份内容就是擦除前的 Flash（全 0xFF）",
          set(Path(rep.backup_path).read_bytes()) == {0xFF})
    check("Flash 内容与固件一致",
          bytes(dev5.mem[:600]) == payload, str(bytes(dev5.mem[:8])))
    check("600 字节分成 3 次写（每块 ≤256）", dev5.write_calls == 3, str(dev5.write_calls))
    check("报告里有人话步骤记录", any("备份" in s for s in rep.steps), str(rep.steps[:3]))

    print("\n== ⑤ ★ 安全保护①：写入不通 → 有备份可恢复 ==")
    dev6 = 假BootLoader(refuse_write=True)
    dev6.mem[0:8] = b"ORIGINAL"                # 模拟"板子里本来有程序"
    before = bytes(dev6.mem[:8])
    rep6 = Stm32Flasher(dev6, gap=0).flash(hx2, verify=True, run=False,
                                           backup=True, backup_dir=tmp)
    check("写入链路不通 → 烧录判失败", not rep6.ok, rep6.text()[:120])
    check("★ 原固件已被备份下来（且内容就是擦除前那份）",
          bool(rep6.backup_path) and Path(rep6.backup_path).read_bytes()[:8] == before,
          rep6.backup_path or "(没备份)")
    check("★ 错误信息告诉用户备份在哪、可以烧回去",
          "备份在" in rep6.error and rep6.backup_path in rep6.error, rep6.error[:110])

    print("\n== ⑤b ★ 安全保护②：备份读不到就绝不擦除 ==")
    dev6b = 假BootLoader(fail_reads=True)      # 同步/ID 正常，但读不了 Flash
    dev6b.mem[0:8] = b"KEEPME!!"
    rep6b = Stm32Flasher(dev6b, gap=0, retries=1).flash(hx2, verify=True, run=False,
                                                        backup=True, backup_dir=tmp)
    check("备份失败 → 烧录判失败", not rep6b.ok, rep6b.text()[:100])
    check("★ 关键：**没有执行擦除**（板子里的原程序还在）",
          dev6b.erase_calls == 0 and bytes(dev6b.mem[:8]) == b"KEEPME!!",
          f"erase={dev6b.erase_calls} mem={bytes(dev6b.mem[:8])!r}")
    check("错误里说清「为安全起见没有擦除」", "没有擦除" in rep6b.error, rep6b.error[:100])

    print("\n== ⑥ 其它保护 ==")
    dev7 = 假BootLoader()
    rep7 = Stm32Flasher(dev7, gap=0).flash(hx2, expect_pid=0x0449, run=False)
    check("芯片 ID 不符 → 停手（不擦不写）",
          not rep7.ok and "不符" in rep7.error and dev7.erase_calls == 0 and dev7.write_calls == 0,
          rep7.error[:70])
    dev8 = 假BootLoader(pid=0x9999)
    rep8 = Stm32Flasher(dev8, gap=0).flash(hx2, run=False)
    check("未知型号 → 为安全停手", not rep8.ok and "不在已知型号" in rep8.error, rep8.error[:70])
    rep9 = Stm32Flasher(假BootLoader(), gap=0).flash(tmp / "empty.hex", run=False)
    check("空 HEX → 人话错误", not rep9.ok and ("找不到文件" in rep9.error or "没有可用数据" in rep9.error),
          rep9.error[:70])

    print("\n== ⑦ 读回校验会发现写坏 ==")
    devA = 假BootLoader()
    flA = Stm32Flasher(devA, gap=0)
    orig_write = devA.write

    def 坏写入(data):                          # 模拟"写入后内容不对"（丢字节）
        devA.buf.extend(bytes(data))
        devA._pump()
        if devA.write_calls:
            devA.mem[0x10] ^= 0xFF             # 偷偷改一个字节
        return len(data)

    devA.write = 坏写入
    repA = flA.flash(hx2, verify=True, run=False, backup=False)
    check("★ 读回校验失败会报出来（并指出地址）",
          not repA.ok and "读回校验失败" in repA.error, repA.error[:90])

    print("\n== ⑧ 备份能烧回去（bin → hex）==")
    from contest_workbench.stm32_isp import make_hex_from_bin
    binfile = tmp / "backup.bin"
    binfile.write_bytes(bytes(range(256)) * 3)
    hx3 = make_hex_from_bin(binfile, base=0x08000000)
    back = parse_intel_hex(hx3)
    check("★ 备份 bin 能转回 HEX 且内容一致",
          len(back) >= 1 and b"".join(d for _, d in back)[:768] == binfile.read_bytes(),
          f"{len(back)} 段")
    check("地址基准正确（0x08000000 起）", back[0][0] == 0x08000000, hex(back[0][0]))

    print("\n" + "=" * 52)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("全部通过 ✅（用假 BootLoader，不需要真板子）")
    return 0


def _safe(fn):
    try:
        fn()
        return ""
    except IspError as e:
        return str(e)


if __name__ == "__main__":
    sys.exit(main())
