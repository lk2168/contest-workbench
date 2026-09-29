#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重建题库：从 CCBP/NUEDC_Topic 仓库下载指定年份的赛题 PDF，抽取文本并存入 data/题库/。

用法：
    python scripts/fetch_history.py             # 默认拉 2025 2023
    python scripts/fetch_history.py 2025 2023 2024 2021

说明：
  - 走 ghproxy 镜像（国内直连 raw.githubusercontent 常失败）
  - 抽完用 clean() 清洗 PDF 的"一字一行"排版
  - ★ 版权提醒：赛题著作权归全国大学生电子设计竞赛组委会及赛区组委会。
    本脚本只为你**本地备赛检索**用；下载物默认躺在 data/（已在 .gitignore 中，不会提交）。
    若要公开发布，请只发布题目名称索引与来源链接（见 docs/历年题名与分类.md）。
"""
from __future__ import annotations

import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# Windows 控制台默认 GBK：不加这两行，打印 emoji/中文会 UnicodeEncodeError 崩掉
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent      # 仓库根目录
HERE = ROOT / "data" / "题库"                       # 题库落地位置
MIRROR = "https://ghproxy.net/https://raw.githubusercontent.com/CCBP/NUEDC_Topic/main/"
# 每年要下的题（题号 -> 文件名，按仓库目录树）
YEAR_FILES = {
    "2025": ["A题_能量回馈的变流器负载试验装置", "B题_单相有源电力滤波实验装置", "C题_基于单目视觉的目标物测量装置",
             "D题_简易以太网双绞线测试仪", "E题_简易自行瞄准装置", "F题_简易自动接收机", "G题_电路模型探究装置",
             "H题_野生动物巡查系统"],
    "2023": ["A_单相逆变器并联运行系统", "B_同轴电缆长度与终端负载检测装置", "C_电感电容测量装置",
             "D_信号调制方式识别与参数估计装置", "E_运动目标控制与自动追踪系统", "F_基于声传播的智能定位系统",
             "G_空地协同智能消防系统", "H_信号分离装置"],
    "2024": ["A题_AC-AC变换电路并联运行", "B题_单相功率分析仪", "C题_无线传输信号模拟系统",
             "D题_立体货架盘点无人机系统", "E题_三子棋游戏装置", "F题_磁悬浮实验装置",
             "G题_简易录音屏蔽系统", "H题_自动行驶小车"],
    "2021": ["A_信号失真度测量装置", "B_三相AC-DC变换电路", "C_三端口DC-DC变换器", "D_基于互联网的摄像测量系统",
             "E_数字-模拟信号混合传输收发机", "F_智能送药小车", "G_植保飞行器", "H_用电器分析识别装置"],
}
YEAR_DIR = {"2025": "2025-国赛", "2023": "2023-国赛", "2024": "2024-省赛", "2021": "2021-国赛"}


def clean(raw: str) -> str:
    lines = [ln.rstrip() for ln in raw.splitlines()]
    out: list[str] = []
    for ln in lines:
        t = ln.strip()
        if not t:
            out.append("")
            continue
        if out and len(t) <= 2 and out[-1] and not out[-1].endswith(("。", "：", "；", "）", ")", "】")):
            out[-1] = out[-1] + t
        else:
            out.append(t)
    text = "\n".join(out)
    text = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def fetch(url: str, dest: Path, retries: int = 3) -> bool:
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                data = r.read()
            dest.write_bytes(data)
            return True
        except Exception as e:
            print(f"    重试 {i+1}/{retries}：{type(e).__name__} {e}")
            time.sleep(2)
    return False


def main(years: list[str]) -> None:
    for year in years:
        outdir = HERE / YEAR_DIR.get(year, year)
        outdir.mkdir(parents=True, exist_ok=True)
        for stem in YEAR_FILES.get(year, []):
            md = outdir / f"{stem}.md"
            if md.exists() and md.stat().st_size > 1500:
                print(f"  跳过（已有）{md.name}")
                continue
            url = MIRROR + urllib.parse.quote(f"{year}/{stem}.pdf")
            pdf = outdir / "_tmp.pdf"
            print(f"  下载 {year}/{stem}.pdf")
            if not fetch(url, pdf):
                print(f"    ❌ 下载失败：{stem}")
                continue
            try:
                r = PdfReader(str(pdf))
                pages = []
                for i, pg in enumerate(r.pages, 1):
                    try:
                        t = pg.extract_text(extraction_mode="layout") or ""
                    except Exception:
                        t = pg.extract_text() or ""
                    pages.append(f"\n===== 第 {i} 页 =====\n{clean(t)}")
                body = "\n".join(pages).strip()
                md.write_text(f"# {stem}\n\n> 来源：CCBP/NUEDC_Topic（{year} 年赛题）\n"
                              f"> 页数：{len(r.pages)}\n\n{body}\n", encoding="utf-8")
                print(f"    ✅ {md.name}（{len(body)} 字符）")
            except Exception as e:
                print(f"    ❌ 解析失败：{e}")
            finally:
                if pdf.exists():
                    pdf.unlink()


if __name__ == "__main__":
    main(sys.argv[1:] or ["2025", "2023"])
