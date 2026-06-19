@echo off
setlocal

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "TARGET=%AUTOANIME_UPLOAD_TARGET%"
if "%TARGET%"=="" set "TARGET=\\InputName\docker\autoanime"

if exist "%ROOT%\package-clean.bat" if exist "%ROOT%\frontend\node_modules\.bin\vite.cmd" (
  call "%ROOT%\package-clean.bat"
  if errorlevel 1 (
    echo Clean package failed.
    exit /b 1
  )
  set "SOURCE=%ROOT%\build\AutoAnime-clean"
) else (
  set "SOURCE=%ROOT%"
)

if not exist "%TARGET%" mkdir "%TARGET%"

robocopy "%SOURCE%" "%TARGET%" ^
  /MIR ^
  /XD ".git" "build" "data" "test-data" "node_modules" ".vite" "frontend_dist" "__pycache__" ".tmp-smoke" ".tmp-smoke-download" ".tmp-smoke-download2" ".tmp-smoke-media" ".tmp-smoke-media2" ^
  /XF "*.zip" "*.log" "*.pyc" ".env" ^
  /R:2 /W:2 /NFL /NDL /NP

set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 (
  echo Upload failed. Robocopy exit code: %RC%
  exit /b %RC%
)

echo Uploaded source to %TARGET%
echo Deploy command on NAS:
echo cd /volume1/docker/autoanime ^&^& docker compose down --remove-orphans ^&^& docker compose up -d --build
exit /b 0
