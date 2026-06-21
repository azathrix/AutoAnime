@echo off
setlocal

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "BUILD_DIR=%ROOT%\build"
set "FRONTEND_DIR=%ROOT%\frontend"
set "VERSION_FILE=%FRONTEND_DIR%\src\version.js"

for /f "usebackq delims=" %%i in (`node -p "require('%FRONTEND_DIR:\=/%/package.json').version"`) do set "APP_VERSION=%%i"
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "Get-Date -Format 'yyyyMMdd-HHmmss'"`) do set "APP_BUILD=%%i"

(
  echo export const APP_VERSION = '%APP_VERSION%'
  echo export const APP_BUILD = '%APP_BUILD%'
) > "%VERSION_FILE%"
if errorlevel 1 (
  echo Failed to write version file.
  exit /b 1
)

pushd "%FRONTEND_DIR%"
call npm run build
if errorlevel 1 (
  popd
  echo Frontend build failed.
  exit /b 1
)
popd

if not exist "%BUILD_DIR%" mkdir "%BUILD_DIR%"

set "TARGET=%BUILD_DIR%\AutoAnime-clean"

if exist "%TARGET%" rmdir /S /Q "%TARGET%"
if not exist "%TARGET%" mkdir "%TARGET%"

robocopy "%ROOT%" "%TARGET%" ^
  /MIR ^
  /XD ".git" "build" "data" "test-data" "node_modules" ".vite" "__pycache__" ".tmp-smoke*" ^
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
