#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网页端离线测试：不调用模型 API，验证页面、接口、上传调参、路径安全。

用法：python tests/test_web.py       （退出码 0 = 通过）
需要：fastapi / httpx（TestClient 依赖）—— 已在 requirements 里
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

# ★ 隔离：把配置目录与 DSH 凭据路径都指到临时目录，测试**绝不碰你真实的配置文件**，
#   并显式清空环境变量，让"有没有 Key"在测试里是确定性的。
_TMP = Path(tempfile.mkdtemp(prefix="diansai-web-test-"))
os.environ["CONTEST_CONFIG_DIR"] = str(_TMP)
os.environ["DSH_HOME"] = str(_TMP / "dsh-home")
os.environ["DEEPSEEK_API_KEY"] = ""

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi.testclient import TestClient                                  # noqa: E402
from contest_workbench.web.app import app                                       # noqa: E402

client = TestClient(app)
PASS, FAIL, SKIP = [], [], []


# 题库因版权不进仓库 → 没建题库的机器（CI）上，依赖题库的断言应「跳过」而非「失败」
def _kb_ok() -> bool:
    try:
        from contest_workbench.tools.shiti import list_shiti_structured
        return len(list_shiti_structured()) > 0
    except Exception:
        return False


KB_OK = _kb_ok()


def check(name: str, cond: bool, detail: str = "", needs_kb: bool = False) -> None:
    if needs_kb and not KB_OK:
        SKIP.append(name)
        print(f"⏭️  跳过（未建题库）：{name}")
        return
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


def main() -> int:
    print("== 1. 页面与健康检查 ==")
    r = client.get("/")
    html = r.text
    check("首页可访问（200）", r.status_code == 200, str(r.status_code))
    check("首页含中文标题", "大学生竞赛工作台" in html)
    check("首页是自包含单文件（含内联样式与脚本）",
          "<style>" in html and "<script>" in html and "cdn" not in html.lower())

    print("\n== 1b. 信息架构：小白第一眼只看得到「选择竞赛」==")
    check("首屏是分区选择视图（含 hero 标题与分区容器）",
          'id="viewHome"' in html and "把竞赛赛题" in html and 'id="featuredCard"' in html)
    check("首屏没有技术参数（最多步数藏在设置里）",
          'id="settingsDrawer"' in html and html.index('id="settingsDrawer"') < html.index('id="steps"'))
    check("设置抽屉默认隐藏（未加 on 类，aria-hidden=true）",
          'id="settingsDrawer"' in html and 'aria-hidden="true"' in html and "drawer on" not in html)
    check("设置里含「最多步数」并带人话解释", "最多步数" in html and "最多思考几步" in html)
    check("调参面板默认 hidden（只在具备该能力的分区显示）",
          re.search(r'id="panelTune"[^>]*\shidden', html) is not None)
    check("显式兜住 hidden（否则 .work{display:grid} 会让 hidden 失效、两个页签同时露出）",
          "[hidden]{display:none" in html.replace(" ", ""))
    check("二级页面有返回入口", 'id="backHome"' in html and "全部竞赛" in html)
    check("首页统计数字来自接口（无硬编码假数据）",
          'id="stats"' in html and '["题库真题"' in html)
    check("样式系统用 CSS 变量（可复用给其他模块）",
          "--b600:" in html and "--sh-md:" in html and "var(--r-card)" in html)
    check("网格子项有 min-width:0 防 grid blowout",
          "minmax(0,1fr)" in html and ".work>*{min-width:0}" in html.replace(" ", ""))
    check("报告预览区固定在页面里（★ 不能塞进 hidden 的调参面板，否则点了没反应）",
          'id="reportView"' in html and '$("panelTune").appendChild' not in html)
    check("支持 ?report= 深链预览报告", 'get("report")' in html and "showReport(" in html)
    emojis = ["🔧", "↳", "✅", "❌", "⚙", "🎯", "🚧"]
    hit = [e for e in emojis if e in html]
    check("界面零 emoji（设计规范：不用字符当图标）", not hit, "仍含：" + "".join(hit))

    r = client.get("/api/health")
    h = r.json()
    check("健康检查 200 且 ok", r.status_code == 200 and h.get("ok") is True)
    check("返回分区列表", any(d["id"] == "diansai" for d in h.get("domains", [])))
    check("返回工具清单（含调参）", "analyze_step_data" in h.get("tools", []))
    check("不泄露 Key 内容（只给来源）", "key" not in str(h).lower() or "key_source" in h)

    print("\n== 2. 分区与题库 ==")
    d = client.get("/api/domains").json()
    check("分区表有 markdown", "diansai" in d.get("table_md", ""))
    caps = {x["id"]: x.get("capabilities", []) for x in d["domains"]}
    check("电赛具备调参能力（capabilities 含 tune）", "tune" in caps.get("diansai", []), str(caps))
    check("规划中分区没有调参能力（页签不会出现）", "tune" not in caps.get("mathmodel", []), str(caps))
    check("所有分区都有赛题分析能力", all("analyze" in v for v in caps.values()))
    s = client.get("/api/shiti?domain=diansai").json()
    check("题库能列出", "题" in s.get("text", "") and len(s["text"]) > 100, needs_kb=True)
    bad = client.get("/api/shiti?domain=不存在的分区")
    check("未知分区返回 400", bad.status_code == 400, str(bad.status_code))

    print("\n== 2b. 结构化题目清单（年份/批次 + 题号，解决年份歧义）==")
    lst = client.get("/api/shiti-list").json()
    items, years = lst["items"], lst["years"]
    check("清单非空", len(items) >= 30, f"{len(items)} 条", needs_kb=True)
    check("覆盖 5 个年份/批次", len(years) >= 5, str(years), needs_kb=True)
    check("每条含 year/code/title/file/label",
          all(all(k in it for k in ("year", "code", "title", "file", "label")) for it in items))
    h_items = [it for it in items if it["code"] == "H"]
    check("同题号跨年份（H 题有多个年份）", len(h_items) >= 3, f"{len(h_items)} 个 H 题", needs_kb=True)
    check("同题号文件名互不相同（可用 file 唯一定位）",
          len({it["file"] for it in h_items}) == len(h_items))
    if not KB_OK:
        print("   （题库未建，跳过与题面内容相关的检查；先跑 scripts/fetch_history.py）")
    # ★ 不能用 next(...) 直接取：题库为空时会抛 StopIteration 让整套测试崩掉
    target = h_items[0] if h_items else None
    if target:
        det = client.get(f"/api/shiti-detail?file={target['file']}").json()
        check("按 file 能读到题面", len(det.get("text", "")) > 300 and "错误" not in det["text"][:8])
        check("题面返回元信息（年份/题号）", (det.get("meta") or {}).get("year") == target["year"])
    else:
        check("按 file 能读到题面", False, needs_kb=True)
        check("题面返回元信息（年份/题号）", False, needs_kb=True)
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

    print("\n== 5b. 设置：填 API Key / 换模型（小白可用性的关键）==")
    s0 = client.get("/api/settings").json()
    check("设置接口可用（含 config_file / out_dir / 可选模型）",
          all(k in s0 for k in ("has_key", "key_source", "masked_key", "model",
                                "config_file", "out_dir", "model_options")))
    check("配置写进**用户目录**（打包后程序目录可能不可写）",
          str(_TMP) in s0["config_file"], s0["config_file"])
    check("未配置时 firstRun 引导会显示（接口如实报告 has_key）", s0["has_key"] is False,
          f"has_key={s0['has_key']} source={s0['key_source']}")

    bad = client.post("/api/settings", json={"api_key": "不是key"})
    check("形状不对的 Key → 400", bad.status_code == 400, str(bad.status_code))
    unknown = client.post("/api/settings", json={"nope": 1})
    check("不认识的设置项 → 400", unknown.status_code == 400, str(unknown.status_code))

    FAKE = "sk-test-1234567890abcdefghij"
    saved = client.post("/api/settings", json={"api_key": FAKE, "model": "deepseek-v4-pro"})
    check("保存 Key + 模型 → 200", saved.status_code == 200, saved.text[:150])
    env_file = Path(saved.json()["saved_to"])
    check("配置文件已写到用户目录", env_file.exists() and str(_TMP) in str(env_file), str(env_file))
    check("文件里确实写入了（供下次启动生效）", FAKE in env_file.read_text(encoding="utf-8"))

    s1 = client.get("/api/settings").json()
    check("保存后 has_key=True", s1["has_key"] is True)
    check("★ 接口只回显打码 Key，不泄露明文",
          s1["masked_key"] != FAKE and "*" in s1["masked_key"] and FAKE not in saved.text,
          s1["masked_key"])
    check("模型已切换", s1["model"] == "deepseek-v4-pro", s1["model"])
    check("Key 来源如实标注为用户配置文件", "用户配置文件" in s1["key_source"], s1["key_source"])

    client.post("/api/settings", json={"api_key": ""})     # 空字符串 = 清空
    check("传空字符串可清空该项（不残留旧 Key）",
          FAKE not in env_file.read_text(encoding="utf-8"))
    t = client.post("/api/settings/test").json()
    check("未配置时「测试连接」给出人话提示而不是异常",
          isinstance(t, dict) and ("ok" in t))

    print("\n== 5c. 自检接口（打包后最易坏的一环）==")
    st = client.get("/api/selftest").json()
    check("自检返回结构完整", all(k in st for k in ("frozen", "python", "items", "all_ok", "out_dir")))
    by_name = {i["name"]: i for i in st["items"]}
    # 这几项**不依赖 API Key**，任何环境都必须过（CI 也一样）
    for must in ("提示词可读（随包资源）", "网页界面可读（随包资源）", "输出目录可写",
                 "Word 转换可用（进程内）", "调参计算可用（numpy）"):
        it = by_name.get(must)
        check(f"自检项通过：{must}", bool(it and it["ok"]), (it or {}).get("detail", "缺少该项"))
    check("自检项包含示例数据与题库（可选）", "示例数据可读（随包资源）" in by_name and "题库已就绪（可选）" in by_name)
    check("源码模式下 frozen=False", st["frozen"] is False)

    print("\n== 6. 分析接口（演示模式，不调模型）==")
    # 演示模式只需要 file/label —— 没题库时用占位值，接口本身仍应正常返回 SSE
    fake = target or {"file": "A题_示例.md", "label": "示例-省赛 · A 题 · 示例", "year": "示例-省赛"}
    with client.stream("POST", "/api/analyze",
                       json={"domain": "diansai", "file": fake["file"],
                             "label": fake["label"], "use_llm": False}) as resp:
        body = "".join(chunk for chunk in resp.iter_text())
    check("SSE 返回 200", resp.status_code == 200, str(resp.status_code))
    check("SSE 里有提示词事件", '"kind": "prompt"' in body or '"kind":"prompt"' in body)
    check("任务里带上了年份/批次（不会分析错年份）", fake["year"] in body, fake["year"], needs_kb=True)
    check("提示词要求用 file 精确定位", "read_shiti" in body and fake["file"] in body)

    print("\n" + "=" * 52)
    tail = f"，跳过 {len(SKIP)} 项（未建题库）" if SKIP else ""
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项{tail}")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("全部通过 ✅（离线测试，不消耗 API）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
