@echo off
REM One-time setup: creates a virtual environment and installs voymap with the GUI.
REM Requires Python 3.10+ from python.org (with "Add python.exe to PATH" ticked).
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% -m venv .venv || goto :fail
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e ".[gui,dev]" || goto :fail
echo.
echo Running the offline self-test...
python -m pytest -q || goto :fail
echo.
echo Setup complete. Double-click run_gui.bat to start voymap.
pause
exit /b 0
:fail
echo.
echo Setup failed. Check that Python 3.10+ is installed and on PATH.
pause
exit /b 1
