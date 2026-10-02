#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演示模式：装一个"模拟设备"，不用真板子也能看到完整效果。

放在包里（而不是 scripts/）的原因：**打包成 exe 后也要能用** ——
试用包承诺"不用装 Python"，所以演示模式必须跟着 exe 走。

怎么打开：设环境变量 `CONTEST_DEMO=1` 再启动（试用包里的「演示模式.bat」就是干这个的），
或者直接调 `安装()`。

模拟设备的行为：看到 `KP=<数值>` 就吐一段对应参数的阶跃响应 ——
参数越大越快但越冲（ωn 随参数增大、ζ 随之减小），也就是真实的 PID 权衡。
★ 除了最底层的串口读写被替换，**其余全部走真实代码路径**（解析、指标、推荐都一样）。
"""
from __future__ import annotations

import math
import threading
import time

演示串口名 = "COM_DEMO"


class 假串口:
    """尽量模仿 pyserial 的 Serial：write 收数据、read 吐数据。"""

    def __init__(self, *a, **kw):
        self.收到的写入 = bytearray()
        self.待读出 = bytearray()
        self.is_open = True
        self.port = kw.get("port", 演示串口名)
        self.timeout = kw.get("timeout", 0.1)
        self._模拟器 = None

    @property
    def in_waiting(self):
        return len(self.待读出)

    def write(self, data):
        self.收到的写入.extend(bytes(data))
        self._启动模拟器()
        return len(data)

    def read(self, size=1):
        out = bytes(self.待读出)[:size]
        del self.待读出[:len(out)]
        return out

    def read_all(self):
        out = bytes(self.待读出)
        self.待读出.clear()
        return out

    def reset_input_buffer(self):
        self.待读出.clear()

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        self.is_open = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ── 内部：盯住写入，按命令生成数据 ──
    def _启动模拟器(self):
        if self._模拟器 is not None and self._模拟器.is_alive():
            return
        self._模拟器 = threading.Thread(target=self._模拟, daemon=True)
        self._模拟器.start()

    def _模拟(self):
        已处理 = 0
        while self.is_open:
            data = bytes(self.收到的写入)
            if len(data) > 已处理:
                新 = data[已处理:].decode("utf-8", "replace")
                已处理 = len(data)
                for line in 新.replace("\r", "\n").split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("KP="):
                        try:
                            值 = float(line.split("=", 1)[1])
                        except ValueError:
                            continue
                        threading.Thread(target=self._吐数据, args=(值,), daemon=True).start()
                    else:
                        self.待读出.extend(f"收到：{line}\r\n".encode("utf-8"))
            time.sleep(0.02)

    def _吐数据(self, kp: float, 目标: float = 1.0, dt: float = 0.02, 点数: int = 300):
        time.sleep(0.15)                       # 假装参数生效要一点时间
        wn = 1.0 + 1.5 * kp
        zeta = max(0.1, 1.0 - 0.25 * kp)
        wd = wn * math.sqrt(max(1e-9, 1 - zeta ** 2))
        for i in range(点数):
            t = i * dt
            y = 目标 * (1 - math.exp(-zeta * wn * t) *
                        (math.cos(wd * t) + (zeta * wn / wd) * math.sin(wd * t)))
            self.待读出.extend(f"{t:.3f},{y:.5f}\r\n".encode("utf-8"))
            time.sleep(0.001)                  # 按串口节奏吐，别一瞬间全给


class _端口:
    """一个串口设备条目（模仿 pyserial 的 ListPortInfo）。"""

    def __init__(self):
        self.device = 演示串口名
        self.description = "演示用虚拟串口（不是真设备）"
        self.hwid = "DEMO"


class _列端口:
    """模仿 `serial.tools.list_ports` 这个**模块**（有 comports 方法）。

    ★ 别写成函数：调用方是 `lister = ser.list_ports; lister.comports()`，
      函数对象上没有 comports ✗；也别在类体里引用自己的类名（NameError ✗）。
    """

    @staticmethod
    def comports():
        return [_端口()]


class _假后端:
    """长得像 `serial` 模块，供 serial_assistant 延迟导入。"""

    Serial = 假串口
    SerialException = OSError
    EIGHTBITS, SEVENBITS = 8, 7
    PARITY_NONE, PARITY_EVEN, PARITY_ODD = "N", "E", "O"
    STOPBITS_ONE, STOPBITS_TWO = 1, 2
    list_ports = _列端口


def 造后端():
    """返回这个假后端（测试和演示都用它）。"""
    return _假后端


def 已开启() -> bool:
    import os
    return bool(os.environ.get("CONTEST_DEMO"))


def 安装() -> str:
    """把串口后端换成模拟设备。返回一句人话说明。"""
    try:
        from .serial_assistant import set_serial_backend
        set_serial_backend(_假后端)
        return (f"演示模式已开启：串口列表里会出现 {演示串口名}（虚拟设备，不用真板子）。"
                f"试试「参数自动扫描」：命令填 KP={{value}}，值填 0.5:2.0:0.25")
    except Exception as e:
        return f"[错误] 演示模式没能开启：{type(e).__name__}: {e}"
