# 数据与第三方资料的版权说明（NOTICE）

本仓库的 **MIT 许可证只覆盖代码**。以下内容的著作权不属于本项目作者，**不在 MIT 授权范围内**：

| 内容 | 著作权归属 | 本仓库的处理方式 |
|---|---|---|
| 全国大学生电子设计竞赛赛题原文（含历年题目 PDF 与文本） | 全国大学生电子设计竞赛组织委员会及各赛区组委会 | **不提交**（`.gitignore` 已排除 `data/`），仅保存在使用者本地 |
| 赛区官方《问题解答（答疑）》 | 相应赛区组委会 | 同上 |
| 评分标准 / 测评表相关内容 | 全国组委会与各赛区专家组 | 仓库中只做**摘要引用并标注来源链接**（见 `docs/`） |
| 题目名称、年份、分类等事实性信息 | 事实信息不受著作权保护 | 整理进 `docs/历年题名与分类.md` 并标注来源 |

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
| `diansai_agent/tools/md2docx.py` | Markdown→Word 转换器 | 本项目自有代码（MIT） |
| 历年题目目录整理 [CCBP/NUEDC_Topic](https://github.com/CCBP/NUEDC_Topic) | 题目索引与来源 | 见其仓库许可 |
