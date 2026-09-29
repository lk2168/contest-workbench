# 维护脚本（跟"大学生竞赛工作台"本身无关，是环境排障用）

这些脚本不参与 Agent 运行，只是把**踩过的环境坑**固化成可复用的修复工具，免得下次重踩。

---

## `修复Word-0xc0000142.cmd`

**症状**：双击任何 `.docx` 都弹
`WINWORD.EXE - Application Error：应用程序无法正常启动(0xc0000142)`；
Word、Excel **都**起不来，安全模式（`/safe`）也不行。

**判定**（本机实测的硬指标，比看屏幕靠谱）：

```powershell
Start-Process "C:\Program Files\Microsoft Office\Root\Office16\WINWORD.EXE" -ArgumentList "`"<某个docx>`""
Start-Sleep 10
Get-Process WINWORD | Select Id,MainWindowHandle,MainWindowTitle,CPU
```

- **坏的样子**：进程存在，但 `MainWindowHandle=0`、标题空、`CPU=0s`、内存仅 ~11 MB
- **铁证**：`Application` 日志里 `Application Popup`（ID 26）写着 `0xc0000142`
  ```powershell
  Get-WinEvent -FilterHashtable @{LogName='Application';StartTime=(Get-Date).AddMinutes(-30)} |
    Where-Object { $_.Message -match 'WINWORD' }
  ```

**用法**：**右键 → 以管理员身份运行**（脚本会自检权限，没权限会直接告诉你）。

脚本分四步，每步都停下来报告结果：

1. 停掉 `OfficePLUS Service`（已知会注入 Office 的组件；**微软签名合法，不是病毒**，只是值得做一次对照实验）
2. 清掉卡住的 Office 进程
3. **启动一次 Word 做对照** —— 能开 → 元凶是 OfficePLUS；还报错 → 进第 4 步
4. 跑 Office **官方快速修复**
   `"C:\Program Files\Common Files\Microsoft Shared\ClickToRun\OfficeClickToRun.exe" scenario=Repair platform=x64 culture=zh-cn`

**还不行**：设置 → 应用 → Microsoft Office → 修改 → **在线修复**；再不行就卸载重装，
或改用正版（学生可申请 **Microsoft 365 教育版**；应急可用 **WPS Office 免费版**）。

**⚠️ 本文件是 GBK 编码**（中文 Windows 控制台原生编码，`cmd` 直接双击才能正确显示中文）。
改它的时候**别存成 UTF-8**，否则控制台里全是乱码；
若确需 UTF-8，请在文件开头加 `chcp 65001` 并接受首行风险。

---

## 附：怎么做一个"双击就以管理员运行"的快捷方式

Windows 的 `.cmd` 没法自己提权，但可以给**快捷方式**打上"以管理员身份运行"标志，
用户双击 → 直接弹 UAC，省掉"右键 → 以管理员身份运行"这一步。

```powershell
$ws = New-Object -ComObject WScript.Shell
$s  = $ws.CreateShortcut("$env:USERPROFILE\Desktop\修复Word.lnk")
$s.TargetPath = "D:\...\修复Word-0xc0000142.cmd"
$s.WorkingDirectory = Split-Path $s.TargetPath
$s.Save()

# 关键一步：把 .lnk 第 0x15 字节的 bit5 置 1（runas 标志）
$b = [System.IO.File]::ReadAllBytes($lnk)
$b[0x15] = $b[0x15] -bor 0x20
[System.IO.File]::WriteAllBytes($lnk, $b)
```

验证：读回 `$b[0x15] -band 0x20` 应为非 0。**这是给非管理员用户准备的**——
agent 自己点不了 UAC，只能把脚本和快捷方式准备好，让用户双击。
