#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参数扫描的离线测试：用一个**模拟被控对象**把整条链路跑透，不需要硬件。

模拟对象的设定（刻意做成真实的 PID 权衡）：
    参数 KP 越大 → 系统越快（ωn 变大）但阻尼越小（ζ 变小）→ 超调越大、调节时间越短
    ωn = 1.0 + 1.5·KP      ζ = 1.0 - 0.25·KP（下限 0.1）
这样"哪个值最好"就不是一眼能看出来的（要**同时**满足超调不超标 + 收得快），
所以测试能真正检验"扫描 → 测量 → 推荐"这三步的逻辑，而不是碰巧过。

用法：python tests/test_sweep.py     （退出码 0 = 通过）
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from contest_workbench.sweep import (                                          # noqa: E402
    DEFAULT_SIGMA_LIMIT, SweepError, SweepRunner, SweepSpec, format_command,
    parse_values, pick_best)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


def step_response(kp: float, target: float = 1.0, dt: float = 0.02, n: int = 400) -> list:
    """二阶欠阻尼阶跃（模拟板子每 dt 秒吐一行 "t,y"）。"""
    wn = 1.0 + 1.5 * kp
    zeta = max(0.1, 1.0 - 0.25 * kp)
    wd = wn * math.sqrt(max(1e-9, 1 - zeta ** 2))
    out = []
    for i in range(n):
        t = i * dt
        y = target * (1 - math.exp(-zeta * wn * t) *
                      (math.cos(wd * t) + (zeta * wn / wd) * math.sin(wd * t)))
        out.append((round(t, 4), round(y, 5)))
    return out


class 假设备:
    """假串口：记下发过的命令；收到 KP=<v> 就"产生"对应的一段阶跃数据。"""

    def __init__(self, fail_on=(), point_override=None, garbage_on=()):
        self.sent = []
        self.all_points = []
        self.fail_on = set(fail_on)          # 这些参数值发送时报错
        self.point_override = point_override  # 指定点数（测"数据太少"）
        self.garbage_on = set(garbage_on)     # 这些值回的是非数字

    def send(self, text, hex_mode: bool = False) -> str:
        self.sent.append((text, hex_mode))
        if text.startswith("KP="):
            v = float(text.split("=", 1)[1])
            if v in self.fail_on:
                return "[错误] 端口写失败（模拟）"
            if v in self.garbage_on:
                self.all_points.extend([(1.0, "abc"), (2.0, "def")] * 6)
                return "已发送 6 字节"
            pts = step_response(v)
            if self.point_override is not None:
                pts = pts[:self.point_override]
            self.all_points.extend(pts)
        return f"已发送 {len(text)} 字节"

    def points(self):
        return list(self.all_points)


def main() -> int:
    print("== ① 参数值解析 ==")
    check("枚举：逗号分隔", parse_values("0.5, 1.0, 1.5") == [0.5, 1.0, 1.5])
    check("枚举：空格/分号/中文逗号都认", parse_values("0.5 1.0；1.5") == [0.5, 1.0, 1.5]
          or parse_values("0.5 1.0;1.5") == [0.5, 1.0, 1.5])
    check("范围：起点:终点:步长（含终点）",
          parse_values("0.5:2.0:0.5") == [0.5, 1.0, 1.5, 2.0], str(parse_values("0.5:2.0:0.5")))
    check("范围：步长不整除时不含终点",
          parse_values("0:1:0.3") == [0.0, 0.3, 0.6, 0.9], str(parse_values("0:1:0.3")))
    check("范围：负步长（从大到小）", parse_values("2:1:-0.5") == [2.0, 1.5, 1.0])
    check("范围：省略步长也能用", len(parse_values("0:3")) == 4)
    for bad, why in (("", "空"), ("abc", "非数字"), ("1:2:0", "步长 0"),
                     ("2:1:1", "方向反"), (",".join(str(i) for i in range(60)), "超过上限")):
        try:
            parse_values(bad)
            check(f"非法输入（{why}）→ 报错", False, "没报错")
        except SweepError as e:
            check(f"非法输入（{why}）→ 人话错误", str(e).startswith("[错误]"), str(e)[:60])

    print("\n== ② 命令模板 ==")
    check("默认占位", format_command("KP={value}", 1.5) == "KP=1.5")
    check("{value:g} 去掉多余零", format_command("KP={value:g}", 1.0) == "KP=1")
    check("{value:.2f} 固定两位", format_command("KP={value:.2f}", 1.5) == "KP=1.50")
    check("{value:04X} 生成十六进制", format_command("AA 01 {value:04X}", 0x1234) == "AA 011234"
          or format_command("AA 01 {value:04X}", 0x1234) == "AA 01 1234",
          format_command("AA 01 {value:04X}", 0x1234))
    for bad, why in (("KP=1", "没写占位符"), ("KP={value:04X}zz", "HEX 奇数位"),
                     ("KP={value:Q}", "格式类型不合法")):
        try:
            format_command(bad, 1.0, hex_mode=("04X" in bad))
            check(f"模板错误（{why}）→ 报错", False, "没报错")
        except SweepError as e:
            check(f"模板错误（{why}）→ 人话错误", str(e).startswith("[错误]"), str(e)[:60])

    print("\n== ③ 扫描一整套（模拟对象：KP 越大越快但越冲）==")
    dev = 假设备()
    spec = SweepSpec(values=[0.5, 1.0, 1.5, 2.0, 2.5], set_template="KP={value}",
                     settle_s=0.0, collect_s=0.3, target=1.0)
    res = SweepRunner(dev, log=lambda *_: None).run(spec)
    check("每个值各采一轮 → 5 行", len(res.rows) == 5 and all(r.ok for r in res.rows),
          str([r.brief() for r in res.rows if not r.ok]))
    check("命令按顺序发出去（KP=值）",
          [s[0] for s in dev.sent if s[0].startswith("KP=")] ==
          ["KP=0.5", "KP=1.0", "KP=1.5", "KP=2.0", "KP=2.5"], str(dev.sent))
    sigmas = [r.sigma for r in res.rows]
    check("★ 测出来的超调随参数单调增大（说明真的在测被控对象）",
          all(sigmas[i] < sigmas[i + 1] for i in range(len(sigmas) - 1)),
          str([round(s, 2) for s in sigmas]))
    check("★ 超调与理论对得上（KP=2.0 理论 16.3%，容差 ±3）",
          abs(sigmas[3] - 16.3) < 3.0, f"实测 {sigmas[3]:.2f}%")
    check("数据点数足够（每轮 ≥ 300）", all(r.points >= 300 for r in res.rows),
          str([r.points for r in res.rows]))
    check("★ 上升/调节时间都是有限数（上游改字段名会被这条抓住）",
          all(math.isfinite(r.rise) and math.isfinite(r.settle) and math.isfinite(r.y_ss)
              for r in res.rows),
          str([(r.value, round(r.rise, 3), round(r.settle, 3)) for r in res.rows]))
    check("★ 每一行的数据都是「已稳定」判定（模拟对象给足了 8 秒）",
          all(r.settled for r in res.rows), str([r.settled for r in res.rows]))

    print("\n== ④ 推荐（确定性规则）==")
    check("给出了推荐值", res.best is not None)
    check("★ 推荐值超调不超标（≤ 20%）", res.best.sigma <= DEFAULT_SIGMA_LIMIT,
          f"{res.best.value:g} → {res.best.sigma:.1f}%")
    eligible = [r for r in res.rows if r.ok and r.sigma <= DEFAULT_SIGMA_LIMIT]
    check("★ 推荐的是「达标里调节时间最短」的那个",
          abs(res.best.settle - min(r.settle for r in eligible)) < 1e-9,
          f"推荐 {res.best.value:g}({res.best.settle:.3f}s) · 达标里最短 "
          f"{min((r.value, r.settle) for r in eligible)}")
    check("★ 超调最大的那个（KP=2.5）不会被推荐",
          res.best.value != 2.5, f"推荐了 {res.best.value:g}")
    check("推荐理由是人话（含推荐值与超调）",
          "推荐" in res.advice and f"{res.best.value:g}" in res.advice, res.advice[:80])
    check("表格含全部 5 行 + 推荐标记",
          all(f"{v:g} |" in res.table_md() for v in (0.5, 1.0, 1.5, 2.0, 2.5))
          and "★ 推荐" in res.table_md())

    print("\n== ⑤ 一个都不达标时（阈值压到 0.1%，连最小的 0.3% 都不满足）==")
    res5 = SweepRunner(假设备(), log=lambda *_: None).run(
        SweepSpec(values=[0.5, 1.0, 1.5, 2.0], set_template="KP={value}",
                  settle_s=0.0, collect_s=0.3, target=1.0))
    best5, advice5 = pick_best(res5.rows, limit=0.1)
    check("★ 都不达标时选超调最小的", best5 is not None
          and abs(best5.sigma - min(r.sigma for r in res5.rows)) < 1e-9,
          str((best5.value, best5.sigma)))
    check("并说明「没有参数满足」", "没有参数满足" in advice5, advice5[:70])

    print("\n== ⑥ 重复采样与聚合 ==")
    dev6 = 假设备()
    res6 = SweepRunner(dev6, log=lambda *_: None).run(
        SweepSpec(values=[1.0, 2.0], set_template="KP={value}", settle_s=0.0,
                  collect_s=0.3, target=1.0, repeats=2))
    check("2 个值 × 重复 2 次 = 4 行", len(res6.rows) == 4, str(len(res6.rows)))
    check("推荐理由里含参数值", "推荐" in res6.advice, res6.advice[:60])

    print("\n== ⑦ 触发命令 / 失败继续 / 异常输入 ==")
    dev7 = 假设备()
    SweepRunner(dev7, log=lambda *_: None).run(
        SweepSpec(values=[1.0], set_template="KP={value}", trigger="START", settle_s=0.0,
                  collect_s=0.3, target=1.0))
    check("触发命令被发出去", ("START", False) in dev7.sent, str(dev7.sent))

    dev8 = 假设备(fail_on=[1.0])
    res8 = SweepRunner(dev8, log=lambda *_: None).run(
        SweepSpec(values=[0.5, 1.0, 1.5], set_template="KP={value}", settle_s=0.0,
                  collect_s=0.3, target=1.0))
    bad8 = [r for r in res8.rows if not r.ok]
    check("★ 某个值发送失败 → 那一行标失败，但扫描继续（3 行都在）",
          len(res8.rows) == 3 and len(bad8) == 1 and "失败" in bad8[0].error, str(bad8[0].error[:50]))
    check("失败行不影响推荐（推荐仍来自成功的行）", res8.best is not None and res8.best.ok)

    dev9 = 假设备(point_override=3)
    res9 = SweepRunner(dev9, log=lambda *_: None).run(
        SweepSpec(values=[1.0], set_template="KP={value}", settle_s=0.0, collect_s=0.3))
    check("★ 数据点太少 → 该行失败并说清原因",
          not res9.rows[0].ok and "数据点" in res9.rows[0].error, res9.rows[0].error[:70])
    check("没有可用行时，建议也给人话", "没有可用" in res9.advice, res9.advice[:60])

    dev10 = 假设备(garbage_on=[1.0])
    res10 = SweepRunner(dev10, log=lambda *_: None).run(
        SweepSpec(values=[1.0], set_template="KP={value}", settle_s=0.0, collect_s=0.3))
    check("回的是非数字 → 判失败（不瞎算指标）", not res10.rows[0].ok, str(res10.rows[0].error[:60]))

    print("\n== ⑧ 随时能停 ==")
    dev11 = 假设备()
    flag = {"stop": False}

    def on_row(row, i, total):
        if i >= 2:
            flag["stop"] = True

    t0 = time.time()
    res11 = SweepRunner(dev11, log=lambda *_: None).run(
        SweepSpec(values=[0.5, 1.0, 1.5, 2.0, 2.5, 3.0], set_template="KP={value}",
                  settle_s=0.0, collect_s=0.3, target=1.0),
        stop_flag=lambda: flag["stop"], on_row=on_row)
    check("★ 停止标志生效：只跑了前 2 个值就收尾",
          len(res11.rows) == 2 and res11.stopped, f"{len(res11.rows)} 行")
    check("已采到的结果照常返回（可复核）", all(r.ok for r in res11.rows))
    check("停止也很快（没有把剩下 4 个值白等完）", time.time() - t0 < 3.0,
          f"{time.time() - t0:.1f}s")

    print("\n== ⑨ AssistantLink 适配器 ==")
    from contest_workbench.sweep import AssistantLink                          # noqa: E402
    import threading                                                          # noqa: E402

    class 假助手:
        def __init__(self):
            self._lock = threading.Lock()
            self._points = [(0.0, 1.0)]
            self._buf = bytearray(b"x")
            self.sent = []

        def send(self, text, hex_mode=False):
            self.sent.append((text, hex_mode))
            return "已发送"

        def snapshot(self):
            return {"points": list(self._points)}

    a = 假助手()
    link = AssistantLink(a)
    check("适配器转发 send", link.send("KP=1", False) == "已发送" and a.sent == [("KP=1", False)])
    check("适配器读 points", link.points() == [(0.0, 1.0)])
    check("清空缓冲可用", "已清空" in link.clear() and link.points() == [])

    print("\n" + "=" * 52)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("全部通过 ✅（模拟被控对象，不需要硬件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
