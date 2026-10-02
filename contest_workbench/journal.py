#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务记录：每一次采集 / 烧录 / 扫描 / 出报告都留一条可追溯的记录。

为什么要有它
------------
出了问题最常见的一句话是「昨天那次为什么失败来着？」—— 没有记录就只能靠回忆。
这个模块把每次动作**结构化**地记下来（时间、类型、摘要、结果、耗时、错误），
并且能**导出一份中文可读的诊断文件**，直接发给别人看，不用复述。

设计要点
--------
- **JSONL 追加写**（一行一条）：一条坏记录不会毁掉整个文件，也不会因为断电丢掉全部历史
- **绝不因为记录失败而影响主流程**：写不进去只当成一条警告，不抛异常打断烧录/采集
- **错误分类**：把常见的串口错误归成人话类别（端口占用 / 打不开 / 超时 / 设备无响应…），
  这样统计和排查时能一眼看出是"偶发"还是"每次都这样"
- 路径可用环境变量 `CONTEST_JOURNAL_FILE` 覆盖（测试时指到临时目录）
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

# 任务类型（常量，避免各处手写字符串写歪）
类型_串口 = "串口"
类型_采集 = "采集"
类型_烧录 = "烧录"
类型_扫描 = "参数扫描"
类型_报告 = "报告"
类型_快照 = "快照"
类型_系统 = "系统"

# 一份诊断最多带多少条记录（太多没人看，也没法贴）
诊断上限 = 200

# ★ 顺序有意义：先判更具体的（文件问题排在"找不到端口"之前，
#   否则「找不到固件文件」会被 "找不到" 这个词抢走）
_错误类别 = [
    ("端口占用", ("拒绝访问", "PermissionError", "Access is denied")),
    ("文件问题", ("找不到文件", "找不到固件", "文件不存在", "没有这个文件")),
    ("找不到端口", ("could not open port", "FileNotFoundError", "串口打不开", "no such port")),
    ("超时", ("timeout", "超时")),
    ("设备无响应", ("没收到", "0 字节", "NACK", "握手", "BootLoader", "芯片没进")),
    ("数据不足", ("数据点", "数据太少", "至少需要")),
    ("参数不合法", ("[错误]", "非法", "不是数字")),
]


def 归因(错误: str) -> str:
    """把一段错误文本归到一个大类里（给人看，不追求精确）。"""
    s = str(错误 or "")
    for 类别, 关键词 in _错误类别:
        if any(k.lower() in s.lower() for k in 关键词):
            return 类别
    return "其它" if s else ""


def 记录文件() -> Path:
    """记录文件路径（环境变量可覆盖，方便测试）。"""
    p = os.environ.get("CONTEST_JOURNAL_FILE")
    if p:
        return Path(p)
    from .config import REPO_ROOT          # 延迟导入：这样本模块不依赖 yaml
    return Path(REPO_ROOT) / "data" / "任务记录.jsonl"


def 记录(类型: str, 摘要: str, 结果: str = "成功", 详情=None,
         耗时: float | None = None, 错误: str = "", 时间戳: float | None = None) -> dict:
    """追加一条记录。**绝不抛异常**（记录失败不能影响正在做的事）。"""
    item = {
        "时间": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(时间戳 or time.time())),
        "ts": round(float(时间戳 or time.time()), 3),
        "类型": str(类型 or "其它"),
        "摘要": str(摘要 or "")[:200],
        "结果": str(结果 or "成功"),
        "耗时秒": round(float(耗时), 2) if 耗时 is not None else None,
        "错误类别": 归因(错误),
        "错误": str(错误 or "")[:500],
        "详情": 详情 if isinstance(详情, (dict, list)) else (str(详情)[:500] if 详情 else None),
    }
    try:
        p = 记录文件()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    except Exception:
        pass                                   # ★ 记录失败就悄悄放过，别打断主流程
    return item


def 读全部() -> list:
    """读回所有记录（坏行跳过，不让一行坏数据毁掉整个列表）。"""
    p = 记录文件()
    if not p.exists():
        return []
    out = []
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if isinstance(d, dict):
                    out.append(d)
            except Exception:
                continue
    except Exception:
        return out
    return out


def 最近(n: int = 50, 类型: str | None = None) -> list:
    """最近 n 条（按时间倒序），可按类型过滤。"""
    items = 读全部()
    if 类型:
        items = [x for x in items if x.get("类型") == 类型]
    return list(reversed(items))[:max(1, int(n))]


def 统计(天数: int = 7) -> dict:
    """按类型统计成功率（默认最近 7 天）—— 一眼看出"偶发"还是"每次都这样"。"""
    cut = time.time() - max(1, int(天数)) * 86400
    items = [x for x in 读全部() if float(x.get("ts") or 0) >= cut]
    总数 = {}
    for it in items:
        t = it.get("类型") or "其它"
        d = 总数.setdefault(t, {"次数": 0, "成功": 0, "失败": 0, "错误类别": {}})
        d["次数"] += 1
        if str(it.get("结果")) == "成功":
            d["成功"] += 1
        else:
            d["失败"] += 1
            k = it.get("错误类别") or "其它"
            d["错误类别"][k] = d["错误类别"].get(k, 0) + 1
    return {"天数": int(天数), "总数": len(items), "各类": 总数}


def 导出诊断(路径=None, 条数: int = 诊断上限) -> str:
    """导出一份**中文可读**的诊断文件（可以直接发给别人看）。返回文件路径。"""
    from .config import OUT_DIR
    路径 = Path(路径) if 路径 else (Path(OUT_DIR) / f"诊断-{time.strftime('%Y%m%d-%H%M%S')}.txt")
    路径.parent.mkdir(parents=True, exist_ok=True)
    st = 统计(30)
    lines = [
        "大学生竞赛工作台 · 运行诊断",
        "=" * 46,
        f"导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    try:
        from . import __version__
        lines.append(f"工作台版本：{__version__}")
    except Exception:
        pass
    lines += [
        f"记录文件：{记录文件()}",
        f"记录总数：{len(读全部())}",
        "",
        f"── 最近 30 天统计（共 {st['总数']} 条）──",
    ]
    if not st["各类"]:
        lines.append("（没有记录）")
    for t, d in sorted(st["各类"].items(), key=lambda kv: -kv[1]["次数"]):
        率 = f"{d['成功'] / d['次数'] * 100:.0f}%" if d["次数"] else "-"
        lines.append(f"  {t}：{d['次数']} 次 · 成功 {d['成功']} · 失败 {d['失败']} · 成功率 {率}"
                     + (f" · 失败原因 {d['错误类别']}" if d["错误类别"] else ""))
    lines += ["", f"── 最近 {条数} 条明细 ──"]
    for it in 最近(条数):
        head = f"[{it.get('时间')}] {it.get('类型')} · {it.get('结果')} · {it.get('摘要')}"
        if it.get("耗时秒") is not None:
            head += f"（{it['耗时秒']}s）"
        lines.append(head)
        if it.get("错误"):
            lines.append(f"      错误（{it.get('错误类别')}）：{it['错误'][:200]}")
        if it.get("详情"):
            lines.append(f"      详情：{json.dumps(it['详情'], ensure_ascii=False)[:220]}")
    路径.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(路径)
