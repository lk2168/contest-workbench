#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""串口助手的离线测试：**不碰任何真实串口、不联网**。

做法：
  1. 用 `set_serial_backend()` 注入一个"像 pyserial 的假后端"，验证打开/占用/发送等路径；
  2. 用 `feed_text()` / `feed_bytes()`（假串口注入）验证与真实串口**完全相同**的解析管线
     —— 半包、粘包、\\r\\n、编码回退、坏行、滑窗、64 KB 溢出保护；
  3. CSV 落盘后真的读回文件校验表头与行数；`analyze()` 真的走一遍已有的调参助手。

用法：python tests/test_serial.py     （退出码 0 = 全部通过）
"""
from __future__ import annotations

import math
import re
import sys
import tempfile
import time
import types
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from contest_workbench import serial_assistant                        # noqa: E402
from contest_workbench.serial_assistant import (SerialAssistant,     # noqa: E402
                                                decode_bytes, list_ports,
                                                parse_hex, set_serial_backend)

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    """累加式断言：通过/失败各记一笔，失败时打印细节。"""
    (PASS if cond else FAIL).append(name)
    print(f"[{'通过' if cond else '失败'}] {name}" + (f"  —— {detail}" if detail and not cond else ""))


# --------------------------------------------------------------------------- #
# 假串口后端（对外的行为尽量和 pyserial 一致）
# --------------------------------------------------------------------------- #
class 假串口异常(Exception):
    """对应 pyserial 的 SerialException。"""


class 假串口:
    def __init__(self, port=None, baudrate=115200, bytesize=8, parity="N",
                 stopbits=1, timeout=0.1):
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.timeout = timeout
        self.is_open = True
        self.收到的写入 = bytearray()          # 测试断言用：send() 到底写了什么
        self.inbox = bytearray()               # 测试用：模拟"对端发过来的数据"
        self.读时抛错 = None                   # 测试用：设了就在 read() 时抛异常

    def write(self, data) -> int:
        self.收到的写入.extend(bytes(data))
        return len(data)

    def flush(self) -> None:
        pass

    def read(self, size: int = 1):
        if self.读时抛错 is not None:
            raise self.读时抛错
        n = max(1, int(size))
        data = bytes(self.inbox[:n])
        del self.inbox[:n]
        return data

    @property
    def in_waiting(self) -> int:
        return len(self.inbox)

    def close(self) -> None:
        self.is_open = False


def 造后端(打开错误: Exception | None = None):
    """造一个"像 pyserial 的假后端"：有 Serial / SerialException / 常量 / list_ports。"""
    mod = types.SimpleNamespace()
    mod.SerialException = 假串口异常
    mod.FIVEBITS, mod.SIXBITS, mod.SEVENBITS, mod.EIGHTBITS = 5, 6, 7, 8
    mod.PARITY_NONE, mod.PARITY_EVEN, mod.PARITY_ODD = "N", "E", "O"
    mod.PARITY_MARK, mod.PARITY_SPACE = "M", "S"
    mod.STOPBITS_ONE, mod.STOPBITS_ONE_POINT_FIVE, mod.STOPBITS_TWO = 1, 1.5, 2
    mod.last = {}

    def _开串口(**kw):
        if 打开错误 is not None:
            raise 打开错误
        s = 假串口(**kw)
        mod.last["ser"] = s
        return s

    mod.Serial = _开串口
    mod.list_ports = types.SimpleNamespace(comports=lambda: [
        types.SimpleNamespace(device="COM7", description="USB-SERIAL CH340",
                              hwid="USB VID:PID=1A86:7523"),
        types.SimpleNamespace(device="COM8", description="蓝牙链接上的标准串行",
                              hwid="BTHENUM"),
    ])
    return mod


def 阶跃文本(n: int = 1500, dt: float = 0.002, zeta: float = 0.5, wn: float = 2.0) -> str:
    """解析解已知的二阶欠阻尼阶跃响应，写成串口那样的"时间,幅值"两列文本。"""
    wd = wn * math.sqrt(1 - zeta ** 2)
    phi = math.acos(zeta)
    rows = []
    for i in range(n):
        t = i * dt
        y = 1 - math.exp(-zeta * wn * t) / math.sqrt(1 - zeta ** 2) * math.sin(wd * t + phi)
        rows.append(f"{t:.4f},{y:.6f}")
    return "\n".join(rows) + "\n"


def 收事件(sa: SerialAssistant, 关键词: str, 超时: float = 1.5) -> list[dict]:
    """轮询 read_events，直到出现含关键词的事件或超时（模拟前端的 SSE 轮询）。"""
    end = time.time() + 超时
    got: list[dict] = []
    while time.time() < end:
        got.extend(sa.read_events(50))
        if any(关键词 in (e.get("text") or "") for e in got):
            return got
        time.sleep(0.02)
    return got


def main() -> int:
    print("== 1. 惰性导入与「没装 pyserial」的容错 ==")
    真装了pyserial = serial_assistant.serial_available()
    if 真装了pyserial:
        print("[跳过] 本机装了 pyserial，真实的「未安装」分支无法在本机复现"
              "（下面用注入方式验证同一条代码路径）")
    else:
        check("本机确实没装 pyserial：serial_available() 返回 False",
              serial_assistant.serial_available() is False)
        check("没装 pyserial：list_ports() 返回空列表而不是抛异常",
              serial_assistant.list_ports() == [], f"{serial_assistant.list_ports()!r}")
        check("没装 pyserial：模块仍能正常 import（本文件能跑起来本身就是证据）", True)
    check("没装 pyserial：list_ports() 结果一定是 list",
          isinstance(list_ports(), list), f"{type(list_ports())}")

    旧后端 = set_serial_backend("missing")
    try:
        sa = SerialAssistant()
        消息 = sa.open("COM3", 115200)
        check("没装 pyserial 时 open() 返回人话错误（含 pyserial 与安装方法）",
              isinstance(消息, str) and 消息.startswith("[错误]") and "pyserial" in 消息, 消息)
        check("没装 pyserial 时 list_ports() 为空列表", list_ports() == [])
        check("没装 pyserial 时发送也只会得到人话错误（不抛异常）",
              sa.send("hello").startswith("[错误]"), sa.send("hello"))
        check("没装 pyserial 时 is_open 仍为 False", sa.is_open is False)
    finally:
        set_serial_backend(旧后端)

    print("\n== 2. 打开串口：成功 / 参数不合法 / 端口不存在 / 被占用 ==")
    后端 = 造后端()
    set_serial_backend(后端)
    sa = SerialAssistant()
    消息 = sa.open("COM7", 9600)
    check("假串口能打开，返回人话消息", 消息.startswith("已打开") and "COM7" in 消息, 消息)
    check("打开后 is_open 为 True，port/baudrate 记录正确",
          sa.is_open is True and sa.port == "COM7" and sa.baudrate == 9600)
    check("参数真的传给了串口对象（波特率 9600）",
          getattr(后端.last.get("ser"), "baudrate", None) == 9600)
    重复 = sa.open("COM8")
    check("已打开时再 open() 返回人话错误（不抛异常）", 重复.startswith("[错误]") and "已经打开" in 重复, 重复)

    sa2 = SerialAssistant()
    for 参数, 说明 in [({"baudrate": -1}, "波特率"), ({"bytesize": 9}, "数据位"),
                     ({"parity": "X"}, "校验位"), ({"stopbits": 3}, "停止位")]:
        错误 = sa2.open("COM7", **参数)
        check(f"参数不合法给出人话错误：{说明}",
              错误.startswith("[错误]") and "不合法" in 错误 and sa2.is_open is False, 错误)
    check("端口号为空给出人话错误", "端口号不能为空" in sa2.open(""), sa2.open(""))

    set_serial_backend(造后端(假串口异常("could not open port 'COM99': "
                                        "FileNotFoundError(2, '系统找不到指定的文件。', None, 2)")))
    不存在 = SerialAssistant().open("COM99")
    check("端口不存在 → 人话错误（提示查 list_ports / 装驱动）",
          不存在.startswith("[错误]") and "不存在" in 不存在, 不存在)

    set_serial_backend(造后端(假串口异常("could not open port 'COM3': "
                                        "PermissionError(13, '拒绝访问。')")))
    占用 = SerialAssistant().open("COM3")
    check("端口被占用 → 人话错误（提示关掉占用程序）",
          占用.startswith("[错误]") and "占用" in 占用, 占用)

    print("\n== 3. 后台读线程 + queue.Queue 交接原始字节 ==")
    后端 = 造后端()
    set_serial_backend(后端)
    sa = SerialAssistant()
    sa.open("COM7")
    ser = 后端.last["ser"]
    ser.inbox.extend("7,8.5\n".encode("utf-8"))
    事件 = 收事件(sa, "7,8.5", 超时=2.0)
    check("后台线程把串口读到的字节交给解析管线（事件里能看到原文）",
          any(e.get("text") == "7,8.5" for e in 事件), f"{事件!r}")
    check("后台线程读到的数据进了滑窗",
          [7.0, 8.5] in sa.snapshot()["points"], f"{sa.snapshot()['points']!r}")

    ser.读时抛错 = 假串口异常("device reports readiness to read but returned no data")
    出错事件 = 收事件(sa, "读取中断", 超时=2.0)
    check("读串口出错时给出 error 事件而不是崩线程",
          any(e.get("kind") == "error" and "读取中断" in e.get("text", "") for e in 出错事件),
          f"{出错事件!r}")
    关闭消息 = sa.close()
    check("close() 返回人话消息且 is_open 变成 False",
          "已关闭" in 关闭消息 and sa.is_open is False, 关闭消息)
    check("重复 close() 不抛异常", isinstance(sa.close(), str))

    print("\n== 4. 发送（文本 / HEX / 未打开 / 非法 HEX）==")
    后端 = 造后端()
    set_serial_backend(后端)
    sa = SerialAssistant()
    check("未打开就发送 → 人话错误（含「未打开」）",
          sa.send("hi").startswith("[错误]") and "未打开" in sa.send("hi"), sa.send("hi"))
    check("未打开就读取 → 人话错误（含「未打开」）",
          sa.read_once().startswith("[错误]") and "未打开" in sa.read_once(), sa.read_once())
    sa.open("COM7")
    ser = 后端.last["ser"]
    check("文本发送按 UTF-8 写出正确字节",
          "已发送" in sa.send("hello") and bytes(ser.收到的写入) == b"hello",
          f"{bytes(ser.收到的写入)!r}")
    ser.收到的写入.clear()
    check("HEX 发送「AA 55 01」→ 0xAA 0x55 0x01",
          "已发送" in sa.send("AA 55 01", hex_mode=True)
          and bytes(ser.收到的写入) == b"\xaa\x55\x01", f"{bytes(ser.收到的写入)!r}")
    ser.收到的写入.clear()
    check("HEX 连续写法「AA5501」也能发",
          sa.send("AA5501", hex_mode=True).startswith("已发送")
          and bytes(ser.收到的写入) == b"\xaa\x55\x01", f"{bytes(ser.收到的写入)!r}")
    ser.收到的写入.clear()
    HEX错误 = sa.send("ZZ", hex_mode=True)
    check("非法 HEX 给人话错误、且一个字节都没发出去",
           HEX错误.startswith("[错误]") and "HEX" in HEX错误 and len(ser.收到的写入) == 0, HEX错误)
    check("HEX 位数不对（ABC）也给人话错误",
          sa.send("ABC", hex_mode=True).startswith("[错误]"), sa.send("ABC", hex_mode=True))
    check("发送空内容给人话错误", sa.send("").startswith("[错误]"), sa.send(""))
    check("read_once() 没数据时返回人话说明（不是错误）",
          "没读到" in sa.read_once(), sa.read_once())
    sa.close()

    print("\n== 5. 解析工具：decode_bytes / parse_hex / parse_numbers ==")
    check("decode_bytes 正常 UTF-8", decode_bytes("温度=25.5".encode("utf-8")) == "温度=25.5")
    check("decode_bytes 回退 GBK（UTF-8 解不开的字节）",
          decode_bytes("温度=25.5".encode("gbk")) == "温度=25.5",
          repr(decode_bytes("温度=25.5".encode("gbk"))))
    check("decode_bytes 两种编码都失败时用替换字符而不是抛异常",
          decode_bytes(b"\xff\xfe") == "\ufffd\ufffd", repr(decode_bytes(b"\xff\xfe")))
    check("parse_hex 能解析「AA 55 01」", parse_hex("AA 55 01")[0] == b"\xaa\x55\x01")
    check("parse_hex 能解析「AA5501」", parse_hex("AA5501")[0] == b"\xaa\x55\x01")
    check("parse_hex 能解析「0xAA,0x55」", parse_hex("0xAA,0x55")[0] == b"\xaa\x55")
    check("parse_hex 非法输入返回人话错误",
          parse_hex("ZZ")[0] is None and parse_hex("ZZ")[1].startswith("[错误]"))
    check("parse_hex 空串返回人话错误", parse_hex("")[1].startswith("[错误]"))

    sa = SerialAssistant()
    check('parse_numbers("12.5") → 单值时 t 用采样序号', sa.parse_numbers("12.5") == (0.0, 12.5),
          f"{sa.parse_numbers('12.5')!r}")
    check('parse_numbers("12.5,3.4") → 逗号分隔', sa.parse_numbers("12.5,3.4") == (12.5, 3.4))
    check('parse_numbers("12.5 3.4") → 空格分隔', sa.parse_numbers("12.5 3.4") == (12.5, 3.4))
    check('parse_numbers 带完整时间戳的日志（2026-10-02 10:00:00 12.5）',
          (sa.parse_numbers("2026-10-02 10:00:00 12.5") or (None, None))[1] == 12.5,
          f"{sa.parse_numbers('2026-10-02 10:00:00 12.5')!r}")
    check('parse_numbers 带方括号时间戳（[12:00:01.123] 0.5 12.5）',
          sa.parse_numbers("[12:00:01.123] 0.5 12.5") == (0.5, 12.5),
          f"{sa.parse_numbers('[12:00:01.123] 0.5 12.5')!r}")
    check('parse_numbers 支持负数与科学计数（t=-3.5e-2,v=2）',
          sa.parse_numbers("-0.035,2") == (-0.035, 2.0))
    坏行前 = sa.snapshot()["bad_lines"]
    check('parse_numbers("这是坏的") 返回 None',
          sa.parse_numbers("这是坏的") is None)
    check("坏行计入 bad_lines", sa.snapshot()["bad_lines"] == 坏行前 + 1,
          f"{坏行前} → {sa.snapshot()['bad_lines']}")
    check("parse_numbers 空行也返回 None", sa.parse_numbers("") is None)

    print("\n== 6. 按行缓冲：半包 / 粘包 / \\r\\n / 残行 ==")
    sa = SerialAssistant()
    sa.feed_text("12.5,3.4")                       # 半包：还没有换行符
    check("半包（只喂一行的一半）不产生数据点",
          sa.snapshot()["points"] == [] and sa.read_events() == [],
          f"{sa.snapshot()!r}")
    check("残行留在缓冲里等下一段（buffer_bytes > 0）", sa.snapshot()["buffer_bytes"] == 8)
    sa.feed_text("\n")
    快照 = sa.snapshot()
    check("半包补齐换行后解析出 1 个点 (12.5, 3.4)",
          len(快照["points"]) == 1 and abs(快照["points"][0][0] - 12.5) < 1e-9
          and abs(快照["points"][0][1] - 3.4) < 1e-9, f"{快照['points']!r}")
    check("补齐后缓冲被清空", sa.snapshot()["buffer_bytes"] == 0)

    sa = SerialAssistant()
    sa.feed_text("1,1.0\n2,2.0\n3,3.0\n")           # 粘包：一次喂三行
    快照 = sa.snapshot()
    check("粘包（一次喂三行）解析出 3 个点",
          len(快照["points"]) == 3 and 快照["total"] == 3, f"{快照['points']!r}")
    check("粘包产生 3 条 line 事件", len(sa.read_events()) == 3)

    sa = SerialAssistant()
    sa.feed_text("10,1.5\r\n11,2.5\r\n")            # \r\n
    事件 = sa.read_events()
    快照 = sa.snapshot()
    check("\\r\\n 换行也能分行（2 个点）", len(快照["points"]) == 2, f"{快照['points']!r}")
    check("\\r 被去掉，事件文本里没有回车符",
          all("\r" not in e["text"] for e in 事件) and 事件[0]["text"] == "10,1.5",
          f"{事件!r}")
    sa.feed_text("12,3.5\n")
    check("\\r\\n 与 \\n 混用时第 3 个点也正确",
          sa.snapshot()["points"][-1] == [12.0, 3.5], f"{sa.snapshot()['points']!r}")

    print("\n== 7. 编码回退 / 坏行 / 滑窗 / 64KB 保护 ==")
    sa = SerialAssistant()
    sa.feed_bytes("温度,25.5\n".encode("gbk"))
    事件 = sa.read_events()
    check("GBK 字节按 GBK 解码回来（UTF-8 先失败）",
          any("温度" in e["text"] for e in 事件), f"{事件!r}")
    check("GBK 行里的数字照样进滑窗", sa.snapshot()["points"][0][1] == 25.5,
          f"{sa.snapshot()['points']!r}")
    sa.feed_bytes(b"\xff\xfe\n")
    事件 = sa.read_events()
    check("两种编码都解不开时用替换字符（不抛异常）",
          any("\ufffd" in e["text"] for e in 事件), f"{事件!r}")

    sa = SerialAssistant()
    sa.feed_text("乱码没有数字\n1,1.0\n又一行坏数据\n")
    快照 = sa.snapshot()
    check("坏行跳过并计数（2 条坏行）", 快照["bad_lines"] == 2, f"{快照['bad_lines']}")
    check("坏行不影响好数据（total=1，点=(1.0,1.0)）",
          快照["total"] == 1 and 快照["points"] == [[1.0, 1.0]], f"{快照!r}")

    sa = SerialAssistant(max_points=3)
    sa.feed_text("1,1\n2,2\n3,3\n4,4\n5,5\n")
    快照 = sa.snapshot()
    check("滑窗只保留最近 max_points=3 个点",
          [p[1] for p in 快照["points"]] == [3.0, 4.0, 5.0], f"{快照['points']!r}")
    check("滑窗丢最旧被计数（dropped=2）", 快照["dropped"] == 2, f"{快照['dropped']}")
    check("total 仍然统计全部解析成功点数（=5）", 快照["total"] == 5, f"{快照['total']}")

    sa = SerialAssistant()
    sa.feed_text("A" * 70000)                       # 一直不换行，超过 64 KB
    事件 = sa.read_events()
    提示 = [e for e in 事件 if e["kind"] == "error"]
    check("超过 64 KB 仍未换行 → 丢弃并给一次人话提示",
          len(提示) == 1 and "64 KB" in 提示[0]["text"], f"{事件[:2]!r}")
    check("丢弃后缓冲被清空（不会一直吃内存）", sa.snapshot()["buffer_bytes"] == 0)
    sa.feed_text("9,9.5\n")
    快照 = sa.snapshot()
    check("溢出丢弃后仍能正常解析后续数据",
          快照["points"] == [[9.0, 9.5]] and 快照["bad_lines"] == 0, f"{快照!r}")

    print("\n== 8. feed_bytes 与 feed_text 走同一条管线 ==")
    a, b = SerialAssistant(), SerialAssistant()
    a.feed_text("1,1.5\n2,2.5\n")
    b.feed_bytes("1,1.5\n2,2.5\n".encode("utf-8"))
    check("feed_text 与 feed_bytes 的滑窗结果一致",
          a.snapshot()["points"] == b.snapshot()["points"],
          f"{a.snapshot()['points']!r} vs {b.snapshot()['points']!r}")
    check("feed_text 与 feed_bytes 的事件文本一致",
          [e["text"] for e in a.read_events()] == [e["text"] for e in b.read_events()])
    check("feed_text 返回人话消息", a.feed_text("x\n").startswith("已注入"))
    check("feed_bytes 收到非法数据返回人话错误",
          SerialAssistant().feed_bytes(object()).startswith("[错误]"))

    print("\n== 9. read_events 语义（给 SSE 用）==")
    sa = SerialAssistant()
    check("没打开串口时 read_events() 返回空列表（不抛异常）", sa.read_events() == [])
    sa.feed_text("1,1\n2,2\n3,3\n4,4\n5,5\n")
    前两条 = sa.read_events(2)
    check("read_events(max_items) 限制单次条数", len(前两条) == 2, f"{len(前两条)}")
    check("事件形如 {kind, text, ts}", all(
        e.get("kind") in ("line", "error") and "text" in e and "ts" in e for e in 前两条))
    剩余 = sa.read_events(10)
    check("取走即清空，剩下的下次取（3 条）", len(剩余) == 3, f"{len(剩余)}")
    check("再取一次为空（事件是「自上次调用以来」的增量）", sa.read_events() == [])

    print("\n== 10. CSV 落盘：读回真实文件校验格式 ==")
    with tempfile.TemporaryDirectory() as td:
        路径 = Path(td) / "记录.csv"
        sa = SerialAssistant()
        返回 = sa.start_record(str(路径))
        check("start_record() 返回写入路径", 返回 == str(路径), f"{返回!r}")
        check("落盘文件当场就建好了", 路径.exists())
        sa.feed_text("".join(f"{i * 0.01:.4f},{i * 0.5:.4f}\n" for i in range(10)))
        check("记录中 snapshot() 能看出正在记录", sa.snapshot()["recording"] == str(路径))
        停止 = sa.stop_record()
        check("stop_record() 返回同一个路径", 停止 == str(路径), f"{停止!r}")
        行 = 路径.read_text(encoding="utf-8").splitlines()
        数据行 = [x for x in 行 if x and not x.startswith("#")]
        check("CSV 表头是「ISO时间,时间,幅值」", 数据行[0] == "ISO时间,时间,幅值", 数据行[0])
        check("CSV 有 10 行数据（与注入的 10 个点一致）", len(数据行) == 11, f"{len(数据行) - 1}")
        check("CSV 每行都是 3 列", all(len(x.split(",")) == 3 for x in 数据行[1:]),
              f"{数据行[1]!r}")
        check("每条数据都带可解析的 ISO 时间戳",
              all(datetime.fromisoformat(x.split(",")[0]) for x in 数据行[1:]),
              f"{数据行[1]!r}")
        首行数值 = 数据行[1].split(",")
        check("第一行数值正确（时间 0，幅值 0）",
              abs(float(首行数值[1])) < 1e-9 and abs(float(首行数值[2])) < 1e-9, f"{首行数值!r}")
        check("第一行是记录开始时就写下的表头（注释 3 行 + 表头 1 行）",
              len([x for x in 行 if x.startswith("#")]) == 3, f"{行[:5]!r}")

        sa = SerialAssistant()
        sa.start_record(str(路径))                  # 同一个文件再记一次 → 追加而不是覆盖
        sa.feed_text("1,1\n2,2\n")
        sa.stop_record()
        行2 = 路径.read_text(encoding="utf-8").splitlines()
        数据行2 = [x for x in 行2 if x and not x.startswith("#")]
        check("第二次记录是追加写（表头不重复，数据变 13 行）",
              len(数据行2) == 13 and 数据行2[0] == "ISO时间,时间,幅值", f"{len(数据行2)}")
        sa3 = SerialAssistant()
        check("没在记录时 stop_record() 返回人话错误",
              sa3.stop_record().startswith("[错误]"), sa3.stop_record())

        print("\n== 11. 一键送调参助手 analyze() ==")
        sa = SerialAssistant()
        sa.feed_text(阶跃文本(3000))                 # 6 秒数据，足够覆盖峰值与调节过程
        快照 = sa.snapshot()
        check("喂出 3000 个点用于分析", 快照["total"] == 3000, f"{快照['total']}")
        报告 = sa.analyze(target=1.0)
        check("analyze() 返回调参助手的现成报告（不是错误）",
              isinstance(报告, str) and not 报告.startswith("[错误]"), 报告[:200])
        check("报告里有超调量字段", "超调量" in 报告)
        check("报告里有峰值时间 / 上升时间 / 调节时间字段",
              "峰值时间" in 报告 and "上升时间" in 报告 and "调节时间" in 报告)
        check("报告里带规则化建议", "建议" in 报告)
        超调稳态 = re.search(r"超调量 σ%（相对稳态值）\*\* \| ([\d.]+)", 报告)
        超调 = re.search(r"超调量（相对目标值）\*\* \| ([\d.]+)", 报告)
        峰时 = re.search(r"峰值时间 t_p\*\* \| ([\d.]+)", 报告)
        稳态 = re.search(r"稳态误差\*\* \| ([\d.]+)", 报告)
        check("报告同时给出两个超调量口径", 超调 is not None and 超调稳态 is not None)
        check("超调量（相对目标值）与解析解一致（理论 16.30%，容差 ±2）",
              超调 is not None and abs(float(超调.group(1)) - 16.30) < 2.0,
              f"{超调.group(1) if 超调 else '没取到'}")
        check("峰值时间与解析解一致（理论 1.814 s，容差 ±0.15）",
              峰时 is not None and abs(float(峰时.group(1)) - 1.814) < 0.15,
              f"{峰时.group(1) if 峰时 else '没取到'}")
        check("稳态误差被算出（target=1 时 ≈0，容差 0.05）",
              稳态 is not None and float(稳态.group(1)) < 0.05,
              f"{稳态.group(1) if 稳态 else '没取到'}")
        check("analyze() 不抛异常且返回字符串", isinstance(sa.analyze(), str))
        太少的 = SerialAssistant()
        太少的.feed_text("1,1\n2,2\n3,3\n")
        check("数据点太少时 analyze() 给人话错误",
              太少的.analyze().startswith("[错误]"), 太少的.analyze())

    print("\n== 12. 对外接口一律不抛异常 ==")
    sa = SerialAssistant()
    全部正常 = True
    细节 = ""
    for 名称, 调用 in [
        ("list_ports", lambda: list_ports()),
        ("open(空端口)", lambda: sa.open(None)),
        ("close", lambda: sa.close()),
        ("send", lambda: sa.send(None)),
        ("send(hex)", lambda: sa.send("ZZ", hex_mode=True)),
        ("read_once", lambda: sa.read_once(-5)),
        ("read_events", lambda: sa.read_events(-1)),
        ("snapshot", lambda: sa.snapshot()),
        ("feed_text", lambda: sa.feed_text(None)),
        ("feed_bytes", lambda: sa.feed_bytes(None)),
        ("parse_numbers", lambda: sa.parse_numbers(None)),
        ("stop_record", lambda: sa.stop_record()),
        ("analyze", lambda: sa.analyze()),
    ]:
        try:
            调用()
        except Exception as e:
            全部正常 = False
            细节 = f"{名称} 抛了 {type(e).__name__}: {e}"
            break
    check("13 个对外调用在极端入参下都不抛异常", 全部正常, 细节)


    print("\n== 合理性守卫（防丢字节/半行拼接撮出的假点）==")
    # 背景：真机长跑时曾出现"峰值 11.37、超调 1045%"，而数据里根本没这个值 ——
    # 解析策略是"取一行里最后两个数字"，一旦丢字节就可能把 1.137 撮成 11.37。
    import math as _m
    _zeta, _wn = 0.5, 2.0
    _wd = _wn * _m.sqrt(1 - _zeta ** 2)
    _lines = []
    for _i in range(150):
        _t = _i * 0.04
        _y = 1 - _m.exp(-_zeta * _wn * _t) * (_m.cos(_wd * _t) + (_zeta * _wn / _wd) * _m.sin(_wd * _t))
        _lines.append(f"{_t:.3f},{_y:.5f}")
    _正文 = "\n".join(_lines) + "\n"          # ★ 末尾要有换行，否则最后一行不算"收到完整一行"

    sa = SerialAssistant()
    sa.feed_text(_正文)
    干净 = sa.snapshot()
    check("干净数据：150 点、零拦截", 干净["total"] == 150 and 干净["outliers"] == 0,
          f"total={干净['total']} outliers={干净['outliers']}")
    报告0 = sa.analyze(target=1.0)
    check("干净数据能正常出报告（含两个超调口径）",
          "相对稳态值" in 报告0 and "相对目标值" in 报告0)

    sa2 = SerialAssistant()
    sa2.feed_text("\n".join(_lines[:80]) + "\n")
    sa2.feed_text("5.000,11.37\n")        # ★ 假点（丢字节撮出来的）
    sa2.feed_text("\n".join(_lines[80:]) + "\n")
    拦 = sa2.snapshot()
    check("★ 假点被拦下：仍是 150 个真点、拦截数=1",
         拦["total"] == 150 and 拦["outliers"] == 1,
          f"total={拦['total']} outliers={拦['outliers']}")
    check("假点没进曲线（所有幅值都在合理范围）", all(abs(v) < 2 for _, v in 拦["points"]))
    _ev = [e for e in sa2.read_events(999) if e.get("kind") == "error"]
    check("拦下时给了人话提示（含量程与已拦截次数）",
          any("疑似坏点" in e.get("text", "") and "11.37" in e.get("text", "") for e in _ev),
          str(_ev[:1])[:160])
    报告2 = sa2.analyze(target=1.0)
    check("★ 有假点时报告依然正确（不再出现 11.37 / 1045%）",
          "11.37" not in 报告2 and "1045" not in 报告2)

    sa3 = SerialAssistant(outlier_guard=False)
    sa3.feed_text("\n".join(_lines[:80]) + "\n")
    sa3.feed_text("5.000,11.37\n")
    sa3.feed_text("\n".join(_lines[80:]) + "\n")
    check("守卫可以关掉（关掉后假点被采信，共 151 点）",
          sa3.snapshot()["total"] == 151 and sa3.snapshot()["outliers"] == 0,
          f"total={sa3.snapshot()['total']}")

    sa4 = SerialAssistant()
    sa4.feed_text("0.000,0.0\n0.040,100.0\n")
    check("起始段不做判定（避免误杀真实的大幅跳变）", sa4.snapshot()["outliers"] == 0)

    print("\n== open() 是否把 DTR/RTS 设成实测正确的状态 ==")
    _旧后端 = set_serial_backend(造后端())
    try:
        sa5 = SerialAssistant()
        sa5.open("COM7")
        _ser = getattr(sa5, "_ser", None)
        check("★ open() 把 RTS 与 DTR 都置为 True（实测：否则芯片不跑程序、一个字节都收不到）",
              getattr(_ser, "rts", None) is True and getattr(_ser, "dtr", None) is True,
              f"rts={getattr(_ser, 'rts', None)} dtr={getattr(_ser, 'dtr', None)}")
        sa5.close()
    finally:
        set_serial_backend(_旧后端)

    print("\n== DTR/RTS 控制（复位 / 进 BootLoader）==")
    class 记录电平串口(假串口):
        """记录 dtr/rts 被设置的顺序，用来验证复位时序。"""
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            object.__setattr__(self, "电平记录", [])
        def __setattr__(self, name, value):
            if name in ("dtr", "rts") and "电平记录" in self.__dict__:
                记录 = object.__getattribute__(self, "电平记录")
                记录.append((name, bool(value)))
            super().__setattr__(name, value)

    _后端 = 造后端()
    _后端.Serial = 记录电平串口
    _旧 = set_serial_backend(_后端)
    try:
        a = SerialAssistant()
        check("未打开串口时设电平 → 人话错误",
              a.set_lines(dtr=True).startswith("[错误]") and "打开" in a.set_lines(dtr=True))
        check("未打开串口时复位 → 人话错误",
              a.pulse_reset("dtr_low").startswith("[错误]"))
        check("不认识的预设名 → 报错并列出可选",
              a.pulse_reset("乱写").startswith("[错误]") and "dtr_low" in a.pulse_reset("乱写"))
        check("未打开串口时自动探测不炸（返回一条 error）",
              len(a.probe_presets()) == 1 and a.probe_presets()[0]["verdict"] == "error")

        a.open("COM7")
        ser = a._ser
        ser.电平记录.clear()
        msg = a.set_lines(dtr=True, rts=False)
        check("set_lines 只动指定的线、并在提示里说清电平",
              ser.电平记录 == [("dtr", True), ("rts", False)] and "DTR=高" in msg and "RTS=低" in msg,
              f"记录={ser.电平记录} msg={msg[:60]}")
        check("line_state 反映当前电平", a.line_state() == {"dtr": True, "rts": False},
              str(a.line_state()))

        ser.电平记录.clear()
        a.pulse_reset("dtr_low_rts_high_boot", hold=0, boot_wait=0)
        check("★ 复位时序正确：先设 Boot 选择线 → 拉复位线 → 放开复位线",
              ser.电平记录 == [("rts", True), ("dtr", False), ("dtr", True)],
              str(ser.电平记录))
        ser.电平记录.clear()
        a.pulse_reset("rts_high", hold=0, boot_wait=0)
        check("不用另一条线的预设：只动复位线（先拉到有效电平再放开）",
              ser.电平记录 == [("rts", True), ("rts", False)], str(ser.电平记录))

        ser.电平记录.clear()
        res = a.probe_presets(warmup=0, window=0)
        check("自动探测覆盖 12 种预设且每项都有判定",
              len(res) == 12 and all(set(x) >= {"preset", "name", "verdict"} for x in res),
              f"{len(res)} 项")
        check("★ 探测时每种都用「两者都高」做基线（否则上一种的电平会污染判定）",
              len([1 for k, v in ser.电平记录 if (k, v) == ("dtr", True)]) >= 12)
        a.close()
    finally:
        set_serial_backend(_旧)
    set_serial_backend(None)
    print("\n" + "=" * 52)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("全部通过（离线假串口，没有使用任何真实串口设备与网络）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
