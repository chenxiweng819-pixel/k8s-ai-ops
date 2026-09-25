@echo off
REM ============================================================
REM  K8s 管理平台 - 一键启动（Windows）
REM  后端 8000 / AI 助手 5001 / 前端 5173
REM ============================================================
setlocal
set ROOT=%~dp0
cd /d "%ROOT%"

echo [1/3] 启动后端 (8000)...
start "k8s-backend" cmd /k "cd /d %ROOT% && python -m backend.app"

echo [2/3] 启动 AI 助手 (5001)...
start "k8s-ai" cmd /k "cd /d %ROOT% && python -m ai.app"

echo [3/3] 启动前端 (5173)...
start "k8s-frontend" cmd /k "cd /d %ROOT%frontend && npm run dev"

echo.
echo 稍等几秒后访问： http://localhost:5173
echo 后端 API:  http://localhost:8000/api/health
echo AI  助手:  http://localhost:5001/health
echo.
echo 关闭时请分别关闭三个新开的窗口，或运行 stop.bat
endlocal
