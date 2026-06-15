@echo off
setlocal

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "TARGET=\\InputName\docker\autoanime"

if not exist "%TARGET%" mkdir "%TARGET%"

robocopy "%ROOT%" "%TARGET%" ^
  /MIR ^
  /XD ".git" "build" "data" "test-data" "frontend\node_modules" "frontend\.vite" "backend\frontend_dist" "backend\app\__pycache__" ^
  /XF "*.zip" "*.log" "*.pyc" ".env" ^
  /R:2 /W:2 /NFL /NDL /NP

set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 (
  echo Upload failed. Robocopy exit code: %RC%
  exit /b %RC%
)

echo Uploaded source to %TARGET%
echo Deploy command on NAS:
echo cd /volume1/docker/autoanime ^&^& docker compose up -d --build
exit /b 0
