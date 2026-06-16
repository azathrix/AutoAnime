@echo off
setlocal

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "BUILD_DIR=%ROOT%\build"

if not exist "%BUILD_DIR%" mkdir "%BUILD_DIR%"

set "ZIP=%BUILD_DIR%\AutoAnime-clean.zip"

pushd "%ROOT%" || exit /b 1
if exist "%ZIP%" del /f /q "%ZIP%" >nul 2>nul
git archive --format=zip --output="%ZIP%" HEAD
if errorlevel 1 (
  for /f "delims=" %%H in ('git rev-parse --short HEAD') do set "SHORT=%%H"
  set "ZIP=%BUILD_DIR%\AutoAnime-clean-%SHORT%.zip"
  git archive --format=zip --output="%ZIP%" HEAD
  if errorlevel 1 (
    popd
    echo Package failed.
    exit /b 1
  )
)
popd

echo Created:
echo %ZIP%
endlocal
