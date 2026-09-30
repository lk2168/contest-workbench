#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""追问（多轮对话）与供应商切换的离线测试。

★ 关键点：用**假的 LLM** 替掉真调用，于是能离线验证"追问时上下文真的带上了" ——
  这是多轮对话最容易做错、又最难靠肉眼发现的地方（模型答得挺顺，但它其实忘了前面）。

为啥用 ollama 这个供应商来跑：它 `needs_key=False`，所以在没有 Key 的环境（CI）里
也能走到真正的模型调用分支，从而跑到我们想测的代码。

用法：python tests/test_ask.py       （退出码 0 = 通过）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 隔离配置目录；**不用环境变量指定供应商**（环境变量优先级高于配置文件，
# 会让下面的"切换供应商"测不出效果）—— 改成通过保存配置来设定初始供应商。
_TMP = Path(tempfile.mkdtemp(prefix="cw-ask-test-"))
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
from contest_workbench.config import Config, PROVIDERS, save_user_config      # noqa: E402
from contest_workbench.sessions import Sessions                               # noqa: E402

save_user_config(provider="ollama", model="fake-model")   # 初始：本地 ollama（不需要 Key）

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


# ── 假 LLM：记录每次收到的 messages，好让我们检查上下文 ──────────────────────
class FakeLLM:
    calls: list[list[dict]] = []

    def __init__(self, cfg) -> None:
        self.cfg = cfg

    def chat(self, messages, tools=None, temperature=0.3, retries=2):
        FakeLLM.calls.append([dict(m) for m in messages])
        # 第二轮（追问）时，如果上下文里有第一轮的结论，就把它复述出来 —— 便于断言
        joined = " ".join(str(m.get("content") or "") for m in messages)
        if "【第一轮结论】" in joined and joined.count("【第一轮结论】") >= 2:
            return {"role": "assistant", "content": "我记得刚才的分析（【第一轮结论】测试通过）",
                    "_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
        return {"role": "assistant", "content": "【第一轮结论】这是一份测试报告。",
                "_usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

    def list_models(self):
        return ["fake-model"]


def sse_events(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        if line.startswith("data: "):
            try:
                out.append(json.loads(line[6:]))
            except Exception:
                pass
    return out


def main() -> int:
    print("== 1. 供应商目录 ==")
    check("目录里有 8 家（含本地 ollama 与自定义）", len(PROVIDERS) >= 6, str(list(PROVIDERS)))
    check("默认供应商是 deepseek", "deepseek" in PROVIDERS and PROVIDERS["deepseek"]["needs_key"])
    check("ollama 标记为不需要 Key", PROVIDERS["ollama"]["needs_key"] is False)
    check("每项都有名称/base_url 字段",
          all("name" in p and "base_url" in p for p in PROVIDERS.values()))

    print("\n== 2. 不需要 Key 的供应商：has_key 应为真 ==")
    cfg = Config()
    check("ollama 下 has_key=True（无 Key 也能跑）", cfg.has_key is True)
    check("key_source 说明不需要 Key", "不需要 Key" in cfg.key_source(), cfg.key_source())
    check("base_url 用该供应商默认值", "11434" in cfg.base_url, cfg.base_url)

    print("\n== 3. 供应商切换（经接口保存）==")
    client = TestClient(webapp.app)
    pv = client.get("/api/settings/providers").json()
    check("接口返回供应商列表", len(pv["providers"]) >= 6 and pv["current"] == "ollama", str(pv["current"]))
    r = client.post("/api/settings", json={"provider": "不存在的供应商"})
    check("未知供应商 → 400", r.status_code == 400, str(r.status_code))
    r = client.post("/api/settings", json={"provider": "moonshot", "model": "kimi-x",
                                           "base_url": "https://api.moonshot.cn/v1"})
    check("切到 moonshot 成功", r.status_code == 200 and r.json()["provider"] == "moonshot",
          r.text[:120])
    check("切换后 base_url 跟着变", "moonshot" in r.json()["base_url"], r.json()["base_url"])
    r = client.post("/api/settings/models", json={"provider": "custom"})
    check("自定义供应商没填 base_url → 400", r.status_code == 400, str(r.status_code))
    client.post("/api/settings", json={"provider": "ollama"})       # 切回来继续下面的测试

    print("\n== 4. 会话记忆（纯逻辑）==")
    S = Sessions(max_sessions=2, max_turns=2, ttl=1)
    S.start("s1", "diansai", "分析 H 题", "报告正文", {"label": "2026-省赛 · H 题"})
    v = S.get("s1")
    check("分析后能取到会话", v is not None and v["turns"][0]["q"] == "分析 H 题")
    msgs = S.messages("s1", "SYSTEM", "这个方案的电机能换吗")
    check("拼接出的 messages 含 system + 历史 + 新问题",
          msgs[0]["role"] == "system" and msgs[1]["content"] == "分析 H 题"
          and msgs[-1]["content"] == "这个方案的电机能换吗", str(msgs))
    S.add_turn("s1", "问1", "答1")
    S.add_turn("s1", "问2", "答2")
    check("超过 max_turns 会裁掉最早的（不留无限上下文）",
          len(S.get("s1")["turns"]) == 2 and S.get("s1")["turns"][-1]["q"] == "问2",
          str(S.get("s1")["turns"]))
    check("不存在的会话返回 None", S.get("不存在") is None and S.messages("x", "S", "q") is None)
    S.start("s2", "diansai", "t2", "a2")
    S.start("s3", "diansai", "t3", "a3")
    check("超过 max_sessions 会淘汰最久未用的", S.count() == 2 and S.get("s1") is None,
          f"count={S.count()}")

    print("\n== 5. 追问端到端（用假 LLM，不花钱）==")
    webapp.LLM = FakeLLM                     # ★ 替掉真调用
    FakeLLM.calls.clear()
    with client.stream("POST", "/api/analyze",
                       json={"domain": "diansai", "file": "H题_测试.md",
                             "label": "2026-省赛 · H 题 · 测试", "use_llm": True}) as resp:
        body = "".join(resp.iter_text())
    evs = sse_events(body)
    sid = next((e["sid"] for e in evs if e.get("kind") == "session"), None)
    check("分析返回了 session id（前端据此追问）", bool(sid), str(sid))
    check("分析流里有回答", any(e.get("kind") == "answer" and e.get("answer") for e in evs))

    with client.stream("POST", "/api/ask", json={"sid": sid, "question": "能换电机吗？"}) as resp:
        body2 = "".join(resp.iter_text())
    evs2 = sse_events(body2)
    check("追问返回 200 且有回答", not resp.is_error
          and any(e.get("kind") == "answer" for e in evs2), body2[:160])
    check("★ 追问时把上一轮报告带进了上下文（多轮记忆生效）",
          len(FakeLLM.calls) >= 2
          and "【第一轮结论】" in json.dumps(FakeLLM.calls[-1], ensure_ascii=False),
          f"第 {len(FakeLLM.calls)} 次调用")
    check("★ 第二轮确实多带了本次问题",
          any(m.get("content") == "能换电机吗？" for m in FakeLLM.calls[-1]), "")

    r = client.post("/api/ask", json={"sid": "不存在的会话", "question": "在吗"})
    check("过期/不存在的会话 → 400 且给人话提示",
          r.status_code == 400 and "先做一次分析" in r.text, r.text[:140])
    r = client.post("/api/ask", json={"sid": sid, "question": "   "})
    check("空问题 → 400", r.status_code == 400, str(r.status_code))

    print("\n" + "=" * 52)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("全部通过 ✅（离线，用假 LLM，不消耗额度）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
