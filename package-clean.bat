@echo off
setlocal

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "BUILD_DIR=%ROOT%\build"

if not exist "%BUILD_DIR%" mkdir "%BUILD_DIR%"

set "TARGET=%BUILD_DIR%\AutoAnime-clean"

if exist "%TARGET%" rmdir /s /q "%TARGET%"
mkdir "%TARGET%"

robocopy "%ROOT%" "%TARGET%" ^
  /MIR ^
  /XD ".git" "build" "data" "test-data" "node_modules" ".vite" "frontend_dist" "__pycache__" ^
  /XF "*.zip" "*.log" "*.pyc" ".env" ^
  /R:2 /W:2 /NFL /NDL /NP

set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 (
  echo Package failed. Robocopy exit code: %RC%
  exit /b %RC%
)

echo Created:
echo %TARGET%
endlocal
