@echo off
setlocal

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "BUILD_DIR=%ROOT%\build"

if not exist "%BUILD_DIR%" mkdir "%BUILD_DIR%"

set "ZIP=%BUILD_DIR%\AutoAnime-clean.zip"

if exist "%ZIP%" del /f /q "%ZIP%"

pushd "%ROOT%" || exit /b 1
git archive --format=zip --output="%ZIP%" HEAD
if errorlevel 1 (
  popd
  echo Package failed.
  exit /b 1
)
popd

echo Created:
echo %ZIP%
endlocal
