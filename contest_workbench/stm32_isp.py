#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STM32 串口 ISP 烧录：不用仿真器，走芯片内置 BootLoader（ST AN3155 协议）。

为什么自己做
------------
正点原子这类板子没有板载仿真器，官方给的是 **ATK-XISP**（闭源 GUI）。
我们有串口助手、有 DTR/RTS 一键复位 —— 把烧录也做进来，"改代码 → 编译 → 烧录 → 采数据 → 调参"
就在一个软件里闭环。

★ 设计上最要紧的一条：**先证明能写，再擦除**
--------------------------------------------
2026-10-02 真机踩过的坑：自研脚本在"写入链路还没验证通过"的情况下先执行了全片擦除，
结果擦除成功、写入失败 → **板子变空**。
所以这里的顺序是：
  ① 进 BootLoader、读芯片 ID（型号不符立刻停手）
  ② **安全探针**：先读出开头一段 Flash，再把它**原样写回原地址**
     —— 内容一模一样，写坏了也不影响功能，但足以证明"写入链路可用"
  ③ 擦除
  ④ 写入
  ⑤ **读回逐字节校验**（ISP 协议支持 Read Memory，写完能验）
  ⑥ 跳转运行

可测性
------
`Stm32Bootloader` 只依赖一个"像 pyserial 的对象"（`write/read/timeout/reset_input_buffer`），
所以测试里可以塞一个**假 BootLoader 模拟器**把协议逻辑跑透（见 tests/test_stm32_isp.py），
真机上只剩"时序"这一层需要验证。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

ACK, NACK = 0x79, 0x1F

# 常见 STM32 芯片 ID（低 16 位）→ 型号。
# ★ 写之前一定核对：ID 不符就停手，避免把别的芯片写坏。
KNOWN_PID = {
    0x0413: "STM32F405/407/415/417",
    0x0411: "STM32F40x/41x",
    0x0419: "STM32F42x/43x",
    0x0421: "STM32F446",
    0x0434: "STM32F469/479",
    0x0449: "STM32F401",
    0x0447: "STM32F411",
    0x0431: "STM32F411",
    0x0451: "STM32F411",
    0x0415: "STM32F410",
}

# ATK-XISP 风格的复位预设（与 serial_assistant.LINE_PRESETS 对应）
DEFAULT_RUN_PRESET = "rts_high_dtr_high_boot"      # 本机实测：两者都高 = 芯片运行用户程序
DEFAULT_BOOT_PRESET = "dtr_low_rts_high_boot"      # 进 BootLoader（RTS 高 = BOOT0 高）


class IspError(RuntimeError):
    """协议层面的错误（会给用户看人话）。"""


# ── Intel HEX 解析 ──────────────────────────────────────────────────────
def parse_intel_hex(path) -> list:
    """解析 Intel HEX → `[(起始地址, 数据), ...]`（相邻记录自动合并成段）。"""
    p = Path(path)
    if not p.exists():
        raise IspError(f"找不到文件：{p}")
    segs: list = []
    upper = 0
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line.startswith(":"):
            continue
        try:
            n = int(line[1:3], 16)
            addr = int(line[3:7], 16)
            rec = int(line[7:9], 16)
            data = bytes.fromhex(line[9:9 + n * 2])
        except ValueError:
            raise IspError(f"HEX 文件格式不对（第 {len(segs)} 段附近）：{line[:40]}")
        if rec == 0x00:
            full = upper + addr
            if segs and segs[-1][0] + len(segs[-1][1]) == full:
                segs[-1] = (segs[-1][0], segs[-1][1] + data)
            else:
                segs.append((full, data))
        elif rec == 0x01:
            break
        elif rec == 0x04:
            upper = int.from_bytes(data, "big") << 16
        elif rec == 0x02:
            upper = int.from_bytes(data, "big") << 4
    if not segs:
        return []
    return [(a, bytes(d)) for a, d in segs]


def _xor(data: bytes) -> int:
    x = 0
    for b in data:
        x ^= b
    return x


# ── 协议层 ──────────────────────────────────────────────────────────────
class Stm32Bootloader:
    """与 STM32 BootLoader 对话（AN3155）。IO 可注入 → 能离线测。

    `io` 只要有 `write(bytes)` / `read(n)` / `timeout` / `reset_input_buffer()` 就行
    （pyserial 的 Serial 满足；测试里用假后端）。

    ★★ `gap` 默认 **0**，这不是随便定的，是真机量出来的：
    在正点原子探索者 STM32F407 + CH340 上，命令字节与它的参数之间**只要插入延时**
    （20 ms 就够），成功率立刻从 **6/6 掉到 0/6**：
    ```
    gap=0.00 → 6/6 成功      gap=0.02 → 0/6
    gap=0.05 → 0/6           gap=0.15 → 0/6
    ```
    原因是 BootLoader 对"命令之后多久收到参数"有**很短的字节级超时**，
    超时后它把参数当成新命令 → 回 NACK / 状态错位。
    ★ 这正是"偶发被吞、时好时坏"的真凶 —— 别再加"保险的延时"，那只会让它必然失败。
    """

    def __init__(self, io, log=None, gap: float = 0.0, retries: int = 3):
        self.io = io
        self.gap = gap
        self.retries = max(1, int(retries))
        self.log = log or (lambda *_: None)

    def _retry(self, fn, what: str):
        """★ 真机实测：命令会**偶发被吞**（发出去没有 ACK）。

        失败时先发一个 0x7F —— AN3155 里这是"复位命令状态"的动作，
        让芯片丢掉半截命令，然后**重试整帧**（而不是接着发剩下的字节）。
        对写/擦除来说重试是安全的：同样的数据写到同一地址是幂等的，
        而且写完之后我们还会读回逐字节校验。
        """
        last = None
        for attempt in range(1, self.retries + 1):
            try:
                return fn()
            except IspError as e:
                last = e
                if attempt < self.retries:
                    self.log(f"（{what} 第 {attempt} 次没成功：{e} → 复位命令状态后重试）")
                    try:
                        self.io.write(b"\x7f")
                        time.sleep(max(0.02, self.gap))
                        self.io.read(32)
                    except Exception:
                        pass
        raise last

    # ── 低层 ──
    def _read(self, n: int, what: str) -> bytes:
        data = self.io.read(n)
        if len(data) != n:
            raise IspError(f"{what} 读超时：期望 {n} 字节，只收到 {len(data)} 字节"
                           f"（芯片没进 BootLoader？或 DTR/RTS 极性不对）")
        return data

    def _ack(self, what: str) -> None:
        b = self._read(1, what)[0]
        if b == NACK:
            raise IspError(f"{what}：芯片回了 NACK（命令被拒绝）")
        if b != ACK:
            raise IspError(f"{what}：期望 ACK(0x79)，收到 0x{b:02X}")

    def _cmd(self, cmd: int, what: str) -> None:
        self.io.write(bytes([cmd, cmd ^ 0xFF]))
        time.sleep(self.gap)
        self._ack(what)

    # ── 命令 ──
    def sync(self, tries: int = 6, gap: float = 0.15) -> None:
        """同步：发 0x7F 等 ACK（这是进入 BootLoader 后第一件事）。"""
        for _ in range(max(1, tries)):
            self.io.write(b"\x7f")
            time.sleep(gap)
            if self.io.read(1) == bytes([ACK]):
                return
        raise IspError("同步失败：芯片没有响应。检查 ①跳线帽/接线 ②串口是否被别的软件占用 "
                       "③换一种 DTR/RTS 极性（用串口页的「自动探测」）")

    def get_id(self) -> int:
        """读芯片 ID（写之前的核对依据）。"""
        return self._retry(self._get_id_once, "GetID")

    def _get_id_once(self) -> int:
        """读芯片 ID。

        ★★ 真机实测（正点原子探索者 STM32F407 + CH340）：BootLoader 3.1 的 GetID 响应是
        `ACK | 01 | 04 | 13 | ACK` —— **比 AN3155 文档描述的 `ACK | PID(2) | ACK` 多一个字节**。
        按文档读 2 字节会把 `01 04` 当成 PID，接着"期望 ACK 却收到 0x13"而失败
        （2026-10-02 两次独立复现）。
        所以这里**从尾部解析**：一直读到尾部 ACK，取它前面那两个字节 —— 两种格式都能对。
        """
        self._cmd(0x02, "GetID")
        raw = b""
        deadline = time.time() + 0.5
        while len(raw) < 8 and time.time() < deadline:
            chunk = self.io.read(1)
            if not chunk:
                continue
            raw += chunk
            if chunk == bytes([ACK]) and len(raw) >= 3:      # 尾部 ACK
                break
        if len(raw) < 3:
            raise IspError(f"GetID 响应太短：{raw.hex(' ') or '(空)'}")
        body = raw[:-1] if raw.endswith(bytes([ACK])) else raw
        if len(body) < 2:
            raise IspError(f"GetID 响应里没有 PID：{raw.hex(' ')}")
        return int.from_bytes(body[-2:], "big")

    def get_version(self) -> tuple:
        """读 BootLoader 版本与支持的命令表。

        ★ AN3155 的坑：响应是 `ACK | N | 版本字节 + N 个命令码 | ACK` ——
        N 只数"命令码"个数，实际要读 **N+1** 个字节。少读一个会让后面所有命令
        整体错位（症状是"GetID 被回 NACK"）。真机实测过。
        """
        return self._retry(self._get_version_once, "Get")

    def _get_version_once(self) -> tuple:
        self.io.write(bytes([0x00, 0xFF]))
        time.sleep(self.gap)
        self._ack("Get")
        n = self._read(1, "Get 长度")[0]
        body = self._read(n + 1, "Get 内容")
        self._ack("Get 尾")
        ver = body[0]
        return (ver >> 4, ver & 0x0F, list(body[1:]))

    def read_memory(self, addr: int, n: int = 256) -> bytes:
        """读 Flash（也用来"读回校验"）。"""
        return self._retry(lambda: self._read_memory_once(addr, n), f"读 0x{addr:08X}")

    def _read_memory_once(self, addr: int, n: int = 256) -> bytes:
        self._cmd(0x11, "读命令")
        a = int(addr).to_bytes(4, "big")
        self.io.write(a + bytes([_xor(a)]))
        time.sleep(self.gap)
        self._ack("读地址")
        nn = max(0, int(n) - 1)
        self.io.write(bytes([nn, nn ^ 0xFF]))
        time.sleep(self.gap)
        self._ack("读长度")
        return self._read(n, "读数据")

    def erase_all(self) -> None:
        """全片擦除（F4 用扩展擦除 0x44；老型号退回标准擦除 0x43）。"""
        return self._retry(self._erase_all_once, "擦除")

    def _erase_all_once(self) -> None:
        old = getattr(self.io, "timeout", None)
        try:
            try:
                self._cmd(0x44, "扩展擦除")
                if old is not None:
                    self.io.timeout = max(30.0, old)
                self.io.write(b"\xff\xff\x00")            # 0xFFFF = 全片
                time.sleep(self.gap)
                self._ack("擦除完成")
            except IspError:
                self._cmd(0x43, "标准擦除")
                if old is not None:
                    self.io.timeout = max(30.0, old)
                self.io.write(b"\xff\x00")
                time.sleep(self.gap)
                self._ack("擦除完成")
        finally:
            if old is not None:
                self.io.timeout = old

    def write(self, addr: int, data: bytes) -> None:
        """写一段（一次最多 256 字节）。"""
        if not data:
            return
        if len(data) > 256:
            raise IspError(f"一次最多写 256 字节，收到 {len(data)}")
        return self._retry(lambda: self._write_once(addr, data), f"写 0x{addr:08X}")

    def _write_once(self, addr: int, data: bytes) -> None:
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
        self._ack("写数据")

    def go(self, addr: int = 0x08000000) -> None:
        """跳转执行。"""
        return self._retry(lambda: self._go_once(addr), "Go")

    def _go_once(self, addr: int = 0x08000000) -> None:
        self._cmd(0x21, "Go")
        a = int(addr).to_bytes(4, "big")
        self.io.write(a + bytes([_xor(a)]))
        time.sleep(self.gap)
        self._ack("Go 尾")


# ── 烧录流程 ────────────────────────────────────────────────────────────
@dataclass
class FlashReport:
    ok: bool = False
    pid: int = 0
    chip: str = ""
    boot_version: str = ""
    total_bytes: int = 0
    written: int = 0
    verified: bool = False
    safe_probe: bool = False
    backup_path: str = ""          # 擦除前把原固件备份到哪了
    steps: list = field(default_factory=list)
    error: str = ""

    def text(self) -> str:
        lines = [f"{'✅' if self.ok else '❌'} STM32 串口 ISP 烧录{'完成' if self.ok else '失败'}"]
        if self.chip:
            lines.append(f"芯片：0x{self.pid:04X} → {self.chip}"
                         + (f"（BootLoader {self.boot_version}）" if self.boot_version else ""))
        if self.total_bytes:
            lines.append(f"固件：{self.total_bytes} 字节 · 已写 {self.written} 字节"
                         + (" · 读回校验通过" if self.verified else ""))
        if self.backup_path:
            lines.append(f"原固件备份：{self.backup_path}")
        lines += [f"  · {s}" for s in self.steps]
        if self.error:
            lines.append(f"错误：{self.error}")
        return "\n".join(lines)


class Stm32Flasher:
    """把「进 BootLoader → 安全检查 → 擦除 → 写入 → 读回校验 → 运行」串起来。

    `serial_like` 可以是 pyserial 的 Serial，也可以是假后端（离线测试）。
    复位/进 BootLoader 由调用方用串口助手的 DTR/RTS 能力完成（`reset_preset` 参数）。
    """

    def __init__(self, serial_like, log=None, gap: float = 0.0, retries: int = 3,
                 reset_fn=None):
        self.io = serial_like
        self.log = log or (lambda *_: None)
        self.reset_fn = reset_fn          # 可选：重新进 BootLoader 的动作（握手失败时用）
        self.bl = Stm32Bootloader(serial_like, log=log, gap=gap, retries=retries)

    def _handshake(self, rep, say):
        """同步 + 读版本 + 读芯片 ID。

        ★ 真机实测：**sync 之后的第一条命令偶发被吞**（约一半概率），
        只重发命令救不回来（状态已经错位）；但**重新复位进 BootLoader 再来一次就好了**。
        所以这里在失败时调用 `reset_fn`（若提供）整段重试。
        """
        attempts = 3 if self.reset_fn else 1
        last = None
        for i in range(1, attempts + 1):
            try:
                if i > 1:
                    say(f"握手失败，重新进 BootLoader 再试（第 {i} 次）")
                    self.reset_fn()
                self.bl.sync()
                try:
                    v1, v2, cmds = self.bl.get_version()
                    rep.boot_version = f"{v1}.{v2}"
                    say(f"BootLoader {rep.boot_version} · 支持 {len(cmds)} 条命令")
                except IspError as e:
                    say(f"（先发 Get 没成功，继续试 GetID：{e}）")
                rep.pid = self.bl.get_id()
                return
            except IspError as e:
                last = e
                say(f"（第 {i} 次握手没成功：{e}）")
        raise IspError(f"进 BootLoader 后握手失败（试了 {attempts} 次）：{last}")

    def flash(self, hexfile, expect_pid=None, verify: bool = True,
              safe_probe: bool = False, run: bool = True, backup: bool = True,
              backup_dir=None, progress=None) -> FlashReport:
        """按安全顺序烧录。返回 FlashReport（不抛异常，错误进 report.error）。"""
        rep = FlashReport()
        say = lambda m: (self.log(m), rep.steps.append(m))          # noqa: E731
        try:
            segs = parse_intel_hex(hexfile)
            if not segs:
                raise IspError("HEX 里没有可用数据（是不是空文件/只含结束记录？）")
            rep.total_bytes = sum(len(d) for _, d in segs)

            # ① 进 BootLoader 后的同步 + 身份核对（失败会自动重进 BootLoader）
            self._handshake(rep, say)
            rep.chip = KNOWN_PID.get(rep.pid, "（未收录的型号）")
            say(f"芯片 0x{rep.pid:04X} → {rep.chip}")
            if expect_pid and rep.pid != int(expect_pid):
                raise IspError(f"芯片 ID 不符：期望 0x{int(expect_pid):04X}，实际 0x{rep.pid:04X} —— 已停手")
            if not expect_pid and rep.pid not in KNOWN_PID:
                raise IspError(f"芯片 ID 0x{rep.pid:04X} 不在已知型号里，为安全起见停手"
                               f"（确认无误可传 expect_pid 强制继续）")

            # ② ★★ 擦除前先把原固件**备份**下来
            #    （比"写探针"更实在：NOR Flash 必须先擦才能写，"原样写回"在未擦除的
            #       Flash 上根本写不进去；而备份能真正解决"擦完写失败 → 板子变空"）
            if backup:
                try:
                    blob = bytearray()
                    for addr, data in segs:
                        off = 0
                        while off < len(data):
                            n = min(256, len(data) - off)
                            blob.extend(self.bl.read_memory(addr + off, n))
                            off += n
                    out_dir = Path(backup_dir) if backup_dir else (Path.cwd() / "out")
                    try:
                        out_dir.mkdir(parents=True, exist_ok=True)
                    except Exception:
                        out_dir = Path.cwd()
                    stamp = time.strftime("%Y%m%d-%H%M%S")
                    path = out_dir / f"flash-backup-{rep.pid:04X}-{stamp}.bin"
                    path.write_bytes(bytes(blob))
                    rep.backup_path = str(path)
                    say(f"原固件已备份：{len(blob)} 字节 → {path.name}"
                        f"（万一写失败可以用它烧回去）")
                except IspError as e:
                    raise IspError(f"备份原固件失败：{e} —— 为安全起见**没有擦除**。"
                                   f"（不想备份可以传 backup=False，但擦除后就无法恢复了）")

            # ③ 擦除
            self.bl.erase_all()
            say("全片擦除完成")

            # ④ 写入
            for i, (addr, data) in enumerate(segs, 1):
                off = 0
                while off < len(data):
                    chunk = data[off:off + 256]
                    self.bl.write(addr + off, chunk)
                    off += len(chunk)
                    rep.written += len(chunk)
                    if progress:
                        progress(rep.written, rep.total_bytes)
                say(f"第 {i}/{len(segs)} 段写入完成：0x{addr:08X} · {len(data)} 字节")

            # ⑤ 读回校验
            if verify:
                for addr, data in segs:
                    off = 0
                    while off < len(data):
                        n = min(256, len(data) - off)
                        back = self.bl.read_memory(addr + off, n)
                        if back != data[off:off + n]:
                            bad = next(k for k in range(n) if back[k] != data[off + k])
                            raise IspError(f"读回校验失败：0x{addr + off + bad:08X} "
                                           f"期望 0x{data[off + bad]:02X}，实际 0x{back[bad]:02X}")
                        off += n
                rep.verified = True
                say("读回校验通过（逐字节比对）")

            # ⑥ 运行
            if run:
                self.bl.go(0x08000000)
                say("已跳转运行")
            rep.ok = True
        except IspError as e:
            rep.error = str(e)
            if rep.backup_path:
                rep.error += f"（原固件备份在 {rep.backup_path}，可烧回去）"
            say(f"失败：{e}")
        except Exception as e:                                        # pragma: no cover
            rep.error = f"{type(e).__name__}: {e}"
            say(f"意外错误：{rep.error}")
        return rep


def make_hex_from_bin(binfile, base: int = 0x08000000, out=None) -> Path:
    """把备份的 .bin 转成 Intel HEX（用于"烧回原固件"）。"""
    data = Path(binfile).read_bytes()
    out = Path(out) if out else Path(binfile).with_suffix(".hex")
    lines = []
    upper_sent = None
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        addr = base + off
        up = (addr >> 16) & 0xFFFF
        if up != upper_sent:
            rec = [0x02, 0x00, 0x00, 0x04, (up >> 8) & 0xFF, up & 0xFF]
            lines.append(":" + "".join(f"{b:02X}" for b in rec)
                         + f"{(-sum(rec)) & 0xFF:02X}")
            upper_sent = up
        rec = [len(chunk), (addr >> 8) & 0xFF, addr & 0xFF, 0x00] + list(chunk)
        lines.append(":" + "".join(f"{b:02X}" for b in rec) + f"{(-sum(rec)) & 0xFF:02X}")
    lines.append(":00000001FF")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def flash_via_assistant(assistant, hexfile, **kw) -> FlashReport:
    """用串口助手的串口（连同它的 DTR/RTS 复位能力）走一遍完整烧录。

    `assistant` 是 `SerialAssistant`（需已打开串口）。
    """
    if not getattr(assistant, "is_open", False):
        r = FlashReport()
        r.error = "串口没打开"
        return r
    ser = assistant._ser                                       # noqa: SLF001 - 同项目内部协作
    preset = kw.pop("boot_preset", DEFAULT_BOOT_PRESET)

    def 重新进BootLoader():
        assistant.pulse_reset(preset)
        time.sleep(0.5)
        try:
            ser.reset_input_buffer()
        except Exception:
            pass

    flasher = Stm32Flasher(ser, reset_fn=重新进BootLoader)
    重新进BootLoader()                                          # 先复位进 BootLoader
    report = flasher.flash(hexfile, **kw)
    if kw.get("run", True):
        time.sleep(0.2)
        assistant.pulse_reset(DEFAULT_RUN_PRESET)               # 烧完回到正常运行
    return report
