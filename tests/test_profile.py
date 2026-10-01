#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用户经验档位（首次引导）的离线测试。

★ 这一套的核心不是"能不能存下来"，而是**档位有没有真的改变输出**：
  填了"第一次参加"却给你一份和"拿过奖"一模一样的报告 = 形式主义问卷。
  所以这里用**假 LLM** 抓住真正发给模型的 system prompt 与任务描述，逐条断言差异。

用法：python tests/test_profile.py      （退出码 0 = 通过）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_TMP = Path(tempfile.mkdtemp(prefix="cw-profile-"))
os.environ["CONTEST_CONFIG_DIR"] = str(_TMP)
os.environ["DSH_HOME"] = str(_TMP / "dsh")
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ.pop("CONTEST_PROVIDER", None)
os.environ.pop("CONTEST_MODEL", None)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi.testclient import TestClient                                     # noqa: E402
import contest_workbench.web.app as webapp                                    # noqa: E402
from contest_workbench.config import save_user_config                         # noqa: E402
from contest_workbench.profile import (CHOICES, is_newbie, load_profile,      # noqa: E402
                                       persona_block, profile_file, report_hint,
                                       save_profile)

save_user_config(provider="ollama", model="fake-model")   # 不需要 Key，好走到模型调用分支

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


class FakeLLM:
    """只记下收到的 messages，不真的调模型。"""
    calls: list[list[dict]] = []

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def chat(self, messages, tools=None, temperature=0.3, retries=2):
        FakeLLM.calls.append([dict(m) for m in messages])
        return {"role": "assistant", "content": "（测试回答）",
                "_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    def list_models(self):
        return ["fake-model"]


def run_analyze(client, label="测试题"):
    FakeLLM.calls.clear()
    with client.stream("POST", "/api/analyze",
                       json={"domain": "diansai", "file": "H题_测试.md", "label": label,
                             "use_llm": True}) as resp:
        body = "".join(resp.iter_text())
    return body


def system_seen() -> str:
    """从假 LLM 收到的第一条消息里取 system 内容。"""
    for call in FakeLLM.calls:
        for m in call:
            if m.get("role") == "system":
                return str(m.get("content") or "")
    return ""


def task_seen() -> str:
    for call in FakeLLM.calls:
        for m in call:
            if m.get("role") == "user":
                return str(m.get("content") or "")
    return ""


def main() -> int:
    print("== ① 读写与兜底 ==")
    check("初始为空（没填过 = 什么都不注入）", load_profile() == {})
    check("空档位时 persona_block 为空字符串", persona_block({}) == "")
    check("空档位时 report_hint 为空字符串", report_hint({}) == "")
    check("档案文件落在本机用户目录（不上传）",
          str(profile_file()).startswith(str(_TMP)), str(profile_file()))

    save_profile(experience="第一次参加", team="一个人做", style="一步步带我做")
    p = load_profile()
    check("保存后能读回三个字段", p.get("experience") == "第一次参加"
          and p.get("team") == "一个人做" and p.get("style") == "一步步带我做", str(p))
    check("is_newbie 正确识别", is_newbie() is True)

    save_profile(style="")                       # 清空一项
    check("传空字符串可清空单项", "style" not in load_profile() and "experience" in load_profile())
    try:
        save_profile(experience="我是高手")       # 非法取值
        check("非法取值被拒绝", False, "没有报错")
    except ValueError as e:
        check("非法取值被拒绝（并给出可选值）", "只能是" in str(e), str(e))
    check("选项表齐全", set(CHOICES) == {"experience", "team", "style"})

    print("\n== ② 档位文本本身（可测的差异）==")
    newbie = persona_block({"experience": "第一次参加"})
    vet = persona_block({"experience": "拿过奖"})
    mid = persona_block({"experience": "参加过但没获奖"})
    check("三档文本互不相同", len({newbie, vet, mid}) == 3)
    check("新手档：要求解释术语/别假设他懂",
          "解释" in newbie and "不要假设他懂" in newbie)
    check("新手档：点名最容易翻车的地方", "翻车" in newbie)
    check("老手档：明确不要科普", "不要科普" in vet or "不要" in vet and "科普" in vet)
    check("中间档：要点出易丢分细节", "丢分" in mid)
    check("档位文本里带上了原始取值（可追溯）", "第一次参加" in newbie)

    h_new, h_vet = report_hint({"experience": "第一次参加"}), report_hint({"experience": "拿过奖"})
    check("新手档报告要求包含「前置知识」一节", "前置知识" in h_new)
    check("老手档报告要求**明确不要**前置知识", "不要" in h_vet and "前置知识" in h_vet)
    check("两档报告要求不同", h_new != h_vet)
    check("协作方式也影响输出：一步步带 → 拆成小步",
          "小步" in persona_block({"style": "一步步带我做"}))
    check("只在我卡住时给提示 → 不直接给完整答案",
          "不要直接给完整答案" in persona_block({"style": "只在我卡住时给提示"}))

    print("\n== ③ 端到端：档位真的进了发给模型的内容 ==")
    client = TestClient(webapp.app)
    webapp.LLM = FakeLLM

    run_analyze(client)
    sys_newbie, task_newbie = system_seen(), task_seen()
    check("分析时 system 里带上了档位说明",
          "本次协作对象的档位" in sys_newbie, sys_newbie[-160:])
    check("★ 新手档：system 里有「不要假设他懂」", "不要假设他懂" in sys_newbie)
    check("★ 新手档：任务描述里有「前置知识」要求", "前置知识" in task_newbie)

    save_profile(experience="拿过奖", style="直接给完整方案", team="和队友分工")
    run_analyze(client)
    sys_vet, task_vet = system_seen(), task_seen()
    check("★ 改用有经验档后，发给模型的 system 变了", sys_newbie != sys_vet)
    check("★ 有经验档：system 里出现「不要科普」", "不要科普" in sys_vet)
    check("★ 有经验档：任务描述里要求**不要**前置知识",
          "不要" in task_vet and "前置知识" in task_vet)
    check("两档的 system 长度不同（详细程度确实变了）",
          len(sys_newbie) != len(sys_vet), f"{len(sys_newbie)} vs {len(sys_vet)}")

    print("\n== ④ 接口 ==")
    d = client.get("/api/profile").json()
    check("GET 返回档位+选项+是否新手",
          d["profile"].get("experience") == "拿过奖" and "experience" in d["choices"]
          and d["is_newbie"] is False)
    r = client.post("/api/profile", json={"experience": "第一次参加"})
    check("POST 保存成功", r.status_code == 200 and r.json()["profile"]["experience"] == "第一次参加")
    check("POST 后 is_newbie 变 True", client.get("/api/profile").json()["is_newbie"] is True)
    r = client.post("/api/profile", json={"不存在的字段": "x"})
    check("未知字段 → 400", r.status_code == 400, r.text[:120])
    r = client.post("/api/profile", json={"experience": "随便写的"})
    check("非法取值 → 400 且提示可选值", r.status_code == 400 and "只能是" in r.text, r.text[:140])
    r = client.post("/api/profile", json={"experience": ""})
    check("清空单项：只清 experience，其它字段保留（部分更新语义）",
          r.status_code == 200 and "experience" not in r.json()["profile"]
          and "team" in r.json()["profile"], str(r.json()["profile"]))
    client.post("/api/profile", json={"team": "", "style": ""})
    check("全部清空后回到空档位（不再注入任何东西）",
          client.get("/api/profile").json()["profile"] == {})

    print("\n" + "=" * 52)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("全部通过 ✅（离线，用假 LLM 验证档位确实影响模型输入）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
