#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""声明式帧解析：把二进制协议帧变成能画曲线、能算指标的数据点。

为什么需要它
------------
串口助手原本只认「每行两个数字」（`0.04,0.55`）。但真实板子（电赛控制类、
传感器模块）常见的是**二进制帧**：

    AA 55 | 长度 | 数据... | CRC16

于是"能看数"变成"看不懂"。这个模块让用户用一段 **JSON 描述**告诉程序帧长什么样，
剩下的切帧、验校验、拆字段全部自动完成 —— 思路借鉴 Serial-Studio 的声明式解析，
代码与实现都是本项目自己的。

设计要点
--------
- **流式**：串口是一段段来的，`FrameDecoder.feed()` 接受任意切分的字节，内部攒缓冲
- **不抛异常**：所有对外函数返回结果或人话错误字符串（与项目其它模块一致）
- **可离线测**：切帧/校验/拆字段全是纯计算，不需要硬件
- **接得住指标**：解析出的字段可以直接变成 `(t, v)` 数据点，送现有的调参助手

配置示例（也是界面里的内置示例）
--------------------------------
```json
{
  "name": "AA55 + 长度 + CRC16 + 两个 float",
  "start": "AA 55",
  "length": {"offset": 2, "size": 1, "includes_header": true},
  "checksum": {"type": "crc16_modbus", "size": 2, "endian": "little"},
  "fields": [
    {"name": "时间", "type": "f32", "endian": "little"},
    {"name": "转速", "type": "f32", "endian": "little"}
  ]
}
```
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

# ── 校验算法 ────────────────────────────────────────────────────────────
def crc16_modbus(data: bytes) -> int:
    """CRC16-MODBUS：多项 0xA001（反射）、初值 0xFFFF。'123456789' → 0x4B37。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def crc16_ccitt(data: bytes) -> int:
    """CRC16-CCITT-FALSE：多项 0x1021、初值 0xFFFF。'123456789' → 0x29B1。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc & 0xFFFF


def crc8(data: bytes) -> int:
    """CRC8：多项 0x07、初值 0x00。'123456789' → 0xF4。"""
    crc = 0x00
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def sum8(data: bytes) -> int:
    """累加和低 8 位。"""
    return sum(data) & 0xFF


def xor8(data: bytes) -> int:
    """逐字节异或。"""
    x = 0
    for b in data:
        x ^= b
    return x


CHECKSUMS = {
    "none": ("不校验", None),
    "sum8": ("累加和（1 字节）", sum8),
    "xor8": ("异或（1 字节）", xor8),
    "crc8": ("CRC8 (0x07)", crc8),
    "crc16_modbus": ("CRC16-MODBUS", crc16_modbus),
    "crc16_ccitt": ("CRC16-CCITT", crc16_ccitt),
}

# ── 字段类型 ────────────────────────────────────────────────────────────
FIELD_TYPES = {
    "u8": ("无符号 8 位", 1, "B"),
    "i8": ("有符号 8 位", 1, "b"),
    "u16": ("无符号 16 位", 2, "H"),
    "i16": ("有符号 16 位", 2, "h"),
    "u32": ("无符号 32 位", 4, "I"),
    "i32": ("有符号 32 位", 4, "i"),
    "f32": ("32 位浮点", 4, "f"),
    "f64": ("64 位浮点", 8, "d"),
}
_ENDIAN = {"little": "<", "big": ">"}

# ── 内置示例（界面里直接可选，也是最常见的三种）────────────────────────
BUILTIN_FORMATS: list = [
    {
        "name": "AA55 帧头 + 长度 + CRC16-MODBUS + 两个 float（常见板子协议）",
        "start": "AA 55",
        "length": {"offset": 2, "size": 1, "includes_header": True},
        "checksum": {"type": "crc16_modbus", "size": 2, "endian": "little"},
        "fields": [
            {"name": "时间", "type": "f32", "endian": "little"},
            {"name": "转速", "type": "f32", "endian": "little"},
        ],
    },
    {
        "name": "0x7E 帧头 + 1 字节长度 + 校验和 + 两个 int16",
        "start": "7E",
        "length": {"offset": 1, "size": 1, "includes_header": True},
        "checksum": {"type": "sum8", "size": 1, "endian": "little"},
        "fields": [
            {"name": "目标", "type": "i16", "endian": "little"},
            {"name": "实际", "type": "i16", "endian": "little"},
        ],
    },
    {
        "name": "无帧头定长（每帧 8 字节 = 两个 f32，无校验）",
        "start": "",
        "length": {"fixed": 8},
        "checksum": {"type": "none"},
        "fields": [
            {"name": "时间", "type": "f32", "endian": "little"},
            {"name": "值", "type": "f32", "endian": "little"},
        ],
    },
]


# ── 配置解析（一律给人话错误）────────────────────────────────────────────
@dataclass
class FrameFormat:
    name: str = "未命名格式"
    start: bytes = b""
    end: bytes = b""
    length_offset: int = 0
    length_size: int = 0
    length_includes_header: bool = True
    length_endian: str = "little"
    length_fixed: int = 0
    checksum: str = "none"
    checksum_size: int = 0
    checksum_endian: str = "little"
    fields: list = field(default_factory=list)

    @property
    def header_len(self) -> int:
        return len(self.start)

    @property
    def trailer_len(self) -> int:
        return len(self.end)

    def fields_size(self) -> int:
        return sum(FIELD_TYPES[f[1]][1] for f in self.fields)

    def to_dict(self) -> dict:
        """转回可保存的 JSON 结构（界面「保存」用）。"""
        out: dict = {"name": self.name, "start": self.start.hex(" ").upper() if self.start else "",
                     "end": self.end.hex(" ").upper() if self.end else ""}
        if self.length_fixed:
            out["length"] = {"fixed": self.length_fixed}
        elif self.length_size:
            out["length"] = {"offset": self.length_offset, "size": self.length_size,
                             "includes_header": self.length_includes_header,
                             "endian": self.length_endian}
        out["checksum"] = {"type": self.checksum, "size": self.checksum_size,
                           "endian": self.checksum_endian}
        out["fields"] = [{"name": n, "type": t, "endian": e} for n, t, e in self.fields]
        return out


def _hex_bytes(text, what: str) -> tuple:
    """把 'AA 55' / 'AA55' / 'aa,55' 解析成字节。返回 (bytes, 错误)。"""
    if text in (None, ""):
        return b"", None
    if isinstance(text, (bytes, bytearray)):
        return bytes(text), None
    s = str(text).replace(",", " ").replace("0x", " ").replace("0X", " ")
    s = "".join(s.split())
    if len(s) % 2:
        return b"", f"[错误] {what} 的十六进制位数必须是偶数（现在是 {len(s)} 位）：{text!r}"
    try:
        return bytes.fromhex(s), None
    except ValueError:
        return b"", f"[错误] {what} 不是合法的十六进制：{text!r}（写法如 \"AA 55\"）"


def parse_format(cfg: dict) -> tuple:
    """校验并把 JSON 配置变成 FrameFormat。返回 (FrameFormat, 错误字符串)。

    错误一定说清**哪里不对、怎么改**，不抛异常。
    """
    if not isinstance(cfg, dict):
        return None, "[错误] 帧格式必须是一个 JSON 对象（{...}）"
    f = FrameFormat(name=str(cfg.get("name") or "未命名格式"))

    f.start, err = _hex_bytes(cfg.get("start"), "帧头")
    if err:
        return None, err
    f.end, err = _hex_bytes(cfg.get("end"), "帧尾")
    if err:
        return None, err

    length = cfg.get("length") or {}
    if not isinstance(length, dict):
        return None, "[错误] length 必须是一个对象，如 {\"offset\": 2, \"size\": 1}"
    if length.get("fixed"):
        try:
            f.length_fixed = int(length["fixed"])
        except Exception:
            return None, f"[错误] length.fixed 必须是整数，现在是 {length['fixed']!r}"
        if f.length_fixed <= 0:
            return None, "[错误] length.fixed 必须大于 0（每帧固定多少字节）"
    elif length.get("size"):
        try:
            f.length_offset = int(length.get("offset", 0))
            f.length_size = int(length["size"])
        except Exception:
            return None, "[错误] length.offset / length.size 必须是整数"
        if f.length_size not in (1, 2, 4):
            return None, f"[错误] length.size 只能是 1 / 2 / 4，现在是 {f.length_size}"
        f.length_includes_header = bool(length.get("includes_header", True))
        f.length_endian = str(length.get("endian", "little")).lower()
        if f.length_endian not in _ENDIAN:
            return None, "[错误] length.endian 只能是 little 或 big"
    if not f.length_fixed and not f.length_size and not f.end and not f.fields:
        return None, "[错误] 至少要有一种切帧依据：length（长度字段/固定长度）或 end（帧尾）"

    ck = cfg.get("checksum") or {}
    if not isinstance(ck, dict):
        return None, "[错误] checksum 必须是一个对象，如 {\"type\": \"crc16_modbus\", \"size\": 2}"
    f.checksum = str(ck.get("type", "none")).lower()
    if f.checksum not in CHECKSUMS:
        return None, (f"[错误] 不认识的校验方式：{f.checksum!r}"
                      f"（可选：{'、'.join(CHECKSUMS)}）")
    f.checksum_size = int(ck.get("size") or (2 if f.checksum.startswith("crc16") else
                                             (1 if f.checksum != "none" else 0)))
    if f.checksum != "none" and f.checksum_size not in (1, 2):
        return None, f"[错误] checksum.size 只能是 1 或 2，现在是 {f.checksum_size}"
    f.checksum_endian = str(ck.get("endian", "little")).lower()
    if f.checksum_endian not in _ENDIAN:
        return None, "[错误] checksum.endian 只能是 little 或 big"

    fields = cfg.get("fields")
    if not isinstance(fields, list) or not fields:
        return None, ("[错误] fields 必须是非空数组，如 "
                      "[{\"name\": \"时间\", \"type\": \"f32\", \"endian\": \"little\"}]")
    for i, item in enumerate(fields, 1):
        if not isinstance(item, dict):
            return None, f"[错误] 第 {i} 个字段必须是对象"
        t = str(item.get("type", "")).lower()
        if t not in FIELD_TYPES:
            return None, (f"[错误] 第 {i} 个字段类型 {item.get('type')!r} 不认识"
                          f"（可选：{'、'.join(FIELD_TYPES)}）")
        e = str(item.get("endian", "little")).lower()
        if e not in _ENDIAN:
            return None, f"[错误] 第 {i} 个字段的 endian 只能是 little 或 big"
        f.fields.append((str(item.get("name") or f"字段{i}"), t, e))
    return f, None


# ── 解码器 ──────────────────────────────────────────────────────────────
class FrameDecoder:
    """流式切帧 + 校验 + 拆字段。

    用法：`dec.feed(串口收到的字节)` → 返回这一批**新解析出来**的帧列表。
    每帧是 `{"ok": bool, "values": {名字: 数值}, "raw": bytes, "error": str, "t": 序号}`。
    """

    def __init__(self, fmt: FrameFormat, max_buffer: int = 8192):
        self.fmt = fmt
        self.max_buffer = max_buffer
        self.buf = bytearray()
        self.frames = 0                 # 成功解析的帧数
        self.bad_checksum = 0           # 校验失败的帧数
        self.bad_frames = 0             # 结构不合法（长度离谱等）
        self.dropped = 0                # 因为找不到帧头而丢掉的字节

    # 让 `SerialAssistant` 能原样吐回配置
    def describe(self) -> str:
        return self.fmt.name

    def feed(self, data: bytes) -> list:
        """喂入一段字节，返回新解析出的帧（可能 0 个）。"""
        out = []
        if not data:
            return out
        self.buf.extend(bytes(data))
        if len(self.buf) > self.max_buffer:
            over = len(self.buf) - self.max_buffer
            del self.buf[:over]                       # 防止无限增长（异常数据）
            self.dropped += over
        while True:
            frame = self._next()
            if frame is None:
                break
            out.append(frame)
        return out

    # ── 内部：取下一帧 ────────────────────────────────────────────────
    def _next(self):
        f = self.fmt
        # ① 对齐到帧头
        if f.start:
            idx = self.buf.find(f.start)
            if idx < 0:
                keep = max(0, len(f.start) - 1)
                if len(self.buf) > keep:
                    self.dropped += len(self.buf) - keep
                    del self.buf[:len(self.buf) - keep]
                return None
            if idx > 0:
                self.dropped += idx
                del self.buf[:idx]
        if not self.buf:
            return None

        # ② 定长？
        if f.length_fixed:
            total = f.length_fixed
        elif f.length_size:
            need = f.length_offset + f.length_size
            if len(self.buf) < need:
                return None
            raw = bytes(self.buf[f.length_offset:need])
            value = int.from_bytes(raw, f.length_endian)
            total = value + (0 if f.length_includes_header else f.header_len)
            if total < f.header_len + f.fields_size() + f.checksum_size or total > self.max_buffer:
                self.bad_frames += 1
                del self.buf[:max(1, f.header_len)]    # 丢掉这个帧头，继续找下一个
                return {"ok": False, "values": {}, "raw": raw,
                        "error": f"长度字段写的是 {value}，推出整帧 {total} 字节，不合理"}
        elif f.end:
            idx = self.buf.find(f.end, f.header_len)
            if idx < 0:
                return None
            total = idx + len(f.end)
        else:
            total = f.header_len + f.fields_size() + f.checksum_size

        if len(self.buf) < total:
            # ★ 长度字段本身也可能被干扰（丢字节、串扰、半帧）。
            #   如果"这一帧"还没收全，但缓冲区里已经冒出**下一个帧头**，
            #   说明这个长度不可信 → 判坏帧、丢掉这一小段、重新对齐。
            #   （真机上丢一个字节就会这样：干等着永远等不到，后面的好帧全被吞）
            if f.start:
                nxt = self.buf.find(f.start, 1)
                if 0 < nxt < total:
                    self.bad_frames += 1
                    bad = bytes(self.buf[:nxt])
                    del self.buf[:nxt]
                    return {"ok": False, "values": {}, "raw": bad,
                            "error": f"长度字段说这帧共 {total} 字节，但第 {nxt} 字节就出现了新的帧头"
                                     f" —— 长度不可信，已重新对齐"}
            return None                                # 还没收全，等下一批

        frame = bytes(self.buf[:total])
        del self.buf[:total]

        # ③ 校验
        if f.checksum != "none" and f.checksum_size:
            body, given_raw = frame[:-f.checksum_size], frame[-f.checksum_size:]
            given = int.from_bytes(given_raw, f.checksum_endian)
            calc = CHECKSUMS[f.checksum][1](body)
            if calc != given:
                self.bad_checksum += 1
                return {"ok": False, "values": {}, "raw": frame,
                        "error": f"{CHECKSUMS[f.checksum][0]} 校验失败：算出 0x{calc:0{len(given_raw) * 2}X}，"
                                 f"帧里是 0x{given:0{len(given_raw) * 2}X}"}
            payload = body
        else:
            payload = frame
        # ★ 数据区起点 = 帧头之后；如果长度字段紧跟在帧头后面，也要一起跳过，
        #   否则拆字段会从"长度字节"开始读 → 数值全是乱码（实测踩过）
        data_start = f.header_len
        if f.length_size:
            data_start = max(data_start, f.length_offset + f.length_size)
        payload = payload[data_start:]

        # ④ 拆字段
        values, off = {}, 0
        try:
            for name, ftype, endian in f.fields:
                _, size, code = FIELD_TYPES[ftype]
                if off + size > len(payload):
                    raise ValueError(f"帧体只剩 {len(payload) - off} 字节，放不下字段「{name}」({ftype})")
                values[name] = struct.unpack_from(_ENDIAN[endian] + code, payload, off)[0]
                off += size
        except Exception as e:
            self.bad_frames += 1
            return {"ok": False, "values": {}, "raw": frame, "error": str(e)}

        self.frames += 1
        return {"ok": True, "values": values, "raw": frame, "error": "",
                "t": self.frames}

    def stats(self) -> dict:
        return {"frames": self.frames, "bad_checksum": self.bad_checksum,
                "bad_frames": self.bad_frames, "dropped": self.dropped,
                "pending": len(self.buf), "format": self.fmt.name}


# ── 接住指标：帧 → 数据点 ────────────────────────────────────────────────
def frame_to_point(frame: dict, index: int) -> tuple | None:
    """把一帧变成 `(t, v)` 数据点（够现有调参助手直接用）。

    规则（够用就好，不搞复杂）：
      - 两个以上数值字段 → 用**前两个**：(字段1, 字段2)（常见就是「时间, 值」）
      - 只有一个数值字段 → `(序号, 值)`（没时间戳时用采样序号）
      - 都不是数值（或没有字段）→ None
    """
    if not frame or not frame.get("ok"):
        return None
    nums = [v for v in frame.get("values", {}).values() if isinstance(v, (int, float))]
    if len(nums) >= 2:
        return (float(nums[0]), float(nums[1]))
    if len(nums) == 1:
        return (float(index), float(nums[0]))
    return None


# ── 帧格式的保存/读取（用户自己的格式）──────────────────────────────────
def formats_dir() -> Path:
    import os
    custom = os.environ.get("CONTEST_FRAMES_DIR")
    d = Path(custom) if custom else (Path.home() / ".contest-workbench" / "frames")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def save_format(cfg: dict) -> tuple:
    """保存到用户目录。返回 (路径, 错误)。"""
    fmt, err = parse_format(cfg)
    if err:
        return None, err
    name = "".join(ch for ch in fmt.name if ch not in '\\/:*?"<>|').strip() or "未命名"
    path = formats_dir() / f"{name}.json"
    try:
        path.write_text(json.dumps(fmt.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return None, f"[错误] 保存失败：{type(e).__name__}: {e}"
    return path, None


def list_formats() -> list:
    """内置示例 + 用户保存的格式。"""
    out = [{"name": c["name"], "builtin": True, "config": c} for c in BUILTIN_FORMATS]
    try:
        for p in sorted(formats_dir().glob("*.json")):
            try:
                cfg = json.loads(p.read_text(encoding="utf-8"))
                out.append({"name": cfg.get("name") or p.stem, "builtin": False, "config": cfg})
            except Exception:
                continue
    except Exception:
        pass
    return out


def load_format(name: str) -> tuple:
    """按名字取格式配置（先内置后用户）。返回 (config, 错误)。"""
    for item in list_formats():
        if item["name"] == name:
            return item["config"], None
    return None, f"[错误] 没有找到名叫「{name}」的帧格式"
