# -*- coding: utf-8 -*-
"""按 stm32flash 的权威实现修 Write 帧：长度+数据+校验一次性发出，长度按 4 字节对齐。"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"D:\work\contest-workbench")


def patch(rel, pairs, label):
    p = ROOT / rel
    t = p.read_text(encoding="utf-8")
    for a, b in pairs:
        assert a in t, f"{label}: 锚点没找到 -> {a[:70]!r}"
        t = t.replace(a, b, 1)
    p.write_text(t, encoding="utf-8")
    print(f"  [ok] {label}")


# ① 模块：写帧按 stm32flash 的写法
patch("contest_workbench/stm32_isp.py", [(
    '''    def _write_once(self, addr: int, data: bytes) -> None:
        self._cmd(0x31, "写命令")
        a = int(addr).to_bytes(4, "big")
        self.io.write(a + bytes([_xor(a)]))
        time.sleep(self.gap)
        self._ack("写地址")
        n = len(data) - 1
        self.io.write(bytes([n, n ^ 0xFF]))
        time.sleep(self.gap)
        self._ack("写长度")
        self.io.write(bytes(data) + bytes([_xor(data)]))
        time.sleep(self.gap)
        self._ack("写数据")''',
    '''    def _write_once(self, addr: int, data: bytes) -> None:
        """写一段（≤256 字节，地址按 4 字节对齐）。

        ★★ 帧格式以 stm32flash（ARMinARM/stm32flash，GPL-2.0）的实现为准：
        它的 `stm32_write_memory()` 是
        ```c
        aligned_len = (len + 3) & ~3;              /* 长度按 4 字节对齐 */
        buf[0] = aligned_len - 1;
        数据 + 不足补 0xFF;
        buf[aligned_len + 1] = 校验;
        port->write(port, buf, aligned_len + 2);   /* 长度+数据+校验 **一次性发出去** */
        stm32_get_ack_timeout(...);                /* 整帧发完才等 **一个** ACK */
        ```
        ★ 我原来把「长度」和「数据」分两次发、中间还等 ACK —— 设备根本不回中间 ACK，
        它在等数据，于是表现成「长度后沉默」，最后状态错位收 NACK。真机卡了很久就是这个。
        """
        self._cmd(0x31, "写命令")
        a = int(addr).to_bytes(4, "big")
        self.io.write(a + bytes([_xor(a)]))
        self._ack("写地址")

        n = len(data)
        aligned = (n + 3) & ~3                      # ★ 4 字节对齐
        frame = bytearray([aligned - 1])            # 长度字段
        frame.extend(data)
        frame.extend(b"\\xFF" * (aligned - n))       # ★ 不足的补 0xFF
        cs = 0
        for b in frame:                             # 校验 = 长度+数据+填充 的异或
            cs ^= b
        frame.append(cs)
        old = getattr(self.io, "timeout", None)
        try:
            if old is not None:
                self.io.timeout = max(2.0, old)     # 写 Flash 要有耐心
            self.io.write(bytes(frame))             # ★ 一次性发出整帧
            self._ack("写数据")
        finally:
            if old is not None:
                self.io.timeout = old''')], "写帧改成一次性发出")

# ② 假芯片也按真协议改：收到 (长度+数据+校验) 整帧才回一个 ACK
patch("tests/test_stm32_isp.py", [(
    '''                self.out.append(ACK)
                if self.state == "read_len":
                    # ★ 收到长度就**立刻**回数据（pump 在缓冲区空时会退出，等不到下一轮）
                    cnt = n + 1
                    off = self.frame["addr"] - self.FLASH_BASE
                    self.out.extend(self.mem[off:off + cnt])
                    self.read_calls += 1
                    self.state = "idle"
                else:
                    self.frame["n"] = n + 1
                    self.state = "write_data"''',
    '''                self.out.append(ACK)
                if self.state == "read_len":
                    # ★ 收到长度就**立刻**回数据（pump 在缓冲区空时会退出，等不到下一轮）
                    cnt = n + 1
                    off = self.frame["addr"] - self.FLASH_BASE
                    self.out.extend(self.mem[off:off + cnt])
                    self.read_calls += 1
                    self.state = "idle"
                else:
                    # ★ 真协议：长度和数据、校验是**一整帧**发过来的，收到整帧才回一个 ACK
                    self.frame["n"] = n + 1
                    self.state = "write_data"''')], "假芯片注释")

# ③ 假芯片的 write_len 阶段：改成"等整帧"（长度 + 数据 + 1 字节校验）
patch("tests/test_stm32_isp.py", [(
    '''            elif self.state in ("read_len", "write_len"):
                if len(self.buf) < 2:
                    return
                n, comp = self.buf[0], self.buf[1]
                del self.buf[:2]
                if comp != (n ^ 0xFF):
                    self._nack()
                    self.state = "idle"
                    continue
                self.out.append(ACK)''',
    '''            elif self.state in ("read_len", "write_len"):
                if self.state == "write_len":
                    # ★ 整帧 = 1 字节长度 + N 字节数据 + 1 字节校验（数据可能含 0xFF 填充）
                    if not self.buf:
                        return
                    cnt = self.buf[0] + 1
                    if len(self.buf) < 1 + cnt + 1:
                        return
                    body = bytes(self.buf[:1 + cnt])
                    chk = self.buf[1 + cnt]
                    del self.buf[:1 + cnt + 1]
                    cs = 0
                    for b in body:
                        cs ^= b
                    if chk != cs:
                        self._nack()
                        self.state = "idle"
                        continue
                    off = self.frame["addr"] - self.FLASH_BASE
                    self.mem[off:off + cnt] = body[1:]
                    self.write_calls += 1
                    self.out.append(ACK)
                    self.state = "idle"
                    continue
                if len(self.buf) < 2:
                    return
                n, comp = self.buf[0], self.buf[1]
                del self.buf[:2]
                if comp != (n ^ 0xFF):
                    self._nack()
                    self.state = "idle"
                    continue
                self.out.append(ACK)''')], "假芯片等整帧")

# ④ 删掉假的 write_data 分支（已经不需要）
patch("tests/test_stm32_isp.py", [(
    '''            elif self.state == "write_data":
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
''', '')], "删掉旧 write_data 分支")
