#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""串口助手 **网页接口** 的离线测试（用 feed 注入 + 假后端，不需要真实硬件）。

覆盖：端口列表 / 打开失败的人话错误 / 注入 → 状态 → 一键调参 / 未打开就发送 /
记录 CSV 的开关 / SSE 能不能推事件 / 合理性守卫的拦截数是否暴露给前端。

用法：python tests/test_serial_api.py     （退出码 0 = 通过）
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["CONTEST_CONFIG_DIR"] = tempfile.mkdtemp(prefix="cw-serialapi-")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi.testclient import TestClient                                     # noqa: E402
import contest_workbench.web.app as webapp                                    # noqa: E402
from contest_workbench.serial_assistant import SerialAssistant                # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


def step_series(n: int = 60, dt: float = 0.04, zeta: float = 0.5, wn: float = 2.0) -> str:
    wd = wn * math.sqrt(1 - zeta ** 2)
    out = []
    for i in range(n):
        t = i * dt
        y = 1 - math.exp(-zeta * wn * t) * (math.cos(wd * t) + (zeta * wn / wd) * math.sin(wd * t))
        out.append(f"{t:.3f},{y:.5f}")
    return "\n".join(out) + "\n"


def main() -> int:
    client = TestClient(webapp.app)
    # ★ 每个用例开始前把单例重置（同一个进程里跑多组，状态会串）
    webapp._SERIAL = SerialAssistant()

    print("== ① 端口列表 ==")
    d = client.get("/api/serial/ports").json()
    check("返回 available/ports/opened 三个字段",
          {"available", "ports", "opened"} <= set(d), str(list(d)))
    check("当前未打开", d["opened"] is False)

    print("\n== ② 打开失败要给人话错误 ==")
    r = client.post("/api/serial/open", json={"port": "COM99"}).json()
    check("不存在的端口 → ok=False 且提示可操作",
          r["ok"] is False and ("不存在" in r["message"] or "没插好" in r["message"]), r["message"][:80])
    r = client.post("/api/serial/open", json={"port": "COM3", "baudrate": "abc"}).json()
    check("波特率不合法 → 人话错误", r["ok"] is False and "波特率" in r["message"], r["message"][:60])

    print("\n== ③ 未打开就发送 ==")
    r = client.post("/api/serial/send", json={"text": "hello"}).json()
    check("未打开 → ok=False 且说明原因", r["ok"] is False and "打开" in r["message"], r["message"][:60])

    print("\n== ④ 注入数据 → 状态 → 一键调参 ==")
    client.post("/api/serial/feed", json={"text": "0,0\n0.04,0.5\n"})
    st = client.get("/api/serial/status").json()
    check("状态里有 points/total/bad_lines/outliers（前端要显示）",
          {"points", "total", "bad_lines", "outliers"} <= set(st), str(list(st))[:120])
    check("注入了 2 个点", st["total"] == 2, f"total={st['total']}")

    r = client.post("/api/serial/analyze", json={"target": 1.0})
    check("点太少 → 400 且提示怎么办",
          r.status_code == 400 and "至少" in r.text, r.text[:100])

    webapp._SERIAL = SerialAssistant()
    client.post("/api/serial/feed", json={"text": step_series(60)})
    st = client.get("/api/serial/status").json()
    check("注入 60 点阶跃后点数正确", st["total"] == 60, f"total={st['total']}")
    r = client.post("/api/serial/analyze", json={"target": 1.0})
    check("一键调参 → 200 且报告含指标与建议",
          r.status_code == 200 and "超调量" in r.json()["report"] and "建议" in r.json()["report"])
    if r.status_code == 200:
        rep = r.json()["report"]
        check("报告里两个超调口径都在", "相对稳态值" in rep and "相对目标值" in rep)
    r = client.post("/api/serial/analyze", json={"target": "不是数字"})
    check("目标值不是数字 → 400", r.status_code == 400, str(r.status_code))

    print("\n== ⑤ 合理性守卫在接口层可见 ==")
    webapp._SERIAL = SerialAssistant()
    client.post("/api/serial/feed", json={"text": step_series(60)})
    client.post("/api/serial/feed", json={"text": "5.000,11.37\n"})     # 假点
    st = client.get("/api/serial/status").json()
    check("★ 假点被拦下且拦截数暴露给前端",
          st["outliers"] == 1 and st["total"] == 60,
          f"outliers={st['outliers']} total={st['total']}")

    print("\n== ⑥ 记录 CSV ==")
    r = client.post("/api/serial/record", json={"start": True}).json()
    check("开始记录 → ok 且返回路径", r["ok"] and ("csv" in str(r["message"])), str(r["message"])[:90])
    rec = client.get("/api/serial/status").json()["recording"]
    check("状态里 recording = CSV 路径（非空即表示正在记录；前端据此显示「记录中」）",
          isinstance(rec, str) and rec.endswith(".csv"), repr(rec))
    r = client.post("/api/serial/record", json={"start": False}).json()
    check("停止记录 → 返回存到哪了（文件路径）",
          r["ok"] and str(r["message"]).endswith(".csv"), str(r["message"])[:90])
    check("停止后 recording 变回空串", client.get("/api/serial/status").json()["recording"] == "")

    print("\n== ⑦ SSE 能推事件 ==")
    client.post("/api/serial/feed", json={"text": "0.1,1.0\n0.2,2.0\n"})
    got = 0
    try:
        with client.stream("GET", "/api/serial/stream?limit=2") as resp:   # ★ 有限流，否则测试会一直等
            for line in resp.iter_lines():
                if line and line.startswith("data: "):
                    ev = json.loads(line[6:])
                    got += 1
                    if got >= 2:
                        break
    except Exception as e:
        check("SSE 读取异常", False, f"{type(e).__name__}: {e}")
    check("SSE 能连续推出事件包", got >= 2, f"只收到 {got} 个")

    print("\n== ⑧ 关闭 ==")
    r = client.post("/api/serial/close").json()
    check("关闭串口 → ok", r["ok"] is True, str(r)[:60])
    check("关闭后 status.is_open=False", client.get("/api/serial/status").json()["is_open"] is False)

    print("\n" + "=" * 52)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("全部通过 ✅（离线，用 feed 注入，不需要真实硬件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
