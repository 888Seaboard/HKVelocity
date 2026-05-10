@echo off
cd /d C:\Users\Tovey\HKGmindex

git add .
git diff --cached --quiet
if %errorlevel% neq 1 (
    echo No changes to commit.
) else (
    git commit -m "update"
    git push origin main
)

pause