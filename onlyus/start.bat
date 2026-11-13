@echo off
chcp 65001 > nul
cls
echo.
echo  =====================================================
echo         ONLY US - Our Little Universe 💕
echo  =====================================================
echo.

cd /d "%~dp0"

echo [1/2] Checking environment...
if not exist "venv\Scripts\python.exe" (
    echo Setting up Python virtual environment...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
)

echo [2/2] Starting server...
echo.
echo  =====================================================
echo   ✓ SERVER IS LIVE!
echo.
echo   💻 Open in browser:  http://localhost:8000
echo   📱 Open on Phone:    http://192.168.0.101:8000
echo.
echo   Login Details:
echo     User 1 (Him):  username: him   password: himher0203
echo     User 2 (Her):  username: her   password: himher0203
echo.
echo   (Press Ctrl+C to stop)
echo  =====================================================
echo.

call venv\Scripts\activate.bat
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload

pause
