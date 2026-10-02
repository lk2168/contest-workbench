# 电赛串口助手（diansai-serial）

给电赛（控制类）调参用的串口工具：**看串口 → 采数据 → 算指标 → 扫参数 → 烧固件**，
命令行就能用，不用开网页、不用独占一个 GUI。

> 它是[大学生竞赛工作台](https://github.com/lk2168/contest-workbench)里的串口部分，
> 单独打包出来是为了"只想要串口工具"的人能直接 `pip install`。
> 工作台那份源码只有一套，不会分叉。

## 为什么不用现成的串口助手

市面上的（SSCOM / XCOM / 正点原子串口助手）都是**看数据**；调参真正费时间的是：

1. 把采到的数据**算成指标**（超调量、上升时间、调节时间）——手算容易错
2. **改一个参数 → 重跑一次 → 记下来 → 再改**——纯体力活
3. 手上没有仿真器时**烧不进程序**

这个工具就是干这三件事的。指标全部由**本地确定性算法**算出（不是模型猜的），
每一步都能手算复核。

## 安装

```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple diansai-serial            # 核心：只有 pyserial
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "diansai-serial[metrics]"  # 加 numpy：算指标、扫参数
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "diansai-serial[plot]"     # 加 matplotlib：画曲线
```

> 现在是从源码装：`pip install .`（在仓库根目录）。依赖分层，缺什么它会明确告诉你装哪个。

## 用法

### 1. 看有哪些串口

```bash
diansai-serial ports
```

### 2. 看串口输出（可定时发送）

```bash
diansai-serial monitor -p COM3
diansai-serial monitor -p COM3 --seconds 30 --send "START" --every 2
```

### 3. 算阶跃指标

数据格式：每行 `时间,数值`（逗号或空格分隔都行）。

```bash
diansai-serial analyze 阶跃响应.csv
diansai-serial analyze 阶跃响应.csv --target 1.0     # 有目标值时，超调按"相对目标值"算
diansai-serial analyze 阶跃响应.csv --json           # 输出 JSON，方便脚本接着处理
```

输出包括：稳态值、超调量、上升时间、**调节时间（±2% 与 ±5% 两个口径）**、稳态误差、振荡次数，
以及一段**规则化建议**（本地规则，不是模型猜的）。

### 4. 自动扫参数（最省时间的一个）

```bash
diansai-serial sweep -p COM3 --values 0.5:2.0:0.25 --template "KP={value}" --target 1.0
```

它会：**发参数 → 等生效 → 采一段数据 → 算指标 → 下一个值**，最后给一张对比表和一句推荐：

```
| 参数值 | 超调 σ% | 上升时间 t_r | 调节时间 t_s | 稳态值 | 数据点 | 备注 |
|---|---|---|---|---|---|---|
| 0.5 | 0.31 | 1.240s | 2.980s | 1.0002 | 300 | 已稳定 |
| 1 | 2.81 | 0.880s | 2.140s | 1.0004 | 300 | 已稳定 |
| 2 | 16.31 | 0.560s | 1.980s | 0.9987 | 300 | 已稳定 ★ 推荐 |

推荐 2：超调 16.3%（≤20%）且调节时间最短（1.980s）。
```

要点：
- `--values` 支持枚举 `0.5,1.0,1.5` 和范围 `0.5:2.0:0.25`
- `--template` 里的 `{value}` 会替换成参数值；HEX 帧用 `--hex-mode` + `{value:04X}`
  （例：`--template "AA 01 {value:04X}" --hex-mode`）
- 有"开始阶跃"这类触发命令时加 `--trigger START`
- **随时 Ctrl+C 停**，已采到的结果照常输出
- 推荐规则是确定的：**超调不超标（默认 ≤20%）的值里选调节时间最短的**；
  一个都不达标就选超调最小的，并说明原因

### 5. 串口 ISP 烧录（没有仿真器也能烧）

```bash
diansai-serial flash -p COM3 固件.hex
```

- 自动复位进 BootLoader（线序不对会自动重试），出错会重新进一次再来
- **烧前先备份原固件**（万一写坏了能烧回去），**烧完逐字节读回校验**
- 芯片 ID 不对、或未知型号 → **停手不擦不写**
- 备份默认放当前目录

## 常见问题

**打开串口后板子不动 / 收不到数据？**
CH340 类芯片的 DTR/RTS 线序各家不同。本工具默认 `dtr=True, rts=True`（实测能让用户程序运行），
`monitor` 也支持 `set_lines` 的组合；如果板子被按在复位态，多半是这两个电平不对。

**"没有检测到串口"？**
① 数据线是否插好 ② 驱动是否装了（CH340 / CP2102 / FT232 都要驱动）。
注意有些线只有充电功能，没有数据线。

**算指标说"数据点太少"？**
至少 8 个点；另外确认每行是"时间,数值"两个数字。

**没有仿真器能烧吗？**
能。`flash` 走的是芯片**内置 BootLoader**（AN3155 协议），只要有串口和 BOOT 跳线/复位电路就行。

## 许可

MIT（见仓库根目录 `LICENSE`）。
