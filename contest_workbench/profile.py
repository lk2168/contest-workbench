# -*- coding: utf-8 -*-
"""用户经验档位：第一次参赛 / 参加过 / 拿过奖，以及"希望怎么被带"。

★ 设计原则（不然就是形式主义问卷）：**档位必须真的改变输出**，而且是**可测地**改变。
  影响三处：
    1. 系统提示词  → persona_block()：新手要术语解释与"为什么"，老手要技术对比与风险
    2. 报告结构    → report_hint()：新手档多一节「前置知识」，老手档明确不要科普
    3. 交互节奏    → 前端读取档位后调整提示文案（guide 字段）

存在本机用户目录（`~/.contest-workbench/profile.yaml`），不上传、不进版本库。
没填过档位时一切照旧（空档位 = 现有行为），不能因为没填就出问题。
"""
from __future__ import annotations

from pathlib import Path

from .config import user_dir

# ── 档位的可选值（同时给界面做下拉框用）────────────────────────────────────
EXPERIENCE = ["第一次参加", "参加过但没获奖", "拿过奖"]
TEAM = ["一个人做", "和队友分工", "有学长或老师带"]
STYLE = ["直接给完整方案", "一步步带我做", "只在我卡住时给提示"]

CHOICES = {"experience": EXPERIENCE, "team": TEAM, "style": STYLE}
LABELS = {"experience": "参赛经验", "team": "队伍情况", "style": "希望我怎么配合"}


def profile_file() -> Path:
    return user_dir() / "profile.yaml"


def load_profile() -> dict:
    """读档位。文件不存在/损坏 → 返回空 dict（= 不注入任何东西）。"""
    p = profile_file()
    if not p.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(v) for k, v in data.items() if k in CHOICES and v in CHOICES[k]}


def save_profile(**fields) -> Path:
    """保存档位（只认合法字段与合法取值；传 None 表示不改，传 "" 表示清空该项）。"""
    data = load_profile()
    for k, v in fields.items():
        if k not in CHOICES or v is None:
            continue
        v = str(v).strip()
        if v == "":
            data.pop(k, None)
        elif v in CHOICES[k]:
            data[k] = v
        else:
            raise ValueError(f"{LABELS.get(k, k)} 只能是：{'、'.join(CHOICES[k])}")
    p = profile_file()
    if not data:
        p.unlink(missing_ok=True)
        return p
    import yaml
    lines = ["# 由「大学生竞赛工作台」首次引导保存（本机私有，可随时在设置里改）",
             "# 它只影响提示词与报告结构，不上传、不参与评分"]
    p.write_text("\n".join(lines + [yaml.safe_dump(data, allow_unicode=True,
                                                   sort_keys=False).strip()]) + "\n",
                 encoding="utf-8")
    return p


def is_newbie(profile: dict | None = None) -> bool:
    p = load_profile() if profile is None else profile
    return p.get("experience") == "第一次参加"


def persona_block(profile: dict | None = None) -> str:
    """生成要追加到系统提示词后面的"用户档位"说明（不同档位文本可测地不同）。"""
    p = load_profile() if profile is None else profile
    if not p:
        return ""
    lines = ["", "## 本次协作对象的档位（由工作台的首次引导提供，影响你的写法）", ""]
    if p.get("experience"):
        lines.append(f"- 参赛经验：**{p['experience']}**")
    if p.get("team"):
        lines.append(f"- 队伍情况：**{p['team']}**")
    if p.get("style"):
        lines.append(f"- 他希望的配合方式：**{p['style']}**")
    lines.append("")

    if p.get("experience") == "第一次参加":
        lines += [
            "按**第一次参赛**来写（这是硬要求，不是建议）：",
            "- 出现专业术语时**顺手一句话解释**（如「超调量：冲过头了多少」），不要假设他懂",
            "- 每个关键决定都写清**为什么这么选**，并点出**最容易翻车的地方**",
            "- 器件与模块要给**具体型号或选型范围**，不要只说「选合适的电机」",
        ]
    elif p.get("experience") == "拿过奖":
        lines += [
            "按**有经验**来写：**不要科普**，直接给技术对比、量化指标、风险与取舍。",
            "篇幅紧凑，重点放在方案差异与得分点上。",
        ]
    else:  # 参加过但没获奖
        lines += [
            "按**参加过但没拿奖**来写：术语不用解释，但**每个方案要给「上次常见做法 vs 更优做法」的对比**，",
            "并指出容易丢分的细节。",
        ]

    if p.get("style") == "一步步带我做":
        lines += ["- 把方案拆成**可执行的小步**，每步给一个明确的「做完你应该看到什么」，不要一次抛全部内容。"]
    elif p.get("style") == "只在我卡住时给提示":
        lines += ["- **不要直接给完整答案**：先给思路与关键判断点，把具体实现留给用户，等他说卡住再展开。"]
    if p.get("team") == "一个人做":
        lines += ["- 他一个人做全部工作：**优先低风险、易实现的方案**，并标出时间不够时可以砍掉的部分。"]
    return "\n".join(lines)


def report_hint(profile: dict | None = None) -> str:
    """给报告的额外要求（拼在任务描述末尾）。新手档要"前置知识"节，老手档明确不要。"""
    p = load_profile() if profile is None else profile
    if not p:
        return ""
    if p.get("experience") == "第一次参加":
        return ("\n\n【报告要求（按新手档）】在报告**开头**加一节 `## 前置知识`："
                "列出这道题需要用到的基础概念（每条 1-2 句，用大白话），"
                "并在正文里对首次出现的术语做括号解释。"
                "结尾加一节 `## 我建议你先补什么`：按优先级列出 2-3 项，每项写清「要会到什么程度」。")
    if p.get("experience") == "拿过奖":
        return ("\n\n【报告要求（按有经验档）】**不要**写「前置知识」或术语解释，"
                "直接给方案对比、量化指标、风险与取舍，篇幅尽量紧凑。")
    return ("\n\n【报告要求（按有过参赛经历档）】不用解释基础术语，"
            "但每个方案要对比「常见做法 vs 更优做法」，并标出容易丢分的细节。")
