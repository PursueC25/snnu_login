@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM 接口版（推荐）：直接 POST 登录表单，不启动浏览器
python auto_login_api.py
pause
