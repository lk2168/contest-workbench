#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据快照与恢复：升级前 / 每日首启 / 手动，都会把用户数据打包存一份。

为什么要有它
------------
这个项目的用户数据是**攒出来的**：题库、学习记录、用户画像、快捷指令、
帧格式、任务记录…… 一旦被改坏或误删，重来代价很大。
★ 之前真实发生过「板子被擦、靠手动备份救回来」的事 —— 那是**设备**数据，
  用户的**工作区**数据同样该有这层保护。
思路来自同类项目 icpc-workbench（每日首启/升级前/大批量导入前自动建恢复点）。

设计要点
--------
- 快照 = 把 `data/` 打包成 zip 放到 `out/快照/`（`out/` 不进版本库，合适放这些）
- **自动触发**：距上次快照超过 24 小时（每日首启）或**应用版本变了**（升级前）
- **恢复前先自动存一份当下的状态**（叫「恢复前」），所以"恢复"本身也可回退 ✓ 不怕手抖
- 保留最近 N 份，自动清理更老的（避免越攒越大）
- 体积守卫：数据太大就跳过并说明原因，而不是闷头压缩几分钟
- 路径可用环境变量覆盖（测试用）：`CONTEST_SNAPSHOT_SRC` / `CONTEST_SNAPSHOT_DIR`
"""
from __future__ import annotations

import json
import os
import shutil
import time
import zipfile
from pathlib import Path

# 保留多少份（含手动）
保留份数 = 10
# 超过这个体积就不自动快照（手动也提示）
体积上限_MB = 200
# 单文件超过这个大小就跳过（多半是缓存/大素材）
单文件上限_MB = 50

跳过目录 = {"__pycache__", ".pytest_cache", "快照", ".git"}
跳过后缀 = {".pyc", ".pyo", ".tmp", ".log"}


def 源目录() -> Path:
    """要保护的数据目录（默认项目里的 data/）。"""
    p = os.environ.get("CONTEST_SNAPSHOT_SRC")
    if p:
        return Path(p)
    try:
        from .config import REPO_ROOT
        return Path(REPO_ROOT) / "data"
    except Exception:
        return Path(__file__).resolve().parent.parent / "data"


def 快照目录() -> Path:
    p = os.environ.get("CONTEST_SNAPSHOT_DIR")
    if p:
        return Path(p)
    try:
        from .config import OUT_DIR
        return Path(OUT_DIR) / "快照"
    except Exception:
        return Path(__file__).resolve().parent.parent / "out" / "快照"


def _索引文件() -> Path:
    return 快照目录() / "索引.json"


def _读索引() -> list:
    p = _索引文件()
    if not p.exists():
        return []
    try:
        数据 = json.loads(p.read_text(encoding="utf-8"))
        return 数据 if isinstance(数据, list) else []
    except Exception:
        return []


def _写索引(items: list) -> None:
    p = _索引文件()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def 需要每日快照(小时: float = 24.0) -> bool:
    """距上一次成功快照是否超过 `小时`（用于"每日首启"）。"""
    items = [x for x in _读索引() if x.get("ts")]
    if not items:
        return True
    return (time.time() - max(float(x["ts"]) for x in items)) > float(小时) * 3600


def _版本标记文件() -> Path:
    return 源目录() / ".上次运行版本"


def 版本变了吗() -> bool:
    """应用版本与上次记录的是否不同（用于"升级前自动快照"）。"""
    try:
        from . import __version__
        现在 = str(__version__)
    except Exception:
        现在 = "未知"
    f = _版本标记文件()
    try:
        上次 = f.read_text(encoding="utf-8").strip() if f.exists() else ""
    except Exception:
        上次 = ""
    return bool(上次) and 上次 != 现在


def 写版本标记() -> None:
    """把当前版本记下来（升级前快照做完再写，否则下次就检测不到了）。"""
    try:
        from . import __version__
        f = _版本标记文件()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(str(__version__), encoding="utf-8")
    except Exception:
        pass


def _要打包的文件(根: Path) -> tuple:
    """列出要打包的文件 + 总字节数（跳过缓存、超大文件）。"""
    文件, 跳过, 总字节 = [], 0, 0
    for p in sorted(根.rglob("*")):
        if not p.is_file():
            continue
        if any(part in 跳过目录 for part in p.parts):
            continue
        if p.suffix.lower() in 跳过后缀:
            跳过 += 1
            continue
        try:
            sz = p.stat().st_size
        except Exception:
            continue
        if sz > 单文件上限_MB * 1024 * 1024:
            跳过 += 1
            continue
        文件.append(p)
        总字节 += sz
    return 文件, 跳过, 总字节


def 建快照(原因: str = "手动", 保留: int | None = None) -> dict:
    """把数据目录打包成一份快照。返回 {ok, 名字, 路径, 文件数, 大小MB, 跳过, 消息}。

    `ok=False` 时 `消息` 里是中文原因（数据目录不存在 / 太大 / 没东西可存）。
    """
    根 = 源目录()
    结果 = {"ok": False, "名字": "", "路径": "", "文件数": 0, "大小MB": 0.0, "跳过": 0, "消息": ""}
    if not 根.exists():
        结果["消息"] = f"[错误] 没有数据目录可备份：{根}"
        return 结果
    文件, 跳过, 总字节 = _要打包的文件(根)
    结果["跳过"] = 跳过
    if not 文件:
        结果["消息"] = f"[错误] 数据目录里没有可备份的文件：{根}"
        return 结果
    if 总字节 > 体积上限_MB * 1024 * 1024:
        结果["消息"] = (f"[错误] 数据太大（{总字节 / 1048576:.0f} MB > {体积上限_MB} MB），"
                        f"先清理一下再备份")
        return 结果

    try:
        名 = f"快照-{time.strftime('%Y%m%d-%H%M%S')}-{原因}.zip"
        目标 = 快照目录() / 名
        目标.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(目标, "w", zipfile.ZIP_DEFLATED) as z:
            for p in 文件:
                z.write(p, p.relative_to(根).as_posix())
    except Exception as e:
        结果["消息"] = f"[错误] 打包失败：{type(e).__name__}: {e}"
        return 结果

    items = _读索引()
    items.append({"名字": 名, "ts": round(time.time(), 3),
                  "时间": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "原因": str(原因), "文件数": len(文件),
                  "大小MB": round(目标.stat().st_size / 1048576, 3),
                  "跳过": 跳过})
    _写索引(items)
    清理旧的(保留 if 保留 is not None else 保留份数)
    结果.update({"ok": True, "名字": 名, "路径": str(目标), "文件数": len(文件),
                 "大小MB": round(目标.stat().st_size / 1048576, 3),
                 "消息": f"已备份 {len(文件)} 个文件（{目标.stat().st_size / 1024:.0f} KB）"})
    return 结果


def 列表() -> list:
    """所有快照，按时间倒序（带体积、文件数、原因）。"""
    items = _读索引()
    存在 = []
    for it in items:
        if (快照目录() / str(it.get("名字"))).exists():
            存在.append(it)
    if len(存在) != len(items):            # 索引里有但文件没了 → 顺手清掉索引
        _写索引(存在)
    return sorted(存在, key=lambda x: -float(x.get("ts") or 0))


def 清理旧的(保留: int = 保留份数) -> int:
    """只保留最近 `保留` 份，删掉更老的（返回删了几份）。"""
    items = sorted(_读索引(), key=lambda x: -float(x.get("ts") or 0))
    if len(items) <= 保留:
        return 0
    留, 删 = items[:保留], items[保留:]
    for it in 删:
        try:
            (快照目录() / str(it.get("名字"))).unlink(missing_ok=True)
        except Exception:
            pass
    _写索引(留)
    return len(删)


def 恢复(名字: str) -> str:
    """把某份快照解回去。★ 恢复前会**先自动存一份当下的状态**，所以恢复也能回退。"""
    src = 快照目录() / str(名字)
    if not src.exists():
        return f"[错误] 找不到这份快照：{名字}"
    根 = 源目录()
    根.mkdir(parents=True, exist_ok=True)

    # ① 先给"现在"存一份（叫「恢复前」），这样恢复错了还能救回来
    before = 建快照("恢复前", 保留=保留份数)
    if not before.get("ok"):
        # 当下没东西可备份不算错（可能是空目录），继续
        pass

    # ② 解压覆盖
    try:
        with zipfile.ZipFile(src) as z:
            z.extractall(根)
    except Exception as e:
        return f"[错误] 恢复失败：{type(e).__name__}: {e}"
    n = len([x for x in 根.rglob("*") if x.is_file()])
    return (f"已从「{名字}」恢复（当前数据 {n} 个文件）"
            + (f"；恢复前的状态也存了一份：{before['名字']}" if before.get("ok") else ""))


def 启动时检查() -> dict:
    """应用启动时调一次：需要就自动快照（升级前 / 每日首启）。返回做了什么。"""
    try:
        升级 = 版本变了吗()
        每日 = 需要每日快照()
        if 升级 or 每日:
            原因 = "升级前" if 升级 else "每日"
            r = 建快照(原因)
            if r.get("ok"):
                写版本标记()
            return {"做了": bool(r.get("ok")), "原因": 原因, "消息": r.get("消息", ""),
                    "名字": r.get("名字", "")}
        写版本标记()
        return {"做了": False, "原因": "", "消息": "距离上次快照还不到 24 小时，且版本没变"}
    except Exception as e:
        系统 = f"{type(e).__name__}: {e}"
        try:
            from . import journal
            journal.记录(journal.类型_快照, "启动时自动快照失败", 结果="失败", 错误=系统)
        except Exception:
            pass
        return {"做了": False, "原因": "", "消息": f"[错误] {系统}"}


def 快照总览() -> dict:
    """给界面用：几份快照、占多少、上次什么时候。"""
    items = 列表()
    return {"份数": len(items), "总大小MB": round(sum(float(x.get("大小MB") or 0)
                                                for x in items), 2),
            "上次": items[0]["时间"] if items else "",
            "保留份数": 保留份数,
            "目录": str(快照目录()),
            "列表": [{"名字": x.get("名字"), "时间": x.get("时间"), "原因": x.get("原因"),
                      "大小MB": x.get("大小MB"), "文件数": x.get("文件数")} for x in items]}
