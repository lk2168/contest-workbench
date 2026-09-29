# 数据与第三方资料的版权说明（NOTICE）

## 授权范围（一句话）

**本项目自己创作的代码、提示词、文档、截图，均以 [MIT](LICENSE) 授权**（可商用、可修改、可再分发，保留版权声明即可）。
**第三方内容的著作权不属于本项目作者，不在 MIT 范围内** —— 详见下表。

## 第三方内容

| 内容 | 著作权归属 | 本仓库的处理方式 |
|---|---|---|
| 全国大学生电子设计竞赛赛题原文（含历年题目 PDF 与文本） | 全国大学生电子设计竞赛组织委员会及各赛区组委会 | **不提交**（`.gitignore` 已排除 `data/`），仅保存在使用者本地 |
| 赛区官方《问题解答（答疑）》 | 相应赛区组委会 | 同上 |
| 评分标准 / 测评表相关内容 | 全国组委会与各赛区专家组 | 仓库中只做**摘要引用并标注来源链接**（见 `docs/`） |
| 题目名称、年份、分类等事实性信息 | 事实信息不受著作权保护 | 整理进 `docs/历年题名与分类.md` 并标注来源 |
| 教育部认可的竞赛清单（84 项） | 中国高等教育学会高校竞赛评估与管理体系研究专家工作组 | `docs/竞赛清单-教育部认可84项.txt` 为**事实性名单**，文件头已标注来源 |

## 使用者的责任

- `scripts/fetch_history.py`、`scripts/extract_shiti.py` 只做**本地备赛检索**的抓取与文本化处理；
  使用者应自行确认其使用方式符合当地法律与竞赛相关规定。
- 若你要 fork 本仓库并公开发布，**请勿把赛题原文、答疑原文提交进公开仓库**（保持 `data/` 不被跟踪即可）。

## 代码中引用的第三方

| 名称 | 用途 | 许可 |
|---|---|---|
| [pypdf](https://github.com/py-pdf/pypdf) | PDF 文本抽取 | BSD-3-Clause |
| [python-docx](https://github.com/python-openxml/python-docx) | 生成 Word 报告 | MIT |
| [requests](https://github.com/psf/requests) | HTTP 调用 | Apache-2.0 |
| [PyYAML](https://github.com/yaml/pyyaml) | 读取 DSH 凭据文件（可选） | MIT |
| [NumPy](https://github.com/numpy/numpy) | 调参指标计算 | BSD-3-Clause |
| [Matplotlib](https://github.com/matplotlib/matplotlib) | 阶跃响应曲线图 | PSF-based（BSD 兼容） |
| [FastAPI](https://github.com/fastapi/fastapi) / [Uvicorn](https://github.com/encode/uvicorn) / [python-multipart](https://github.com/Kludex/python-multipart) | 网页端 | MIT / BSD-3-Clause / Apache-2.0 |
| ★ [Lucide](https://github.com/lucide-icons/lucide) | 网页端图标（`contest_workbench/web/static/index.html` 里的内联 SVG 路径取自 Lucide） | **ISC** |
| `contest_workbench/tools/md2docx.py` | Markdown→Word 转换器 | 本项目自有代码（MIT） |
| 历年题目目录整理 [CCBP/NUEDC_Topic](https://github.com/CCBP/NUEDC_Topic) | 题目索引与来源 | 见其仓库许可 |

> **Lucide 的 ISC 许可要求保留版权与许可声明**，故在此列出：
> Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of Feather (MIT),
> and for portions held by Lucide Contributors 2022. Licensed under the ISC License.
> 设计参考（仅借鉴思路，未复制代码）：[open-props](https://github.com/argyleink/open-props)（MIT）、
> [frontend-design](https://github.com/Ilm-Alan/frontend-design)（Agent Skill）。

