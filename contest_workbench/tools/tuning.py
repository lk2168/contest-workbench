# -*- coding: utf-8 -*-
"""调参助手：把"串口/CSV 采到的阶跃响应数据"变成**可验证的指标 + 下一步建议**。

设计原则（很重要）：
  - **能用确定性算的，绝不交给模型猜**：超调量、上升时间、调节时间、稳态误差这些
    全部用 numpy 算出来，模型只负责"结合赛题要求做判断、写报告"。
  - 每条建议都带"改哪个参数 / 改多少 / 预期效果 / 怎么验证"，方便照做。
  - 没有 scipy 也能跑（只用 numpy；scipy 只用于可选的拟合）。

阶跃响应指标定义（教科书口径）：
  - 稳态值 y_ss   ：最后 10% 采样点的均值
  - 超调量 σ%     ：(峰值 - y_ss) / |y_ss - y0| * 100%
  - 峰值时间 tp   ：从阶跃起点到第一个峰值的秒数
  - 上升时间 tr   ：输出从 10% 到 90% 稳态增量所花的时间
  - 调节时间 ts   ：进入并保持在 ±2%（或 ±5%）带内的时刻
  - 稳态误差      ：|目标值 - y_ss|（给了 --target 才算）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

# --------------------------------------------------------------------------- #
# 1. 读数据：兼容 CSV / 制表符 / 空格分隔 / 带时间戳的串口日志
# --------------------------------------------------------------------------- #
_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def parse_two_columns(text: str) -> tuple[np.ndarray, np.ndarray]:
    """从文本里抽出 (时间, 幅值) 两列。规则：

    - 跳过空行、以 # 或 // 开头的注释行、以及"不含两个数字"的行
    - 每行取**最后两个数字**：这样 `[12:00:01] 0.123  45.6` 这类串口日志也能吃
    - 若不写时间列（只有一个数字），则用采样序号代替时间（后面按 dt=1 处理）
    """
    ts: list[float] = []
    ys: list[float] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "//", ";")):
            continue
        nums = _NUM.findall(line)
        if len(nums) >= 2:
            try:
                ts.append(float(nums[-2]))
                ys.append(float(nums[-1]))
            except ValueError:
                continue
        elif len(nums) == 1:
            try:
                ts.append(float(len(ts)))
                ys.append(float(nums[0]))
            except ValueError:
                continue
    if len(ys) < 8:
        raise ValueError(f"数据太少（只解析到 {len(ys)} 个采样点，至少 8 个）")
    return np.asarray(ts, dtype=float), np.asarray(ys, dtype=float)


def read_data(path: str | Path) -> tuple[np.ndarray, np.ndarray]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"数据文件不存在：{p}")
    raw = p.read_bytes()
    for enc in ("utf-8", "gbk", "utf-16", "latin-1"):
        try:
            return parse_two_columns(raw.decode(enc))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"无法解码文件（试过 utf-8/gbk/utf-16）：{p}")


# --------------------------------------------------------------------------- #
# 2. 指标计算
# --------------------------------------------------------------------------- #
@dataclass
class StepMetrics:
    n: int
    dt: float                     # 采样周期（秒，估计值）
    y0: float                     # 初始值（前 5% 采样均值）
    y_ss: float                   # 稳态值（后 10% 采样均值）
    step: float                   # 阶跃幅值 y_ss - y0
    peak: float
    overshoot_pct: float | None   # 超调量（相对**稳态值**，教材口径；未稳定时偏小）
    peak_time: float | None
    rise_time: float | None       # 10% → 90%
    settle_time_2: float | None   # ±2% 带
    settle_time_5: float | None   # ±5% 带
    steady_error: float | None    # 需要 target
    oscillations: int             # 稳态带内穿越次数（衡量振荡）
    noise_std: float              # 末段残差标准差（衡量噪声）
    t_start: float = 0.0
    target: float | None = None
    smooth_window: int = 1        # 平滑窗口（采样点数），1 = 未平滑
    warnings: list[str] = field(default_factory=list)
    # ★ 下面两个是后加的，必须放在所有"无默认值"字段之后（dataclass 的硬约束）
    overshoot_vs_target: float | None = None   # 超调量（相对**目标值**，给了 target 才有）
    settled: bool = True                       # 末尾是否已稳定（不稳时 y_ss 不可靠）

    def table(self) -> str:
        """Markdown 表格（报告里直接可用）。"""
        def fmt(v, unit="", nd=4):
            return "—" if v is None else f"{v:.{nd}g}{unit}"
        rows = [
            ("采样点数", f"{self.n}"),
            ("采样周期 dt（估计）", fmt(self.dt, " s")),
            ("**计时基准 t_start**（检测到的阶跃起始）", fmt(self.t_start, " s")),
            ("初始值 y₀", fmt(self.y0)),
            ("稳态值 y_ss", fmt(self.y_ss)),
            ("阶跃幅值 Δy", fmt(self.step)),
            ("峰值", fmt(self.peak)),
            ("**超调量 σ%（相对稳态值）**", fmt(self.overshoot_pct, " %", 3)),
            ("**超调量（相对目标值）**", fmt(self.overshoot_vs_target, " %", 3)),
            ("**峰值时间 t_p**", fmt(self.peak_time, " s", 3)),
            ("**上升时间 t_r（10%→90%）**", fmt(self.rise_time, " s", 3)),
            ("**调节时间 t_s（±2%）**", fmt(self.settle_time_2, " s", 3)),
            ("调节时间 t_s（±5%）", fmt(self.settle_time_5, " s", 3)),
            ("**稳态误差**", fmt(self.steady_error, "", 3)),
            ("振荡（穿越稳态带次数）", f"{self.oscillations}"),
            ("末段噪声 σ", fmt(self.noise_std)),
            ("数据末尾是否已稳定", "是" if self.settled else "**否（稳态值不可靠）**"),
        ]
        out = ["| 指标 | 值 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rows]
        if self.target is not None:
            out.append(f"\n> 目标值：{self.target:g}")
        if self.warnings:
            out.append("\n> ⚠️ " + "；".join(self.warnings))
        return "\n".join(out)


def _smooth(y: np.ndarray, dt: float, win_sec: float = 0.02) -> tuple[np.ndarray, int]:
    """滑动平均（窗口约 20ms），**两端用端点值填充**。

    噪声大的数据必须平滑后再判稳态/数振荡，否则噪声会在稳态带里反复穿越，
    把"振荡次数"和"调节时间"全污染（实测振荡数会虚高到 100、t_s 被拖到 3.7s）。

    ⚠️ 不能用 `np.convolve(y, k, mode="same")`：它把两端按 0 补齐，
    结果最后 ~w/2 个采样被拉向 0，稳态段会被误判成"一直偏出 ±2% 带" → t_s 算不出来（None）。
    """
    if dt <= 0:
        return y, 1
    w = int(round(win_sec / dt))
    if w % 2 == 0:                       # 取奇数，便于两端各填 w//2 后长度不变
        w += 1
    w = min(max(w, 3), max(3, len(y) // 50))
    if w < 3 or len(y) < w + 2:
        return y, 1
    pad = w // 2
    ypad = np.pad(y, pad, mode="edge")
    sm = np.convolve(ypad, np.ones(w) / w, mode="valid")
    return sm[:len(y)], w


def _robust_noise(y: np.ndarray) -> float:
    """稳健噪声估计：尾部 10% 的 MAD×1.4826（比标准差抗离群点）。"""
    tail = y[-max(1, len(y) // 10):]
    return float(1.4826 * np.median(np.abs(tail - np.median(tail))))


def analyze(t: np.ndarray, y: np.ndarray, target: float | None = None,
            settle_band: float = 0.02) -> StepMetrics:
    n = len(y)
    warn: list[str] = []

    # 采样周期：取时间列差分的中位数（比均值抗抖动）
    dt = float(np.median(np.diff(t))) if n > 1 and np.ptp(t) > 0 else 1.0
    if dt <= 0:
        dt = 1.0
        warn.append("时间列非单调，已按采样序号处理")

    noise_est = _robust_noise(y)
    ys, win = _smooth(y, dt)
    if win > 1:
        pass  # 已平滑，指标基于平滑曲线（报告里会写明）
    rng = float(np.ptp(ys))

    # ── 阶跃起始点检测（这一步很关键，且必须对噪声免疫）──────────
    # 不能用"前 5% 采样"当基线：很多数据从第 0 点就开始上升 → 基线估高、阶跃幅值估小、
    # 超调被系统性放大（实测偏大 14%）。
    # 阈值取 max(幅值的 0.3%, 3×噪声)：噪声大的数据必须抬高阈值，否则第一个采样点
    # 的噪声抖动就会被误判成"阶跃开始"（实测 t_start 会错成 0，而真实阶跃在 0.3s）。
    thr = max(1e-12, 0.003 * rng, 3.0 * noise_est)
    dev = np.abs(ys - ys[0])
    onset = 0
    for i in np.where(dev > thr)[0]:
        if i + 2 < n and dev[i + 1] > thr and dev[i + 2] > thr:
            onset = int(i)
            break
    if onset == 0:
        warn.append("未检测到明显的阶跃起始（信号可能一直在变，或被噪声淹没），基线按首个采样点估计")
        y0 = float(ys[0])
    else:
        y0 = float(np.mean(ys[:onset]))          # 起点之前的平坦段 = 真基线
    t0 = float(t[max(0, onset - 1)])             # 阶跃施加时刻 ≈ 最后一个基线采样点

    kss = max(1, int(n * 0.10))
    y_ss = float(np.mean(ys[-kss:]))
    step = y_ss - y0
    if abs(step) < 1e-12:
        warn.append("阶跃幅值≈0（信号可能没加上/没变化），指标不可信")

    # ★ 末尾是否已稳定：后 10% 的峰峰值若仍超出 ±2% 带（且超过噪声量级），
    #   说明系统还在振荡/漂移 —— 此时"后 10% 均值"不是稳态值，超调量会被算小
    #   （实测：ζ=0.5 的系统只采 2 秒，超调量被算成 0.26%，真值是 16.3%）。
    # 判定要用三条证据，单看"尾部起伏"会被慢振荡骗过：
    #   实测 ζ=0.5 / ωn=2 的系统（振荡周期 3.6 s）只采 2 s 时，
    #   最后 0.2 s 的窗口看起来几乎是平的 → 误判为"已稳定"。
    #   ① 尾部峰峰值在带内  ② 相邻两窗均值不漂移  ③（给了 target 时）y_ss 贴近 target
    tail = ys[-kss:]
    prev = ys[-2 * kss:-kss] if n >= 2 * kss else ys[:kss]
    tail_ptp = float(tail.max() - tail.min())
    noise_tail = float(np.std(np.diff(tail))) / np.sqrt(2.0) if kss > 1 else 0.0
    band = max(0.02 * abs(step), 3.0 * noise_tail)
    drift = abs(float(tail.mean()) - float(prev.mean()))
    near_target = (target is None or abs(float(target)) < 1e-12
                   or abs(y_ss - float(target)) <= 0.02 * abs(float(target)))
    settled = bool(abs(step) < 1e-12
                   or (tail_ptp <= band and drift <= band and near_target))
    if not settled:
        warn.append("数据末尾仍未稳定（后 10% 峰峰值 "
                    f"{tail_ptp:.4g}，超出 ±2% 带）—— 稳态值不可靠，"
                    "「相对稳态值」的超调量会偏小，请以「相对目标值」为准或采到稳定为止")

    peak_idx = int(np.argmax(ys)) if step >= 0 else int(np.argmin(ys))
    peak = float(ys[peak_idx])

    overshoot = None if abs(step) < 1e-12 else (peak - y_ss) / abs(step) * 100.0
    # 相对目标值的超调量：用户明确给了 target 时，这才是他要的"冲过头多少"
    overshoot_vs_target = None
    if target is not None and abs(float(target)) > 1e-12:
        overshoot_vs_target = (peak - float(target)) / abs(float(target)) * 100.0
    peak_time = float(t[peak_idx] - t0) if peak_idx >= onset else None

    # 上升时间：10% → 90%（以 y0 为基准，按 step 方向找首个穿越点）
    rise = None
    if abs(step) > 1e-12:
        lo, hi = y0 + 0.1 * step, y0 + 0.9 * step
        seg, tseg = ys[onset:], t[onset:]
        try:
            i_lo = int(np.where((seg - lo) * np.sign(step) >= 0)[0][0])
            i_hi = int(np.where((seg - hi) * np.sign(step) >= 0)[0][0])
            if i_hi > i_lo:
                rise = float(tseg[i_hi] - tseg[i_lo])
        except IndexError:
            warn.append("信号未达到 90% 稳态值，上升时间无法确定")

    # 调节时间：最后一次离开 ±band 的时刻（相对阶跃起点；用平滑曲线，避免噪声拖延）
    def settle(band: float) -> float | None:
        if abs(step) < 1e-12:
            return None
        tol = abs(step) * band
        outside = np.where(np.abs(ys[onset:] - y_ss) > tol)[0]
        if len(outside) == 0:
            return 0.0
        return float(t[onset + outside[-1]] - t0) if outside[-1] < len(ys) - onset - 1 else None

    ts2, ts5 = settle(settle_band), settle(0.05)

    # 振荡：数**真实摆动**，不数噪声抖动。
    # 规则：在稳态段上取平滑曲线，找相邻两次穿越 y_ss，且这两次之间至少有一侧偏出
    # 0.5 倍稳态带 —— 否则算噪声，不计。返回完整摆动次数（周期数）。
    osc = 0
    if abs(step) > 1e-12:
        tol = abs(step) * settle_band
        seg = ys[onset:]
        d = seg - y_ss
        sign = np.sign(d)
        changes = np.where(np.abs(np.diff(sign)) > 0)[0]
        if len(changes) > 1:
            approved = 0
            for a, b in zip(changes[:-1], changes[1:]):
                window = d[a:b + 1]
                if np.max(np.abs(window)) > 0.5 * tol:   # 这一摆够大，才算一次真振荡
                    approved += 1
            osc = approved // 2                          # 两次穿越 = 一个完整周期
    if win == 1 and noise_est > 0.02 * abs(step):
        warn.append("数据噪声较大且未做平滑，指标可信度下降")

    if noise_est > abs(step) * 0.02:
        warn.append(f"末段噪声偏大（σ≈{noise_est:.3g}），建议先滤波/提高采样质量")
    if dt > 0.02:
        warn.append(f"采样周期 dt≈{dt:.3f}s 偏大，快过程（t_r 很小）会测不准")

    steady_err = None if target is None else abs(target - y_ss)

    return StepMetrics(n=n, dt=dt, y0=y0, y_ss=y_ss, step=step, peak=peak,
                       overshoot_pct=overshoot, peak_time=peak_time, rise_time=rise,
                       settle_time_2=ts2, settle_time_5=ts5, steady_error=steady_err,
                       oscillations=osc, noise_std=noise_est, t_start=t0, target=target,
                       overshoot_vs_target=overshoot_vs_target, settled=settled,
                       warnings=warn, smooth_window=win)


# --------------------------------------------------------------------------- #
# 3. 规则化调参建议（确定性：同样的指标永远给同样的建议）
# --------------------------------------------------------------------------- #
@dataclass
class Suggestion:
    param: str
    action: str
    why: str
    expect: str
    verify: str

    def line(self) -> str:
        return (f"- **{self.param}**：{self.action}\n"
                f"  - 依据：{self.why}\n"
                f"  - 预期：{self.expect}\n"
                f"  - 验证：{self.verify}")


def suggest_pid(m: StepMetrics) -> list[Suggestion]:
    """按"一次只动一个参数"的纪律给出建议，按优先级排序。"""
    out: list[Suggestion] = []
    os_ = m.overshoot_pct
    tr = m.rise_time
    err = m.steady_error
    osc = m.oscillations

    if osc >= 3:
        out.append(Suggestion("Kp / Kd", "**先降 Kp 10–20%**，同时把 Kd 提高 10–20%（先只动 Kp）",
                              f"稳态带内穿越 {osc} 次，属明显振荡",
                              "振荡次数降到 ≤1，超调同步下降",
                              "重采一次阶跃，看 σ% 与振荡次数"))
    if os_ is not None and os_ > 20:
        out.append(Suggestion("Kp（或 Kd）", "**降 Kp 10–20%**；若响应变慢，再补 Kd",
                              f"超调 {os_:.1f}% 超过 20% 经验线",
                              "超调落到 10–20% 区间（多数赛题要求 ≤10–20%）",
                              "重采阶跃，比较 σ%；注意调整后 t_r 不应显著变长"))
    elif os_ is not None and 0 <= os_ < 5 and (tr or 0) > 0.5:
        out.append(Suggestion("Kp", "**升 Kp 10–20%**",
                              f"超调仅 {os_:.1f}% 但上升时间 {tr:.3f}s 偏慢",
                              "上升时间缩短，超调仍在可接受范围",
                              "重采阶跃：t_r 下降且 σ% 不超过上限"))
    if err is not None and err > max(1e-9, abs(m.step) * 0.02):
        out.append(Suggestion("Ki（积分）", "**加/增 Ki（从小到大，先 10% 步长）**",
                              f"稳态误差 {err:.4g}（>2% 阶跃幅值）存在静差",
                              "稳态误差趋于 0；注意积分会加大超调",
                              "等稳态后再看末段均值与目标值之差；同时观察超调是否上升"))
    if tr is None and m.warnings:
        out.append(Suggestion("整体", "先解决数据质量问题（见上方告警）再谈调参",
                              "上升时间无法确定，说明信号未达稳态或数据不可用",
                              "拿到干净的阶跃曲线",
                              "重新采集：加阶跃前先采 0.5s 基线、保证采到完整稳态段"))
    if m.dt > 0.02:
        out.append(Suggestion("采样/控制周期", f"把采样周期从 {m.dt:.3f}s 降到 ≤0.01s（或提高控制频率）",
                              "周期偏大时，PID 的 D 项会被量化噪声放大、且快过程测不准",
                              "曲线更平滑、D 项可用",
                              "改周期后重采，比较噪声 σ 与超调"))
    if not out:
        out.append(Suggestion("保持", "指标已在经验区间，建议做**重复性验证**（同工况采 3 次）",
                              "无单点明显异常",
                              "三次 σ%、t_r 波动 <10%",
                              "同一工况重复三次，看指标离散度"))
    return out


def suggestions_md(sugs: list[Suggestion]) -> str:
    return "\n".join([f"{i+1}. " + s.line() for i, s in enumerate(sugs)])


# --------------------------------------------------------------------------- #
# 4. 画图（matplotlib，中文字体，输出 PNG 供 Word 用）
# --------------------------------------------------------------------------- #
def plot(t: np.ndarray, y: np.ndarray, m: StepMetrics, out_png: str | Path,
         title: str = "阶跃响应") -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    # 中文字体：优先微软雅黑（KB：PIL/matplotlib 默认字体不含中文）
    for cand in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if Path(cand).exists():
            font_manager.fontManager.addfont(cand)
            matplotlib.rcParams["font.sans-serif"] = [font_manager.FontProperties(fname=cand).get_name()]
            break
    matplotlib.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=130)
    ax.plot(t - t[0], y, lw=1.4, color="#1f4e79", label="实测响应")
    ax.axhline(m.y_ss, ls="--", lw=1, color="#888", label=f"稳态 {m.y_ss:.4g}")
    ax.axhspan(m.y_ss - abs(m.step) * 0.02, m.y_ss + abs(m.step) * 0.02,
               color="#ffd966", alpha=0.30, label="±2% 带")
    if m.target is not None:
        ax.axhline(m.target, ls=":", lw=1.2, color="#c00", label=f"目标 {m.target:g}")
    if m.peak_time is not None:
        ax.annotate(f"峰值 {m.peak:.4g}\nσ={m.overshoot_pct:.1f}%",
                    xy=(m.peak_time, m.peak), xytext=(m.peak_time, m.peak),
                    fontsize=8, color="#7f2704",
                    xycoords="data", textcoords="data",
                    arrowprops=dict(arrowstyle="->", color="#7f2704", lw=0.8))
    ax.set_xlabel("时间 (s)")
    ax.set_ylabel("输出")
    ax.set_title(title)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return str(out)


# --------------------------------------------------------------------------- #
# 5. 面向 Agent 的工具接口：一次调用给出"指标 + 曲线图 + 规则建议"
# --------------------------------------------------------------------------- #
def analyze_step_data(path: str, target: float | None = None, title: str | None = None) -> str:
    """读数据 → 算指标 → 画图 → 给规则化建议，返回一段 Markdown。

    这是**确定性**的：同样的数据永远得到同样的数字与建议，模型只在此基础上做判断。
    """
    from ..config import OUT_DIR

    t, y = read_data(path)
    m = analyze(t, y, target=target)
    name = Path(path).stem
    ttl = title or f"{name} 阶跃响应"
    try:
        png = plot(t, y, m, OUT_DIR / f"{name}-阶跃响应.png", title=ttl)
        pic = f"\n**曲线图**：`{png}`\n"
    except Exception as e:  # 画图失败不影响指标
        pic = f"\n（画图失败：{type(e).__name__}: {e}）\n"

    md = [f"### {ttl}", "", m.table(), pic, "### 规则化建议（本地规则，非模型猜测）", "",
          suggestions_md(suggest_pid(m)), ""]
    md.append("> 说明：以上指标由本地算法计算，可与手算/示波器读数交叉验证；"
              "建议遵循「一次只改一个参数、每次 10–20%」的纪律。")
    return "\n".join(md)
