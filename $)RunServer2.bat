@echo off
:: 設定編碼為 UTF-8 以支援中文顯示
chcp 65001 >nul

echo ========================================
echo   正在準備更新 GitHub 倉庫...
echo ========================================

:: 1. 強制設定遠端網址 (確保名字改了也能連上)
:: 請將下方網址換成你目前的 GitHub 倉庫網址
git remote set-url origin https://github.com/hkvel0city852063852/你的倉庫名.git

:: 2. 加入所有變更
echo 正在加入檔案變更...
git add .

:: 3. 提交 (Commit)
set /p msg="請輸入本次更新說明 (直接按 Enter 則使用 'Auto Update'): "
if "%msg%"=="" set msg=Auto Update
git commit -m "%msg%"

:: 4. 推送到 GitHub
echo 正在推送到 GitHub...
git push -u origin main

echo ========================================
echo   更新完成！Vercel 應該已經開始部署了。
echo ========================================
pause