#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网页端离线测试：不调用模型 API，验证页面、接口、上传调参、路径安全。

用法：python tests/test_web.py       （退出码 0 = 通过）
需要：fastapi / httpx（TestClient 依赖）—— 已在 requirements 里
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi.testclient import TestClient                                  # noqa: E402
from diansai_agent.web.app import app                                       # noqa: E402

client = TestClient(app)
PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


def main() -> int:
    print("== 1. 页面与健康检查 ==")
    r = client.get("/")
    check("首页可访问（200）", r.status_code == 200, str(r.status_code))
    check("首页含中文标题", "竞赛 Agent 平台" in r.text)
    check("首页是自包含单文件（含内联样式与脚本）",
          "<style>" in r.text and "<script>" in r.text and "cdn" not in r.text.lower())

    r = client.get("/api/health")
    h = r.json()
    check("健康检查 200 且 ok", r.status_code == 200 and h.get("ok") is True)
    check("返回分区列表", any(d["id"] == "diansai" for d in h.get("domains", [])))
    check("返回工具清单（含调参）", "analyze_step_data" in h.get("tools", []))
    check("不泄露 Key 内容（只给来源）", "key" not in str(h).lower() or "key_source" in h)

    print("\n== 2. 分区与题库 ==")
    d = client.get("/api/domains").json()
    check("分区表有 markdown", "diansai" in d.get("table_md", ""))
    s = client.get("/api/shiti?domain=diansai").json()
    check("题库能列出", "题" in s.get("text", "") and len(s["text"]) > 100)
    bad = client.get("/api/shiti?domain=不存在的分区")
    check("未知分区返回 400", bad.status_code == 400, str(bad.status_code))

    print("\n== 2b. 结构化题目清单（年份/批次 + 题号，解决年份歧义）==")
    lst = client.get("/api/shiti-list").json()
    items, years = lst["items"], lst["years"]
    check("清单非空", len(items) >= 30, f"{len(items)} 条")
    check("覆盖 5 个年份/批次", len(years) >= 5, str(years))
    check("每条含 year/code/title/file/label",
          all(all(k in it for k in ("year", "code", "title", "file", "label")) for it in items))
    h_items = [it for it in items if it["code"] == "H"]
    check("同题号跨年份（H 题有多个年份）", len(h_items) >= 3, f"{len(h_items)} 个 H 题")
    check("同题号文件名互不相同（可用 file 唯一定位）",
          len({it["file"] for it in h_items}) == len(h_items))

    target = next(it for it in items if it["code"] == "H")
    det = client.get(f"/api/shiti-detail?file={target['file']}").json()
    check("按 file 能读到题面", len(det.get("text", "")) > 300 and "错误" not in det["text"][:8])
    check("题面返回元信息（年份/题号）", (det.get("meta") or {}).get("year") == target["year"])
    check("读不存在的题 → 404", client.get("/api/shiti-detail?file=不存在.md").status_code == 404)

    print("\n== 3. 调参接口（上传示例数据，不调模型）==")
    sample = ROOT / "samples" / "step-response-sample.csv"
    if not sample.exists():
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_sample_step.py")], check=True)
    with sample.open("rb") as f:
        r = client.post("/api/tune", files={"file": ("it.csv", f, "text/csv")},
                        data={"target": "1.0", "use_llm": "false"})
    check("上传调参返回 200", r.status_code == 200, r.text[:200])
    j = r.json()
    check("返回指标表（含超调量）", "超调量" in j.get("metrics_md", ""))
    check("返回曲线图文件名", bool(j.get("plot")), str(j))
    check("未开模型时不生成调参报告", not j.get("report_md"))
    if j.get("plot"):
        p = client.get(f"/api/file/{j['plot']}")
        check("曲线图可下载（PNG）", p.status_code == 200 and len(p.content) > 5000,
              f"{p.status_code} {len(p.content)} 字节")

    print("\n== 4. 错误处理与安全 ==")
    r = client.post("/api/tune", files={"file": ("bad.csv", b"abc\ndef\n", "text/csv")},
                    data={"target": "", "use_llm": "false"})
    check("坏数据返回 400（而不是 500）", r.status_code == 400, str(r.status_code))
    r = client.post("/api/tune", files={"file": ("x.csv", sample.read_bytes(), "text/csv")},
                    data={"target": "不是数字", "use_llm": "false"})
    check("非法目标值返回 400", r.status_code == 400, str(r.status_code))
    r = client.get("/api/file/" + "..%2F..%2Fetc%2Fpasswd")
    check("路径穿越被挡（404/400）", r.status_code in (400, 404), str(r.status_code))
    r = client.get("/api/file/不存在的东西.md")
    check("不存在文件返回 404", r.status_code == 404, str(r.status_code))

    print("\n== 5. 报告列表 ==")
    r = client.get("/api/reports").json()
    check("报告列表可用（items 是列表）", isinstance(r.get("items"), list))

    print("\n== 6. 分析接口（演示模式，不调模型）==")
    with client.stream("POST", "/api/analyze",
                       json={"domain": "diansai", "file": target["file"],
                             "label": target["label"], "use_llm": False}) as resp:
        body = "".join(chunk for chunk in resp.iter_text())
    check("SSE 返回 200", resp.status_code == 200, str(resp.status_code))
    check("SSE 里有提示词事件", '"kind": "prompt"' in body or '"kind":"prompt"' in body)
    check("任务里带上了年份/批次（不会分析错年份）", target["year"] in body, target["year"])
    check("提示词要求用 file 精确定位", "read_shiti" in body and target["file"] in body)

    print("\n" + "=" * 52)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("全部通过 ✅（离线测试，不消耗 API）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
