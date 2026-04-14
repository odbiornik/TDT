@echo off
setlocal

cd /d "%~dp0"
set "RUNTIME_DIR=%cd%\.runtime\gui"
set "VENV_DIR=%RUNTIME_DIR%\venv"

if not exist "%RUNTIME_DIR%" (
  mkdir "%RUNTIME_DIR%"
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo Creating virtual environment...
  py -m venv "%VENV_DIR%"
)

echo Installing/updating dependencies...
call "%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
call "%VENV_DIR%\Scripts\python.exe" -m pip install -r requirements.txt

echo.
echo Starting TDT GUI...
call "%VENV_DIR%\Scripts\python.exe" "src\tdt_gui.py"

endlocal
