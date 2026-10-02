# -*- coding: utf-8 -*-
"""收尾：CI 加"独立安装"任务 + 主 README 链接 + 十四套全量 + 提交。"""
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"D:\work\contest-workbench")
PY = sys.executable

# ① CI：加一个"干净环境独立安装"的任务
wf = ROOT / ".github" / "workflows" / "tests.yml"
wt = wf.read_text(encoding="utf-8")
if "independent-install" not in wt:
    job = """
  # ★ 独立安装验证：核心只装 pyserial，装完必须能列串口、且不能拖进重依赖
  independent-install:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - name: 只装核心（应该只拉 pyserial）
        run: |
          python -m pip install --upgrade pip
          pip install .
      - name: 核心装完不该有 numpy/fastapi/matplotlib
        run: |
          python - <<'PY'
          import sys
          import contest_workbench.serial_assistant, contest_workbench.frame_parser
          import contest_workbench.stm32_isp, contest_workbench.serial_cli
          heavy = [m for m in ("numpy", "fastapi", "matplotlib", "uvicorn", "yaml")
                   if m in sys.modules]
          print("拖进来的重依赖:", heavy)
          assert not heavy, f"核心安装不该依赖 {heavy}"
          PY
      - name: 命令行入口可用（中文帮助）
        run: |
          diansai-serial --help
          diansai-serial --version
          diansai-serial ports || true        # 无串口时退出码 1，属正常
      - name: 缺 numpy 时给的是人话（不是栈）
        run: |
          printf '0.0,0.0\\n0.1,0.5\\n' > /tmp/x.csv
          if diansai-serial analyze /tmp/x.csv 2>/tmp/err.txt; then exit 1; fi
          cat /tmp/err.txt
          grep -q "metrics" /tmp/err.txt
      - name: 装 [metrics] 后能算指标
        run: |
          pip install ".[metrics]"
          python -c "import contest_workbench.sweep; print('sweep 可导入')"
"""
    wt = wt.rstrip("\n") + "\n" + job
    wf.write_text(wt, encoding="utf-8")
    print("  [ok] CI 加了 independent-install 任务")
else:
    print("  （CI 里已有 independent-install）")

# ② 主 README：指向独立 README
rp = ROOT / "README.md"
rt = rp.read_text(encoding="utf-8")
if "README-serial.md" not in rt:
    # 找串口相关段落插一句；找不到就追加到文件末尾
    if "串口" in rt:
        i = rt.find("串口")
        行尾 = rt.find("\n", rt.find("串口", rt.rfind("\n", 0, i)))
        rt = (rt[:行尾 + 1]
              + "\n> **只想要串口工具？** 串口部分可以单独安装使用（命令行 + 独立说明）："
                "见 [`README-serial.md`](README-serial.md)，装法 `pip install .`（核心只依赖 pyserial）。\n"
              + rt[行尾 + 1:])
    else:
        rt = rt.rstrip("\n") + ("\n\n> **只想要串口工具？** 见 [`README-serial.md`](README-serial.md)"
                                "（`pip install .`，核心只依赖 pyserial）。\n")
    rp.write_text(rt, encoding="utf-8")
    print("  [ok] 主 README 已链接独立说明")
else:
    print("  （主 README 已有链接）")

# ③ 跑十四套
SUITES = ["test_offline", "test_tuning", "test_web", "test_launcher", "test_frontend_runtime",
          "test_ask", "test_learning", "test_profile", "test_serial", "test_serial_api",
          "test_frame", "test_stm32_isp", "test_sweep", "test_cli"]
print("\n== 十四套测试 ==")
total, bad = 0, 0
for s in SUITES:
    r = subprocess.run([PY, str(ROOT / "tests" / f"{s}.py")], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, timeout=600)
    m = re.search(r"通过 (\d+) 项", r.stdout)
    n = int(m.group(1)) if m else 0
    total += n
    if r.returncode:
        bad += 1
        print(f"  ❌ {s}")
        print("\n".join(r.stdout.splitlines()[-8:]))
    else:
        print(f"  OK   {s:<22} {n:>4} 项")
print(f"\n  合计 {total} 项 · 失败套数 {bad}")
if bad:
    sys.exit(1)

rt = rp.read_text(encoding="utf-8")
rt = re.sub(r"# 6\) 测试：十[三四]套共 \d+ 项", f"# 6) 测试：十四套共 {total} 项", rt)
rp.write_text(rt, encoding="utf-8")

msg = f"""feat(cli): 独立安装通道 —— 串口这套东西可以 pip install 单独用了

一个仓库、一套源码，不做分叉：串口部分既能作为工作台的一部分，也能单独装：
    pip install .                       # 核心：只有 pyserial
    pip install ".[metrics]"            # 加 numpy：算指标、扫参数
    pip install ".[plot]"               # 加 matplotlib：画曲线
    pip install ".[web]"                # 网页版工作台

新增：
- `pyproject.toml`：依赖**分层**（核心只有 pyserial；numpy/matplotlib/fastapi 各自一组可选依赖），
  入口点 `diansai-serial`，MIT 许可与仓库 LICENSE 一致
- `contest_workbench/serial_cli.py`：命令行入口，5 个子命令
  ports / monitor / analyze / sweep / flash —— 全部中文帮助与中文错误
  （还专门把 argparse 内置的 usage:/options: 也换成了中文）
- `README-serial.md`：单独安装使用说明（含常见问题），主 README 已链接

设计要点：
- 缺可选依赖时给的是**人话**：「这个功能需要 numpy（算指标用），装它：pip install diansai-serial[metrics]」
  而不是一坨 ImportError 栈；想调试可设 DIANSAI_DEBUG=1 看完整调用栈
- 命令行里所有错误走 stderr、退出码 1（方便脚本接）

★ 真实验证（新建干净虚拟环境，不继承当前环境）：
  ① pip install . 之后环境里**只有 pyserial**（numpy/fastapi/matplotlib/uvicorn/pyyaml 都不在）
  ② `diansai-serial --help` 能跑、而且全是中文
  ③ 没有 numpy 时 analyze 给人话提示（退出码 1，无调用栈）
  ④ 装上 [metrics] 后 analyze 真算出指标（60 点 → 超调 2.18%、上升 0.86s）
  ⑤ 导入串口核心 + CLI 不会拖进任何重依赖（测试里用子进程查 sys.modules 断言）

测试：新增 tests/test_cli.py（48 项，含"依赖分层"断言）；CI 新增 independent-install 任务，
在干净环境里重复上面的验证；十四套共 {total} 项全过。
"""
mf = ROOT / ".git-msg-cli.txt"
mf.write_text(msg, encoding="utf-8")
subprocess.run(["git", "add", "-A"], cwd=ROOT, capture_output=True)
r = subprocess.run(["git", "-c", "user.name=Lu Kuo",
                    "-c", "user.email=288989345+lk2168@users.noreply.github.com",
                    "commit", "-q", "-F", str(mf)], cwd=ROOT, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
mf.unlink(missing_ok=True)
print("  提交:", (r.stdout or r.stderr).strip()[:70] or "done")
print("  " + subprocess.run(["git", "log", "--oneline", "-1"], cwd=ROOT, capture_output=True,
                            text=True).stdout.strip())
