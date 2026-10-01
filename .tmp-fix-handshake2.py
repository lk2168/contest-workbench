# -*- coding: utf-8 -*-
"""按实测调整握手：握手阶段用 retries=1（不插 0x7F 重发），失败靠"重进 BootLoader"；
握手成功后恢复正常重试次数。"""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
P = Path(r"D:\work\contest-workbench\contest_workbench\stm32_isp.py")
t = P.read_text(encoding="utf-8")

old = '''        attempts = 3 if self.reset_fn else 1
        last = None
        for i in range(1, attempts + 1):
            try:
                if i > 1:
                    say(f"握手失败，重新进 BootLoader 再试（第 {i} 次）")
                    self.reset_fn()
                self.bl.sync()
                try:
                    v1, v2, cmds = self.bl.get_version()
                    rep.boot_version = f"{v1}.{v2}"
                    say(f"BootLoader {rep.boot_version} · 支持 {len(cmds)} 条命令")
                except IspError as e:
                    say(f"（先发 Get 没成功，继续试 GetID：{e}）")
                rep.pid = self.bl.get_id()
                return
            except IspError as e:
                last = e
                say(f"（第 {i} 次握手没成功：{e}）")
        raise IspError(f"进 BootLoader 后握手失败（试了 {attempts} 次）：{last}")'''

new = '''        attempts = 5 if self.reset_fn else 1
        last = None
        keep_retries = self.bl.retries
        # ★ 实测：握手阶段**不要**让底层"发 0x7F 再重发命令" —— 那会把 BootLoader 的
        #   命令状态搅乱，反而更糟（今晚对照实验里，成功那次用的就是不重发 + 重进 BootLoader）。
        self.bl.retries = 1
        try:
            for i in range(1, attempts + 1):
                try:
                    if i > 1:
                        say(f"握手失败，重新进 BootLoader 再试（第 {i} 次）")
                        self.reset_fn()
                    self.bl.sync()
                    try:
                        v1, v2, cmds = self.bl.get_version()
                        rep.boot_version = f"{v1}.{v2}"
                        say(f"BootLoader {rep.boot_version} · 支持 {len(cmds)} 条命令")
                    except IspError as e:
                        say(f"（先发 Get 没成功，继续试 GetID：{e}）")
                    rep.pid = self.bl.get_id()
                    self.bl.retries = keep_retries      # 握手成功后再恢复正常重试
                    return
                except IspError as e:
                    last = e
                    say(f"（第 {i} 次握手没成功：{e}）")
        finally:
            if self.bl.retries == 1:
                self.bl.retries = keep_retries
        raise IspError(f"进 BootLoader 后握手失败（试了 {attempts} 次）：{last}")'''

assert old in t, "锚点没找到"
P.write_text(t.replace(old, new, 1), encoding="utf-8")
print("  [ok] 握手阶段改成不重发、失败重进 BootLoader（最多 5 次）")
