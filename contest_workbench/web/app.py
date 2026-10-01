# -*- coding: utf-8 -*-
"""网页端：把命令行能力包成"队友零安装就能用"的界面。

设计取舍：
  - **FastAPI + 原生 HTML/JS**，不引前端框架、不引构建工具 —— 仓库里没有 node_modules，
    谁 clone 下来 `pip install -r requirements.txt` 就能用（这是"给不懂 AI 的大学生用"的前提）。
  - **SSE 推送 Agent 进度**：Agent 的每一步、每次工具调用都实时显示，
    让使用者看得见"AI 在干什么"，而不是盯着转圈等结果（可视化是理解 Agent 的关键）。
  - 上传的数据、生成的报告都落在 out/ 下，前端直接预览图片与下载 Word。
"""
from __future__ import annotations

import json
import queue
import re
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from ..config import (OUT_DIR, PROVIDERS, REPO_ROOT, Config, save_user_config,
                      user_env_file)
from ..domains import DOMAINS, get_domain, list_domains, prompt_text
from ..llm import LLM
from ..loop import Agent
from ..sessions import SESSIONS
from ..tools import ERROR_PREFIX, TOOL_SCHEMAS, call_tool, is_error
from ..tools.shiti import list_shiti, list_shiti_structured

# 本机实测可用的模型（界面下拉用；仍允许手填别的）
MODEL_OPTIONS = ["deepseek-flash", "deepseek-v4-pro"]

# ── 平台自我介绍（也是首页"这个项目是什么"的事实来源，改这里就够）───────────
PLATFORM = {
    "name": "大学生竞赛工作台",
    "tagline": "把竞赛赛题变成能照着干的方案；控制类数据算成可复核的指标",
    "repo": "https://github.com/lk2168/contest-workbench",
}

MAX_UPLOAD = 20 * 1024 * 1024          # 单个上传文件上限 20 MB
UPLOAD_DIR = OUT_DIR / "uploads"
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title=PLATFORM["name"], version="0.5.0")


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def _safe_name(name: str) -> str:
    """只保留合法字符，防止路径穿越（本地工具也要防）。"""
    name = Path(name or "upload.csv").name
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name)[:120] or "upload.csv"


def _domain_or_400(domain_id: str):
    try:
        return get_domain(domain_id)
    except KeyError:
        raise HTTPException(status_code=400, detail=f"未知分区：{domain_id}")


def _list_report_files() -> list[dict]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(OUT_DIR.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.is_dir() or p.name.startswith("."):
            continue
        items.append({
            "name": p.name,
            "size": p.stat().st_size,
            "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)),
            "kind": p.suffix.lstrip(".").lower(),
        })
    return items


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    # 不缓存首页：否则本地改完前端、浏览器还拿旧页面（改完刷新即可生效）
    return HTMLResponse(html, headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/api/health")
def health() -> JSONResponse:
    cfg = Config()
    return JSONResponse({
        "ok": True,
        "platform": PLATFORM,
        "model": cfg.model,
        "key_source": cfg.key_source(),
        "has_key": cfg.has_key,
        "domains": [{"id": d.id, "name": d.name, "implemented": d.implemented} for d in DOMAINS.values()],
        "tools": [t["function"]["name"] for t in TOOL_SCHEMAS],
    })


@app.get("/api/domains")
def api_domains() -> JSONResponse:
    """分区清单，并带上该分区**能提供哪些能力**（网页端据此决定显示哪些页签）。

    能力由 domains.py 的 Domain 配置推导：有 tune_template 才有"调参"页签，
    这样"只有电赛用得上的功能"就不会出现在别的分区里。
    """
    out = []
    for d in DOMAINS.values():
        caps = ["analyze"]
        if d.tune_template:
            caps.append("tune")
        out.append({"id": d.id, "name": d.name, "implemented": d.implemented,
                    "note": d.note, "capabilities": caps})
    return JSONResponse({"table_md": list_domains(), "domains": out})


@app.get("/api/shiti")
def api_shiti(domain: str = "diansai") -> JSONResponse:
    import os
    os.environ["CONTEST_DOMAIN"] = domain
    _domain_or_400(domain)
    return JSONResponse({"text": list_shiti()})


@app.get("/api/shiti-list")
def api_shiti_list(domain: str = "diansai") -> JSONResponse:
    """结构化题目清单：年份/批次 + 题号 + 题名 + 文件名（网页端两级选择用）。

    额外返回 `searched`（搜过哪些目录）与 `recommended`（推荐放哪）——
    打包成 exe 后"题库在哪"对用户是黑盒，界面必须把它讲清楚。
    """
    import os
    os.environ["CONTEST_DOMAIN"] = domain
    _domain_or_400(domain)
    from ..tools.shiti import searched_dirs
    items = list_shiti_structured()
    years = sorted({x["year"] for x in items}, reverse=True)
    cands = [str(p) for p in searched_dirs()]
    return JSONResponse({"items": items, "years": years, "count": len(items),
                         "searched": cands, "recommended": cands[0] if cands else ""})


@app.post("/api/reveal")
def api_reveal(payload: dict) -> JSONResponse:
    """在文件管理器里打开某个目录（题库/输出/配置），并保证它存在。

    给"不懂命令行"的用户用：界面上点一下就看到该把题库放哪。仅本机可用。
    """
    import os
    import subprocess as _sp
    from ..config import OUT_DIR, user_dir
    from ..domains import get_domain

    what = ((payload or {}).get("what") or "").strip()
    if what == "kb":
        sub = get_domain((payload or {}).get("domain") or "diansai").kb_subdir
        d = user_dir() / "题库" / sub
    elif what == "out":
        d = OUT_DIR
    elif what == "config":
        d = user_dir()
    else:
        raise HTTPException(status_code=400, detail="what 只能是 kb / out / config")
    try:
        d.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            _sp.Popen(["explorer", str(d)])
        elif sys.platform == "darwin":
            _sp.Popen(["open", str(d)])
        else:
            _sp.Popen(["xdg-open", str(d)])
        return JSONResponse({"ok": True, "opened": str(d)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}", "path": str(d)})


@app.get("/api/learning")
def api_learning(file: str = "", name: str = "", domain: str = "diansai",
                 max_items: int = 6) -> JSONResponse:
    """「这题要会什么」：知识点 + 自测判据 + 搜索词 + 搜索入口（**确定性，不调用模型**）。

    题面用 file 精确定位（避免年份歧义）；也可以只给 name。
    """
    import os
    os.environ["CONTEST_DOMAIN"] = domain
    _domain_or_400(domain)
    from ..tools.learning import learning_for
    text = ""
    meta = None
    if file or name:
        got = call_tool("read_shiti", {"file": file, "name": name, "max_chars": 12000})
        if is_error(got):
            raise HTTPException(status_code=404, detail=got.replace(ERROR_PREFIX, "").strip())
        text = got
        if file:
            meta = next((x for x in list_shiti_structured() if x["file"] == file), None)
    data = learning_for(domain, text=text, top_k=max(1, min(int(max_items), 20)),
                        title=(meta or {}).get("title", ""))
    return JSONResponse({**data, "meta": meta})


@app.get("/api/shiti-detail")
def api_shiti_detail(file: str, domain: str = "diansai", max_chars: int = 12000) -> JSONResponse:
    """读某一道题的正文（用 file 精确定位，避免年份歧义）。"""
    import os
    os.environ["CONTEST_DOMAIN"] = domain
    _domain_or_400(domain)
    text = call_tool("read_shiti", {"file": file, "max_chars": max_chars})
    if is_error(text):
        raise HTTPException(status_code=404, detail=text.replace(ERROR_PREFIX, "").strip())
    meta = next((x for x in list_shiti_structured() if x["file"] == file), None)
    return JSONResponse({"text": text, "meta": meta})


@app.post("/api/analyze")
def api_analyze(payload: dict) -> StreamingResponse:
    """分析赛题：SSE 流式返回 Agent 的每一步。

    定位方式：优先 `file`（网页端两级选择给出的精确文件名，能确定年份），
    否则退回 `name`（题号或关键词，会提示年份歧义）。
    """
    domain = (payload or {}).get("domain", "diansai")
    file = ((payload or {}).get("file") or "").strip()
    name = ((payload or {}).get("name") or "").strip()
    label = ((payload or {}).get("label") or "").strip()
    use_llm = bool((payload or {}).get("use_llm", True))
    steps = int((payload or {}).get("steps", 12))
    d = _domain_or_400(domain)
    cfg = Config()
    if not file and not name:
        raise HTTPException(status_code=400, detail="请选择一道题（或填题号/关键词）")

    # 目标描述：带上年份/批次，报告与提示词里都不会再有歧义
    target_desc = label or (f"{file}" if file else f"题号/关键词「{name}」")
    sid = uuid.uuid4().hex[:12]

    def gen():
        def send(obj):
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        # ★ 先把会话 id 告诉前端：跑完就能拿它继续追问
        yield send({"kind": "session", "sid": sid, "label": target_desc})

        if not use_llm or not cfg.has_key:
            why = "演示模式（未调用模型）" if use_llm is False else "未配置模型 Key"
            yield send({"kind": "log", "text": f"⚠️ {why}：本次只展示将要发给模型的提示词。"})
            system = prompt_text(d, "system")
            task = _build_task(d, target_desc, file, name)
            yield send({"kind": "prompt", "system": system, "task": task})
            yield send({"kind": "done", "answer": ""})
            return

        q: queue.Queue = queue.Queue()

        def worker():
            try:
                agent = Agent(LLM(cfg), prompt_text(d, "system"), max_steps=steps,
                              verbose=False, on_event=q.put)
                task = _build_task(d, target_desc, file, name)
                answer = agent.run(task)
                SESSIONS.start(sid, domain, task, answer, {"label": target_desc})
                q.put({"kind": "usage", **agent.usage_total})
                q.put({"kind": "done", "answer": answer})
            except Exception as e:               # 任何异常都要让前端看到，不能静默
                q.put({"kind": "error", "text": f"{type(e).__name__}: {e}"})
                q.put({"kind": "done", "answer": ""})

        threading.Thread(target=worker, daemon=True).start()
        while True:
            ev = q.get()
            yield send(ev)
            if ev.get("kind") == "done":
                break

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/ask")
def api_ask(payload: dict) -> StreamingResponse:
    """**追问**：接着上一次分析继续问（SSE 流式，事件格式与 /api/analyze 一致）。

    模型在这轮里**仍然带工具**，所以它可以回去再读题面 / 检索答疑，而不是只凭记忆回答。
    """
    sid = ((payload or {}).get("sid") or "").strip()
    question = ((payload or {}).get("question") or "").strip()
    steps = int((payload or {}).get("steps", 8))
    if not question:
        raise HTTPException(status_code=400, detail="请先写问题")
    sess = SESSIONS.get(sid)
    if not sess:
        raise HTTPException(status_code=400,
                            detail="这个会话已过期或不存在 —— 请先做一次分析，再追问")
    d = _domain_or_400(sess.get("domain", "diansai"))
    cfg = Config()
    if not cfg.has_key:
        raise HTTPException(status_code=400, detail="还没有配置模型 Key，无法追问")

    system = prompt_text(d, "system")
    messages = SESSIONS.messages(sid, system, question)

    def gen():
        def send(obj):
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        yield send({"kind": "question", "text": question})
        q: queue.Queue = queue.Queue()

        def worker():
            try:
                agent = Agent(LLM(cfg), system, max_steps=steps, verbose=False, on_event=q.put)
                answer = agent.run(messages=messages)
                SESSIONS.add_turn(sid, question, answer)
                q.put({"kind": "usage", **agent.usage_total})
                q.put({"kind": "done", "answer": answer})
            except Exception as e:
                q.put({"kind": "error", "text": f"{type(e).__name__}: {e}"})
                q.put({"kind": "done", "answer": ""})

        threading.Thread(target=worker, daemon=True).start()
        while True:
            ev = q.get()
            yield send(ev)
            if ev.get("kind") == "done":
                break

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _build_task(d, target_desc: str, file: str, name: str) -> str:
    """拼出交给模型的任务描述（把年份/文件名说清楚，避免分析错年份）。"""
    lines = [prompt_text(d, "analyze"),
             f"\n\n【本次任务】分析这道题：**{target_desc}**"]
    if file:
        lines.append(f"\n读题时请用 `read_shiti(file=\"{file}\")` 精确读取，"
                     f"**不要**只按题号读（题库里多个年份有同题号，会读错年份）。")
    elif name:
        lines.append(f"\n读题时用 `read_shiti(name=\"{name}\")`；"
                     f"若它提示存在多个年份的同题号，请先向用户确认要哪一年。")
    lines.append("\n报告文件名请带上年份/批次，例如 `2025-国赛-H题-分析报告.md`。")
    return "".join(lines)


@app.post("/api/tune")
async def api_tune(file: UploadFile = File(...),
                   target: str = Form(""),
                   use_llm: str = Form("false"),
                   title: str = Form("")) -> JSONResponse:
    """上传阶跃数据 → 本地算指标 + 出图（确定性，不花额度）；可选再让模型写诊断报告。"""
    raw = await file.read()
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(status_code=400, detail=f"文件超过 {MAX_UPLOAD // 1024 // 1024} MB")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    name = _safe_name(file.filename)
    path = UPLOAD_DIR / name
    path.write_bytes(raw)

    tgt = None
    if str(target).strip():
        try:
            tgt = float(target)
        except ValueError:
            raise HTTPException(status_code=400, detail="目标值必须是数字")

    try:
        metrics_md = call_tool("analyze_step_data",
                               {"path": str(path), "target": tgt, "title": title or None})
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"数据解析/计算失败：{type(e).__name__}: {e}")
    # ★ 工具层把错误当"正常返回"（好让模型自纠），接口层必须自己判断，
    #   否则前端会把错误字符串当指标渲染、自动化脚本也会被 200 骗过
    if is_error(metrics_md):
        raise HTTPException(status_code=400, detail=metrics_md.replace(ERROR_PREFIX, "").strip())

    # 曲线图文件名与 analyze_step_data 内部保持一致
    plot_name = f"{path.stem}-阶跃响应.png"
    plot_path = OUT_DIR / plot_name

    report_md = ""
    cfg = Config()
    if str(use_llm).lower() in ("1", "true", "on") and cfg.has_key:
        d = get_domain("diansai")
        task = (prompt_text(d, "tune")
                + f"\n\n【本次任务】\n被测数据文件：{path}\n目标值：{tgt if tgt is not None else '未给出'}\n\n"
                + "以下是我已经用本地算法算好的**确定性结果**（数字请直接引用，不要重算）：\n\n"
                + metrics_md
                + f"\n\n请按模板写出调参报告，并用 write_report 落盘，文件名用 `{path.stem}-调参报告.md`。")
        try:
            agent = Agent(LLM(cfg), prompt_text(d, "system"), max_steps=8, verbose=False)
            agent.run(task)
            report_md = f"{path.stem}-调参报告.md"
        except Exception as e:
            metrics_md += f"\n\n> ⚠️ 模型诊断失败（本地指标不受影响）：{type(e).__name__}: {e}"

    return JSONResponse({
        "metrics_md": metrics_md,
        "plot": plot_name if plot_path.exists() else None,
        "report_md": report_md,
        "report_docx": (f"{path.stem}-调参报告.docx"
                        if report_md and (OUT_DIR / f"{path.stem}-调参报告.docx").exists() else None),
        "saved_data": name,
    })


@app.get("/api/settings")
def api_settings_get() -> JSONResponse:
    """当前配置状态（Key 只回显打码后的样子，绝不返回明文）。"""
    cfg = Config()
    return JSONResponse({
        "has_key": cfg.has_key,
        "key_source": cfg.key_source(),
        "masked_key": cfg.masked_key(),
        "model": cfg.model,
        "base_url": cfg.base_url,
        "provider": cfg.provider,
        "provider_name": cfg.provider_name,
        "config_file": str(user_env_file()),
        "out_dir": str(OUT_DIR),
        "model_options": MODEL_OPTIONS,
    })


@app.get("/api/settings/providers")
def api_providers() -> JSONResponse:
    """可选的模型供应商目录（任何 OpenAI 兼容接口都能接，含本地 Ollama）。"""
    return JSONResponse({"providers": [{"id": k, **v} for k, v in PROVIDERS.items()],
                         "current": Config().provider})


@app.post("/api/settings/models")
def api_provider_models(payload: dict) -> JSONResponse:
    """按指定供应商（或当前配置）拉一次可用模型列表 —— 不预设模型名，直接问对方。"""
    p = (payload or {}).get("provider") or Config().provider
    if p not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"未知供应商：{p}")
    prof = PROVIDERS[p]
    base = ((payload or {}).get("base_url") or prof.get("base_url") or "").strip()
    if not base:
        raise HTTPException(status_code=400, detail="这个供应商需要你自己填 base_url")
    key = ((payload or {}).get("api_key") or "").strip() or Config(provider=p).api_key
    if prof.get("needs_key", True) and not key:
        raise HTTPException(status_code=400, detail="请先填 Key（本地 Ollama 可随便填 ollama）")
    try:
        import requests as _rq
        r = _rq.get(f"{base.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {key or 'ollama'}"}, timeout=30)
        if r.status_code != 200:
            return JSONResponse({"ok": False, "error": f"HTTP {r.status_code}：{r.text[:200]}"})
        data = r.json()
        models = [m.get("id", "") for m in (data.get("data") or []) if m.get("id")]
        return JSONResponse({"ok": bool(models), "models": sorted(models)[:200],
                             "count": len(models)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"})


@app.post("/api/settings")
def api_settings_post(payload: dict) -> JSONResponse:
    """保存设置到用户配置文件（~/.contest-workbench/.env）。

    字段语义：**不传** = 不改；**传空字符串** = 清空该项。
    打包成 exe 后程序目录可能不可写，所以设置一律存用户目录。
    """
    p = payload or {}
    allowed = {"api_key", "model", "base_url", "provider"}
    unknown = set(p) - allowed
    if unknown:
        raise HTTPException(status_code=400, detail=f"不认识的设置项：{', '.join(sorted(unknown))}")
    if "provider" in p and p["provider"] and p["provider"] not in PROVIDERS:
        raise HTTPException(status_code=400,
                            detail=f"未知供应商 {p['provider']}，可用：{', '.join(PROVIDERS)}")
    key = p.get("api_key")
    prof = PROVIDERS.get(p.get("provider") or Config().provider, {})
    if (key is not None and key.strip() and prof.get("needs_key", True)
            and prof.get("key_env") == "DEEPSEEK_API_KEY"
            and not key.strip().startswith("sk-")):
        # 只对默认供应商做形状检查，其它家前缀各不相同，别乱判
        raise HTTPException(status_code=400, detail="DeepSeek 的 Key 通常以 sk- 开头，请检查")
    try:
        path = save_user_config(api_key=key, model=p.get("model"),
                                base_url=p.get("base_url"), provider=p.get("provider"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置失败：{type(e).__name__}: {e}")
    cfg = Config()
    return JSONResponse({"saved_to": str(path), "has_key": cfg.has_key,
                         "key_source": cfg.key_source(), "masked_key": cfg.masked_key(),
                         "model": cfg.model, "base_url": cfg.base_url,
                         "provider": cfg.provider, "provider_name": cfg.provider_name})


@app.post("/api/settings/test")
def api_settings_test() -> JSONResponse:
    """用当前 Key 真连一次模型列表，验证可用性（会消耗极少网络请求，不花 token）。"""
    cfg = Config()
    if not cfg.has_key:
        return JSONResponse({"ok": False, "error": f"{cfg.provider_name}：还没填 Key"})
    try:
        models = LLM(cfg).list_models()
        ok = bool(models)
        warn = ""
        if ok and cfg.model not in models:
            warn = f"当前模型 {cfg.model} 不在可用列表里，建议改成 {models[0]}"
        return JSONResponse({"ok": ok, "models": models, "current_model": cfg.model,
                             "provider": cfg.provider, "warning": warn})
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}",
                             "provider": cfg.provider})


@app.get("/api/selftest")
def api_selftest() -> JSONResponse:
    """一键自检：把"能不能用"逐项验一遍（打不开/跑不通时先跑它）。

    特别包含 **Word 转换**这一项 —— 打包成 exe 后它最容易坏
    （原来用子进程调转换脚本，冻结后 sys.executable 就是 exe 自己，会把程序再启动一遍）。
    """
    import tempfile
    from ..config import BUNDLE_ROOT, FROZEN
    from ..domains import prompt_text, get_domain

    items: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        items.append({"name": name, "ok": bool(ok), "detail": detail})

    cfg = Config()
    add("API Key 已配置", cfg.has_key, cfg.key_source())

    d = get_domain("diansai")
    try:
        n = len(prompt_text(d, "system"))
        add("提示词可读（随包资源）", n > 200, f"{n} 字符" + ("（打包模式）" if FROZEN else "（源码模式）"))
    except Exception as e:
        add("提示词可读（随包资源）", False, f"{type(e).__name__}: {e}")

    try:
        idx = STATIC_DIR / "index.html"
        add("网页界面可读（随包资源）", idx.exists(), str(idx))
    except Exception as e:
        add("网页界面可读（随包资源）", False, str(e))

    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        probe = OUT_DIR / ".selftest-probe"
        probe.write_text("1", encoding="utf-8")
        probe.unlink()
        add("输出目录可写", True, str(OUT_DIR))
    except Exception as e:
        add("输出目录可写", False, f"{OUT_DIR}：{type(e).__name__}: {e}")

    # Word 转换：真写一份小报告，看 .docx 有没有生成（打包后最易坏的一环）
    try:
        with tempfile.TemporaryDirectory() as td:
            md = Path(td) / "selftest.md"
            docx = Path(td) / "selftest.docx"
            md.write_text("# 自检\n\n| 项目 | 值 |\n|---|---|\n| 中文 | 正常 |\n", encoding="utf-8")
            from ..tools.report import _to_docx
            ok, err = _to_docx(md, docx)
            kb = round(docx.stat().st_size / 1024) if docx.exists() else 0
            add("Word 转换可用（进程内）", ok and kb > 0, f"{kb} KB" if ok else err)
    except Exception as e:
        add("Word 转换可用（进程内）", False, f"{type(e).__name__}: {e}")

    # 示例数据（随包资源）
    try:
        s = BUNDLE_ROOT / "samples" / "step-response-sample.csv"
        add("示例数据可读（随包资源）", s.exists(), f"{s}（{s.stat().st_size} 字节）" if s.exists() else str(s))
    except Exception as e:
        add("示例数据可读（随包资源）", False, str(e))

    # 调参计算（numpy/matplotlib 在冻结环境下是否可用）
    try:
        from ..tools.tuning import analyze
        import numpy as np
        t = np.arange(0, 3, 0.002)
        y = 1 - np.exp(-6 * t)
        m = analyze(t, y, target=1.0)
        add("调参计算可用（numpy）", m.y_ss > 0.9, f"稳态 {m.y_ss:.4f}")
    except Exception as e:
        add("调参计算可用（numpy）", False, f"{type(e).__name__}: {e}")

    # 题库（不随包走，因版权；没有属正常）
    try:
        items_kb = list_shiti_structured()
        add("题库已就绪（可选）", True,
            f"{len(items_kb)} 道题" if items_kb else "还没建题库 —— 用 scripts/fetch_history.py 重建（不影响其余功能）")
    except Exception as e:
        add("题库已就绪（可选）", False, str(e))

    return JSONResponse({"frozen": FROZEN, "python": sys.version.split()[0],
                         "config_file": str(user_env_file()), "out_dir": str(OUT_DIR),
                         "items": items, "all_ok": all(i["ok"] for i in items)})


@app.get("/api/reports")
def api_reports() -> JSONResponse:
    return JSONResponse({"items": _list_report_files()})


@app.get("/api/file/{name}")
def api_file(name: str):
    """下载/预览 out/ 下的产物（md / docx / png）。"""
    safe = _safe_name(name)
    p = OUT_DIR / safe
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(p, filename=safe)


@app.get("/api/file-text/{name}")
def api_file_text(name: str) -> JSONResponse:
    """读取 markdown 文本（前端渲染用）。"""
    safe = _safe_name(name)
    p = OUT_DIR / safe
    if not p.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if p.suffix.lower() not in (".md", ".txt", ".csv"):
        raise HTTPException(status_code=400, detail="只支持文本类文件")
    return JSONResponse({"text": p.read_text(encoding="utf-8", errors="replace")})
