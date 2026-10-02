#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据安全测试：任务记录（可观测性）+ 数据快照与恢复。

这两样都是"平时看不出用处、出事时救命"的功能，所以测试要覆盖**真实往返**：
把数据改坏 → 恢复 → 逐字节比对内容是否回来了。

用法：python tests/test_data_safety.py   （退出码 0 = 通过）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'✅' if cond else '❌'} {name}" + (f"  —— {detail}" if detail and not cond else ""))


# 全程用临时目录，别碰用户的真实数据
临时 = Path(tempfile.mkdtemp(prefix="数据安全测试-"))
数据目录 = 临时 / "data"
快照目录_ = 临时 / "快照"
数据目录.mkdir(parents=True, exist_ok=True)
os.environ["CONTEST_SNAPSHOT_SRC"] = str(数据目录)
os.environ["CONTEST_SNAPSHOT_DIR"] = str(快照目录_)
os.environ["CONTEST_JOURNAL_FILE"] = str(临时 / "任务记录.jsonl")

from contest_workbench import journal, snapshot                            # noqa: E402


def main() -> int:
    print("== ① 任务记录：写入与读回 ==")
    journal.记录(journal.类型_烧录, "烧录 atk_f407.hex", 结果="成功", 耗时=19.8,
                 详情={"芯片": "0x0413", "字节": 7800})
    journal.记录(journal.类型_扫描, "扫描 KP", 结果="失败", 耗时=3.2,
                 错误="could not open port COM3: PermissionError(13, '拒绝访问')")
    journal.记录(journal.类型_采集, "阶跃响应采集")
    items = journal.最近(10)
    check("写入 3 条能读回 3 条", len(items) == 3, str(len(items)))
    check("★ 最近的在最前面（倒序）", items[0]["摘要"] == "阶跃响应采集", items[0]["摘要"])
    check("详情（字典）原样保存", items[-1]["详情"] == {"芯片": "0x0413", "字节": 7800},
          str(items[-1]["详情"]))
    check("耗时被记下来", items[-1]["耗时秒"] == 19.8, str(items[-1]["耗时秒"]))
    check("时间戳可读（年月日时分秒）", len(str(items[0]["时间"])) >= 16, str(items[0]["时间"]))
    check("按类型过滤", len(journal.最近(10, 类型=journal.类型_扫描)) == 1)

    print("\n== ② 错误归因（一眼看出是偶发还是每次都这样）==")
    用例 = [("could not open port COM3: PermissionError(13, '拒绝访问')", "端口占用"),
            ("读超时：期望 1 字节，只收到 0 字节", "超时"),
            ("芯片回了 NACK（命令被拒绝）", "设备无响应"),
            ("只收到 3 个数据点，至少需要 8 个", "数据不足"),
            ("找不到固件文件：D:/x.hex", "文件问题"),
            ("[错误] 值不是数字：'abc'", "参数不合法"),
            ("某种没见过的怪错误", "其它"),
            ("", "")]
    for 文本, 期望 in 用例:
        got = journal.归因(文本)
        check(f"归因「{文本[:22] or '(空)'}」→ {期望 or '(空)'}", got == 期望, str(got))

    print("\n== ③ 统计与诊断导出 ==")
    st = journal.统计(7)
    check("统计里包含所有类型", {"烧录", "参数扫描", "采集"} <= set(st["各类"]), str(list(st["各类"])))
    check("烧录成功率 100%", st["各类"]["烧录"]["成功"] == 1 and st["各类"]["烧录"]["失败"] == 0)
    check("★ 失败会被归类（扫描那次是端口占用）",
          st["各类"]["参数扫描"]["错误类别"].get("端口占用") == 1,
          str(st["各类"]["参数扫描"]))
    诊断 = Path(journal.导出诊断(临时 / "诊断.txt"))
    check("诊断文件生成了", 诊断.exists() and 诊断.stat().st_size > 100)
    文本 = 诊断.read_text(encoding="utf-8")
    check("诊断是中文可读的", "运行诊断" in 文本 and "最近 30 天统计" in 文本)
    check("诊断里有明细与错误原因", "烧录 atk_f407.hex" in 文本 and "端口占用" in 文本)

    print("\n== ④ 记录模块必须「绝不拖累主流程」==")
    with (临时 / "任务记录.jsonl").open("a", encoding="utf-8") as f:
        f.write("这一行是坏的，不是 JSON\n")           # 故意插一行坏数据
    check("★ 坏行被跳过，不影响其它记录", len(journal.最近(50)) == 3, str(len(journal.最近(50))))
    旧 = os.environ["CONTEST_JOURNAL_FILE"]
    os.environ["CONTEST_JOURNAL_FILE"] = str(临时 / "不存在的目录" / "x" / "记录.jsonl")
    结果 = journal.记录("系统", "这条写不进去")        # 目录建不起来也不会抛
    check("★ 写不进去也不抛异常（返回记录本身）", isinstance(结果, dict) and 结果["摘要"] == "这条写不进去")
    os.environ["CONTEST_JOURNAL_FILE"] = 旧

    print("\n== ⑤ 快照：真实往返（改了 → 恢复 → 内容一模一样）==")
    (数据目录 / "题库").mkdir(exist_ok=True)
    (数据目录 / "题库" / "真题.md").write_text("第一版内容：小车+钢球+摆杆\n", encoding="utf-8")
    (数据目录 / "用户画像.md").write_text("大三 · 自动化\n", encoding="utf-8")
    (数据目录 / "__pycache__").mkdir(exist_ok=True)
    (数据目录 / "__pycache__" / "x.pyc").write_bytes(b"\x00\x01\x02")     # 应该被跳过
    r = snapshot.建快照("手动")
    check("建快照成功", r["ok"] is True, r.get("消息", ""))
    check("打包了 2 个文件（跳过了 __pycache__/.pyc）", r["文件数"] == 2, str(r["文件数"]))
    check("zip 文件真的存在", Path(r["路径"]).exists() and zipfile.is_zipfile(r["路径"]))
    with zipfile.ZipFile(r["路径"]) as z:
        名字 = z.namelist()
    check("zip 里路径是相对路径（不含盘符/绝对路径）",
          all(not n.startswith("/") and ":" not in n for n in 名字), str(名字))
    check("zip 里有题库/真题.md", any("真题.md" in n for n in 名字), str(名字))

    # 改坏 + 删掉数据
    (数据目录 / "题库" / "真题.md").write_text("被我改坏了\n", encoding="utf-8")
    (数据目录 / "用户画像.md").unlink()
    check("数据确实坏了（准备恢复）", (数据目录 / "题库" / "真题.md").read_text(
        encoding="utf-8") == "被我改坏了\n")

    消息 = snapshot.恢复(r["名字"])
    check("恢复执行成功", "已从" in 消息, 消息[:80])
    check("★ 被改坏的文件恢复了原内容",
          (数据目录 / "题库" / "真题.md").read_text(encoding="utf-8") == "第一版内容：小车+钢球+摆杆\n",
          (数据目录 / "题库" / "真题.md").read_text(encoding="utf-8")[:40])
    check("★ 被删掉的文件也回来了", (数据目录 / "用户画像.md").exists())
    check("★ 恢复前自动存了一份「恢复前」快照（恢复也能回退）",
          any(x.get("原因") == "恢复前" for x in snapshot.列表()),
          str([x.get("原因") for x in snapshot.列表()]))
    check("恢复不存在的快照 → 人话错误",
          snapshot.恢复("根本没有这份.zip").startswith("[错误]"))

    print("\n== ⑥ 触发条件：升级前 / 每日首启 ==")
    check("刚快照过 → 不需要每日快照", snapshot.需要每日快照(24) is False)
    check("把阈值设成 0 小时 → 又需要了", snapshot.需要每日快照(0.0) is True)
    check("没有版本标记时不算版本变化", snapshot.版本变了吗() is False)
    snapshot.写版本标记()
    check("写好版本标记后，版本没变 → False", snapshot.版本变了吗() is False)
    (数据目录 / ".上次运行版本").write_text("0.0.1-旧版本", encoding="utf-8")
    check("★ 版本号变了 → 检测到（升级前该快照）", snapshot.版本变了吗() is True)
    做 = snapshot.启动时检查()
    check("★ 版本变化时启动会自动快照", 做["做了"] is True and 做["原因"] == "升级前", str(做))
    check("快照后版本标记被更新（不会每次启动都备份）", snapshot.版本变了吗() is False)

    print("\n== ⑦ 保留份数与清理 ==")
    for i in range(4):
        time.sleep(0.01)
        snapshot.建快照(f"测试{i}", 保留=3)
    check("★ 只保留最近 3 份", len(snapshot.列表()) <= 3, str(len(snapshot.列表())))
    check("索引里的文件都真实存在",
          all((snapshot.快照目录() / x["名字"]).exists() for x in snapshot.列表()))
    总览 = snapshot.快照总览()
    check("总览字段齐全", {"份数", "总大小MB", "上次", "列表"} <= set(总览), str(list(总览)))
    check("总览的份数与列表一致", 总览["份数"] == len(snapshot.列表()))

    print("\n== ⑧ 各种异常路径都要给人话 ==")
    空目录 = 临时 / "空数据"
    空目录.mkdir(exist_ok=True)
    旧源 = os.environ["CONTEST_SNAPSHOT_SRC"]
    os.environ["CONTEST_SNAPSHOT_SRC"] = str(空目录)
    r2 = snapshot.建快照("手动")
    check("空数据目录 → 失败并说清", r2["ok"] is False and "[错误]" in r2["消息"], r2["消息"][:60])
    os.environ["CONTEST_SNAPSHOT_SRC"] = str(临时 / "根本不存在")
    r3 = snapshot.建快照("手动")
    check("数据目录不存在 → 失败并说清", r3["ok"] is False and "没有数据目录" in r3["消息"],
          r3["消息"][:60])
    os.environ["CONTEST_SNAPSHOT_SRC"] = str(数据目录)
    旧上限 = snapshot.体积上限_MB
    snapshot.体积上限_MB = 0
    r4 = snapshot.建快照("手动")
    check("★ 体积超上限 → 拒绝并说明（不闷头压缩）",
          r4["ok"] is False and "太大" in r4["消息"], r4["消息"][:70])
    snapshot.体积上限_MB = 旧上限
    check("启动检查异常也不抛（返回消息）", isinstance(snapshot.启动时检查(), dict))

    print("\n== ⑨ 快照与任务记录联动 ==")
    journal.记录(journal.类型_快照, "建了一份快照", 详情={"名字": r["名字"]})
    check("快照动作也能进任务记录",
          any(x["类型"] == journal.类型_快照 for x in journal.最近(20)))

    print("\n== ⑩ 默认路径也得能用（隔离测试最容易掩盖这类 bug）==")
    # ★ 上面的用例都设了环境变量，等于绕开了 config —— 真实运行时数据写哪儿？
    #   这里特意把它们去掉，只**解析路径**（不写文件），确认默认路径是对的。
    暂存 = {k: os.environ.pop(k, None) for k in
            ("CONTEST_JOURNAL_FILE", "CONTEST_SNAPSHOT_SRC", "CONTEST_SNAPSHOT_DIR")}
    try:
        from contest_workbench import config
        记录 = journal.记录文件()
        check("★ 默认记录文件落在项目的 data/ 下",
              str(记录).endswith("任务记录.jsonl") and 记录.parent.name == "data", str(记录))
        check("★ 默认数据目录是项目里的 data/（config 常量名没写错）",
              记录.parent == Path(config.REPO_ROOT) / "data", str(记录.parent))
        源 = snapshot.源目录()
        check("★ 默认数据源目录 = 项目 data/", 源 == Path(config.REPO_ROOT) / "data", str(源))
        快 = snapshot.快照目录()
        check("★ 默认快照目录在 out/ 快照 下",
              快.name == "快照" and 快.parent.name == "out", str(快))
    finally:
        for k, v in 暂存.items():
            if v is not None:
                os.environ[k] = v

    print("\n== ⑪ 网页接口（备份/恢复/记录/导出）==")
    from fastapi.testclient import TestClient
    from contest_workbench.web import app as webapp
    client = TestClient(webapp.app)

    d = client.get("/api/snapshots").json()
    check("快照总览接口字段齐全", {"份数", "总大小MB", "上次", "列表", "目录"} <= set(d), str(list(d)))
    check("列表里每项都有名字/时间/原因",
          all({"名字", "时间", "原因", "大小MB"} <= set(x) for x in d["列表"]))

    r = client.post("/api/snapshots/create", json={"reason": "接口测试"}).json()
    check("接口能建快照", r["ok"] is True and r["名字"], str(r)[:80])
    check("返回里带了最新总览", r["总览"]["份数"] >= 1)

    r = client.post("/api/snapshots/restore", json={"name": ""}).json()
    check("恢复不指定名字 → 拒绝", r["ok"] is False and "指定" in r["message"], r["message"][:50])
    r = client.post("/api/snapshots/restore", json={"name": "x.zip"}).json()
    check("★ 没确认就恢复 → 拒绝（覆盖数据要有确认）",
          r["ok"] is False and "确认" in r["message"], r["message"][:60])
    r = client.post("/api/snapshots/restore", json={"name": "x.zip", "确认": True}).json()
    check("确认了但快照不存在 → 人话错误", r["ok"] is False and "找不到" in r["message"],
          r["message"][:60])

    d = client.get("/api/journal?n=5").json()
    check("任务记录接口字段齐全", {"items", "统计", "文件", "总数"} <= set(d), str(list(d)))
    check("能按类型过滤", client.get("/api/journal?n=5&type=快照").status_code == 200)

    r = client.post("/api/journal/export").json()
    check("导出诊断接口可用", r["ok"] is True and Path(r["path"]).exists(), str(r)[:90])
    check("诊断文件名是中文", "诊断" in Path(r["path"]).name, Path(r["path"]).name)

    print("\n" + "=" * 52)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
        return 1
    print("全部通过 ✅（任务记录 + 快照恢复，都是真往返验证）")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        import shutil
        shutil.rmtree(临时, ignore_errors=True)
        for k in ("CONTEST_SNAPSHOT_SRC", "CONTEST_SNAPSHOT_DIR", "CONTEST_JOURNAL_FILE"):
            os.environ.pop(k, None)
    sys.exit(code)
