# 贡献指南（CONTRIBUTING）

> 这是一个**给大学生竞赛用的 Agent 工具**，希望它好用到**不熟悉 AI 的同学也愿意用、也看得懂**。
> 所以这里的门槛刻意定得很低 —— **不会写代码也能帮上忙**，见下面第一节。

## 一、你能贡献什么（按"不需要编程"到"需要编程"排序）

| 我想做什么 | 怎么做 | 需要会编程吗 |
|---|---|---|
| **许愿一个竞赛分区**（数学建模 / 智能汽车 / 蓝桥杯…） | 开 [分区许愿 Issue](../../issues/new?template=feature_request.yml)，说清"这个竞赛考什么、有什么公开资料" | 不用 |
| **报 bug** | 开 [Bug Issue](../../issues/new?template=bug_report.yml)，附**报错原文 + 复现步骤**（有截图最好） | 不用 |
| **贡献题库线索** | 开 [题库 Issue](../../issues/new?template=tiku.yml)，给出**公开可获取**的赛题来源链接 | 不用 |
| **改文档 / 修错别字 / 补例子** | 直接提 PR 改 `README.md`、`docs/*.md` | 基本不用 |
| **接一个新竞赛分区** | 见下面第三节（题目 + 提示词 + 一处登记，**不用改核心代码**） | 要一点 Python |
| **加一个工具 / 加测试** | 见 `contest_workbench/tools/` 与 `tests/` | 要 Python |

> ⚠️ **不要提交赛题原文、赛区《答疑》、评分标准**（版权不属于我们，见 [NOTICE.md](NOTICE.md)）。
> 题库只放在你**本地** `data/题库/`，该目录已被 `.gitignore` 排除。

## 二、本地跑起来（10 分钟）

```bash
git clone https://github.com/lk2168/contest-workbench.git
cd contest-workbench
pip install -r requirements.txt        # 国内建议加 -i https://pypi.tuna.tsinghua.edu.cn/simple

# 看配置与工具（不花钱）
python cli.py --check
python cli.py --domains
python cli.py analyze H --dry-run      # 只打印提示词，不调用模型

# 网页端
python cli.py web                      # → http://127.0.0.1:8765
```

**要真正跑分析**才需要 API Key：环境变量 `DEEPSEEK_API_KEY`，或复制 `.env.example` 为 `.env` 填进去。

## 三、接一个新的竞赛分区（本项目最欢迎的贡献）

三件事，**不用动 `loop.py` / `llm.py` / 工具代码**：

1. **题库**：把该竞赛的赛题整理成 Markdown 放进
   `data/题库/<分区id>/<年份批次>/<题号>题_<题名>.md`
   （PDF 可用 `python scripts/extract_shiti.py <源目录> <目标目录>` 批量转文本）
2. **提示词**：在 `contest_workbench/prompts/` 加一份，至少要有「角色 + 铁律 + 报告模板」
   （照抄 `analyze.md` 的结构改）
3. **登记**：在 `contest_workbench/domains.py` 的 `DOMAINS` 里加一条 `Domain(...)`，
   填 `id / name / kb_subdir / system_prompt / analyze_template / tools`

然后 `python cli.py --domain <分区id> analyze <题号>` 就能用；网页端的页签会**自动**按
`capabilities` 显示（比如没有 `tune_template` 就不会出现"调参助手"）。

## 四、提 PR 前请跑一遍测试（全部离线、不花钱）

```bash
python tests/test_offline.py     # 工具链 / 分区 / 报告落盘
python tests/test_tuning.py      # 调参算法（对解析解校验 —— 改了算法必须过）
python tests/test_web.py         # 网页端接口 / 上传调参 / 路径安全
```

> 没建题库也能跑：依赖题库的断言会自动「跳过」，不是失败。

## 五、几条硬约定（评审会看）

1. **中文**：注释、日志、文档、提交信息一律中文（本项目面向中文用户）。
2. **可验证优先**：能用确定性算法算的，**不要交给模型猜**；新算法要配"已知正确样本"的测试
   （参考 `tests/test_tuning.py` 用二阶系统解析解校验超调量）。
3. **零构建**：网页端不引前端框架、不加打包步骤、不引 CDN
   —— 这是"队友零安装"的前提，**破坏它的 PR 不会被合**。
4. **不许编造**：提示词与文档里不写没验证过的指标/器件参数；不确定就标「经验值」或「待确认」。
5. **一次一件事**：提交信息用 `类型: 做了什么`（如 `feat(web): 成果库支持 PDF 预览`），
   一个 PR 只做一类改动。
6. **不提交版权内容**：赛题原文、答疑、评分标准（见上文）。

## 六、遇到问题

- 先看 [README 的常见问题](README.md) 与 [`docs/平台路线图`](docs/)；
- 再搜/开 Issue；
- **不要**在 Issue 里贴 API Key（贴了请立刻去平台吊销重发）。
