@echo off
echo 啟動 HKJC Flask app2.py...
cd /d "%~dp0"
if exist venv (
    call venv\Scripts\activate.bat
    echo 已啟動 venv
) else (
    echo 警告：未發現 venv，請先跑 python -m venv venv
    pause
    exit /b 1
)
python app2.py
pause