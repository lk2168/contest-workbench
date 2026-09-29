@echo off
title 修复 Word 无法启动（0xc0000142）
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
  echo ============================================================
  echo  [!] 需要管理员权限
  echo  请关掉这个窗口，右键本文件 →「以管理员身份运行」
  echo ============================================================
  pause
  exit /b 1
)

echo ============================================================
echo   修复 Word 无法启动（错误码 0xc0000142）
echo   本脚本做四件事，每步都会停下来告诉你结果
echo ============================================================
echo.

echo [1/4] 停掉 OfficePLUS 插件服务（微软签名的合法插件，但会注入 Office）
sc query "OfficePLUS Service" >nul 2>&1
if errorlevel 1 (
  echo       未找到该服务，跳过
) else (
  sc stop "OfficePLUS Service" >nul 2>&1
  echo       已请求停止（随时可用「sc start "OfficePLUS Service"」还原）
)
echo.

echo [2/4] 结束可能卡住的 Office 进程
taskkill /f /im WINWORD.EXE >nul 2>&1
taskkill /f /im EXCEL.EXE   >nul 2>&1
taskkill /f /im POWERPNT.EXE >nul 2>&1
echo       已清理
echo.

echo [3/4] 现在启动一次 Word（这一步是关键实验）
echo       如果 Word 窗口正常出现 -^> 说明元凶就是 OfficePLUS，去「设置-应用」把它卸载即可
echo       如果又弹 0xc0000142      -^> 继续第 4 步做官方修复
echo.
pause
start "" "C:\Program Files\Microsoft Office\Root\Office16\WINWORD.EXE"
echo       已发出启动命令，请看屏幕...
timeout /t 12 /nobreak >nul
tasklist /fi "imagename eq WINWORD.EXE" | find /i "WINWORD.EXE" >nul
if errorlevel 1 (
  echo       Word 进程没起来
) else (
  echo       Word 进程在运行。若你看到了窗口 -^> 问题解决；
  echo       若只看到报错弹窗 -^> 继续第 4 步
)
echo.

echo [4/4] 执行 Office 官方快速修复（从本机缓存修复，通常 3-10 分钟）
echo       想跳过就直接关窗口。想继续请按任意键...
pause >nul
taskkill /f /im WINWORD.EXE >nul 2>&1
taskkill /f /im EXCEL.EXE   >nul 2>&1
"C:\Program Files\Common Files\Microsoft Shared\ClickToRun\OfficeClickToRun.exe" scenario=Repair platform=x64 culture=zh-cn DisplayLevel=True
echo.
echo       修复命令已结束（返回码 %errorlevel%）。
echo       现在再双击一个 .docx 试试。
echo       若仍然不行：设置 - 应用 - Microsoft Office - 修改 - 在线修复；
echo       再不行就考虑卸载重装，或改用正版（学生可申请 Microsoft 365 教育版 / 先用 WPS 免费版）。
echo.
echo   小提示：本脚本停掉的 OfficePLUS 服务如需还原：
echo           sc start "OfficePLUS Service"
echo.
pause
