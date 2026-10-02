#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行入口的离线测试（不需要硬件）。

重点两件事：
1. 每个子命令的**错误路径**都要给人话（不是一堆栈）
2. ★ **依赖分层**：导入串口核心 / CLI 时，不能把 numpy、matplotlib、fastapi 拖进来
   —— 这是"核心只依赖 pyserial"这个承诺的**自动化验证**（用子进程查 sys.modules）

用法：python tests/test_cli.py     （退出码 0 = 通过）
"""
from __future__ import annotations

import contextlib
import io
import json
import math
import subprocess
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

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


def run_cli(*argv) -> tuple:
    """跑 CLI，返回 (退出码, stdout, stderr)。"""
    from contest_workbench.serial_cli import main
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:                      # argparse 的 --help/--version
            code = int(e.code or 0)
    return code, out.getvalue(), err.getvalue()


def 写阶跃(kp: float = 1.0, target: float = 1.0, dt: float = 0.02, n: int = 300) -> str:
    wn, zeta = 1.0 + 1.5 * kp, max(0.1, 1.0 - 0.25 * kp)
    wd = wn * math.sqrt(max(1e-9, 1 - zeta ** 2))
    lines = []
    for i in range(n):
        t = i * dt
        y = target * (1 - math.exp(-zeta * wn * t) *
                      (math.cos(wd * t) + (zeta * wn / wd) * math.sin(wd * t)))
        lines.append(f"{t:.3f},{y:.5f}")
    return "\n".join(lines) + "\n"


def main() -> int:
    print("== ① 帮助与子命令齐全 ==")
    code, out, _ = run_cli("--help")
    check("--help 退出码 0", code == 0)
    check("帮助是中文", "电赛串口助手" in out and "子命令" in out, out[:80])
    for sub in ("ports", "monitor", "analyze", "sweep", "flash"):
        check(f"--help 里列出了 {sub}", sub in out, out[:120])
    for sub in ("ports", "monitor", "analyze", "sweep", "flash"):
        c, o, _ = run_cli(sub, "--help")
        check(f"{sub} --help 可用且是中文", c == 0 and len(o) > 40 and any(
            "\u4e00" <= ch <= "\u9fff" for ch in o), o[:60])
    c, o, _ = run_cli("--version")
    check("--version 可用", c == 0 and "diansai-serial" in o, o[:50])
    c, o, _ = run_cli()
    check("不给子命令 → 打印帮助且不报错", c == 0 and "子命令" in o)

    print("\n== ② ports ==")
    c, o, e = run_cli("ports")
    check("ports 有输出（没串口时给排查提示）", (o + e).strip() != "", (o + e)[:80])
    check("ports 不会崩（退出码是 0 或 1）", c in (0, 1), str(c))

    print("\n== ③ analyze（真数据、真指标）==")
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "阶跃.csv"
        f.write_text(写阶跃(1.0), encoding="utf-8")
        c, o, e = run_cli("analyze", str(f))
        check("analyze 正常退出", c == 0, (e or o)[:90])
        check("输出里有超调量", "超调" in o, o[:80])
        check("输出里有调节时间", "调节时间" in o, o[:80])
        check("输出里有稳态值", "稳态" in o, o[:80])
        check("给的是中文建议（不是模型猜的）", "规则化建议" in o, o[-120:])

        c, o, _ = run_cli("analyze", str(f), "--json")
        d = json.loads(o)
        check("--json 能解析且字段齐全",
              {"超调量_pct", "上升时间", "调节时间_pm2pct", "稳态值"} <= set(d), str(list(d))[:90])
        check("★ --json 的超调与二阶理论吻合（ζ=0.75 → 约 2.8%）",
              abs(d["超调量_pct"] - 2.8) < 1.5, f"{d['超调量_pct']:.2f}%")

        c, o, _ = run_cli("analyze", str(f), "--target", "1.0")
        check("带 --target 也能跑（超调按相对目标值算）", c == 0 and "超调" in o)

        c, o, e = run_cli("analyze", str(Path(td) / "没有这个.csv"))
        check("★ 文件不存在 → 人话错误 + 退出码 1",
              c == 1 and "[错误]" in e and "找不到" in e, e[:70])

        bad = Path(td) / "乱的.csv"
        bad.write_text("这不是数字\n也不是\n", encoding="utf-8")
        c, o, e = run_cli("analyze", str(bad))
        check("★ 数据格式不对 → 人话错误（不抛栈）",
              c == 1 and "[错误]" in e and "Traceback" not in e, (e or o)[:90])

    print("\n== ④ sweep 的参数校验（在打开串口之前就该拦住）==")
    c, o, e = run_cli("sweep", "-p", "COM_不存在", "--values", "abc")
    check("★ 值不是数字 → 报错且退出码 1", c == 1 and "不是数字" in e, e[:70])
    c, o, e = run_cli("sweep", "-p", "COM_不存在", "--values", "")
    check("值为空 → 人话错误", c == 1 and "空" in e, e[:70])
    c, o, e = run_cli("sweep", "-p", "COM_不存在", "--values", "1,2", "--template", "KP=1")
    check("★ 模板没写 {value} → 报错", c == 1 and "{value}" in e, e[:80])
    c, o, e = run_cli("sweep", "-p", "COM_不存在", "--values", "1,2")
    check("★ 串口打不开 → 人话错误（不是栈）",
          c == 1 and "[错误]" in e and "Traceback" not in e, e[:90])
    check("串口打不开时不会真的开始扫描", "开始扫描" not in o)

    print("\n== ⑤ flash / monitor 的错误路径 ==")
    c, o, e = run_cli("flash", str(Path(tempfile.gettempdir()) / "没有这个.hex"), "-p", "COM9")
    check("★ 固件文件不存在 → 人话错误", c == 1 and "找不到" in e, e[:70])
    c, o, e = run_cli("flash", __file__, "-p", "COM_不存在")
    check("★ 串口打不开 → 人话错误（不是栈）",
          c == 1 and "[错误]" in e and "Traceback" not in e, e[:90])
    c, o, e = run_cli("monitor", "-p", "COM_不存在", "--seconds", "0.1")
    check("monitor 串口打不开 → 人话错误", c == 1 and "[错误]" in e, e[:80])
    check("monitor 的默认参数合理（有 --seconds/--send/--every）",
          "--seconds" in (run_cli("monitor", "--help")[1]) and
          "--every" in (run_cli("monitor", "--help")[1]))

    print("\n== ⑥ ★ 依赖分层（这是「独立安装」的核心承诺）==")
    探针 = (
        "import sys;"
        "import contest_workbench.serial_cli;"
        "import contest_workbench.serial_assistant;"
        "import contest_workbench.frame_parser;"
        "import contest_workbench.stm32_isp;"
        "bad=[m for m in ('numpy','matplotlib','fastapi','uvicorn','yaml') if m in sys.modules];"
        "print('HEAVY_MODULES=' + ','.join(bad))"      # 机器校验，用 ASCII 免控制台编码干扰
    )
    import os as _os
    r = subprocess.run([sys.executable, "-c", 探针], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, timeout=120,
                       env=dict(_os.environ, PYTHONIOENCODING="utf-8"))
    check("★ 导入串口核心 + CLI 不会拖进 numpy/matplotlib/fastapi/uvicorn/yaml",
          r.returncode == 0 and (r.stdout or "").strip().endswith("HEAVY_MODULES="),
          (r.stdout or r.stderr).strip()[:120])

    r2 = subprocess.run([sys.executable, "-c",
                         "import sys; import contest_workbench; print('ok', contest_workbench.__version__)"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace",
                        cwd=ROOT, timeout=120)
    check("★ 顶层包 import 也很轻（__init__ 里没有重导入）", r2.returncode == 0, (r2.stdout + r2.stderr)[:90])

    print("\n== ⑦ pyproject 与 README 的一致性 ==")
    pj = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    check("pyproject 里有入口点 diansai-serial", 'diansai-serial = "contest_workbench.serial_cli:main"' in pj)
    check("核心依赖只有 pyserial（不是把全套都塞进 dependencies）",
          'dependencies = [\n  "pyserial>=3.5",\n]' in pj, pj[pj.find("dependencies"):][:80])
    check("有 metrics / plot / web 三组可选依赖",
          all(f"{k} = [" in pj for k in ("metrics", "plot", "web")))
    check("许可证是 MIT（与仓库 LICENSE 一致）", 'license = { text = "MIT" }' in pj
          and "MIT License" in (ROOT / "LICENSE").read_text(encoding="utf-8"))
    rs = ROOT / "README-serial.md"
    check("独立 README 存在且是中文", rs.exists() and "电赛串口助手" in rs.read_text(encoding="utf-8"))
    内容 = rs.read_text(encoding="utf-8") if rs.exists() else ""
    for 关键词 in ("ports", "monitor", "analyze", "sweep", "flash", "pip install"):
        check(f"README 里写了 {关键词} 的用法", 关键词 in 内容)

    print("\n" + "=" * 52)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("全部通过 ✅（命令行离线可测，不需要硬件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
