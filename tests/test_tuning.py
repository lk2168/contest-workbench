#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调参助手的可验证测试：用**解析解已知**的二阶系统检验指标算法。

方法（KB 教训：规则系统必须先过"已知正确样本"这一关）：
  取标准二阶系统 G(s)=ωn²/(s²+2ζωn s+ωn²)，单位阶跃的解析指标是：
      超调量   σ% = exp(-πζ/√(1-ζ²)) × 100%
      峰值时间 t_p = π / (ωn√(1-ζ²))
      调节时间 t_s(±2%) ≈ 4/(ζωn)
  把解析解与程序算出来的值比对，误差超容差就算失败。

用法：python tests/test_tuning.py     （退出码 0 = 通过）
"""
from __future__ import annotations

import math
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

import numpy as np                                                    # noqa: E402
from contest_workbench.tools.tuning import (analyze, parse_two_columns,    # noqa: E402
                                        suggest_pid)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


def second_order(zeta: float, wn: float, t_end: float = 10.0, dt: float = 0.002):
    """标准二阶系统的单位阶跃响应（欠阻尼/临界/过阻尼三种都支持）。"""
    t = np.arange(0.0, t_end, dt)
    if zeta < 1:                      # 欠阻尼
        wd = wn * math.sqrt(1 - zeta ** 2)
        y = 1 - np.exp(-zeta * wn * t) / math.sqrt(1 - zeta ** 2) * np.sin(wd * t + math.acos(zeta))
    elif abs(zeta - 1) < 1e-9:        # 临界阻尼
        y = 1 - np.exp(-wn * t) * (1 + wn * t)
    else:                             # 过阻尼（ζ>1）：用双曲函数形式，避免 sqrt(负数)
        s = wn * math.sqrt(zeta ** 2 - 1)
        y = 1 - np.exp(-zeta * wn * t) * (np.cosh(s * t) + (zeta * wn / s) * np.sinh(s * t))
    return t, y


def main() -> int:
    print("== 1. 解析解 vs 程序算（ζ=0.5, ωn=2）==")
    zeta, wn = 0.5, 2.0
    t, y = second_order(zeta, wn)
    m = analyze(t, y, target=1.0)

    sigma_expect = math.exp(-math.pi * zeta / math.sqrt(1 - zeta ** 2)) * 100
    tp_expect = math.pi / (wn * math.sqrt(1 - zeta ** 2))
    ts_expect = 4.0 / (zeta * wn)
    tr_expect = (1.0 - 0.4167 * zeta + 2.917 * zeta ** 2) / wn

    def rel(got, exp):
        return abs(got - exp) / abs(exp)

    check(f"超调量 σ% ≈ {sigma_expect:.2f}（算出 {m.overshoot_pct:.2f}）",
          rel(m.overshoot_pct, sigma_expect) < 0.05, f"相对误差 {rel(m.overshoot_pct, sigma_expect):.1%}")
    # t_p 的容差给 3%：起始点检测必然比真实阶跃晚几个采样点（二阶系统起始段上升慢），
    # 这是**定义差**而非算法错误 —— 报告里已把 t_start 作为计时基准一并给出。
    check(f"峰值时间 t_p ≈ {tp_expect:.3f}s（算出 {m.peak_time:.3f}）",
          rel(m.peak_time, tp_expect) < 0.03, f"相对误差 {rel(m.peak_time, tp_expect):.1%}")
    check(f"调节时间 t_s(±2%) ≈ {ts_expect:.3f}s（算出 {m.settle_time_2:.3f}）",
          rel(m.settle_time_2, ts_expect) < 0.15, f"相对误差 {rel(m.settle_time_2, ts_expect):.1%}")
    check(f"上升时间 t_r(10-90%) ≈ {tr_expect:.3f}s（算出 {m.rise_time:.3f}）",
          rel(m.rise_time, tr_expect) < 0.15, f"相对误差 {rel(m.rise_time, tr_expect):.1%}")
    check("稳态值 ≈ 1.0", abs(m.y_ss - 1.0) < 0.002, f"算出 {m.y_ss:.5f}")
    check("稳态误差 ≈ 0（给了 target）", m.steady_error is not None and m.steady_error < 0.005,
          f"算出 {m.steady_error}")
    check("采样周期估计正确（0.002s）", abs(m.dt - 0.002) < 1e-6, f"算出 {m.dt}")

    print("\n== 2. 已知答案的极端情形 ==")
    # 过阻尼 ζ=1.5：不应有超调
    t2, y2 = second_order(1.5, 2.0, t_end=15.0)
    m2 = analyze(t2, y2, target=1.0)
    check("过阻尼（ζ=1.5）超调 ≈ 0", m2.overshoot_pct is not None and m2.overshoot_pct < 1.0,
          f"算出 {m2.overshoot_pct:.3f}%")

    # 纯比例控制带静差：y → 0.8（模拟 Kp 小），target=1 → 稳态误差 0.2
    t3 = np.arange(0.0, 6.0, 0.002)
    y3 = 0.8 * (1 - np.exp(-8 * t3))
    m3 = analyze(t3, y3, target=1.0)
    check("静差 0.2 被算出（稳态误差）", abs(m3.steady_error - 0.2) < 0.01, f"算出 {m3.steady_error:.4f}")

    print("\n== 3. 数据解析（CSV / 串口日志 / 单列）==")
    t4, y4 = parse_two_columns("t,y\n0.000,0.0\n0.010,0.12\n0.020,0.35\n0.030,0.61\n"
                               "0.040,0.82\n0.050,0.95\n0.060,1.00\n0.070,1.01\n0.080,1.00\n")
    check("CSV 两列解析（表头被跳过，9 行数据）",
          len(y4) == 9 and abs(y4[-1] - 1.0) < 1e-9, f"解析到 {len(y4)} 个：{y4.tolist()}")

    t5, y5 = parse_two_columns("[12:00:01.123] 0.000 0.0\n[12:00:01.133] 0.010 0.55\n"
                               "[12:00:01.143] 0.020 0.90\n[12:00:01.153] 0.030 1.02\n"
                               "[12:00:01.163] 0.040 1.00\n[12:00:01.173] 0.050 1.00\n"
                               "[12:00:01.183] 0.060 1.00\n[12:00:01.193] 0.070 1.00\n")
    check("串口日志（带时间戳前缀）解析出最后两列", len(y5) == 8 and abs(y5[1] - 0.55) < 1e-9,
          f"{y5.tolist()}")

    t6, y6 = parse_two_columns("\n".join(["0.12", "0.35", "0.61", "0.82", "0.95", "1.00", "1.01", "1.00"]))
    check("单列数据自动补采样序号", len(y6) == 8 and t6[-1] == 7.0, f"t={t6.tolist()}")

    print("\n== 4. 建议规则（确定性：同输入同输出）==")
    s_big = suggest_pid(m)                      # 超调 16% + dt=0.002 → 应给"重复性验证/保持"
    s_over = suggest_pid(analyze(*second_order(0.25, 2.0), target=1.0))   # 超调很大 + 振荡
    txt_over = "\n".join(s.line() for s in s_over)
    check("大超调样本给出『降 Kp』建议", "降 Kp" in txt_over, txt_over[:120])
    txt_big = "\n".join(s.line() for s in s_big)
    check("指标良好样本给出『保持/重复性验证』", ("保持" in txt_big or "重复" in txt_big), txt_big[:120])
    check("建议是纯函数（两次调用结果一致）",
          [s.line() for s in suggest_pid(m)] == [s.line() for s in suggest_pid(m)])

    print("\n== 5. 健壮性 ==")
    for bad, why in [("", "空文件"), ("abc\ndef\n", "没有数字"), ("1\n2\n3\n", "点太少")]:
        try:
            parse_two_columns(bad)
            check(f"坏数据应报错：{why}", False, "居然没报错")
        except ValueError:
            check(f"坏数据报错：{why}", True)

    print("\n== 6. 端到端：写文件 → 读文件 → 出图 ==")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "step.csv"
        p.write_text("t,y\n" + "\n".join(f"{a:.4f},{b:.6f}" for a, b in zip(t[:3000], y[:3000])),
                     encoding="utf-8")
        from contest_workbench.tools.tuning import read_data, plot
        t7, y7 = read_data(p)
        m7 = analyze(t7, y7, target=1.0)
        check("从文件读回并算出超调", m7.overshoot_pct is not None and 10 < m7.overshoot_pct < 25,
              f"{m7.overshoot_pct}")
        try:
            png = plot(t7, y7, m7, Path(td) / "step.png")
            check("生成 PNG 曲线图", Path(png).exists() and Path(png).stat().st_size > 5000,
                  f"{Path(png).stat().st_size if Path(png).exists() else 0} 字节")
        except Exception as e:
            check("生成 PNG 曲线图", False, f"{type(e).__name__}: {e}")

    print("\n" + "=" * 52)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("全部通过 ✅（纯本地计算，不消耗 API）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
