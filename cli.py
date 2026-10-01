#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""电赛/竞赛 Agent 命令行入口（多分区平台）。

用法：
    python cli.py --domains                     # 列出所有竞赛分区
    python cli.py --check                       # 检查配置 / 题库 / 模型名（不花钱）
    python cli.py analyze H                     # 分析赛题 → out/H题-分析报告.md/.docx
    python cli.py analyze H --dry-run           # 只打印提示词，不调用模型（0 成本）
    python cli.py tune data.csv --target 1.0    # 调参：算指标+出图+让模型写调参报告
    python cli.py tune data.csv --no-llm        # 只本地算（0 成本，出 md+docx）
    python cli.py --domain mathmodel analyze A  # 换竞赛分区（该分区仍需先建题库）
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Windows 控制台默认 GBK，输出中文/emoji 会崩 —— 强制 UTF-8（写在最前面）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from contest_workbench.config import REPO_ROOT, Config
from contest_workbench.domains import DOMAINS, get_domain, list_domains, prompt_text
from contest_workbench.llm import LLM
from contest_workbench.profile import load_profile, persona_block, report_hint
from contest_workbench.loop import Agent
from contest_workbench.tools import TOOL_SCHEMAS, call_tool
from contest_workbench.tools.shiti import list_shiti

DEFAULT_DOMAIN = "diansai"


def cmd_check(cfg: Config, domain_id: str) -> int:
    d = get_domain(domain_id)
    print("── 竞赛分区 ──")
    print(f"当前分区：{d.id} —— {d.name}（{'已实现' if d.implemented else '规划中'}）")
    print(list_domains())
    print("\n── 配置检查 ──")
    print("API Key 来源：", cfg.key_source())
    print("Base URL    ：", cfg.base_url)
    print("模型（默认）：", cfg.model)
    print("输出目录    ：", REPO_ROOT / "out")
    print("\n── 工具清单 ──")
    print("  " + ", ".join(t["function"]["name"] for t in TOOL_SCHEMAS))
    print("\n── 本地题库 ──")
    print(list_shiti())
    if not cfg.has_key:
        print("\n❌ 没找到 API Key：请设置环境变量 DEEPSEEK_API_KEY，或在仓库根目录建 .env（见 .env.example）")
        return 2
    print("\n── 试连模型列表 ──")
    try:
        models = LLM(cfg).list_models()
        print("可用模型：", ", ".join(models) if models else "（返回为空）")
        if models and cfg.model not in models:
            print(f"⚠️ 默认模型 {cfg.model} 不在列表里，请用 --model 或改 .env 的 DEEPSEEK_MODEL")
    except Exception as e:
        print("⚠️ 取模型列表失败（不影响分析，可能是接口不支持该端点）：", e)
    return 0


def cmd_analyze(cfg: Config, domain_id: str, name: str, steps: int, dry_run: bool) -> int:
    d = get_domain(domain_id)
    # ★ 带上用户档位：命令行与网页端的行为要一致（不然同一档位两边输出不同）
    system = prompt_text(d, "system") + persona_block(load_profile())
    task = (prompt_text(d, "analyze") + f"\n\n【本次任务】分析「{name}」这道题。"
            + report_hint(load_profile()))

    if dry_run:
        print(f"（分区：{d.id} / {d.name}）")
        print("── System Prompt ──\n" + system)
        print("\n── User（任务）──\n" + task)
        print("\n（--dry-run：未调用模型，0 成本）")
        return 0

    if not cfg.has_key:
        print("❌ 没找到 API Key，无法调用模型。")
        return 2

    agent = Agent(LLM(cfg), system, max_steps=steps, verbose=True)
    answer = agent.run(task)
    print("\n" + "=" * 60 + "\n【最终回答】\n" + answer)
    u = agent.usage_total
    print(f"\n── 本次消耗 ──\n输入 {u['prompt_tokens']} tokens / 输出 {u['completion_tokens']} tokens "
          f"/ 合计 {u['total_tokens']} tokens")
    print("输出目录：", REPO_ROOT / "out")
    return 0


def cmd_tune(cfg: Config, domain_id: str, path: str, target: float | None,
             title: str | None, no_llm: bool, steps: int) -> int:
    d = get_domain(domain_id)
    if d.tune_template is None:
        print(f"❌ 分区「{d.id}」没有调参模板（tune），该分区暂不支持 tune 子命令。")
        return 2
    p = Path(path).expanduser()
    if not p.exists():
        # 允许只写文件名：自动去 out/ 与当前目录找
        for cand in (REPO_ROOT / "out" / path, Path.cwd() / path):
            if cand.exists():
                p = cand
                break
    if not p.exists():
        print(f"❌ 数据文件不存在：{path}")
        return 2

    print(f"── 本地计算（确定性，不花额度）──\n数据：{p}")
    local_md = call_tool("analyze_step_data", {"path": str(p), "target": target, "title": title})
    print(local_md)

    stem = p.stem
    report_name = f"{stem}-调参报告.md"

    if no_llm or not cfg.has_key:
        if not cfg.has_key and not no_llm:
            print("\n⚠️ 没找到 API Key，退化为只出本地结果。")
        body = (f"# {stem} 阶跃响应调参报告（本地计算版）\n\n"
                f"> 由 `contest-workbench` 本地算法生成（未调用模型）。\n\n{local_md}\n")
        print("\n" + call_tool("write_report", {"filename": report_name, "content": body}))
        return 0

    system = prompt_text(d, "system")
    task = (prompt_text(d, "tune")
            + f"\n\n【本次任务】\n被测数据文件：{p}\n目标值：{target if target is not None else '未给出'}\n\n"
            + "以下是我已经用本地算法算好的**确定性结果**（数字请直接引用，不要重算）：\n\n"
            + local_md
            + f"\n\n请按模板写出调参报告，并用 write_report 落盘，文件名用 `{report_name}`。")
    agent = Agent(LLM(cfg), system, max_steps=steps, verbose=True)
    answer = agent.run(task)
    print("\n" + "=" * 60 + "\n【最终回答】\n" + answer)
    u = agent.usage_total
    print(f"\n── 本次消耗 ──\n合计 {u['total_tokens']} tokens")
    print("输出目录：", REPO_ROOT / "out")
    return 0


def cmd_web(host: str, port: int, reload: bool) -> int:
    """启动网页端（uvicorn）。"""
    try:
        import uvicorn
    except ImportError:
        print("❌ 没装 fastapi/uvicorn。请先：pip install fastapi \"uvicorn[standard]\" python-multipart")
        return 2
    from contest_workbench.web.app import app
    shown = host if host not in ("0.0.0.0",) else "127.0.0.1"
    print(f"── 网页端启动中 ──\n浏览器打开：http://{shown}:{port}\n"
          f"（局域网共享：换成 --host 0.0.0.0，队友用你的局域网 IP 访问；Ctrl+C 停止）")
    uvicorn.run(app, host=host, port=port, reload=reload, log_level="warning")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="竞赛 Agent（多分区）：赛题分析 / 控制类调参")
    ap.add_argument("--version", action="version", version="contest-workbench 0.5.0")
    ap.add_argument("--domain", default=DEFAULT_DOMAIN, help=f"竞赛分区（默认 {DEFAULT_DOMAIN}）")
    ap.add_argument("--domains", action="store_true", help="列出所有分区后退出")
    ap.add_argument("--check", action="store_true", help="检查配置与题库，不调用模型")
    ap.add_argument("--model", help="临时指定模型名（覆盖 .env）")

    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("analyze", help="分析一道赛题 → 作战方案")
    p.add_argument("name", help="题号（如 H）或文件名片段（如 滚球）")
    p.add_argument("--steps", type=int, default=12, help="最多跑多少步，默认 12")
    p.add_argument("--dry-run", action="store_true", help="只打印提示词，不调用模型")

    q = sub.add_parser("tune", help="调参：阶跃响应指标 + PID 建议 + 报告")
    q.add_argument("path", help="数据文件（CSV/TXT/串口日志，两列：时间,幅值）")
    q.add_argument("--target", type=float, help="目标值（给了才算稳态误差）")
    q.add_argument("--title", help="报告/图标题")
    q.add_argument("--no-llm", action="store_true", help="只本地计算，不调用模型（0 成本）")
    q.add_argument("--steps", type=int, default=8, help="最多跑多少步，默认 8")

    w = sub.add_parser("web", help="启动网页端（队友零安装就能用）")
    w.add_argument("--host", default="127.0.0.1", help="监听地址（局域网共享用 0.0.0.0）")
    w.add_argument("--port", type=int, default=8765, help="端口，默认 8765")
    w.add_argument("--reload", action="store_true", help="改代码自动重载（开发用）")

    args = ap.parse_args()

    # 分区写进环境变量：题库工具按它找题库
    domain_id = (args.domain or DEFAULT_DOMAIN).strip().lower()
    if domain_id not in DOMAINS:
        print(f"❌ 未知分区「{domain_id}」。可用：{', '.join(DOMAINS)}")
        return 2
    os.environ["CONTEST_DOMAIN"] = domain_id

    if args.domains:
        print(list_domains())
        return 0

    cfg = Config()
    if args.model:
        cfg.model = args.model

    if args.check:
        return cmd_check(cfg, domain_id)
    if args.cmd == "analyze":
        return cmd_analyze(cfg, domain_id, args.name, args.steps, args.dry_run)
    if args.cmd == "tune":
        return cmd_tune(cfg, domain_id, args.path, args.target, args.title, args.no_llm, args.steps)
    if args.cmd == "web":
        return cmd_web(args.host, args.port, args.reload)

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
