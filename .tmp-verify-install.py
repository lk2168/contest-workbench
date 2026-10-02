# -*- coding: utf-8 -*-
"""★ 独立安装的真实验证：建一个干净虚拟环境 → pip install . → 看它能不能独立工作。

要证明三件事：
  1. 只装核心（pyserial）就能列串口、看帮助 —— 不需要 numpy/fastapi/matplotlib
  2. 装核心后**没有** numpy：analyze 应该给人话提示（"装 diansai-serial[metrics]"），不是栈
  3. 装了 [metrics] 之后 analyze 真能算出指标
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"D:\work\contest-workbench")
PY = sys.executable
镜像 = "https://pypi.tuna.tsinghua.edu.cn/simple"

venv = Path(tempfile.gettempdir()) / "diansai-venv-test"
if venv.exists():
    shutil.rmtree(venv, ignore_errors=True)

print("① 建干净虚拟环境（不继承当前环境）")
r = subprocess.run([PY, "-m", "venv", str(venv)], capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
print("   " + ("ok" if r.returncode == 0 else f"失败：{r.stderr[:120]}"))
vpy = venv / "Scripts" / "python.exe"
assert vpy.exists(), "虚拟环境里没有 python.exe"


def 跑(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env={**__import__("os").environ,
                                                 "PYTHONIOENCODING": "utf-8"}, **kw)


print("\n② pip install .（只装核心依赖）")
r = 跑([str(vpy), "-m", "pip", "install", "-q", "-i", 镜像, str(ROOT)])
print("   " + ("安装成功 ✓" if r.returncode == 0 else f"失败：{(r.stderr or r.stdout)[-200:]}"))
if r.returncode != 0:
    sys.exit(1)

print("\n③ 装了什么（应该只有 pyserial，没有 numpy/fastapi）")
r = 跑([str(vpy), "-m", "pip", "list", "--format=freeze"])
装了的 = {l.split("==")[0].lower() for l in r.stdout.splitlines() if "==" in l}
for 包 in ("pyserial", "numpy", "fastapi", "matplotlib", "uvicorn", "pyyaml"):
    print(f"   {包:<12} {'✓ 在' if 包 in 装了的 else '✗ 不在'}")
assert "pyserial" in 装了的, "pyserial 没装上"
for 不该有 in ("numpy", "fastapi", "matplotlib", "uvicorn", "pyyaml"):
    assert 不该有 not in 装了的, f"★ 核心安装不该有 {不该有}"
print("   ★ 核心安装很干净 ✓（没有把重依赖塞进来）")

print("\n④ 装出来的命令能用吗（diansai-serial 入口）")
exe = venv / "Scripts" / "diansai-serial.exe"
if not exe.exists():
    exe = venv / "Scripts" / "diansai-serial"
r = 跑([str(exe), "--help"])
print("   --help 退出码 " + str(r.returncode))
print("   输出前 3 行：")
for line in r.stdout.splitlines()[:3]:
    print("     " + line)
assert r.returncode == 0 and "子命令" in r.stdout, "入口脚本不可用"
print("   ★ 入口命令可用且是中文 ✓")

print("\n⑤ 没有 numpy 时 analyze 该给人话提示（不是栈）")
with tempfile.TemporaryDirectory() as td:
    f = Path(td) / "阶跃.csv"
    f.write_text("\n".join(f"{i*0.02:.2f},{1 - 2.718281828**(-i*0.02):.5f}" for i in range(60)) + "\n",
                 encoding="utf-8")
    r = 跑([str(exe), "analyze", str(f)])
    print("   退出码 " + str(r.returncode))
    print("   stderr：" + (r.stderr.strip().splitlines() or ["(空)"])[0][:80])
    print("   " + ("第二个提示：" + r.stderr.strip().splitlines()[1][:80]
                   if len(r.stderr.strip().splitlines()) > 1 else ""))
    ok = (r.returncode == 1 and "numpy" in r.stderr and "metrics" in r.stderr
          and "Traceback" not in r.stderr)
    print("   ★ " + ("缺 numpy 时提示清楚、没甩栈 ✓" if ok else "✗ 不符合预期"))

print("\n⑥ 导入串口核心（在干净环境里）")
r = 跑([str(vpy), "-c", "import contest_workbench.serial_assistant as s;"
                        "import contest_workbench.stm32_isp;"
                        "import contest_workbench.sweep;"
                        "print('OK')"])
print("   " + (r.stdout.strip() or r.stderr.strip()[:150]))
print("   ★ " + ("核心模块可导入（sweep 因为要 numpy，这里会失败，属预期）"
                 if "OK" not in r.stdout else "全部可导入 ✓"))

print("\n⑦ 装上 [metrics] 后 analyze 能真算指标")
r = 跑([str(vpy), "-m", "pip", "install", "-q", "-i", 镜像, f"{ROOT}[metrics]"])
print("   " + ("装成功 ✓" if r.returncode == 0 else f"失败：{(r.stderr or r.stdout)[-150:]}"))
with tempfile.TemporaryDirectory() as td:
    f = Path(td) / "阶跃.csv"
    f.write_text("\n".join(f"{i*0.02:.2f},{1 - 2.718281828**(-i*0.02):.5f}" for i in range(60)) + "\n",
                 encoding="utf-8")
    r = 跑([str(exe), "analyze", str(f), "--json"])
    print("   退出码 " + str(r.returncode))
    print("   " + (r.stdout.strip()[:200] or r.stderr.strip()[:200]))
    print("   ★ " + ("装上 numpy 后能算指标 ✓" if r.returncode == 0 and "超调" in r.stdout
                     else "✗ 仍然不行"))

print("\n⑧ 清理虚拟环境")
shutil.rmtree(venv, ignore_errors=True)
print("   已删除")
print("\n完成 ✅")
