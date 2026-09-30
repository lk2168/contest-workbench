# -*- coding: utf-8 -*-
"""v0.6.0 收尾：打包 → 验证 exe（窗口/供应商接口/追问接口）→ 提交 → 发布。"""
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"D:\work\contest-workbench")
PY = sys.executable
TITLE = "大学生竞赛工作台"
GH = r"D:\tools\gh\bin\gh.exe"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=ROOT, **kw)


def kill():
    subprocess.run(["taskkill", "/f", "/im", "contest-workbench.exe"], capture_output=True)


kill()
time.sleep(2)
print("== 1. 打包 ==")
r = run([PY, str(ROOT / "scripts" / "build_exe.py"), "--clean"])
for l in r.stdout.splitlines():
    if "打包完成" in l or "大小" in l or "模式：" in l:
        print("  " + l.strip())
if r.returncode != 0:
    print(r.stdout[-800:], r.stderr[-800:])
    sys.exit(1)

print("\n== 2. 提交 ==")
run(["git", "add", "-A"])
c = run(["git", "-c", "user.name=Lu Kuo",
         "-c", "user.email=288989345+lk2168@users.noreply.github.com",
         "commit", "-F", ".git-msg-v060.txt"])
print("  " + (c.stdout.strip().splitlines() or ["（无输出）"])[-1])
(ROOT / ".git-msg-v060.txt").unlink(missing_ok=True)
p = run(["git", "push"])
print("  " + (p.stderr.strip().splitlines() or ["pushed"])[-1])

print("\n== 3. 启动 exe 验证 ==")
subprocess.Popen([str(ROOT / "dist" / "contest-workbench.exe")])
time.sleep(28)
user32 = ctypes.windll.user32
found = []


def _cb(hwnd, _):
    if user32.IsWindowVisible(hwnd):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, b, n + 1)
            if b.value == TITLE:
                found.append(b.value)
    return True


user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(_cb), 0)
print("  原生窗口:", found or "❌")

base = None
for port in range(8765, 8790):
    try:
        if requests.get(f"http://127.0.0.1:{port}/api/health", timeout=2).json().get("ok"):
            base = f"http://127.0.0.1:{port}"
            break
    except Exception:
        continue
print("  服务:", base or "❌")
ok = bool(found) and bool(base)
if base:
    pv = requests.get(base + "/api/settings/providers", timeout=20).json()
    print(f"  ★ 供应商 {len(pv['providers'])} 家，当前 {pv['current']}：" +
          ", ".join(p["id"] for p in pv["providers"]))
    s = requests.get(base + "/api/selftest", timeout=120).json()
    print(f"  自检: frozen={s['frozen']} all_ok={s['all_ok']}")
    a = requests.post(base + "/api/ask", json={"sid": "不存在", "question": "在吗"}, timeout=20)
    print(f"  ★ 追问接口（无会话时应给人话 400）: HTTP {a.status_code} · {a.text[:60]}")
    ok = ok and len(pv["providers"]) >= 6 and a.status_code == 400
kill()

print("\n== 4. 发布 v0.6.0 ==")
rel = run([GH, "release", "create", "v0.6.0", "--repo", "lk2168/contest-workbench",
           "--target", "main", "--title", "v0.6.0：追问（多轮对话）+ 支持 8 家模型供应商",
           "--notes-file", "docs/releases/v0.6.0.md",
           "dist/contest-workbench.exe#contest-workbench.exe（Windows 单文件，免装 Python）"])
print("  " + (rel.stdout.strip() or rel.stderr.strip())[:200])

print("\n结论：" + ("全部通过 ✅" if ok else "有问题 ❌"))
sys.exit(0 if ok else 1)
