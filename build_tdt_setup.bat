@echo off
setlocal

cd /d "%~dp0"

echo Building GUI executable payload...
call "build_tdt_gui_exe.bat"
if errorlevel 1 (
  echo Failed to build GUI executable.
  exit /b 1
)

set "ISCC="
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"

if "%ISCC%"=="" (
  echo Inno Setup 6 not found.
  echo Install it from: https://jrsoftware.org/isdl.php
  exit /b 1
)

echo Building one-file installer...
pushd "installer"
call "%ISCC%" "TDTStudio.iss"
if errorlevel 1 (
  popd
  echo Installer build failed.
  exit /b 1
)
popd

echo.
echo Installer ready:
echo "%cd%\installer\output\TDTStudio_Setup.exe"

endlocal
