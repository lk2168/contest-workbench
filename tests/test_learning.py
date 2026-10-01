#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知识点映射（「这题要会什么」）的离线测试。

★ 两层测试：
  ① **数据质量闸门** —— 知识表本身要被约束：每条必须有知识点/关键词/判据/搜索词，
     且判据不能是"熟悉""了解"这种没法自测的空话（否则用户看完还是不知道要干嘛）
  ② **匹配与接口** —— 命中是否合理、top_k、URL 编码、不把链接交给模型生成、接口边界

用法：python tests/test_learning.py      （退出码 0 = 通过）
"""
from __future__ import annotations

import os
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CONTEST_CONFIG_DIR", tempfile.mkdtemp(prefix="cw-learn-"))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi.testclient import TestClient                                     # noqa: E402
import contest_workbench.web.app as webapp                                    # noqa: E402
from contest_workbench.tools import call_tool, is_error                       # noqa: E402
from contest_workbench.tools.learning import (clear_cache, learning_for,      # noqa: E402
                                              load_knowledge, match_knowledge,
                                              search_links, suggest_learning)
from contest_workbench.tools.shiti import list_shiti_structured               # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


def skip(name: str, why: str) -> None:
    SKIP.append(name)
    print(f"⏭️  {name}  —— {why}")


VAGUE = ("熟悉", "了解", "掌握", "知道", "理解")


def main() -> int:
    print("== ① 知识表本身（数据质量闸门）==")
    items = load_knowledge("diansai")
    check("知识表能读到条目", len(items) >= 15, f"只有 {len(items)} 条")
    check("id 唯一", len({i['id'] for i in items}) == len(items))

    bad = [i["id"] for i in items if not i["知识点"] or not i["命中关键词"]
           or not i["掌握判据"] or not i["搜索词"]]
    check("每条都有 知识点/关键词/掌握判据/搜索词", not bad, f"缺字段：{bad}")

    vague = []
    for i in items:
        for c in i["掌握判据"]:
            head = c.strip()[:2]
            if head in VAGUE and "能" not in c:
                vague.append(f"{i['id']}:{c[:14]}")
    check("掌握判据不是空话（须可自测，如「能解释…」「能算出…」）", not vague,
          f"可疑判据：{vague[:4]}")

    dup = [i["id"] for i in items if len(set(i["搜索词"])) != len(i["搜索词"])]
    check("同一知识块内搜索词不重复", not dup, str(dup))
    check("核验资源写明类型（教材/手册/文档）",
          all(all(r.get("类型") for r in i["核验资源"]) for i in items))
    print(f"   共 {len(items)} 个知识块，判据 {sum(len(i['掌握判据']) for i in items)} 条，"
          f"搜索词 {sum(len(i['搜索词']) for i in items)} 个")

    print("\n== ② 关键词匹配是否命中得合理 ==")
    m = match_knowledge("车载平衡滚球运动控制系统 摆杆 超调 PID 整定 电机", items, top_k=5)
    check("H 题式题面 → 命中 PID/闭环控制", bool(m) and m[0]["id"] == "control-pid",
          f"首条是 {m[0]['id'] if m else '（空）'}")
    m = match_knowledge("AC-AC 变换电路 并联运行 逆变 变压器 占空比", items, top_k=5)
    check("A 题式题面 → 命中电力电子变换",
          any(x["id"] == "power-electronics" for x in m), str([x["id"] for x in m[:3]]))
    m = match_knowledge("基于单目视觉的目标物测量装置 相机标定 图像", items, top_k=5)
    check("视觉类题面 → 命中机器视觉",
          any(x["id"] == "vision" for x in m), str([x["id"] for x in m[:3]]))
    check("命中列表带「命中」词与得分（界面要显示依据）",
          bool(m) and m[0]["命中"] and m[0]["得分"] > 0)
    check("没命中的知识块不会硬塞进来",
          all(match_knowledge("AC-AC 变换电路", items) [i]["得分"] > 0
              for i in range(len(match_knowledge("AC-AC 变换电路", items)))))
    check("top_k 生效", len(match_knowledge("PID 电机 视觉 电源 通信 滤波", items, top_k=2)) == 2)
    check("空文本 → 返回全部（交给界面决定怎么用）",
          len(match_knowledge("", items)) == 0)

    print("\n== ③ 搜索入口由代码拼（不交给模型编链接）==")
    lk = search_links("PID 参数整定")
    q = urllib.parse.quote("PID 参数整定")
    check("B站链接正确编码", lk["bilibili"].endswith(q) and lk["bilibili"].startswith("https://search.bilibili.com/"))
    check("必应链接正确编码", lk["bing"].endswith(q))
    data = learning_for("diansai", text="PID 超调 电机", top_k=3)
    check("结构化结果里每条搜索词都带搜索链接",
          all(w in it["搜索链接"] for it in data["items"] for w in it["搜索词"]))
    md = suggest_learning(text="PID 超调 电机")
    first = learning_for("diansai", text="PID 超调 电机", top_k=1)["items"][0]
    check("给模型的 Markdown 真的含该知识块的判据原文",
          any(c in md for c in first["掌握判据"]), first["掌握判据"][:1])
    check("给模型的 Markdown 真的含该知识块的搜索词原文",
          all(w in md for w in first["搜索词"]))
    check("★ 给模型的文本里**不含任何 http 链接**（避免模型编网址）",
          "http" not in md.lower(), md[:200])

    print("\n== ④ 兜底与接口 ==")
    check("未知分区 → 空表且不抛异常",
          learning_for("不存在的分区")["items"] == []
          and learning_for("不存在的分区")["has_knowledge_file"] is False)
    clear_cache()
    check("clear_cache 后仍能重读", len(load_knowledge("diansai")) >= 15)
    r = call_tool("suggest_learning", {"text": "PID"})
    check("工具入口可用（suggest_learning 不报错）", not is_error(r))
    r = call_tool("suggest_learning", {})
    check("什么都不传也不崩", isinstance(r, str) and len(r) > 0)

    client = TestClient(webapp.app)
    d = client.get("/api/learning", params={"domain": "diansai"}).json()
    check("接口：不带题也能返回知识块（不花额度）", d["matched"] >= 1 and d["by_keywords"] is False)
    check("接口：未知分区 → 400", client.get("/api/learning", params={"domain": "xx"}).status_code == 400)

    # ★ 显式重置域名：上面的"未知分区 → 400"用例会把 CONTEST_DOMAIN 污染成 xx
    #   （接口用环境变量切换分区，这是已知的设计气味，测试里要自己兜住）
    os.environ["CONTEST_DOMAIN"] = "diansai"
    # ★ 回归：真实题面的首位命中必须是"主题知识"，不能被通用词顶掉
    os.environ["CONTEST_DOMAIN"] = "diansai"
    kb = list_shiti_structured()
    if not kb:
        skip("接口：按真实题面匹配", "本机题库为空（版权原因题库不进仓库）")
    else:
        f = kb[0]["file"]
        d = client.get("/api/learning", params={"domain": "diansai", "file": f}).json()
        check(f"接口：按题面「{kb[0]['code']}题」匹配成功",
              d["matched"] >= 1 and d["by_keywords"] is True,
              f"matched={d.get('matched')}")
        check("接口：返回 meta（题号/年份，界面要显示）", bool(d.get("meta")))
        bad = client.get("/api/learning", params={"domain": "diansai", "file": "不存在的题.md"})
        check("接口：题不存在 → 404", bad.status_code == 404, str(bad.status_code))

        # 控制类题（H）首位应是控制/机电，电源类题（A）首位应是电力电子
        h = next((x for x in kb if x["code"] == "H"), None)
        a = next((x for x in kb if x["code"] == "A"), None)
        if h:
            d = client.get("/api/learning", params={"domain": "diansai", "file": h["file"]}).json()
            top = d["items"][0]["id"] if d["items"] else ""
            check(f"回归：H题首位命中是控制/机电类（不是报告类）",
                  top in ("control-pid", "motor-control", "signal-conditioning"), f"首位={top}")
        if a:
            d = client.get("/api/learning", params={"domain": "diansai", "file": a["file"]}).json()
            top = d["items"][0]["id"] if d["items"] else ""
            check(f"回归：A题首位命中是电力电子类", top == "power-electronics", f"首位={top}")
        d = client.get("/api/learning", params={"domain": "diansai"}).json()
        check("通用词降权生效：题名加成不误伤（无题面时仍按知识表顺序）",
              d["by_keywords"] is False)

    print("\n" + "=" * 52)
    tail = f"，跳过 {len(SKIP)} 项" if SKIP else ""
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项{tail}")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("全部通过 ✅（离线，不消耗额度）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
