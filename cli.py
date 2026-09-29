#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""电赛 Agent 命令行入口。

用法：
    python cli.py --check                      # 检查 Key / 模型名（不花钱）
    python cli.py analyze H                    # 分析 H 题 → out/H题-分析报告.md/.docx
    python cli.py analyze H --dry-run           # 只打印提示词，不调用模型（0 成本）
    python cli.py analyze "滚球" --steps 16     # 文件名片段也行；最多 16 步
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Windows 控制台默认 GBK，输出中文/emoji 会崩 —— 强制 UTF-8（写在最前面）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from diansai_agent.config import REPO_ROOT, Config
from diansai_agent.llm import LLM
from diansai_agent.loop import Agent
from diansai_agent.tools.shiti import list_shiti

PROMPT_DIR = Path(__file__).resolve().parent / "diansai_agent" / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def cmd_check(cfg: Config) -> int:
    print("── 配置检查 ──")
    print("API Key 来源：", cfg.key_source())
    print("Base URL    ：", cfg.base_url)
    print("模型（默认）：", cfg.model)
    print("输出目录    ：", cfg.out_dir if hasattr(cfg, "out_dir") else REPO_ROOT / "out")
    print("\n── 本地真题库 ──")
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


def cmd_analyze(cfg: Config, name: str, steps: int, dry_run: bool) -> int:
    system = load_prompt("system.md")
    task = load_prompt("analyze.md") + f"\n\n【本次任务】分析「{name}」这道题。"

    if dry_run:
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


def main() -> int:
    ap = argparse.ArgumentParser(description="电赛 Agent v0.1")
    ap.add_argument("--check", action="store_true", help="检查配置与真题库，不调用模型")
    ap.add_argument("--model", help="临时指定模型名（覆盖 .env）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("analyze", help="分析一道赛题")
    p.add_argument("name", help="题号（如 H）或文件名片段（如 滚球）")
    p.add_argument("--steps", type=int, default=12, help="最多跑多少步，默认 12")
    p.add_argument("--dry-run", action="store_true", help="只打印提示词，不调用模型")

    args = ap.parse_args()
    cfg = Config()
    if args.model:
        cfg.model = args.model

    if args.check:
        return cmd_check(cfg)
    if args.cmd == "analyze":
        return cmd_analyze(cfg, args.name, args.steps, args.dry_run)

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
