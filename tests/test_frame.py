#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""声明式帧解析的离线测试（纯计算，不需要任何硬件）。

覆盖：校验算法已知向量 / 配置校验的人话错误 / 流式切帧（含逐字节喂）/
      坏校验与坏长度 / 定长与帧尾切法 / 字段类型与字节序 / 帧→数据点 / 格式存取。

用法：python tests/test_frame.py     （退出码 0 = 通过）
"""
from __future__ import annotations

import json
import os
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["CONTEST_FRAMES_DIR"] = tempfile.mkdtemp(prefix="cw-frames-")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from contest_workbench.frame_parser import (                                   # noqa: E402
    BUILTIN_FORMATS, FrameDecoder, crc8, crc16_ccitt, crc16_modbus, frame_to_point,
    list_formats, load_format, parse_format, save_format, sum8, xor8)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


def make_frame(t: float, v: float) -> bytes:
    """造一个内置示例 1 的合法帧：AA 55 | 长度 | f32 时间 | f32 值 | CRC16-MODBUS(小端)"""
    body = b"\xAA\x55"
    payload = struct.pack("<ff", t, v)
    length = 2 + 1 + len(payload) + 2
    raw = body + bytes([length]) + payload
    return raw + struct.pack("<H", crc16_modbus(raw))


def main() -> int:
    print("== ① 校验算法（已知向量）==")
    check("CRC16-MODBUS('123456789') = 0x4B37", crc16_modbus(b"123456789") == 0x4B37,
          hex(crc16_modbus(b"123456789")))
    check("CRC16-CCITT('123456789') = 0x29B1", crc16_ccitt(b"123456789") == 0x29B1,
          hex(crc16_ccitt(b"123456789")))
    check("CRC8('123456789') = 0xF4", crc8(b"123456789") == 0xF4, hex(crc8(b"123456789")))
    check("sum8 = 累加低 8 位", sum8(b"\x01\x02\x03") == 6 and sum8(b"\xff\xff") == 0xFE)
    check("xor8 = 逐字节异或", xor8(b"\x01\x02\x03") == 0 and xor8(b"\xaa\x55") == 0xFF)
    check("空数据也不炸", crc16_modbus(b"") == 0xFFFF and crc8(b"") == 0)

    print("\n== ② 配置校验：错误必须说人话 ==")
    fmt, err = parse_format({"start": "AA5", "fields": [{"type": "f32"}]})
    check("帧头位数奇数 → 指出帧头", fmt is None and "帧头" in err, str(err))
    fmt, err = parse_format({"start": "ZZ", "fields": [{"type": "f32"}]})
    check("帧头不是十六进制 → 指出帧头", fmt is None and "十六进制" in err, str(err))
    fmt, err = parse_format({"length": {"fixed": 8}, "checksum": {"type": "crc32"},
                             "fields": [{"type": "f32"}]})
    check("不认识的校验方式 → 列出可选项", fmt is None and "crc16_modbus" in err, str(err))
    fmt, err = parse_format({"length": {"fixed": 8}, "fields": [{"type": "float"}]})
    check("不认识的字段类型 → 列出可选项", fmt is None and "f32" in err, str(err))
    fmt, err = parse_format({"length": {"fixed": 8}, "fields": []})
    check("fields 为空 → 报错", fmt is None and "fields" in err, str(err))
    fmt, err = parse_format({"fields": [{"type": "f32"}]})
    check("没有任何切帧依据 → 报错", fmt is None and "切帧" in err, str(err))
    fmt, err = parse_format({"length": {"offset": 1, "size": 3}, "fields": [{"type": "f32"}]})
    check("length.size 只能是 1/2/4", fmt is None and "1 / 2 / 4" in err, str(err))
    fmt, err = parse_format(BUILTIN_FORMATS[0])
    check("内置示例 1 能通过校验", fmt is not None and err is None, str(err))

    print("\n== ③ 流式切帧 ==")
    fmt, _ = parse_format(BUILTIN_FORMATS[0])
    dec = FrameDecoder(fmt)
    raw = make_frame(0.04, 0.55)
    fr = dec.feed(raw)
    check("一整帧 → 解析出 1 帧", len(fr) == 1 and fr[0]["ok"], str(fr)[:80])
    check("★ 字段值正确（f32 小端）",
          abs(fr[0]["values"]["时间"] - 0.04) < 1e-6 and abs(fr[0]["values"]["转速"] - 0.55) < 1e-6,
          str(fr[0]["values"]))
    check("原始字节被保留（可回看）", fr[0]["raw"] == raw)

    dec = FrameDecoder(fmt)
    got = []
    for b in raw:                                # ★ 逐字节喂：串口最常见的情形
        got += dec.feed(bytes([b]))
    check("★ 逐字节喂同样能切出 1 帧（流式缓冲正确）",
          len(got) == 1 and got[0]["ok"], f"{len(got)} 帧")

    dec = FrameDecoder(fmt)
    fr = dec.feed(b"\x00\x11\x22" + raw)          # 前面有垃圾
    check("帧头前有垃圾：能对齐并解析", len(fr) == 1 and fr[0]["ok"])
    check("垃圾字节被计数（dropped=3）", dec.dropped == 3, str(dec.dropped))

    dec = FrameDecoder(fmt)
    fr = dec.feed(raw + raw + raw)
    check("一次喂 3 帧 → 出 3 帧", len(fr) == 3 and all(x["ok"] for x in fr))
    check("帧序号递增", [x["t"] for x in fr] == [1, 2, 3])

    dec = FrameDecoder(fmt)
    fr = dec.feed(raw[:-1])                       # 差最后 1 字节
    check("帧没收全 → 先不出帧（不误判）", fr == [] and dec.stats()["pending"] == len(raw) - 1)
    fr = dec.feed(raw[-1:])
    check("补上最后 1 字节 → 出帧", len(fr) == 1 and fr[0]["ok"])

    print("\n== ④ 坏帧处理 ==")
    bad = bytearray(make_frame(0.04, 0.55))
    bad[-1] ^= 0xFF                               # 弄坏校验
    dec = FrameDecoder(fmt)
    fr = dec.feed(bytes(bad))
    check("校验错 → ok=False 且给出两个数值对比",
          len(fr) == 1 and not fr[0]["ok"] and "校验失败" in fr[0]["error"], str(fr)[:100])
    check("校验错被单独计数（bad_checksum=1）", dec.bad_checksum == 1 and dec.frames == 0)
    fr = dec.feed(make_frame(1.0, 2.0))           # 坏帧之后还能恢复
    check("★ 坏帧之后能自动恢复（继续解析后续帧）", len(fr) == 1 and fr[0]["ok"])

    broken = bytearray(make_frame(0.04, 0.55))
    broken[2] = 200                               # 长度字段写离谱
    dec = FrameDecoder(fmt)
    fr = dec.feed(bytes(broken) + make_frame(3.0, 4.0))
    check("长度字段离谱 → 判为坏帧并给出可读原因",
          any(not x["ok"] and "长度字段" in x["error"] for x in fr), str(fr)[:120])
    check("★ 坏长度之后仍能找到下一帧",
          any(x["ok"] and abs(x["values"].get("转速", 0) - 4.0) < 1e-6 for x in fr), str(fr)[:120])

    print("\n== ⑤ 其它切帧方式 ==")
    fmt2, _ = parse_format(BUILTIN_FORMATS[1])    # 7E + 1 字节长度 + sum8 + 两个 i16
    payload = struct.pack("<hh", -100, 250)
    body = b"\x7E" + bytes([1 + 1 + len(payload) + 1]) + payload
    frame2 = body + bytes([sum8(body)])
    dec2 = FrameDecoder(fmt2)
    fr = dec2.feed(frame2)
    check("内置示例 2：sum8 + 两个 i16（含负数）解析正确",
          len(fr) == 1 and fr[0]["ok"] and fr[0]["values"]["目标"] == -100
          and fr[0]["values"]["实际"] == 250, str(fr)[:100])

    fmt3, _ = parse_format(BUILTIN_FORMATS[2])    # 无帧头定长 8 字节
    dec3 = FrameDecoder(fmt3)
    a = struct.pack("<ff", 1.5, 2.5)
    b = struct.pack("<ff", 3.5, 4.5)
    fr = dec3.feed(a + b)
    check("内置示例 3：无帧头定长切帧正确",
          len(fr) == 2 and abs(fr[0]["values"]["时间"] - 1.5) < 1e-6
          and abs(fr[1]["values"]["值"] - 4.5) < 1e-6, str(fr)[:100])

    fmt4, err4 = parse_format({
        "name": "帧尾切帧", "start": "AA", "end": "0D 0A",
        "checksum": {"type": "none"},
        "fields": [{"name": "a", "type": "u8"}, {"name": "b", "type": "u16", "endian": "big"}],
    })
    check("帧尾切法配置能通过", fmt4 is not None, str(err4))
    dec4 = FrameDecoder(fmt4)
    fr = dec4.feed(b"\xAA\x01\x02\x03\r\n")
    check("按帧尾切帧 + 大端 u16 正确",
          len(fr) == 1 and fr[0]["values"]["a"] == 1 and fr[0]["values"]["b"] == 0x0203,
          str(fr)[:100])

    print("\n== ⑥ 帧 → 数据点（接住调参助手）==")
    f_ok = {"ok": True, "values": {"时间": 0.04, "转速": 0.55}, "t": 1}
    check("两个字段 → (时间, 值)", frame_to_point(f_ok, 1) == (0.04, 0.55))
    check("一个字段 → (序号, 值)", frame_to_point({"ok": True, "values": {"v": 3.5}}, 7) == (7.0, 3.5))
    check("坏帧 → None", frame_to_point({"ok": False, "values": {}}, 1) is None)
    check("没有数值字段 → None", frame_to_point({"ok": True, "values": {}}, 1) is None)

    print("\n== ⑦ 格式的保存 / 列举 / 读取 ==")
    path, err = save_format(BUILTIN_FORMATS[1])
    check("保存到用户目录成功", path is not None and path.exists(), str(err))
    names = [x["name"] for x in list_formats()]
    check("列表里有 3 个内置 + 刚保存的", len([n for n in names if n]) >= 4, str(names))
    cfg, err = load_format(BUILTIN_FORMATS[0]["name"])
    check("按名字取回内置格式", cfg is not None and cfg["name"] == BUILTIN_FORMATS[0]["name"], str(err))
    cfg, err = load_format("不存在的格式")
    check("取不存在的格式 → 人话错误", cfg is None and "没有找到" in err, str(err))
    path2, err2 = save_format({"name": "坏/名:字", "length": {"fixed": 4},
                               "fields": [{"type": "u8"}]})
    check("文件名里的非法字符被清掉", path2 is not None and "/" not in path2.name, str(path2))
    fmt5, _ = parse_format(BUILTIN_FORMATS[0])
    back = fmt5.to_dict()
    check("to_dict 结果能再解析回来（可保存/可还原）",
          parse_format(back)[1] is None and json.loads(json.dumps(back)) == back)

    print("\n== ⑧ 接进串口助手（与真串口同一条解析管线）==")
    from contest_workbench.serial_assistant import SerialAssistant
    a = SerialAssistant()
    msg = a.set_frame_format(BUILTIN_FORMATS[0])
    check("启用帧解析 → 人话提示（含格式名/校验/字段数）",
          "已启用帧解析" in msg and "crc16_modbus" in msg and "2 个字段" in msg, msg)
    check("snapshot 里带上帧统计", a.snapshot().get("frame", {}).get("format") == BUILTIN_FORMATS[0]["name"])

    a.feed_bytes(make_frame(0.04, 0.55) + make_frame(0.08, 0.90))
    snap = a.snapshot()
    check("★ 两帧 → 两个数据点", snap["total"] == 2, str(snap["total"]))
    check("★ 数据点的值就是帧里的字段（时间,转速）",
          abs(snap["points"][0][0] - 0.04) < 1e-6 and abs(snap["points"][0][1] - 0.55) < 1e-6
          and abs(snap["points"][1][1] - 0.90) < 1e-6,
          str(snap["points"][:2]))
    evs = a.read_events(99)
    check("事件里能直接看到每一帧的字段内容",
          any(e.get("kind") == "frame" and "转速" in str(e.get("text")) for e in evs),
          str([e.get("text") for e in evs][:2]))

    badpkt = bytearray(make_frame(1.0, 2.0))
    badpkt[-1] ^= 0xFF
    a.feed_bytes(bytes(badpkt))
    check("坏校验 → 计入 bad_checksum 并给一条人话事件",
          a.frame_stats().get("bad_checksum") == 1
          and any("坏帧" in str(e.get("text")) for e in a.read_events(99)))

    a.feed_bytes(make_frame(0.5, 1.5))            # 坏帧之后还能继续
    check("★ 坏帧之后继续解析（真机上丢字节很常见）", a.snapshot()["total"] == 3)

    check("停用帧解析 → 回到按行解析", "已停用" in a.set_frame_format(None))
    a.feed_text("0.30,1.70\n")
    check("停用后按行解析照旧可用", a.snapshot()["total"] == 4, str(a.snapshot()["total"]))
    check("配置写错 → 返回人话错误（不启用）",
          a.set_frame_format({"length": {"fixed": 4}, "fields": [{"type": "xx"}]}).startswith("[错误]"))
    check("停用后 frame 统计为空", a.frame_stats() == {})

    print("\n" + "=" * 52)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("全部通过 ✅（纯计算，不需要硬件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
