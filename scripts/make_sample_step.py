#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成一份"像真实采集"的阶跃响应示例数据，供 tune 子命令试跑。

刻意加了三个真实世界特征（而不是干净的仿真曲线）：
  1. 阶跃前有 0.3s 基线（真实的采集往往先采一段静息值）
  2. 有测量噪声（σ=0.006）
  3. 有稳态静差（终值 0.94 而非 1.00，像只有比例控制的情形）
用法：python scripts/make_sample_step.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples" / "step-response-sample.csv"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main() -> int:
    rng = np.random.default_rng(20260930)
    dt = 0.002
    t_base = np.arange(0.0, 0.3, dt)                    # 基线段
    t_step = np.arange(0.0, 4.0, dt)                    # 阶跃后
    zeta, wn, amp, offset = 0.35, 6.0, 0.94, 0.02       # 欠阻尼（会振荡）+ 静差
    wd = wn * math.sqrt(1 - zeta ** 2)
    y_step = amp * (1 - np.exp(-zeta * wn * t_step) / math.sqrt(1 - zeta ** 2)
                    * np.sin(wd * t_step + math.acos(zeta))) + offset
    y_base = np.full_like(t_base, offset)
    y = np.concatenate([y_base, y_step]) + rng.normal(0, 0.006, len(t_base) + len(t_step))
    t = np.concatenate([t_base, t_base[-1] + dt + t_step])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        f.write("# 阶跃响应示例数据（列：时间(s), 输出）\n")
        f.write("# 生成脚本：scripts/make_sample_step.py\n")
        f.write("# 特征：0.3s 基线 + 噪声 σ=0.006 + 稳态静差(终值≈0.96)\n")
        f.write("t,y\n")
        for a, b in zip(t, y):
            f.write(f"{a:.4f},{b:.6f}\n")
    print(f"已生成：{OUT}（{len(t)} 个采样点，{t[-1]:.2f}s）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
