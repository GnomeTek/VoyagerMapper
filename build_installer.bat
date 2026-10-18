@echo off
REM Local Windows build: PyInstaller folder build + Inno Setup installer.
REM Needs: the project venv (setup.bat) and Inno Setup 6 (https://jrsoftware.org/isinfo.php).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo Run setup.bat first. & pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install -q -e ".[gui,build]" || goto :fail
for /f %%v in ('python -c "import voymap;print(voymap.__version__)"') do set VER=%%v
echo Building voymap %VER% ...
rmdir /s /q build dist 2>nul
pyinstaller packaging\voymap.spec --noconfirm || goto :fail
echo Smoke test...
dist\voymap\voymap-cli.exe sources >nul || goto :fail
set ISCC=
for %%p in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe") do if exist %%p set ISCC=%%p
if "%ISCC%"=="" (where iscc >nul 2>nul && set ISCC=iscc)
if "%ISCC%"=="" (echo Inno Setup 6 not found; the portable build is in dist\voymap. & pause & exit /b 0)
%ISCC% /DAppVersion=%VER% packaging\voymap.iss || goto :fail
echo.
echo Done: dist\voymap-setup-%VER%.exe
pause
exit /b 0
:fail
echo Build failed.
pause
exit /b 1
