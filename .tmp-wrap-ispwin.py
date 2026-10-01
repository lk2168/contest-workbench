# -*- coding: utf-8 -*-
"""改经验库（Write 已打通！）+ 提交修复 + 看板子跑没跑。"""
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(r"D:\work\contest-workbench")
KB = Path(r"C:\Users\33083\.dsh\agent-kb")

# ① 经验库：把"Write 未打通"改写成"已打通 + 根因"
note = KB / "notes" / "stm32-serial-isp-protocol-measured.md"
t = note.read_text(encoding="utf-8")
old_start = t.find("## ❌ Write 命令：长度字段试了 6 种写法都不对")
old_end = t.find("## ★★ 设计教训")
assert old_start > 0 and old_end > old_start, "找不到要替换的段落"
new_section = """## ✅ Write 命令：**长度 + 数据 + 校验必须一次性发出**（2026-10-02 解决）

**最终结论（真机烧录成功验证）**：帧是**一整个**发出去的，
设备在**整帧收完**之后才回**一个** ACK：

```
0x31 0xCE                    → ACK
地址(4B) + 异或校验(1B)        → ACK
长度(1B) + 数据 + 填充 + 校验(1B) → ACK      ★ 这三部分必须**连着发**，中间不能等 ACK
```
- **长度 = 对齐到 4 字节后的字节数 - 1**（`(len + 3) & ~3 - 1`）
- **不足 4 字节的部分补 `0xFF`**（填充也算进长度和校验）
- **校验 = 长度字节 + 数据 + 填充 的逐字节异或**

### 我是怎么错的（值得记住）

我把「长度」单独发出去、**等它回 ACK**，再发数据 —— 设备根本不回这个中间 ACK，
它在**等数据**；于是表现成"长度发完就没动静"，我以为是长度字段格式不对，
穷举了 6 种写法（1/2 字节、大小端、N 与 N-1、干脆不发）**全是白费功夫** ✗。

### 怎么找到的（方法比结果更重要）

**不装驱动、不抓包**：直接去读**已知可用**的开源实现 —— `ARMinARM/stm32flash`
（C，★252，GPL-2.0）的 `stm32_write_memory()`：
```c
aligned_len = (len + 3) & ~3;
buf[0] = aligned_len - 1;
数据 + 不足补 0xFF;
buf[aligned_len + 1] = 校验;
port->write(port, buf, aligned_len + 2);   /* 一次全发 */
stm32_get_ack_timeout(...);                /* 只等一个 ACK */
```
★ **"先找现成的"这条规则再次值回票价** —— 我猜了 6 轮，读源码一轮就解决。

### 另一个关键：握手阶段不要"重发命令"

- 失败时发 `0x7F` 复位命令状态、再重发整帧 —— 这个做法在**握手阶段反而更糟**
  （会把 BootLoader 状态搅乱）
- 实测有效的做法：握手阶段**不重发**，失败就**重新复位进 BootLoader 再来一遍**（最多 5 次）
- 握手成功后再启用命令级重试

### 真机结果

```
✅ 芯片 0x0413 → STM32F405/407/415/417（BootLoader 3.1）
✅ 原固件已备份（7800 字节）
✅ 全片擦除 → 写入 7800 字节 → **读回逐字节校验通过**
耗时 19.8 秒（含备份与校验）
```

"""
t = t[:old_start] + new_section + t[old_end:]
note.write_text(t, encoding="utf-8")
print("  [ok] 经验笔记已改写（Write 已打通 + 根因 + 方法）")

# 索引也改一下
idx = KB / "_index.md"
ti = idx.read_text(encoding="utf-8")
ti = ti.replace("**Write 没打通**（长度字段 6 种写法全被 NACK，下一步用 com0com 抓 ATK-XISP 流量对照）",
                "★ **Write 已打通**：帧必须「长度+数据+校验」**一次性发出**（读 stm32flash 源码找到的；"
                "我分两次发还等中间 ACK，害得穷举 6 种写法全白费）；握手阶段失败要重进 BootLoader 而不是重发命令")
idx.write_text(ti, encoding="utf-8")
print("  [ok] 索引已改")

subprocess.run(["git", "add", "-A"], cwd=KB, capture_output=True)
subprocess.run(["git", "-c", "user.name=Lu Kuo",
                "-c", "user.email=288989345+lk2168@users.noreply.github.com",
                "commit", "-q", "-m",
                "notes: ★ STM32 串口 ISP 的 Write 打通了 —— 帧必须「长度+数据+校验」一次性发出"
                "（读 stm32flash 源码找到，我猜了 6 轮全白费）；握手失败要重进 BootLoader 而非重发命令"],
               cwd=KB, capture_output=True)
pr = subprocess.run(["git", "push"], cwd=KB, capture_output=True, text=True,
                    encoding="utf-8", errors="replace")
print("  KB:", ((pr.stderr or pr.stdout).strip().splitlines() or ["ok"])[-1])

# ② 提交代码修复
msg = """fix(isp): ★ 一键烧录打通了 —— 写帧必须「长度+数据+校验」一次性发出

真机结果：芯片 0x0413 → 写入 7800 字节 → **读回逐字节校验通过**，耗时 19.8 秒。

根因（读了 stm32flash 的权威实现才找到）：
Write Memory 的帧是**一整个**发出去的，设备在整帧收完后只回**一个** ACK：
  0x31 0xCE → ACK ；地址+xor → ACK ；**长度 + 数据(+0xFF 填充) + 校验 → ACK**
我原来把「长度」单独发出并等 ACK → 设备在等数据、根本不回中间 ACK，
表现成「长度发完没动静」，我误以为是长度字段格式问题，穷举了 6 种写法（1/2 字节、
大小端、N 与 N-1、不发长度）**全是白费**。
★ 教训：先读已知可用的开源实现（ARMinARM/stm32flash，GPL-2.0），比盲猜快一个数量级。

同时修正：
- 长度按 **4 字节对齐**（`(len+3) & ~3`），不足补 0xFF（填充也计入长度与校验）
- 写 Flash 时把串口超时放宽到 ≥2 秒（写一块要时间）
- ★ 握手阶段**不要**用「0x7F + 重发命令」那套重试（会搅乱 BootLoader 状态）；
  改为失败就**重新复位进 BootLoader 再来一遍**（最多 5 次），握手成功后再启用命令级重试

测试：假 BootLoader 也按真协议改成"整帧才回 ACK"，36 项全过。
"""
mf = ROOT / ".git-msg-ispfix.txt"
mf.write_text(msg, encoding="utf-8")
subprocess.run(["git", "add", "-A"], cwd=ROOT, capture_output=True)
rr = subprocess.run(["git", "-c", "user.name=Lu Kuo",
                     "-c", "user.email=288989345+lk2168@users.noreply.github.com",
                     "commit", "-q", "-F", str(mf)], cwd=ROOT, capture_output=True, text=True,
                    encoding="utf-8", errors="replace")
mf.unlink(missing_ok=True)
print("  提交:", (rr.stdout or rr.stderr).strip()[:70] or "done")
pp = subprocess.run(["git", "push"], cwd=ROOT, capture_output=True, text=True,
                    encoding="utf-8", errors="replace")
print("  推送:", ((pp.stderr or pp.stdout).strip().splitlines() or ["ok"])[-1])

# ③ 看板子跑没跑（复位后听）
sys.path.insert(0, str(ROOT))
from contest_workbench.serial_assistant import SerialAssistant      # noqa: E402
a = SerialAssistant()
a.open("COM3", 115200)
time.sleep(0.4)
a.set_lines(dtr=True, rts=True)
time.sleep(0.3)
a.read_events(999)
t0, text = time.time(), ""
while time.time() - t0 < 4.0:
    time.sleep(0.3)
    evs = a.read_events(99)
    text += "".join(str(e.get("text", "")) for e in evs if e.get("dir") != "tx")
print(f"\n  复位前监听：{'★ 程序在跑 ✓' if '请输入' in text else '没听到'}")
if "请输入" not in text:
    a.pulse_reset("dtr_high", hold=0.2, boot_wait=0.8)
    a.read_events(999)
    t0, text = time.time(), ""
    while time.time() - t0 < 4.0:
        time.sleep(0.3)
        evs = a.read_events(99)
        text += "".join(str(e.get("text", "")) for e in evs if e.get("dir") != "tx")
    print(f"  再复位一次：{'★ 程序在跑 ✓' if '请输入' in text else '还是没听到（按一下板上复位键）'}")
a.close()
