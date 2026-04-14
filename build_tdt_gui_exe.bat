@echo off
setlocal

cd /d "%~dp0"
set "RUNTIME_DIR=%cd%\.runtime\build_gui"
set "VENV_DIR=%RUNTIME_DIR%\venv"
set "PYI_WORK=%RUNTIME_DIR%\pyinstaller_work"
set "PYI_DIST=%RUNTIME_DIR%\pyinstaller_dist"
set "INSTALLER_DIR=%cd%\installer\TDTStudio"

if not exist "%RUNTIME_DIR%" (
  mkdir "%RUNTIME_DIR%"
)
if not exist "%PYI_WORK%" (
  mkdir "%PYI_WORK%"
)
if not exist "%PYI_DIST%" (
  mkdir "%PYI_DIST%"
)
if not exist "%INSTALLER_DIR%" (
  mkdir "%INSTALLER_DIR%"
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
  echo Creating virtual environment...
  py -m venv "%VENV_DIR%"
)

echo Installing/updating build dependencies...
call "%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
call "%VENV_DIR%\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller

echo Building GUI executable...
call "%VENV_DIR%\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed --name TDTConsole --specpath "%RUNTIME_DIR%" --workpath "%PYI_WORK%" --distpath "%PYI_DIST%" "src\tdt_gui.py"

if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

copy /Y "%PYI_DIST%\TDTConsole.exe" "%INSTALLER_DIR%\TDTConsole.exe" >nul

echo.
echo Build finished successfully.
echo Runtime executable: "%PYI_DIST%\TDTConsole.exe"
echo Installer payload: "%INSTALLER_DIR%\TDTConsole.exe"

endlocal
