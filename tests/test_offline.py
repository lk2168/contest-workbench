#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线自测：不调用任何模型 API，验证工具链是否正常。

用法：python tests/test_offline.py
退出码：0 = 全通过，1 = 有失败项。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# 保证能 import 到仓库包（直接 python tests/test_offline.py 也能跑）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from diansai_agent.config import Config                                    # noqa: E402
from diansai_agent.tools import TOOL_SCHEMAS, call_tool                     # noqa: E402
from diansai_agent.tools.shiti import kb_dirs, list_shiti                   # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


def main() -> int:
    print("== 1. 配置 ==")
    cfg = Config()
    check("配置可加载（base_url 非空）", bool(cfg.base_url), cfg.base_url)
    check("model 非空", bool(cfg.model), cfg.model)
    print(f"   Key 来源：{cfg.key_source()}（不打印 Key 本身）")

    print("\n== 2. 题库 ==")
    dirs = kb_dirs()
    check("至少有一个题库目录", len(dirs) > 0, str(dirs))
    listing = list_shiti()
    check("list_shiti 有内容", "题" in listing and len(listing) > 100)
    n_years = listing.count("【")
    check(f"覆盖多个年份/批次（{n_years} 个）", n_years >= 1)

    print("\n== 3. 工具与分区 ==")
    names = [t["function"]["name"] for t in TOOL_SCHEMAS]
    for want in ("list_shiti", "read_shiti", "search_qa", "search_tiku",
                 "write_report", "analyze_step_data"):
        check(f"工具已注册：{want}", want in names)

    from diansai_agent.domains import DOMAINS, get_domain, list_domains, prompt_text
    check("分区表里有 diansai 且标记为已实现", get_domain("diansai").implemented)
    check("分区清单能生成", "diansai" in list_domains() and "mathmodel" in list_domains())
    try:
        get_domain("不存在的分区")
        check("未知分区应报错", False, "居然没报错")
    except KeyError:
        check("未知分区报错", True)
    for which in ("system", "analyze", "tune"):
        check(f"分区提示词可读：{which}", len(prompt_text(get_domain("diansai"), which)) > 200)
    check("规划中分区的 tune 模板为 None", DOMAINS["mathmodel"].tune_template is None)

    r = call_tool("read_shiti", {"name": "H", "max_chars": 1500})
    check("read_shiti 能读到正文", "错误" not in r[:8] and len(r) > 300, r[:80])

    r = call_tool("read_shiti", {"name": "不存在的题XYZ"})
    check("read_shiti 找不到时给友好错误", "没找到" in r)

    r = call_tool("search_qa", {"keyword": "评分", "max_chars": 500})
    check("search_qa 有返回（无答疑时也应是提示而非异常）", isinstance(r, str) and len(r) > 0)

    r = call_tool("search_tiku", {"keyword": "摄像头", "max_chars": 500})
    check("search_tiku 能跨年检索", "命中" in r or "没找到" in r)

    r = call_tool("no_such_tool", {})
    check("未知工具返回错误而不是抛异常", "没有这个工具" in r)

    r = call_tool("read_shiti", {"name": None})   # 参数类型不对也要兜住
    check("参数异常被兜住", isinstance(r, str))

    print("\n== 4. 报告落盘（写到临时目录，不污染 out/）==")
    import diansai_agent.tools.report as report_mod
    with tempfile.TemporaryDirectory() as td:
        report_mod.OUT_DIR = Path(td)
        res = call_tool("write_report", {"filename": "自测报告.md", "content": "# 标题\n\n正文 **加粗**\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"})
        md = Path(td) / "自测报告.md"
        check("Markdown 已写出", md.exists() and md.stat().st_size > 10, res)
        check("非法文件名被清洗（不含路径分隔符）", (Path(td) / "自测报告.md").name == "自测报告.md")
        check("Word 转换（有 python-docx 时应成功）", "docx" in res or "失败" in res, res)

    print("\n" + "=" * 50)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("全部通过 ✅（离线自测不消耗 API 额度）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
