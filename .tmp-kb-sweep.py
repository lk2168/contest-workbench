# -*- coding: utf-8 -*-
"""经验库：把新笔记加进索引并提交。"""
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
KB = Path(r"C:\Users\33083\.dsh\agent-kb")
idx = KB / "_index.md"
t = idx.read_text(encoding="utf-8")

# 找一个合适的位置插进去（放在"测试/工程方法"相关区域；没有就追加到文件末尾的列表）
line = ("- [[simulated-plant-testing]] ★ 用**模拟被控对象**把「采数据→算指标→推荐」整条链路测透："
        "模拟对象要**故意做成有取舍的**（越大越快但越冲）否则测试没意义；"
        "★ 两个静默 bug 的教训 —— 采样起点必须记在**发命令之前**、"
        "跨模块读字段**别用 getattr 兜底**（会把"接口改名"变成"数据恰好是默认值"）；"
        "顺带：`scripts/fix_cn_quotes.py` 自动修中文串里嵌 ASCII 引号（检查工具自身也要验）")
if "simulated-plant-testing" not in t:
    if t.endswith("\n"):
        t = t + line + "\n"
    else:
        t = t + "\n" + line + "\n"
    idx.write_text(t, encoding="utf-8")
    print("  [ok] 索引已加 simulated-plant-testing")
else:
    print("  （索引里已有）")

subprocess.run(["git", "add", "-A"], cwd=KB, capture_output=True)
r = subprocess.run(["git", "-c", "user.name=Lu Kuo",
                    "-c", "user.email=288989345+lk2168@users.noreply.github.com",
                    "commit", "-q", "-m",
                    "notes: 用模拟被控对象测透测量链路（含两个静默 bug：采样起点记晚了、"
                    "getattr 兜底吞掉字段改名）+ fix_cn_quotes 工具（检查工具自身也要验）"],
                   cwd=KB, capture_output=True, text=True, encoding="utf-8", errors="replace")
print("  提交:", (r.stdout or r.stderr).strip()[:60] or "done")
pr = subprocess.run(["git", "push"], cwd=KB, capture_output=True, text=True,
                    encoding="utf-8", errors="replace")
print("  推送:", ((pr.stderr or pr.stdout).strip().splitlines() or ["ok"])[-1])
