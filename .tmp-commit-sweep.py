# -*- coding: utf-8 -*-
"""只做提交（上一步因为提交信息里含 % 又用了 % 格式化而崩）。"""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"D:\work\contest-workbench")
TOTAL = 579

msg = f"""feat(sweep): 参数自动扫描 —— 自动改参数 → 采数据 → 算指标 → 出对比表与推荐

调参的体力活是「改一个参数 → 重跑一次 → 记下超调和调节时间 → 再改」。这个功能把它自动化：
发参数命令 → 等生效 → 采一段数据 → 用本地确定性算法算指标 → 出一张对比表并推荐一个值。

模块 contest_workbench/sweep.py（纯逻辑 + 可注入 IO）：
- parse_values()：支持枚举（0.5,1.0,1.5，中文逗号/分号也认）与范围（0.5:2.0:0.25）
- format_command()：模板 KP={{value}}，支持 {{:g}} / {{:.2f}} / {{:04X}}（HEX 帧）
- SweepRunner：只依赖 link 的 send/points 两个方法 → 真实串口与"模拟被控对象"都能驱动
- pick_best()：确定性推荐规则（超调不超标的行里选调节时间最短的；都不达标则选超调最小的）
- 随时可停（等待期间分小步轮询停止标志，点停止不用等一整轮）

接口：GET /api/sweep/state、POST /api/sweep/start、POST /api/sweep/stop
      （后台线程跑，前端 0.7 秒轮询进度）
界面：「进阶工具」里新增「参数自动扫描」卡片（按"新手在上、进阶折叠"的层级放）

★ 测试用模拟被控对象把整条链路测透（不需要硬件）：频率随参数增大、阻尼随之减小 ——
即真实的 PID 权衡（越大越快但越冲）。所以测试不是"碰巧过"，而是能验证推荐逻辑真的选对了值：
测出的超调与二阶理论吻合（阻尼比 0.5 时 16.3 个百分点），超调随参数单调增大，
推荐值满足"不超标且最快"。另有一个环路模拟端到端：真实 HTTP 接口 + 假串口 +
一个模拟设备线程（看到参数命令就把阶跃数据吐回去），验证 4 个值全部采到、算出指标、出表并推荐。

过程中修掉两个真 bug（都写进了注释）：
1. 采数起点记晚了 —— 真实设备收到参数命令就开始吐数据，起点必须记在**发命令之前**
   （否则每轮都变成"0 个数据点"）；
2. 指标字段用 getattr(..., 默认值) 取值 → 上游字段名对不上时**静默变成 NaN**，
   推荐逻辑误判成"没有达标值"。改成直接取属性（改名就报错），并加了回归断言。

顺带加了 scripts/fix_cn_quotes.py：自动修掉「中文串里嵌 ASCII 双引号」这个我反复犯的
语法错（自带自检：先用一个坏文件验证它真能抓到 —— 第一版它自己就因为用相对路径 +
临时工作目录而假报"通过"）。

测试：新增 tests/test_sweep.py（47 项）；test_serial_api 60→72（含环路模拟端到端）；
十三套共 {TOTAL} 项全过。
"""
mf = ROOT / ".git-msg-sweep.txt"
mf.write_text(msg, encoding="utf-8")
subprocess.run(["git", "add", "-A"], cwd=ROOT, capture_output=True)
rr = subprocess.run(["git", "-c", "user.name=Lu Kuo",
                     "-c", "user.email=288989345+lk2168@users.noreply.github.com",
                     "commit", "-q", "-F", str(mf)], cwd=ROOT, capture_output=True, text=True,
                    encoding="utf-8", errors="replace")
mf.unlink(missing_ok=True)
print("  提交:", (rr.stdout or rr.stderr).strip()[:80] or "done")
print("  " + subprocess.run(["git", "log", "--oneline", "-1"], cwd=ROOT, capture_output=True,
                            text=True).stdout.strip())
print("  " + subprocess.run(["git", "diff", "--stat", "HEAD~1", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip().replace("\n", " | "))
pp = subprocess.run(["git", "push"], cwd=ROOT, capture_output=True, text=True,
                    encoding="utf-8", errors="replace")
print("  推送:", ((pp.stderr or pp.stdout).strip().splitlines() or ["ok"])[-1])
