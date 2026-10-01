# -*- coding: utf-8 -*-
"""串口助手：**纯逻辑 + 薄 IO** 的串口收发与数据解析模块。

设计要点（为什么这么写）：
  1. **惰性导入 pyserial**：模块本身不依赖 pyserial。没装时照样能 import、能跑离线测试，
     只有"真去开串口"的功能返回人话错误（`[错误] 未安装 pyserial ...`）。
  2. **解析管线与 IO 解耦**：真实串口读到的字节先进 `queue.Queue`，再喂给
     「字节缓冲 → 按行切分 → 容错解码 → 取数 → 滑窗」这条管线；
     `feed_bytes()` / `feed_text()`（假串口注入）走的是**同一条**管线 ——
     所以没有硬件也能演示、也能被自动化测试覆盖。
  3. **绝不抛异常给调用方**：所有对外方法返回结果，或者以 `[错误]` 开头的中文错误串
     （项目约定）。唯一例外是 `start_record()/stop_record()` 成功时返回文件路径字符串。
  4. 读串口在**后台线程**里做，主线程/SSE 线程只从 `read_events()` 取事件。

典型用法（集成到网页端）::

    from contest_workbench.serial_assistant import SerialAssistant, list_ports
    sa = SerialAssistant()
    list_ports()                       # 下拉框里列端口
    sa.open("COM3", 115200)            # 返回人话消息或 [错误] 开头的错误
    sa.start_record()                  # 边收边落盘（CSV，可直接送调参助手）
    ...
    sa.read_events(200)                # SSE：自上次调用以来的新事件（已取走即清空）
    sa.snapshot()                      # 图表用：最近的采样点 + 统计
    sa.analyze(target=1.0)             # 一键送调参助手，返回现成的 Markdown 报告
"""
from __future__ import annotations

import queue
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

__all__ = [
    "SerialAssistant",
    "list_ports",
    "serial_available",
    "set_serial_backend",
    "decode_bytes",
    "parse_hex",
    "MAX_LINE_BUFFER",
]

# 单行（换行符之前）最多累计这么多字节，超过就丢弃并提示 —— 防止"一直不换行"把内存吃满
MAX_LINE_BUFFER = 64 * 1024

# 解码失败的替代字符（UTF-8 与 GBK 都解不开时用）
REPLACEMENT = "\ufffd"

_MISSING_SERIAL_MSG = (
    "[错误] 未安装 pyserial，无法操作真实串口。安装方法：在命令行执行 "
    "pip install pyserial（国内镜像：pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pyserial）。"
    "没有硬件时可以用 feed_text()/feed_bytes() 注入假数据做演示与测试。"
)

# ── 惰性导入 pyserial：模块级缓存，只尝试一次 ────────────────────────────────
_SERIAL_MOD = None          # 真实的 pyserial 模块（或测试注入的假后端）
_SERIAL_TRIED = False
_SERIAL_OVERRIDE = None     # 测试/集成用：注入假后端，或字符串 "missing"
_SERIAL_LOCK = threading.Lock()


def set_serial_backend(backend):
    """注入串口后端（**测试与离线演示用**），参数取三种值：

    - 一个"像 pyserial 那样"的对象：需要 `Serial(...)`、`SerialException`、
      可选 `list_ports.comports()`，以及常量如 `EIGHTBITS` / `PARITY_NONE` / `STOPBITS_ONE`；
    - 字符串 `"missing"`：强制模拟"没装 pyserial"，用来验证容错路径；
    - `None`：恢复正常的惰性导入。

    返回上一次的后端（便于测试收尾还原）。
    """
    global _SERIAL_OVERRIDE
    old = _SERIAL_OVERRIDE
    _SERIAL_OVERRIDE = backend
    return old


def _get_serial():
    """取得串口后端模块；拿不到返回 None（不抛异常）。"""
    global _SERIAL_MOD, _SERIAL_TRIED
    if _SERIAL_OVERRIDE is not None:
        if _SERIAL_OVERRIDE == "missing":
            return None
        return _SERIAL_OVERRIDE
    with _SERIAL_LOCK:
        if not _SERIAL_TRIED:
            _SERIAL_TRIED = True
            try:
                import serial as _s        # 惰性导入：没装也不影响本模块被 import
                _SERIAL_MOD = _s
            except Exception:
                _SERIAL_MOD = None
    return _SERIAL_MOD


def serial_available() -> bool:
    """当前环境能不能真开串口（即 pyserial 是否可用）。"""
    return _get_serial() is not None


def list_ports() -> list[dict]:
    """列出当前可用串口，返回 `[{device, description, hwid}, ...]`。

    没装 pyserial、或系统里没有串口设备时返回**空列表**，不抛异常。
    """
    ser = _get_serial()
    if ser is None:
        return []
    try:
        lister = getattr(ser, "list_ports", None)
        if lister is None:                 # 真 pyserial：串口枚举在 serial.tools.list_ports
            from serial.tools import list_ports as lister     # type: ignore
        out = []
        for p in lister.comports():
            out.append({
                "device": str(getattr(p, "device", "") or ""),
                "description": str(getattr(p, "description", "") or ""),
                "hwid": str(getattr(p, "hwid", "") or ""),
            })
        return out
    except Exception:
        return []


# ── 解析工具（纯函数，便于单独测试）─────────────────────────────────────────
# 与 tools/tuning.py 同口径的数字正则：支持 12.5 / -3 / 1.5e-3
_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")

# 行首的时间戳前缀：直接"抠掉"，免得日期/时分秒被当成数据
_TS_PATTERNS = (
    re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?"),
    re.compile(r"\[?\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\]?"),
)

_HEX_TOKEN = re.compile(r"^[0-9A-Fa-f]{1,2}$")


def decode_bytes(data: bytes) -> str:
    """容错解码：先 UTF-8 → 失败回退 GBK → 再失败用 `errors='replace'`（绝不抛异常）。"""
    if not data:
        return ""
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def parse_hex(text: str) -> tuple[bytes | None, str]:
    """把 `"AA 55 01"` / `"AA5501"` / `"0xAA,0x55"` 变成字节。

    返回 `(data, "")` 或 `(None, "[错误] ...")`，不抛异常。
    """
    raw = str(text or "").strip()
    if not raw:
        return None, '[错误] HEX 内容是空的，正确写法如 "AA 55 01" 或 "AA5501"'
    parts = [p for p in re.split(r"[\s,;:_\-]+", raw) if p]
    tokens = []
    for p in parts:
        if p[:2].lower() == "0x" and len(p) > 2:
            p = p[2:]
        if len(p) > 2 and len(p) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in p):
            tokens.extend(p[i:i + 2] for i in range(0, len(p), 2))   # 连续写法 AA5501
        else:
            tokens.append(p)
    bad = [t for t in tokens if not _HEX_TOKEN.match(t)]
    if bad:
        return None, (f'[错误] HEX 格式不正确："{bad[0]}" 不是 1~2 位十六进制数字。'
                      '正确写法如 "AA 55 01" 或 "AA5501"')
    if not tokens:
        return None, '[错误] HEX 内容是空的，正确写法如 "AA 55 01" 或 "AA5501"'
    try:
        return bytes(int(t, 16) for t in tokens), ""
    except Exception as e:                                   # pragma: no cover - 兜底
        return None, f"[错误] HEX 解析失败：{type(e).__name__}: {e}"


def _strip_timestamps(line: str) -> str:
    s = line
    for p in _TS_PATTERNS:
        s = p.sub(" ", s)
    return s


def _fmt_num(x: float) -> str:
    """写 CSV 用的紧凑数字格式（保留有效位，又不会写出 nan/inf 那种没法解析的东西）。"""
    try:
        v = float(x)
    except Exception:
        return "0"
    if v != v or v in (float("inf"), float("-inf")):          # nan / inf
        return "0"
    return f"{v:.9g}"


class SerialAssistant:
    """串口助手（纯逻辑 + 薄 IO）。所有对外方法都不抛异常，只返回结果或人话错误串。"""

    #: 字节数/校验/停止位的取值 → pyserial 常量名
    _BS_NAMES = {5: "FIVEBITS", 6: "SIXBITS", 7: "SEVENBITS", 8: "EIGHTBITS"}
    _PAR_NAMES = {"N": "PARITY_NONE", "E": "PARITY_EVEN", "O": "PARITY_ODD",
                  "M": "PARITY_MARK", "S": "PARITY_SPACE"}
    _SB_NAMES = {1: "STOPBITS_ONE", 1.5: "STOPBITS_ONE_POINT_FIVE", 2: "STOPBITS_TWO"}

    def __init__(self, max_points: int = 5000, max_buffer: int = MAX_LINE_BUFFER,
                 outlier_guard: bool = True, outlier_factor: float = 8.0,
                 outlier_min_samples: int = 12):
        try:
            max_points = int(max_points)
        except Exception:
            max_points = 5000
        self.max_points = max(1, max_points)
        try:
            self.max_buffer = max(1024, int(max_buffer))
        except Exception:
            self.max_buffer = MAX_LINE_BUFFER

        self._lock = threading.RLock()
        self._ser = None
        self._port = ""
        self._baudrate = 0
        self._thread: threading.Thread | None = None
        self._stop_flag = threading.Event()

        self._raw_queue: queue.Queue = queue.Queue()          # 读出线程 → 解析管线
        self._buf = bytearray()                               # 行缓冲（半包/粘包都在这儿解决）
        self._points: deque = deque(maxlen=self.max_points)    # 滑窗
        self._events: deque = deque(maxlen=4000)               # 待取走的事件（给 SSE）
        self._total = 0                                        # 累计解析成功的点数
        self._bad_lines = 0                                    # 坏行计数
        # ★ 合理性守卫：串口是字节流，发生丢字节/半行拼接时可能撮出一个假数字
        #   （实测：1.137 被撮成 11.37 → 峰值 11.37、超调量 1045%，把报告毁掉）。
        #   规则：已经积累了足够样本后，若新点的幅值与当前量程相差超过 outlier_factor 倍，
        #   就判为坏点**忽略并计数**（可在界面上看到，也能关掉）。
        self.outlier_guard = bool(outlier_guard)
        self.outlier_factor = max(2.0, float(outlier_factor))
        self.outlier_min_samples = max(2, int(outlier_min_samples))
        self._outliers = 0                                      # 被守卫拦下的坏点数
        self._dropped = 0                                      # 被滑窗挤掉的最旧点数

        self._rec_file = None
        self._rec_path = ""
        self.record_rows = 0                                   # 已写入记录文件的行数

    # ── 端口与状态 ────────────────────────────────────────────────────────
    @property
    def is_open(self) -> bool:
        ser = self._ser
        if ser is None:
            return False
        try:
            return bool(getattr(ser, "is_open", False))
        except Exception:
            return False

    @property
    def port(self) -> str:
        return self._port

    @property
    def baudrate(self) -> int:
        return self._baudrate

    @property
    def record_path(self) -> str:
        """正在记录时的文件路径（没有记录则为空串）。"""
        return self._rec_path

    # ── 打开 / 关闭 ──────────────────────────────────────────────────────
    def open(self, port, baudrate: int = 115200, bytesize: int = 8,
             parity: str = "N", stopbits=1, timeout: float = 0.1) -> str:
        """打开串口。成功返回人话消息，失败返回 `[错误]` 开头的人话错误（区分原因）。"""
        # ① 先做参数合法性检查（不依赖 pyserial，没装也能给出准确原因）
        name = str(port or "").strip()
        if not name:
            return '[错误] 端口号不能为空。Windows 形如 "COM3"，Linux 形如 "/dev/ttyUSB0"'
        try:
            baud = int(baudrate)
        except Exception:
            return f"[错误] 波特率不合法：{baudrate!r} 不是整数（常用 9600 / 115200）"
        if baud <= 0:
            return f"[错误] 波特率不合法：{baud} 必须大于 0（常用 9600 / 115200）"
        try:
            bs_key = int(bytesize)
        except Exception:
            bs_key = -1
        if bs_key not in self._BS_NAMES:
            return f"[错误] 数据位不合法：{bytesize!r}，只能是 5 / 6 / 7 / 8"
        par_key = str(parity or "").strip().upper()[:1]
        if par_key not in self._PAR_NAMES:
            return f'[错误] 校验位不合法：{parity!r}，只能是 N（无）/ E（偶）/ O（奇）/ M / S'
        try:
            sb_key = float(stopbits)
        except Exception:
            sb_key = -1.0
        if sb_key not in self._SB_NAMES:
            return f"[错误] 停止位不合法：{stopbits!r}，只能是 1 / 1.5 / 2"
        try:
            tmo = float(timeout)
        except Exception:
            return f"[错误] 超时时间不合法：{timeout!r} 不是数字（单位：秒）"
        if tmo < 0:
            return f"[错误] 超时时间不合法：{tmo} 不能为负数（单位：秒）"

        # ② 后端可用性
        ser_mod = _get_serial()
        if ser_mod is None:
            return _MISSING_SERIAL_MSG
        if self.is_open:
            return f"[错误] 串口已经打开了（{self._port}），请先 close() 再打开新的端口"

        def const(attr: str, fallback):
            return getattr(ser_mod, attr, fallback)

        # ③ 真开：把各种失败翻译成人话
        try:
            ser = ser_mod.Serial(port=name, baudrate=baud, bytesize=const(self._BS_NAMES[bs_key], bs_key),
                                 parity=const(self._PAR_NAMES[par_key], par_key),
                                 stopbits=const(self._SB_NAMES[sb_key], sb_key), timeout=tmo)
        except Exception as e:
            return self._explain_open_error(name, e)

        # ★★★ 实测（正点原子探索者 V3 + CH340 一键下载电路，2026-10-02）：
        #   pyserial 的 DTR/RTS 极性与官方手册的**相反** —— 只有 **两者都为 True**
        #   时芯片才运行用户程序；任意一个为 False 都会把芯片按住/不启动，
        #   现象是"串口能正常打开、但一个字节都收不到"。
        #   （4 组对照实验 RTS×DTR 全组合，只有「高·高」能收到周期性打印。）
        #   进 BootLoader 时是另一套时序，见 stm32_flash.py。
        for line_name in ("rts", "dtr"):
            try:
                setattr(ser, line_name, True)
            except Exception:
                pass                              # 假后端/平台不支持就算了

        with self._lock:
            self._ser = ser
            self._port = name
            self._baudrate = baud
            self._stop_flag.clear()
        thread = threading.Thread(target=self._reader_loop, name="串口读取线程", daemon=True)
        self._thread = thread
        thread.start()
        return (f"已打开串口 {name}（波特率 {baud}，{bs_key}{par_key}{sb_key:g}，超时 {tmo:g} 秒）。"
                f"收到的数据会自动按行解析，可在界面上查看或送调参助手。")

    @staticmethod
    def _explain_open_error(port: str, err: Exception) -> str:
        """把 pyserial 的英文异常翻译成能照着做的中文提示。"""
        msg = str(err) or type(err).__name__
        low = msg.lower()
        denied = ("拒绝访问" in msg or "access is denied" in low or "permission" in low
                  or "occup" in low or "busy" in low or isinstance(err, PermissionError))
        notfound = (isinstance(err, FileNotFoundError) or "could not open port" in low
                    or "filenotfound" in low or "no such file" in low or "找不到" in msg
                    or "不存在" in msg)
        if denied:
            return (f"[错误] 端口 {port} 已被其他程序占用（常见占用方：另一个串口助手、"
                    f"Arduino IDE 串口监视器、Keil/STM32CubeProgrammer 的下载口、蓝牙虚拟串口）。"
                    f"请关掉占用它的程序，或换一个端口再试。原始信息：{msg}")
        if notfound:
            return (f"[错误] 端口 {port} 不存在或没插好（可用 list_ports() 查看当前可用端口；"
                    f"也可能是 USB 转串口驱动没装）。原始信息：{msg}")
        if isinstance(err, ValueError) or "invalid" in low or "not supported" in low or "参数" in msg:
            return f"[错误] 串口参数不合法：{msg}"
        return f"[错误] 打开端口 {port} 失败：{msg}"

    def close(self) -> str:
        """关闭串口（可重复调用，不抛异常）。"""
        with self._lock:
            ser = self._ser
            port = self._port
            self._stop_flag.set()
        if ser is None:
            return "串口本来就没有打开，无需关闭"
        try:
            ser.close()
        except Exception as e:
            return f"[错误] 关闭串口 {port} 时出错：{type(e).__name__}: {e}"
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            try:
                thread.join(timeout=1.5)
            except Exception:
                pass
        self._pump()                     # 把关之前最后一小段数据也解析掉
        with self._lock:
            self._ser = None
            self._thread = None
        return f"已关闭串口 {port}，共解析 {self._total} 个数据点"

    # ── 后台读线程 ────────────────────────────────────────────────────────
    def _reader_loop(self) -> None:
        """后台读线程：只负责"读出字节 → 丢进队列"，解析交给 _pump()。"""
        ser = self._ser
        while not self._stop_flag.is_set():
            try:
                waiting = 0
                try:
                    waiting = int(getattr(ser, "in_waiting", 0) or 0)
                except Exception:
                    waiting = 0
                data = ser.read(waiting if waiting > 0 else 1)
            except Exception as e:
                if not self._stop_flag.is_set():
                    self._push_event("error", f"[错误] 串口读取中断（可能被拔线或占用）："
                                              f"{type(e).__name__}: {e}")
                break
            if data:
                try:
                    self._raw_queue.put(bytes(data))
                except Exception:
                    break
                self._pump()             # 及时解析（不依赖界面是否在轮询 read_events）
            else:
                time.sleep(0.005)

    def _pump(self) -> None:
        """把读出线程放进队列的原始字节喂进解析管线（队列空则立即返回）。"""
        while True:
            try:
                chunk = self._raw_queue.get_nowait()
            except queue.Empty:
                return
            with self._lock:
                self._consume_locked(chunk)

    # ── 假串口注入：与真实串口完全同一条解析管线 ──────────────────────────
    def feed_bytes(self, data) -> str:
        """把一段**原始字节**当作"刚从串口收到"喂进解析管线（离线测试/无硬件演示入口）。"""
        try:
            chunk = bytes(data)
        except Exception as e:
            return f"[错误] 注入的数据无法转成字节：{type(e).__name__}: {e}"
        if not chunk:
            return "注入 0 字节（数据没变化）"
        with self._lock:
            self._consume_locked(chunk)
        return f"已注入 {len(chunk)} 字节（与真实串口走同一条解析管线）"

    def feed_text(self, text) -> str:
        """把一段**文本**按 UTF-8 编码后喂进解析管线，等价于 feed_bytes(text.encode('utf-8'))。"""
        try:
            chunk = str(text).encode("utf-8")
        except Exception as e:
            return f"[错误] 注入的文本无法编码：{type(e).__name__}: {e}"
        return self.feed_bytes(chunk)

    def read_once(self, max_bytes: int = 256) -> str:
        """主动读一次串口（界面上"点一下读一次"用）。返回人话结果或 `[错误]` 开头的错误。"""
        if not self.is_open:
            return "[错误] 串口未打开，无法读取。请先调用 open(端口号)"
        try:
            n = max(1, int(max_bytes))
        except Exception:
            n = 256
        try:
            data = self._ser.read(n)
        except Exception as e:
            return f"[错误] 读取串口失败：{type(e).__name__}: {e}"
        if not data:
            return "本次没读到数据（读超时，说明对端这段时间没发东西）"
        chunk = bytes(data)
        with self._lock:
            self._consume_locked(chunk)
        return f"读到 {len(chunk)} 字节：{decode_bytes(chunk)}"

    # ── 解析管线（调用方持有 _lock）──────────────────────────────────────
    def _consume_locked(self, chunk: bytes) -> None:
        """字节缓冲 → 按行切分（半包留到下次）→ 逐行处理 → 超长缓冲丢弃并提示。"""
        buf = self._buf
        buf.extend(chunk)
        while True:
            i = buf.find(b"\n")
            if i < 0:
                break
            line = bytes(buf[:i])
            del buf[:i + 1]
            if line.endswith(b"\r"):        # 支持 \r\n
                line = line[:-1]
            self._handle_line_locked(line)
        if len(buf) > self.max_buffer:
            del buf[:]
            self._push_event_locked(
                "error",
                f"[错误] 已累计超过 {self.max_buffer // 1024} KB 还没收到换行符，这段数据被丢弃了。"
                f"请检查：① 波特率是否和单片机一致；② 发送方是否以 \\n 结尾；"
                f"③ 是不是在发二进制而不是文本。")

    def _value_range_locked(self) -> tuple:
        """当前已接收点的幅值范围（用于提示与守卫）。"""
        if not self._points:
            return 0.0, 0.0
        vs = [v for _, v in self._points]
        return min(vs), max(vs)

    def _is_outlier_locked(self, point) -> bool:
        """这个点是不是"看起来不可能"的坏点（丢字节/半行拼接造成的）。"""
        if not self.outlier_guard or len(self._points) < self.outlier_min_samples:
            return False                     # 样本太少不判，免得把起始段误杀
        vs = [v for _, v in self._points]
        lo, hi = min(vs), max(vs)
        span = max(hi - lo, abs(hi), abs(lo), 1e-12)
        return abs(point[1]) > span * self.outlier_factor

    def _handle_line_locked(self, raw: bytes) -> None:
        if not raw.strip():                 # 空行不算数据、也不算坏行
            return
        text = decode_bytes(raw)
        point = self.parse_numbers(text)
        if point is not None and self._is_outlier_locked(point):
            self._outliers += 1
            lo, hi = self._value_range_locked()
            self._push_event_locked(
                "error",
                f"[提示] 已忽略一个疑似坏点：幅值 {point[1]:g}（当前量程 {lo:g}~{hi:g}，"
                f"相差超过 {self.outlier_factor:g} 倍）。常见原因是串口丢字节或半行拼接；"
                f"本次已忽略 {self._outliers} 个。若你的信号确实会跳这么大，"
                f"可以在设置里关掉守卫。")
            return
        if point is not None:
            if len(self._points) == self._points.maxlen:
                self._dropped += 1          # 滑窗挤掉最旧的一个
            self._points.append(point)
            self._total += 1
            self._write_row_locked(point)
        self._push_event_locked("line", text, values=(list(point) if point else None), dir="rx")

    def parse_numbers(self, line: str) -> tuple[float, float] | None:
        """从一行里取 1~2 个浮点数，返回 `(t, v)`；取不到返回 None 并把坏行计数 +1。

        支持的写法：
          - `"12.5"`                            → 只有一个数：t 用累计采样序号代替
          - `"12.5,3.4"` / `"12.5 3.4"`         → 前一个是时间，后一个是幅值
          - `"2026-10-02 10:00:00 12.5"`        → 先抠掉时间戳，再取数
          - `"12.5,3.4,坏行"`                    → 取**最后两个**数字（兼容带前缀的日志）
        """
        s = _strip_timestamps(str(line or ""))
        nums = _NUM.findall(s)
        if len(nums) >= 2:
            try:
                return (float(nums[-2]), float(nums[-1]))
            except ValueError:                                  # pragma: no cover - 正则已保证
                pass
        elif len(nums) == 1:
            try:
                return (float(self._total), float(nums[0]))
            except ValueError:                                  # pragma: no cover - 正则已保证
                pass
        with self._lock:
            self._bad_lines += 1
        return None

    # ── 事件与快照（给 SSE / 界面用）──────────────────────────────────────
    def _push_event(self, kind: str, text: str, **extra) -> None:
        with self._lock:
            self._push_event_locked(kind, text, **extra)

    def _push_event_locked(self, kind: str, text: str, **extra) -> None:
        now = time.time()
        ev = {"kind": kind, "text": text, "ts": now,
              "time": datetime.fromtimestamp(now).isoformat(timespec="milliseconds")}
        ev.update(extra)
        self._events.append(ev)

    def read_events(self, max_items: int = 200) -> list[dict]:
        """取出**自上次调用以来**的新事件（取走即清空），每次最多 max_items 条。

        事件形如 `{"kind": "line"|"error", "text": "...", "ts": 秒, ...}`。
        串口没打开时返回空列表（不算错误），方便 SSE 循环一直调用。
        """
        try:
            limit = int(max_items)
        except Exception:
            limit = 200
        if limit <= 0:
            return []
        self._pump()
        out: list[dict] = []
        with self._lock:
            while self._events and len(out) < limit:
                out.append(self._events.popleft())
        return out

    def snapshot(self) -> dict:
        """当前状态快照：`{points, total, bad_lines, dropped, ...}`（点=滑窗内最近的数据）。"""
        self._pump()
        with self._lock:
            return {
                "points": [[float(t), float(v)] for t, v in self._points],
                "total": self._total,
                "bad_lines": self._bad_lines,
            "outliers": self._outliers,
                "dropped": self._dropped,
                "buffer_bytes": len(self._buf),
                "pending_events": len(self._events),
                "is_open": self.is_open,
                "port": self._port,
                "baudrate": self._baudrate,
                "recording": self._rec_path if self._rec_file is not None else "",
                "max_points": self.max_points,
            }

    # ── 发送 ──────────────────────────────────────────────────────────────
    def send(self, text: str, hex_mode: bool = False) -> str:
        """发送数据。文本模式按 UTF-8 编码；HEX 模式支持 `"AA 55 01"` / `"AA5501"`。"""
        if not self.is_open:
            return "[错误] 串口未打开，无法发送。请先调用 open(端口号)"
        if hex_mode:
            data, err = parse_hex(text)
            if err:
                return err
            shown = " ".join(f"{b:02X}" for b in data)
        else:
            data = str(text if text is not None else "").encode("utf-8")
            shown = str(text)
        if not data:
            return "[错误] 要发送的内容是空的，请填写要发送的文本或 HEX 字节"
        try:
            self._ser.write(data)
            try:
                self._ser.flush()
            except Exception:
                pass
        except Exception as e:
            return f"[错误] 发送失败：{type(e).__name__}: {e}"
        self._push_event("line", f"发送 → {shown}", dir="tx", values=None)
        return f"已发送 {len(data)} 字节：{shown}"

    # ── 记录落盘（CSV）────────────────────────────────────────────────────
    def start_record(self, path=None) -> str:
        """开始把解析出的数据点追加写入 CSV。成功返回**文件路径**，失败返回 `[错误]` 开头的错误。

        文件格式（表头固定，`analyze_step_data()` 能直接读）::

            # 串口记录（大学生竞赛工作台·串口助手）
            # 端口：COM3    波特率：115200
            # 开始时间：2026-10-02T01:23:45
            ISO时间,时间,幅值
            2026-10-02T01:23:45.123,0.010,0.000
        """
        if self._rec_file is not None:
            return f"[错误] 已经在记录了（{self._rec_path}），请先 stop_record() 再开始新的一次"
        if path:
            target = Path(path)
        else:
            try:
                from .config import OUT_DIR
                base = Path(OUT_DIR)
            except Exception:
                base = Path.cwd()
            target = base / f"serial-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fresh = (not target.exists()) or target.stat().st_size == 0
            f = target.open("a", encoding="utf-8", newline="")
            if fresh:
                f.write("# 串口记录（大学生竞赛工作台·串口助手）\n")
                f.write(f"# 端口：{self._port or '（未打开串口，仅假数据注入）'}"
                        f"    波特率：{self._baudrate or '—'}\n")
                f.write(f"# 开始时间：{datetime.now().isoformat(timespec='seconds')}\n")
                f.write("ISO时间,时间,幅值\n")
                f.flush()
        except Exception as e:
            return f"[错误] 无法创建记录文件 {target}：{type(e).__name__}: {e}"
        with self._lock:
            self._rec_file = f
            self._rec_path = str(target)
        return str(target)

    def _write_row_locked(self, point) -> None:
        f = self._rec_file
        if f is None:
            return
        try:
            f.write(f"{datetime.now().isoformat(timespec='milliseconds')},"
                    f"{_fmt_num(point[0])},{_fmt_num(point[1])}\n")
            self.record_rows += 1
        except Exception as e:
            self._rec_file = None
            self._rec_path = ""
            self._push_event_locked("error", f"[错误] 写记录文件失败，已停止记录：{type(e).__name__}: {e}")

    def stop_record(self) -> str:
        """停止记录并关闭文件。成功返回**文件路径**，没在记录时返回 `[错误]`。"""
        with self._lock:
            f, p = self._rec_file, self._rec_path
            self._rec_file = None
        if f is None:
            return "[错误] 当前没有在记录文件，无法停止（请先调用 start_record()）"
        try:
            f.flush()
            f.close()
        except Exception as e:
            return f"[错误] 关闭记录文件失败：{type(e).__name__}: {e}"
        self._rec_path = ""
        return p

    # ── 一键送调参助手 ────────────────────────────────────────────────────
    def analyze(self, target: float | None = None) -> str:
        """把当前滑窗里的数据写成 CSV，交给**已有的** `analyze_step_data()` 出指标+图+建议。

        成功返回那段 Markdown（含超调量、峰值时间、上升时间、调节时间、稳态误差等），
        数据太少或失败时返回 `[错误]` 开头的人话错误。
        """
        self._pump()
        with self._lock:
            pts = [[float(t), float(v)] for t, v in self._points]
        if len(pts) < 8:
            return (f"[错误] 缓冲里只有 {len(pts)} 个数据点，至少需要 8 个才能分析"
                    f"（让串口多收一会儿，或用 feed_text() 注入一段阶跃响应数据）")
        try:
            from .config import OUT_DIR
            path = Path(OUT_DIR) / f"serial-analyze-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="") as f:
                f.write("# 由串口助手缓冲导出（时间,幅值）\n")
                f.write("时间,幅值\n")
                for t, v in pts:
                    f.write(f"{_fmt_num(t)},{_fmt_num(v)}\n")
        except Exception as e:
            return f"[错误] 导出缓冲数据失败：{type(e).__name__}: {e}"
        try:
            from .tools.tuning import analyze_step_data
            return analyze_step_data(str(path), target=target)
        except Exception as e:
            return f"[错误] 调参助手分析失败：{type(e).__name__}: {e}"
