@echo off
REM 停止 K8s 管理平台占用的 8000 / 5001 / 5173 端口进程
setlocal enabledelayedexpansion

for %%P in (8000 5001 5173) do (
  echo 检查端口 %%P ...
  for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%%P " ^| findstr "LISTENING"') do (
    echo   结束进程 PID %%A
    taskkill /PID %%A /F >nul 2>&1
  )
)

echo 完成。
endlocal
