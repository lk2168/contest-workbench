#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""电赛串口助手的命令行入口（装完就能用，不必开网页）。

    diansai-serial ports                      列出可用串口
    diansai-serial monitor -p COM3            看串口输出
    diansai-serial analyze 数据.csv            算阶跃指标（超调/上升/调节时间）
    diansai-serial sweep -p COM3 --values 0.5:2.0:0.25 --template "KP={value}"
                                              自动扫参数并推荐一个值
    diansai-serial flash -p COM3 固件.hex      串口 ISP 烧录（不用仿真器）

设计上和项目里其它模块一样：**能用确定性算的，不交给模型猜**；
依赖分层 —— 核心只依赖 pyserial，指标要 numpy，画图要 matplotlib，
缺哪个会明确提示你装哪个（不会抛一堆看不懂的栈）。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 让 `python -m contest_workbench.serial_cli` 和安装后的入口都能用
if __package__ in (None, ""):                                  # 直接跑这个文件时
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .serial_assistant import SerialAssistant, list_ports, serial_available   # noqa: E402


def _out(msg: str = "") -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _需要(模块: str, 说明: str, 安装提示: str) -> bool:
    """少了可选依赖时给一句人话，而不是让用户看 ImportError 栈。"""
    try:
        __import__(模块)
        return True
    except ImportError:
        _err(f"[错误] 这个功能需要 {说明}，但没装。")
        _err(f"       装它：{安装提示}")
        return False


# ── 子命令：ports ──────────────────────────────────────────────────────
def cmd_ports(args) -> int:
    if not serial_available():
        _err("[错误] 没装 pyserial（串口库）。装它：pip install pyserial")
        return 1
    ports = list_ports()
    if not ports:
        _out("没有检测到串口。检查：① 数据线是否插好（有些线只能充电）② 驱动是否装了"
             "（CH340/CP2102/FT232 都要驱动）")
        return 1
    _out(f"检测到 {len(ports)} 个串口：")
    for p in ports:
        dev = p.get("device") or p.get("name") or "?"
        desc = p.get("description") or ""
        hwid = p.get("hwid") or ""
        line = f"  {dev}"
        if desc and desc != "n/a":
            line += f"  {desc}"
        if hwid:
            line += f"  [{hwid}]"
        _out(line)
    return 0


# ── 子命令：monitor ────────────────────────────────────────────────────
def cmd_monitor(args) -> int:
    a = SerialAssistant()
    msg = a.open(args.port, args.baud)
    if msg.startswith("[错误]"):
        _err(msg)
        return 1
    _out(msg)
    # 打开后默认给 DTR/RTS 高电平（本机实测：只有 dtr=True,rts=True 才运行用户程序）
    try:
        a.set_lines(dtr=True, rts=True)
    except Exception:
        pass
    _out(f"（{args.seconds} 秒后自动退出，Ctrl+C 也可以）")
    t0 = time.time()
    last_send = 0.0
    try:
        while time.time() - t0 < args.seconds:
            if args.send and (time.time() - last_send) >= args.every:
                a.send(args.send, hex_mode=args.hex_mode)
                last_send = time.time()
                _out(f"→ {args.send}")
            time.sleep(0.1)
            for e in a.read_events(200):
                if e.get("dir") == "tx":
                    continue
                text = str(e.get("text", ""))
                if text:
                    _out(text.rstrip("\n"))
    except KeyboardInterrupt:
        _out("\n（已中断）")
    finally:
        a.close()
    return 0


# ── 子命令：analyze ────────────────────────────────────────────────────
def cmd_analyze(args) -> int:
    if not _需要("numpy", "numpy（算指标用）", "pip install diansai-serial[metrics]"):
        return 1
    from .tools.tuning import analyze_step_data, read_data, analyze

    path = Path(args.file)
    if not path.exists():
        _err(f"[错误] 找不到文件：{path}")
        return 1
    if args.json:
        t, y = read_data(path)
        m = analyze(t, y, target=args.target)
        import json
        _out(json.dumps({
            "数据点": m.n, "采样间隔": m.dt, "稳态值": m.y_ss,
            "超调量_pct": m.overshoot_pct, "上升时间": m.rise_time,
            "调节时间_pm2pct": m.settle_time_2, "稳态误差": m.steady_error,
            "振荡次数": m.oscillations, "已稳定": m.settled,
        }, ensure_ascii=False, indent=2))
        return 0
    _out(analyze_step_data(str(path), target=args.target))
    return 0


# ── 子命令：sweep ──────────────────────────────────────────────────────
def cmd_sweep(args) -> int:
    if not _需要("numpy", "numpy（算指标用）", "pip install diansai-serial[metrics]"):
        return 1
    from .sweep import AssistantLink, SweepError, SweepRunner, SweepSpec, format_command, parse_values

    try:
        values = parse_values(args.values)
        format_command(args.template, values[0], args.hex_mode)
    except SweepError as e:
        _err(str(e))
        return 1

    a = SerialAssistant()
    msg = a.open(args.port, args.baud)
    if msg.startswith("[错误]"):
        _err(msg)
        return 1
    try:
        a.set_lines(dtr=True, rts=True)
    except Exception:
        pass

    spec = SweepSpec(values=values, set_template=args.template, hex_mode=args.hex_mode,
                     trigger=args.trigger, settle_s=args.settle, collect_s=args.collect,
                     target=args.target, repeats=args.repeats)
    link = AssistantLink(a)
    link.clear()
    _out(f"开始扫描 {len(values)} 个值，每个约 {spec.settle_s + spec.collect_s:.1f} 秒"
         f"（Ctrl+C 可随时停止）")
    runner = SweepRunner(link, log=_out)
    try:
        res = runner.run(spec)
    except KeyboardInterrupt:
        _err("\n（已中断）")
        return 1
    finally:
        a.close()

    _out("")
    _out(res.table_md())
    _out("")
    _out(res.advice)
    return 0 if res.ok_rows() else 1


# ── 子命令：flash ──────────────────────────────────────────────────────
def cmd_flash(args) -> int:
    from .stm32_isp import flash_via_assistant

    hexfile = Path(args.hex)
    if not hexfile.exists():
        _err(f"[错误] 找不到固件文件：{hexfile}")
        return 1
    a = SerialAssistant()
    msg = a.open(args.port, args.baud)
    if msg.startswith("[错误]"):
        _err(msg)
        return 1
    _out(f"准备烧录：{hexfile.name}")
    try:
        a.pause_reader()
        rep = flash_via_assistant(a, hexfile, verify=not args.no_verify, backup=not args.no_backup,
                                  run=not args.no_run, boot_preset=args.boot_preset,
                                  backup_dir=Path(args.backup_dir), log=_out)
        _out("")
        _out(rep.text())
        return 0 if rep.ok else 1
    except KeyboardInterrupt:
        _err("\n（已中断）")
        return 1
    finally:
        try:
            a.resume_reader()
        except Exception:
            pass
        a.close()


# ── 参数解析 ───────────────────────────────────────────────────────────
class 中文解析器(argparse.ArgumentParser):
    """argparse 默认那行 `-h, --help  show this help message and exit` 是英文。
    用户能看到的地方一律用中文，所以这里自己加一个 -h。"""

    def __init__(self, *args, **kw):
        kw["add_help"] = False
        super().__init__(*args, **kw)
        self.add_argument("-h", "--help", action="help", help="显示这段帮助并退出")

    # argparse 的 "usage:" / "options:" 之类标题也是英文，一并换掉
    def format_usage(self):
        return super().format_usage().replace("usage:", "用法:", 1)

    def format_help(self):
        return (super().format_help()
                .replace("usage:", "用法:", 1)
                .replace("options:", "选项:")
                .replace("positional arguments:", "位置参数:")
                .replace("show this help message and exit", "显示这段帮助并退出"))


def build_parser() -> argparse.ArgumentParser:
    p = 中文解析器(
        prog="diansai-serial",
        description="电赛串口助手（命令行版）：看串口、算指标、自动扫参数、串口烧录。",
        epilog="例：diansai-serial sweep -p COM3 --values 0.5:2.0:0.25 --template \"KP={value}\" --target 1.0",
    )
    p.add_argument("--version", action="version", version="diansai-serial 0.1.0",
                   help="显示版本号并退出")
    sub = p.add_subparsers(dest="cmd", metavar="子命令", parser_class=中文解析器)

    sp = sub.add_parser("ports", help="列出可用串口", description="列出电脑上可用的串口（含芯片型号描述）")
    sp.set_defaults(func=cmd_ports)

    sp = sub.add_parser("monitor", help="看串口输出（可定时发送）", description="打开串口看输出；可以顺带按固定间隔发送内容")
    sp.add_argument("-p", "--port", required=True, help="串口号，如 COM3")
    sp.add_argument("-b", "--baud", type=int, default=115200, help="波特率（默认 115200）")
    sp.add_argument("--seconds", type=float, default=10.0, help="看多少秒（默认 10）")
    sp.add_argument("--send", default="", help="要定时发送的内容（可选）")
    sp.add_argument("--every", type=float, default=1.0, help="每隔几秒发一次（默认 1）")
    sp.add_argument("--hex-mode", action="store_true", help="按十六进制发送")
    sp.set_defaults(func=cmd_monitor)

    sp = sub.add_parser("analyze", help="算阶跃指标（超调/上升/调节时间）", description="读一个「时间,数值」的数据文件，算出超调量/上升时间/调节时间等指标")
    sp.add_argument("file", help="数据文件（每行两个数字：时间,数值）")
    sp.add_argument("--target", type=float, default=None, help="目标值（用于算相对目标值的超调）")
    sp.add_argument("--json", action="store_true", help="输出 JSON（方便脚本接）")
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("sweep", help="自动扫参数并推荐一个值", description="自动扫一串参数值：每轮发参数、采数据、算指标，最后出对比表并推荐一个值")
    sp.add_argument("-p", "--port", required=True, help="串口号，如 COM3")
    sp.add_argument("-b", "--baud", type=int, default=115200, help="波特率（默认 115200）")
    sp.add_argument("--values", required=True, help="要扫的值：0.5,1.0,1.5 或 0.5:2.0:0.25")
    sp.add_argument("--template", default="KP={value}", help="参数命令模板（默认 KP={value}）")
    sp.add_argument("--hex-mode", action="store_true", help="命令按十六进制发（可用 {value:04X}）")
    sp.add_argument("--trigger", default="", help="每轮改完参数后发的触发命令（可选）")
    sp.add_argument("--settle", type=float, default=1.5, help="改完参数等几秒生效（默认 1.5）")
    sp.add_argument("--collect", type=float, default=6.0, help="每轮采几秒数据（默认 6）")
    sp.add_argument("--target", type=float, default=None, help="目标值（可选）")
    sp.add_argument("--repeats", type=int, default=1, help="每个值重复几次（默认 1）")
    sp.set_defaults(func=cmd_sweep)

    sp = sub.add_parser("flash", help="串口 ISP 烧录（不用仿真器）", description="通过芯片内置 BootLoader 用串口烧写固件（没有仿真器也能烧）")
    sp.add_argument("hex", help="固件文件（.hex）")
    sp.add_argument("-p", "--port", required=True, help="串口号，如 COM3")
    sp.add_argument("-b", "--baud", type=int, default=115200, help="波特率（默认 115200）")
    sp.add_argument("--no-verify", action="store_true", help="烧完不做读回校验（不建议）")
    sp.add_argument("--no-backup", action="store_true", help="烧前不备份原固件（不建议）")
    sp.add_argument("--no-run", action="store_true", help="烧完不复位运行")
    sp.add_argument("--boot-preset", default="dtr_low_rts_high_boot", help="进 BootLoader 的线序预设")
    sp.add_argument("--backup-dir", default=".", help="备份放哪个目录（默认当前目录）")
    sp.set_defaults(func=cmd_flash)
    return p


def main(argv=None) -> int:
    # Windows 的控制台默认是 GBK，中文会显示成乱码 —— 这里统一成 UTF-8
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        _err("\n（已中断）")
        return 1
    except Exception as e:
        # 命令行工具不该把一坨调用栈甩给用户（§ 一律说中文 + 人话错误）
        import os
        if os.environ.get("DIANSAI_DEBUG"):
            raise
        _err(f"[错误] {type(e).__name__}: {e}")
        _err("       （想看完整调用栈：设环境变量 DIANSAI_DEBUG=1 再跑一次）")
        return 1


if __name__ == "__main__":
    sys.exit(main())
