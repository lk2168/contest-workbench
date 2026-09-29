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
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from ..config import OUT_DIR, REPO_ROOT, Config
from ..domains import DOMAINS, get_domain, list_domains, prompt_text
from ..llm import LLM
from ..loop import Agent
from ..tools import ERROR_PREFIX, TOOL_SCHEMAS, call_tool, is_error
from ..tools.shiti import list_shiti

# ── 平台自我介绍（也是首页"这个项目是什么"的事实来源，改这里就够）───────────
PLATFORM = {
    "name": "竞赛 Agent 平台",
    "tagline": "把竞赛赛题变成能照着干的作战方案 —— 第一个给不懂 AI 的大学生用的开源 Agent",
    "repo": "https://github.com/lk2168/diansai-agent",
}

MAX_UPLOAD = 20 * 1024 * 1024          # 单个上传文件上限 20 MB
UPLOAD_DIR = OUT_DIR / "uploads"
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title=PLATFORM["name"], version="0.3.0")


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
    return HTMLResponse(html)


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
    return JSONResponse({"table_md": list_domains(),
                         "domains": [{"id": d.id, "name": d.name, "implemented": d.implemented,
                                      "note": d.note} for d in DOMAINS.values()]})


@app.get("/api/shiti")
def api_shiti(domain: str = "diansai") -> JSONResponse:
    import os
    os.environ["DIANSAI_DOMAIN"] = domain
    _domain_or_400(domain)
    return JSONResponse({"text": list_shiti()})


@app.post("/api/analyze")
def api_analyze(payload: dict) -> StreamingResponse:
    """分析赛题：SSE 流式返回 Agent 的每一步。"""
    domain = (payload or {}).get("domain", "diansai")
    name = ((payload or {}).get("name") or "").strip()
    use_llm = bool((payload or {}).get("use_llm", True))
    steps = int((payload or {}).get("steps", 12))
    d = _domain_or_400(domain)
    cfg = Config()
    if not name:
        raise HTTPException(status_code=400, detail="请填题号或题目关键词")

    def gen():
        def send(obj):
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        if not use_llm or not cfg.has_key:
            why = "演示模式（未调用模型）" if use_llm is False else "未找到 API Key"
            yield send({"kind": "log", "text": f"⚠️ {why}：本次只展示将要发给模型的提示词。"})
            system = prompt_text(d, "system")
            task = prompt_text(d, "analyze") + f"\n\n【本次任务】分析「{name}」这道题。"
            yield send({"kind": "prompt", "system": system, "task": task})
            yield send({"kind": "done", "answer": ""})
            return

        q: queue.Queue = queue.Queue()

        def worker():
            try:
                agent = Agent(LLM(cfg), prompt_text(d, "system"), max_steps=steps,
                              verbose=False, on_event=q.put)
                answer = agent.run(prompt_text(d, "analyze") + f"\n\n【本次任务】分析「{name}」这道题。")
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
