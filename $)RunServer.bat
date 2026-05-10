@echo off
cd /d C:\Users\Tovey\HKGmindex

call venv\Scripts\activate.bat

set FLASK_APP=app.py
set FLASK_DEBUG=1

python -m flask run --host=127.0.0.1 --port=5000

pause