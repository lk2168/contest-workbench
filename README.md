# 电赛 Agent（diansai-agent）

> 把一道**全国大学生电子设计竞赛（电赛）赛题**，变成一份**参赛队第二天早上就能照着干的作战方案**。
> 这不是"调一次 API 问一句"，而是一个**真正的 Agent**：它自己决定下一步查什么资料、调哪个工具，直到把报告写完。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![status](https://img.shields.io/badge/status-v0.1-orange)

---

## 它是什么 / 不是什么

| ✅ 它做 | ❌ 它不做 |
|---|---|
| 读赛题 → 逐条拆解要求并**量化** | 替你焊电路、写最终固件（它给骨架与参数初值） |
| 查**官方答疑**与**历年同类题**（跨年检索） | 编造指标或器件参数（提示词里明令禁止，缺数据就标"待确认"） |
| 出：任务拆解表 / 评分点推断 / 方案对比 / 器件清单 / 算法与 PID 初值 / 4天3夜时间线 / 风险预案 | 猜官方评分细则（官方不公开，它只做**基于历年规律的推断**，并标注"需以官方细则为准"） |
| 报告自动落盘为 Markdown **+ Word** | 联网替你去比赛（赛期禁止与队外交流，方案里也不会出现这类建议） |

**实测样例**：用 2026 赛区赛 **H 题《车载平衡滚球运动控制系统》** 跑完整流程 —— 自主 8 步、24 次工具调用（其中答疑检索 20 次）、产出 18.8 KB 报告 + Word；报告里给出逐条分值（6/16/13/20/20/20/5/20）、从赛道几何**推算出整圈 6.14 m** 与所需速度、3 个方案对比（舵机直推 / 步进丝杆 / 双闭环），并抓到答疑里的硬约束（循迹**只能用红外光电模块**、摆杆 25 cm PPR 管、**球位必须用摄像头**）。

---

## 快速开始

```bash
# 0) 依赖
pip install -r requirements.txt          # requests / PyYAML / pypdf / python-docx

# 1) 自检：Key 从哪来、题库有什么题、模型名对不对（不花钱）
python cli.py --check

# 2) 空跑：只打印提示词，不调用模型（0 成本，改提示词时用这个）
python cli.py analyze H --dry-run

# 3) 真跑：分析 H 题 → out/H题-分析报告.md + .docx
python cli.py analyze H

# 4) 离线自测：19 项检查，验证工具链（不消耗额度）
python tests/test_offline.py
```

`analyze` 后面可以写**题号**（`H`）或**文件名片段**（`滚球`）；跨年的同题号会自动提示年份。

### 命令行参数

| 参数 | 作用 |
|---|---|
| `--check` | 检查配置 / 题库 / 模型名（`GET /models`），不调用对话接口 |
| `--version` | 版本号 |
| `analyze <题号或片段>` | 生成作战方案 |
| `--steps N` | 最多跑多少步（默认 12；防模型卡死烧额度） |
| `--dry-run` | 只打印提示词，0 成本 |
| `--model <名>` | 临时换模型（覆盖 `.env`） |

---

## 它是怎么工作的（30 秒看懂 Agent）

```
         ┌──────────────── 循环（最多 N 步）────────────────┐
你 ──▶ messages ──▶ 模型 ──┬─▶ 直接回答 ──▶ 结束（写报告）   │
                          └─▶ 要调工具 ──▶ 执行 ──▶ 结果回灌 ┘
```

核心就是 `diansai_agent/loop.py` 里那 30 行：**模型只负责"决定下一步做什么"，程序负责"真的去做"，再把结果喂回去**。
所有 Agent（Claude Code、Codex、DSH 自己）都是这个骨架 —— 这里没有魔法，也没有框架黑盒。

### 5 个工具

| 工具 | 干什么 |
|---|---|
| `list_shiti` | 列出题库（按年份分组，题号/标题/大小） |
| `read_shiti` | 读赛题正文（**已修掉 PDF"一字一行"的排版问题**） |
| `search_qa` | 在赛区官方《问题解答（答疑）》里按关键词检索段落 —— 指标口径、器材限制都在这 |
| `search_tiku` | **跨年份检索**整个题库（历年赛题 + 答疑 + 规律文档）："往年考过什么类似的？" |
| `write_report` | 报告写入 `out/`，并自动转一份 Word |

---

## 项目结构

```
diansai-agent/
├── cli.py                       # 命令行入口
├── diansai_agent/
│   ├── config.py                # Key/模型/题库路径（Key 来源：环境变量 → .env → DSH 凭据文件）
│   ├── llm.py                   # 一次模型调用（requests 直连，不用 SDK）
│   ├── loop.py                  # ★ Agent Loop（心脏）
│   ├── prompts/
│   │   ├── system.md            # 角色与铁律（不许编造 / 必须标经验值 / 必须查答疑与历年题）
│   │   └── analyze.md           # 报告模板（8 个章节）
│   └── tools/
│       ├── __init__.py          # 工具注册表（JSON Schema + 执行 + 错误兜底）
│       ├── shiti.py             # 题库：列出 / 读取 / 检索答疑 / 跨年检索
│       ├── report.py            # 写 md + 转 docx
│       └── md2docx.py           # 随仓库带走的 Markdown→Word 转换器
├── docs/                        # 历年题名与分类、历年规律与 2027 选题预测（含 Word 版）
├── tests/test_offline.py        # 离线自测（19 项，不含 API 调用）
├── data/题库/                    # 本地赛题语料（★ 不进版本控制，见下）
├── requirements.txt
├── LICENSE                      # MIT（只覆盖代码）
└── out/                         # 产物（不进版本控制）
```

---

## 题库：内置什么、为什么不在 Git 里

本地题库覆盖 **5 个年份/批次、41 道题**（全部做过 PDF 排版清洗，可直接检索）：

| 年份 | 内容 |
|---|---|
| 2021 / 2023 / 2025 | 国赛本科组 **A–H 各 8 题** |
| 2024 | 省赛 **A–H 8 题** |
| 2026 | 赛区赛（TI 杯）**A–H 8 题完整 + 官方答疑汇总** |

- 赛前想加新题：把 PDF 丢进 `data/题库/<年份>/`，或改 `data/题库/_fetch_history.py` 里的年份清单再跑一次。
- 题库位置可配：环境变量 `DIANSAI_KB` 或在 `.env` 里写 `DIANSAI_KB=...`。
- ⚠️ **赛题原文不进 Git**（`.gitignore` 已排除 `data/`）：赛题著作权属于全国大学生电子设计竞赛组织委员会及赛区组委会，本仓库只提交**题目名称索引与来源链接**（见 `docs/历年题名与分类.md`）。

---

## 历年规律与 2027 选题预测

`docs/` 下有两份可直接阅读的文档（`.md` + `.docx`）：

- **`历年题名与分类.md`** —— 2021–2026 逐年题名、类别，以及"每年固定出现的五个坑位"
- **`历年规律与2027预测.md`** —— 赛制（单数年国赛 / 双数年专题赛）、**评分标准（基本要求 50 + 发挥 50 + 设计报告 20 = 120，另加综合测评 30）**、四条"换皮不换骨"的技术复用链、2027 年 8 题的类别配比预测（含概率与"赛前器件清单可验证信号"）

> 结论摘要：**国赛 8 题 ≈ 电源 2 + 仪器 2 + 控制 2 + 信号/通信 1 + 无人机·机器人 1**；只准备一条线就准备**控制类**。

---

## 成本与安全

| 项目 | 说明 |
|---|---|
| API Key 来源（优先级） | `环境变量 DEEPSEEK_API_KEY` → 仓库 `.env` → **DSH 凭据文件**（`$DSH_HOME/.credentials.yaml` 的 `refs.DEEPSEEK_API_KEY`） |
| Key 会不会被打印/提交 | **不会**。程序只读、只打印"来源"，`.gitignore` 排除 `.env` |
| 一次分析成本 | 8–12 次模型调用，用 `deepseek-flash` 属**几分钱**量级；跑完打印 token 统计 |
| 0 成本调试 | `--dry-run`（只打印提示词） |
| 模型名 | ★ 本机实测账号可用：**`deepseek-flash` / `deepseek-v4-pro`**（不是 `deepseek-chat`）。写错会 400，先跑 `--check` 用 `GET /models` 探一次 |

---

## 已知限制（v0.1 不做）

- 只做"赛题 → 方案"，还没有调参助手（v0.2）与自动评测（v0.3）
- 赛题 PDF 里的**图片/示意图读不到**（只抽文字），图上的尺寸标注需人工补
- 单轮执行，没有多轮追问；没有缓存（重复分析会重复花钱）
- 不做成本折算（只统计 token）

## Roadmap

| 版本 | 内容 | 验收标准 |
|---|---|---|
| **v0.2** | 调参助手：串口/CSV → 曲线 + 自动算**超调/上升时间/稳态误差** + PID 调整建议 | 用真实数据跑通，指标与手算一致 |
| **v0.3** | **评测**：10 条真题任务 → 自动打分（指标覆盖/器件齐全/时间线可行/风险识别）→ 回归报告 | 能看出"改提示词前后"分数变化 |
| v0.4 | DSH 技能薄壳（在 DeepSeek Harness 里直接调用）+ 可选 Web 界面 | 队友不装 Python 也能用 |

---

## 常见问题（踩过的坑）

| 问题 | 原因与解法 |
|---|---|
| `400 Bad Request` / 模型不存在 | 模型名写错。本账号是 `deepseek-flash` / `deepseek-v4-pro`，先 `python cli.py --check` 探一下 |
| `UnicodeEncodeError: 'gbk' codec can't encode ...` | Windows 控制台默认 GBK，打印 emoji 会崩。脚本开头已加 `sys.stdout.reconfigure(encoding="utf-8")` |
| 赛题读出来"一字一行" | PDF 排版问题。用 `extract_text(extraction_mode="layout")` + 短行合并清洗（见 `data/题库/_fetch_history.py` 的 `clean()`） |
| 同一道题被列出两遍 | 题库同时存在于内置与外部目录。已按「年份目录 + 文件名」去重，内置优先 |
| 为什么不用 LangChain 之类框架 | 本项目的目的就是**学会 Agent 骨架**；框架会把骨架藏起来。依赖越少越不容易坏 |

---

## 数据来源与致谢

- 历年赛题与分类：[CCBP/NUEDC_Topic](https://github.com/CCBP/NUEDC_Topic)（1994–2026）、[官方历届试题](https://nuedc.org/problems/)
- 赛制与评分：[2025 年实施过程说明（全国竞赛培训网）](https://www.nuedc-training.com.cn/index/news/details/new_id/333.html)、[2026 年专题赛通知](https://www.nuedc-training.com.cn/index/news/details/new_id/345)
- 2026 赛区赛赛题包：[官方赛题公示](https://res.nuedc-training.com.cn/topic/2026/topic_from_31.html)
- 完整来源清单见 `docs/历年规律与2027预测.md` 第 6 节

## 许可

代码：[MIT](LICENSE)。**赛题原文与评分标准等资料版权归全国大学生电子设计竞赛组织委员会及赛区组委会**，不在本许可范围内，且默认不进入本仓库。

---

_本项目是「电赛工作台」的 v0.1：先用起来，再长大。_
