# -*- coding: utf-8 -*-
"""竞赛分区（Domain）：让同一套 Agent 服务不同竞赛。

为什么要有这一层：
  项目目标是**大学生竞赛平台**（先电赛，之后数学建模、IT 杯、创业竞赛……）。
  不同竞赛的差别只在四件事上：
    ① 题库放哪（data/题库/<分区>/）
    ② 系统提示词怎么说（角色、铁律）
    ③ 报告模板长什么样（章节结构）
    ④ 需要哪些专属工具（电赛要调参、数学建模要数据处理……）
  把差异收进"分区配置"，Agent 骨架（loop/llm/config）与通用工具（报告落盘、题库检索、
  调参分析）全部复用 —— 新增一个竞赛 = 加一份配置 + 一个题库目录，**不动核心代码**。

已实现：diansai（电赛）
预留（只登记，未实现）：mathmodel（数学建模）、itcup（IT 杯/计算机类）、startup（创业竞赛）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
KB_ROOT = REPO_ROOT / "data" / "题库"   # 兼容旧引用；实际查找走 kb_candidates()
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass
class Domain:
    id: str                       # 目录名 / --domain 取值
    name: str                     # 中文全称
    kb_subdir: str                # 题库子目录（KB_ROOT 下）
    system_prompt: str            # prompts/ 下的文件名
    analyze_template: str         # 分析报告的模板文件名
    tune_template: str | None = None   # 调参报告模板（没有则该分区不提供 tune）
    tools: tuple[str, ...] = ()   # 该分区启用的工具名
    implemented: bool = True
    note: str = ""                # 未实现分区：说明需要补什么

    def kb_dir(self) -> Path:
        """★ 第一个存在的题库目录；都不存在时返回**推荐位置**（用户目录）。

        为什么不是单一路径：打包成 exe 后，"题库放哪"对用户是黑盒 ——
        下载来的 exe 旁边没有题库，直接说"没找到"会让人一脸懵。所以按下面的顺序找，
        并且把搜过的位置报给界面显示（见 kb_candidates()）。
        """
        for d in kb_candidates(self.kb_subdir):
            if d.exists() and any(d.rglob("*.md")):
                return d
        return kb_candidates(self.kb_subdir)[-1]     # 推荐位置：用户目录


def kb_candidates(subdir: str) -> list[Path]:
    """题库的候选目录（按优先级）。界面会把这份清单显示给用户，告诉他放哪。

    顺序：
      1. 用户目录 ~/.contest-workbench/题库/<分区>   ← ★ 下载 exe 的人放这里（推荐）
      2. exe/仓库 同级的 data/题库/<分区>            ← 开发者与"把题库放程序旁边"的人
      3. 程序目录的上一层 data/题库/<分区>          ← exe 在 dist/ 里时也能被找到
      4. 当前工作目录 data/题库/<分区>
    """
    from .config import APP_DIR, user_dir
    out: list[Path] = [user_dir() / "题库" / subdir,
                       APP_DIR / "data" / "题库" / subdir,
                       APP_DIR.parent / "data" / "题库" / subdir,
                       Path.cwd() / "data" / "题库" / subdir]
    seen, uniq = set(), []
    for p in out:
        try:
            rp = p.resolve()
        except Exception:
            continue
        if rp not in seen:
            seen.add(rp)
            uniq.append(p)
    return uniq


# 通用工具：所有分区都能用
_COMMON_TOOLS = ("list_shiti", "read_shiti", "search_qa", "search_tiku", "write_report")
# 电赛专属：阶跃响应指标 + PID 建议
_TUNING_TOOLS = ("analyze_step_data",)

DOMAINS: dict[str, Domain] = {
    "diansai": Domain(
        id="diansai",
        name="全国大学生电子设计竞赛（电赛）",
        kb_subdir="diansai",
        system_prompt="system.md",
        analyze_template="analyze.md",
        tune_template="tune.md",
        tools=_COMMON_TOOLS + _TUNING_TOOLS,
        note="控制/测量/电源/信号四类赛题；调参助手面向控制类（阶跃响应指标 + PID 建议）",
    ),
    # ── 以下是"平台化"的占位：接入时只需补题库 + 提示词 + 专属工具 ──────────────
    "mathmodel": Domain(
        id="mathmodel", name="全国大学生数学建模竞赛", kb_subdir="mathmodel",
        system_prompt="system.md", analyze_template="analyze.md",
        tools=_COMMON_TOOLS, implemented=False,
        note="需补：历年赛题（A/B/C 题）、优秀论文评阅要点、数据处理与建模工具（拟合/优化/统计）",
    ),
    "itcup": Domain(
        id="itcup", name="中国大学生计算机设计大赛 / IT 类竞赛", kb_subdir="itcup",
        system_prompt="system.md", analyze_template="analyze.md",
        tools=_COMMON_TOOLS, implemented=False,
        note="需补：历年赛题与作品要求、答辩评分表、原型开发与演示工具",
    ),
    "startup": Domain(
        id="startup", name="“互联网+”/挑战杯等创新创业竞赛", kb_subdir="startup",
        system_prompt="system.md", analyze_template="analyze.md",
        tools=_COMMON_TOOLS, implemented=False,
        note="需补：各赛事评审规则、商业计划书结构与评分维度、财务测算与路演材料工具",
    ),
}


def get_domain(domain_id: str | None) -> Domain:
    did = (domain_id or "diansai").strip().lower()
    if did not in DOMAINS:
        raise KeyError(f"未知分区「{did}」。可用：{', '.join(DOMAINS)}")
    return DOMAINS[did]


def list_domains() -> str:
    """给用户/模型看的分区清单。"""
    lines = ["| 分区 id | 名称 | 状态 | 说明 |", "|---|---|---|---|"]
    for d in DOMAINS.values():
        lines.append(f"| `{d.id}` | {d.name} | {'✅ 已实现' if d.implemented else '🚧 规划中'} | {d.note} |")
    return "\n".join(lines)


def prompt_text(domain: Domain, which: str) -> str:
    """读取分区提示词（which: 'system' / 'analyze' / 'tune'）。"""
    name = {"system": domain.system_prompt,
            "analyze": domain.analyze_template,
            "tune": domain.tune_template}.get(which)
    if not name:
        raise KeyError(f"分区 {domain.id} 没有 {which} 提示词")
    p = PROMPT_DIR / name
    if not p.exists():
        raise FileNotFoundError(f"提示词文件不存在：{p}")
    return p.read_text(encoding="utf-8")
