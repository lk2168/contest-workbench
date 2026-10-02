# -*- coding: utf-8 -*-
"""摸清依赖图：串口这套模块到底 import 了哪些第三方库（决定 pyproject 的依赖怎么划）。"""
import ast
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"D:\work\contest-workbench\contest_workbench")

串口模块 = ["serial_assistant.py", "frame_parser.py", "stm32_isp.py", "sweep.py",
            "tools/tuning.py", "config.py"]

标准库 = set(sys.stdlib_module_names)


def 顶层导入(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                out.add("(相对导入)")
            elif node.module:
                out.add(node.module.split(".")[0])
    return out


print("  模块                                第三方依赖 / 相对导入")
print("  " + "-" * 66)
allthird = {}
for m in 串口模块:
    p = ROOT / m
    if not p.exists():
        print(f"  {m:<34} （不存在）")
        continue
    imps = 顶层导入(p)
    third = sorted(i for i in imps if i not in 标准库 and i != "(相对导入)")
    rel = "(相对导入)" in imps
    print(f"  {m:<34} {', '.join(third) or '（纯标准库）'}{'  +相对导入' if rel else ''}")
    for t in third:
        allthird.setdefault(t, []).append(m)

print("\n  汇总（第三方 → 用它的模块）：")
for k, v in sorted(allthird.items()):
    print(f"    {k:<14} ← {', '.join(v)}")

print("\n  ★ 结论：如果上面出现 fastapi/matplotlib/uvicorn，说明串口部分被它们污染了")
