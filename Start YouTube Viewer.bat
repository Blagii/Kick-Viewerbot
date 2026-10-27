@echo off
setlocal
cd /d "%~dp0"
title YouTube Viewer

rem The downloadable Windows package includes Python. A source checkout can use
rem the Python launcher or Python on PATH instead.
if exist "%~dp0runtime\python\python.exe" goto portable
where py >nul 2>&1
if errorlevel 1 goto check_python_path
py -3 -c "import sys; sys.exit(sys.version_info < (3, 9) or sys.maxsize < 2**32)" >nul 2>&1
if not errorlevel 1 goto python_launcher

:check_python_path
where python >nul 2>&1
if errorlevel 1 goto missing_python
python -c "import sys; sys.exit(sys.version_info < (3, 9) or sys.maxsize < 2**32)" >nul 2>&1
if not errorlevel 1 goto python_path

:missing_python

echo Python was not found. Download the portable Windows build, or install
echo 64-bit Python 3.11 and Node.js 22 before running from source.
set "RESULT=1"
goto finish

:portable
set "PYTHONHOME=%~dp0runtime\python"
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"
"%~dp0runtime\python\python.exe" "%~dp0scripts\start_windows.py" %*
set "RESULT=%ERRORLEVEL%"
goto finish

:python_launcher
py -3 "%~dp0scripts\start_windows.py" %*
set "RESULT=%ERRORLEVEL%"
goto finish

:python_path
python "%~dp0scripts\start_windows.py" %*
set "RESULT=%ERRORLEVEL%"

:finish
if not "%RESULT%"=="0" (
  echo.
  echo Startup failed. Check the message above for details.
  if not defined CI pause
)
exit /b %RESULT%
