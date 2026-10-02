#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参数扫描：自动改参数 → 采数据 → 算指标 → 出对比表与推荐。

为什么要有它
------------
调参的体力活是「改一个参数 → 重新跑一次 → 记下超调和调节时间 → 再改」。
这个模块把这一圈自动化掉，最后给一张**可复核**的表和一条明确推荐 ——
指标全部复用 `tools/tuning.py` 的确定性算法（不是模型猜的）。

设计要点
--------
- **纯逻辑 + 可注入 IO**：`SweepRunner` 只依赖一个很小的 link 接口
  （`send` / `points`），真实串口和测试里的**模拟被控对象**都满足它
  → 整条链路可以**离线测透**（见 tests/test_sweep.py：用一个"参数越大超调越大"的
  模拟对象，验证"扫描 → 测量 → 推荐"真的能选出对的那个值）。
- **随时能停**：等待期间分小步轮询停止标志，不会卡住一分钟。
- **不抛异常**：对外返回结果或 `[错误]` 开头的中文错误串（项目约定）。
- **有防呆**：值太多、值为空、数据点太少都会被人话拦住，而不是默默跑一小时。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .tools.tuning import analyze

# 一次扫描最多多少个值（防呆：别让人不小心挂着跑一下午）
MAX_VALUES = 50
# 判断"超调合格"的默认阈值（%）：多数赛题要求 ≤10~20%
DEFAULT_SIGMA_LIMIT = 20.0
# 算指标至少需要的数据点
MIN_POINTS = 8


class SweepError(ValueError):
    """参数或用法不对（会给用户看人话）。"""


# ── 参数解析 ────────────────────────────────────────────────────────────
def parse_values(text: str) -> list:
    """解析要扫的值。支持两种写法：

    - 枚举：`0.5, 1.0, 1.5`（逗号/空格/分号都行）
    - 范围：`0.5:2.0:0.25`（起点:终点:步长，含起点；终点能被步长整除时也含终点）

    数量上限 `MAX_VALUES`，超了报错（防呆）。
    """
    s = str(text or "").strip()
    if not s:
        raise SweepError("[错误] 要扫的参数值是空的（例如 0.5, 1.0, 1.5 或 0.5:2.0:0.25）")
    vals: list = []
    if ":" in s and "," not in s:
        parts = [p for p in s.split(":") if p.strip() != ""]
        if len(parts) not in (2, 3):
            raise SweepError("[错误] 范围的写法是 起点:终点[:步长]，例如 0.5:2.0:0.25")
        try:
            start, stop = float(parts[0]), float(parts[1])
            step = float(parts[2]) if len(parts) == 3 else (1.0 if stop >= start else -1.0)
        except ValueError:
            raise SweepError(f"[错误] 范围里有不是数字的内容：{s!r}")
        if step == 0:
            raise SweepError("[错误] 步长不能是 0")
        if (stop - start) * step < 0:
            raise SweepError(f"[错误] 步长方向不对：从 {start:g} 到 {stop:g} 应该用"
                             f"{'正' if stop >= start else '负'}步长")
        n = int(abs(stop - start) / abs(step) + 1e-9) + 1
        vals = [start + i * step for i in range(n)]
        if abs(vals[-1] - stop) > 1e-9 and abs((vals[-1] + step) - stop) < 1e-9:
            vals.append(vals[-1] + step)
        vals = [round(v, 10) for v in vals]
    else:
        for p in (s.replace(";", ",").replace("；", ",").replace("，", ",")
                  .replace("、", ",").replace(" ", ",").split(",")):
            p = p.strip()
            if not p:
                continue
            try:
                vals.append(float(p))
            except ValueError:
                raise SweepError(f"[错误] 参数值里有不是数字的内容：{p!r}")
    if not vals:
        raise SweepError("[错误] 没有解析出任何参数值")
    if len(vals) > MAX_VALUES:
        raise SweepError(f"[错误] 一次最多扫 {MAX_VALUES} 个值（现在 {len(vals)} 个）——"
                         f"值太多会跑很久，缩小范围或加大步长")
    return vals


def format_command(template: str, value: float, hex_mode: bool = False) -> str:
    """把 `{value}` 占位符替换成参数值，生成要发的命令。

    支持 Python 的格式写法：`{value}` / `{value:g}` / `{value:.2f}` / `{value:04X}`
    （HEX 模式下常用 `{value:04X}` 生成两字节十六进制）。
    """
    tpl = str(template or "")
    if "{value" not in tpl:
        raise SweepError("[错误] 参数命令里要用 {value} 占位（例如 KP={value} 或 AA 01 {value:04X}）")
    try:
        out = tpl.format(value=value)
    except (KeyError, ValueError, IndexError) as e:
        raise SweepError(f"[错误] 命令模板写错了：{type(e).__name__}: {e}"
                         f"（可用 {{{{value}}}}、{{{{value:g}}}}、{{{{value:.2f}}}}、{{{{value:04X}}}}）")
    if hex_mode:
        s = "".join(out.replace(",", " ").replace("0x", " ").replace("0X", " ").split())
        if len(s) % 2:
            raise SweepError(f"[错误] HEX 模式下命令必须是偶数个十六进制位：{out!r}")
        try:
            bytes.fromhex(s)
        except ValueError:
            raise SweepError(f"[错误] HEX 模式下命令不是合法的十六进制：{out!r}")
    return out


# ── 数据结构 ────────────────────────────────────────────────────────────
@dataclass
class SweepSpec:
    values: list = field(default_factory=list)
    set_template: str = "KP={value}"
    hex_mode: bool = False
    trigger: str = ""                 # 可选：改完参数后发的触发命令（比如"开始阶跃"）
    settle_s: float = 1.5             # 改完参数等多久才采数
    collect_s: float = 6.0            # 每轮采多久
    target: float | None = None       # 目标值（给指标用）
    repeats: int = 1                  # 每个值重复几次

    def __post_init__(self):
        self.settle_s = max(0.0, float(self.settle_s))
        self.collect_s = max(0.5, float(self.collect_s))
        self.repeats = max(1, min(10, int(self.repeats)))
        if self.target is not None:
            self.target = float(self.target)


@dataclass
class SweepRow:
    value: float
    repeat: int = 1
    ok: bool = False
    sigma: float = float("nan")       # 超调量（有目标值就用相对目标值口径）
    rise: float = float("nan")
    settle: float = float("nan")
    y_ss: float = float("nan")
    settled: bool = False
    points: int = 0
    error: str = ""

    def brief(self) -> str:
        if not self.ok:
            return f"{self.value:g} → 失败（{self.error[:40]}）"
        return (f"{self.value:g} → 超调 {self.sigma:.1f}% · 上升 {self.rise:.3f}s · "
                f"调节 {self.settle:.3f}s · 稳态 {self.y_ss:.4f}")


@dataclass
class SweepResult:
    rows: list = field(default_factory=list)
    best: SweepRow | None = None
    advice: str = ""
    stopped: bool = False
    limit: float = DEFAULT_SIGMA_LIMIT

    def ok_rows(self) -> list:
        return [r for r in self.rows if r.ok]

    def table_md(self) -> str:
        """对比表（Markdown）：每行一个参数值及其指标。"""
        if not self.rows:
            return "（没有数据）"
        head = ("| 参数值 | 超调 σ% | 上升时间 t_r | 调节时间 t_s | 稳态值 | 数据点 | 备注 |\n"
                "|---|---|---|---|---|---|---|")
        lines = [head]
        for r in self.rows:
            if not r.ok:
                lines.append(f"| {r.value:g} | — | — | — | — | {r.points} | ⚠️ {r.error[:40]} |")
                continue
            star = " ★ 推荐" if (self.best is not None and r is self.best) else ""
            lines.append(f"| {r.value:g} | {r.sigma:.2f} | {r.rise:.3f}s | {r.settle:.3f}s | "
                         f"{r.y_ss:.4f} | {r.points} |"
                         f"{'已稳定' if r.settled else '未稳定'}{star} |")
        return "\n".join(lines)


# ── 扫描器 ──────────────────────────────────────────────────────────────
class SweepRunner:
    """把「发参数 → 等生效 → 采数据 → 算指标」串起来跑一遍。

    `link` 只需要两个方法（真实串口与测试里的假对象都满足）：
      - `send(text, hex_mode=False) -> str`：发送；返回 `[错误]…` 表示失败
      - `points() -> list[(t, v)]`：当前已收到的数据点（用来取增量）
    """

    def __init__(self, link, log=None):
        self.link = link
        self.log = log or (lambda *_: None)

    # 等待时**分小步**轮询停止标志（这样点"停止"能马上停，不用等一整轮）
    def _sleep(self, seconds: float, stop_flag=None) -> bool:
        end = time.time() + max(0.0, float(seconds))
        while time.time() < end:
            if stop_flag is not None and stop_flag():
                return False
            time.sleep(min(0.1, max(0.01, end - time.time())))
        return not (stop_flag is not None and stop_flag())

    def _one_round(self, spec: SweepSpec, value: float, repeat: int,
                   stop_flag=None) -> SweepRow:
        row = SweepRow(value=value, repeat=repeat)
        # ★ 起点要在**发参数之前**记：真实设备一收到参数命令就可能开始吐数据，
        #   记晚了会把这一轮的数据全算进起点（实测过：每轮都变成"0 个数据点"）
        mark = len(self.link.points())
        try:
            cmd = format_command(spec.set_template, value, spec.hex_mode)
        except SweepError as e:
            row.error = str(e)
            return row
        msg = self.link.send(cmd, spec.hex_mode)
        if str(msg).startswith("[错误]"):
            row.error = f"发参数失败：{msg}"
            return row
        if not self._sleep(spec.settle_s, stop_flag):
            row.error = "已停止"
            return row
        if spec.trigger:
            msg = self.link.send(spec.trigger, spec.hex_mode)
            if str(msg).startswith("[错误]"):
                row.error = f"发触发命令失败：{msg}"
                return row
        if not self._sleep(spec.collect_s, stop_flag):
            row.error = "已停止"
            return row
        pts = list(self.link.points())[mark:]
        row.points = len(pts)
        if len(pts) < MIN_POINTS:
            row.error = f"只收到 {len(pts)} 个数据点，至少需要 {MIN_POINTS} 个（检查波特率/输出格式）"
            return row
        try:
            t = np.array([float(p[0]) for p in pts], dtype=float)
            y = np.array([float(p[1]) for p in pts], dtype=float)
        except Exception as e:
            row.error = f"数据点不是数字：{type(e).__name__}: {e}"
            return row
        try:
            m = analyze(t, y, target=spec.target)
            # ★ 直接取字段，不用 getattr 兜底：万一上游改了字段名，这里要**报错**，
            #   而不是静默变成 NaN（我第一版就是 getattr(..., nan)，结果 settle 全是 NaN，
            #   推荐逻辑当成了"没有达标值"，很难查）
            #   超调口径：有目标值时用"相对目标值"（更贴合赛题），否则用"相对稳态值"
            sigma = m.overshoot_vs_target if spec.target is not None else None
            if sigma is None or not np.isfinite(sigma):
                sigma = m.overshoot_pct
            row.sigma = float(sigma) if np.isfinite(sigma) else float("nan")
            row.rise = float(m.rise_time)
            row.settle = float(m.settle_time_2)          # ±2% 调节时间（赛题常用口径）
            row.y_ss = float(m.y_ss)
            row.settled = bool(m.settled)
        except Exception as e:
            row.error = f"算指标失败：{type(e).__name__}: {e}"
            return row
        row.ok = True
        return row

    def run(self, spec: SweepSpec, stop_flag=None, on_row=None) -> SweepResult:
        """跑完整轮扫描。`stop_flag()` 返回 True 时尽快收尾（已采到的结果照常返回）。"""
        res = SweepResult()
        total = len(spec.values) * max(1, spec.repeats)
        i = 0
        for v in spec.values:
            for r in range(1, max(1, spec.repeats) + 1):
                if stop_flag is not None and stop_flag():
                    res.stopped = True
                    break
                i += 1
                self.log(f"[{i}/{total}] 参数 {v:g}" + (f"（第 {r} 次）" if spec.repeats > 1 else ""))
                row = self._one_round(spec, v, r, stop_flag)
                res.rows.append(row)
                self.log("   " + row.brief())
                if on_row is not None:
                    try:
                        on_row(row, i, total)
                    except Exception:
                        pass
            if res.stopped:
                break
        if stop_flag is not None and stop_flag():
            res.stopped = True
        res.best, res.advice = pick_best(res.rows, res.limit)
        return res


# ── 推荐（确定性规则，可复核）──────────────────────────────────────────
def pick_best(rows: list, limit: float = DEFAULT_SIGMA_LIMIT) -> tuple:
    """从扫描结果里选一个推荐值 + 一句人话理由。

    规则（简单、可复核，不搞玄学）：
      1. 只在**成功**且**已稳定**、且有有限超调值的行里选；
      2. 优先选「超调 ≤ limit」里**调节时间最短**的（既不过冲、又收得快）；
      3. 没有满足 limit 的 → 选**超调最小**的，并说明"都没达标，先压超调"；
      4. 只有一次采样时相同处理；多次采样时同一参数取**平均超调**再比。
    """
    good = [r for r in rows if r.ok and np.isfinite(r.sigma)]
    if not good:
        return None, "没有可用的采样结果 —— 先确认板子在按预期输出数据（每行两个数字）。"
    # 同一参数多次采样 → 取平均超调/平均调节时间
    by_value: dict = {}
    for r in good:
        by_value.setdefault(r.value, []).append(r)
    agg = []
    for v, rs in by_value.items():
        sig = float(np.mean([r.sigma for r in rs]))
        st = float(np.mean([r.settle for r in rs if np.isfinite(r.settle)] or [float("nan")]))
        agg.append((v, sig, st, rs[-1]))
    within = [a for a in agg if a[1] <= limit and np.isfinite(a[2])]
    if within:
        v, sig, st, row = min(within, key=lambda a: a[2])
        others = [a for a in agg if a[0] != v]
        trend = ""
        if others:
            smaller = [a[1] for a in others if a[0] < v]
            if smaller and float(np.mean(smaller)) < sig:
                trend = "（参数调小后超调也变小 → 说明这个参数确实是往「更冲」的方向使劲）"
        return row, (f"推荐 **{v:g}**：超调 {sig:.1f}%（≤{limit:g}%）且调节时间最短"
                     f"（{st:.3f}s）。{trend}")
    v, sig, st, row = min(agg, key=lambda a: a[1])
    return row, (f"没有参数满足「超调 ≤ {limit:g}%」——先选超调最小的 **{v:g}**（{sig:.1f}%）"
                 f"把过冲压下来，再回头收紧调节时间。")


# ── 真实串口的适配器 ────────────────────────────────────────────────────
class AssistantLink:
    """把 `SerialAssistant` 适配成 `SweepRunner` 需要的接口。"""

    def __init__(self, assistant):
        self.a = assistant

    def send(self, text, hex_mode: bool = False) -> str:
        return self.a.send(text, hex_mode=hex_mode)

    def points(self) -> list:
        return list(self.a.snapshot().get("points", []))

    def clear(self) -> str:
        """清空已收数据（这样每轮的 mark 都从 0 开始，更直观）。"""
        try:
            with self.a._lock:                                  # noqa: SLF001 - 同项目内部协作
                self.a._points.clear()
                self.a._buf.clear()
            return "已清空接收缓冲"
        except Exception as e:
            return f"[错误] 清空失败：{type(e).__name__}: {e}"
