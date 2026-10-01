# -*- coding: utf-8 -*-
"""全量测试 + CI/README 加 test_stm32_isp + 提交。"""
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"D:\work\contest-workbench")
PY = sys.executable
SUITES = ["test_offline", "test_tuning", "test_web", "test_launcher", "test_frontend_runtime",
          "test_ask", "test_learning", "test_profile", "test_serial", "test_serial_api",
          "test_frame", "test_stm32_isp"]

print("== 十二套测试 ==")
counts, bad = {}, 0
for s in SUITES:
    r = subprocess.run([PY, str(ROOT / "tests" / f"{s}.py")], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT, timeout=300)
    m = re.search(r"通过 (\d+) 项", r.stdout)
    counts[s] = int(m.group(1)) if m else 0
    bad += (r.returncode != 0)
    if r.returncode:
        print(f"  ❌ {s}")
        print("\n".join(r.stdout.splitlines()[-8:]))
total = sum(counts.values())
print(f"  合计 {total} 项 · 失败套数 {bad}")
if bad:
    sys.exit(1)

# CI
ci = ROOT / ".github/workflows/tests.yml"
ci_t = ci.read_text(encoding="utf-8")
if "test_stm32_isp" not in ci_t:
    anchor = ("      # 声明式帧解析（纯计算，不需要硬件）\n"
              "      - name: 帧解析（离线）\n        run: python tests/test_frame.py\n")
    ci_t = ci_t.replace(anchor, anchor +
                        "\n      # STM32 串口 ISP（用假 BootLoader，不需要硬件）\n"
                        "      - name: STM32 ISP（离线）\n        run: python tests/test_stm32_isp.py\n", 1)
    ci.write_text(ci_t, encoding="utf-8")
    print("  [ok] CI 加 test_stm32_isp")

# README
rp = ROOT / "README.md"
rt = rp.read_text(encoding="utf-8")
rt = re.sub(r"# 6\) 测试：十一套共 \d+ 项", f"# 6) 测试：十二套共 {total} 项", rt)
if "test_stm32_isp.py" not in rt:
    rt = rt.replace("python tests/test_frame.py",
                    "python tests/test_stm32_isp.py       #  STM32 串口 ISP（假 BootLoader，无需硬件）\n"
                    "python tests/test_frame.py", 1)
    rt = re.sub(r"(python tests/test_stm32_isp\.py\s+#\s*)",
                rf"\g<1>{counts['test_stm32_isp']} 项：", rt)
rp.write_text(rt, encoding="utf-8")
print(f"  README 更新为 {total} 项")

subprocess.run(["git", "add", "-A"], cwd=ROOT, capture_output=True)
rr = subprocess.run(["git", "-c", "user.name=Lu Kuo",
                     "-c", "user.email=288989345+lk2168@users.noreply.github.com",
                     "commit", "-q", "-m",
                     "test(isp): 把 STM32 ISP 加入 CI 与 README（十二套共 %d 项）" % total],
                    cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
print("  提交:", (rr.stdout or rr.stderr).strip()[:70] or "done")
pp = subprocess.run(["git", "push"], cwd=ROOT, capture_output=True, text=True,
                    encoding="utf-8", errors="replace")
print("  推送:", ((pp.stderr or pp.stdout).strip().splitlines() or ["ok"])[-1])
print(f"\n各套：{counts}")
