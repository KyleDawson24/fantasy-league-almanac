@echo off
setlocal

rem Always work from the extracted release folder. pushd handles ordinary
rem folders, paths with spaces, OneDrive folders, and UNC paths.
pushd "%~dp0" >nul 2>&1
if errorlevel 1 goto :bad_folder

set "FLA_ROOT=%CD%\"
set "FLA_VENV=%FLA_ROOT%.venv"
set "FLA_VENV_PYTHON=%FLA_VENV%\Scripts\python.exe"
set "FLA_BOOTSTRAP_PYTHON="

echo ============================================================
echo Fantasy League Almanac - guided Windows setup
echo ============================================================
echo.
echo Keep this window open. It will prepare a private project environment,
echo guide you through ESPN setup, and offer to create your Google workbook.
echo You do not need to edit any configuration files.
echo.

where py >nul 2>&1
if errorlevel 1 goto :try_python
py -3.13 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)" >nul 2>&1
if not errorlevel 1 set "FLA_BOOTSTRAP_PYTHON=py -3.13"

:try_python
if defined FLA_BOOTSTRAP_PYTHON goto :python_found
where python >nul 2>&1
if errorlevel 1 goto :python_missing
python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)" >nul 2>&1
if not errorlevel 1 set "FLA_BOOTSTRAP_PYTHON=python"

:python_found
if not defined FLA_BOOTSTRAP_PYTHON goto :python_missing

echo [1/3] Preparing the private Python 3.13 environment...
%FLA_BOOTSTRAP_PYTHON% -m venv "%FLA_VENV%"
if errorlevel 1 goto :venv_failed

"%FLA_VENV_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)" >nul 2>&1
if errorlevel 1 goto :venv_failed

echo [2/3] Checking the required packages...
"%FLA_VENV_PYTHON%" "%FLA_ROOT%tools\windows_launcher.py" %*
set "FLA_EXIT=%ERRORLEVEL%"
echo.
if "%FLA_EXIT%"=="0" goto :success
if "%FLA_EXIT%"=="130" goto :interrupted
echo The Almanac stopped before completing. Nothing is fixed by running as
echo Administrator. Read the message above, then double-click START_ALMANAC
echo again. Completed downloads and saved setup can be reused safely.
goto :finish

:success
echo Finished. You may close this window.
goto :finish

:interrupted
echo The run was interrupted. Double-click START_ALMANAC again when ready.
echo The installer will safely finish any incomplete package setup, and the
echo Almanac runner will reuse completed local work where its contract allows.
goto :finish

:python_missing
echo [STOPPED] Python 3.13 was not found.
echo.
echo Install the 64-bit Python 3.13 release from:
echo https://www.python.org/downloads/
echo.
echo During installation, select "Add python.exe to PATH". Then close this
echo window and double-click START_ALMANAC again.
set "FLA_EXIT=2"
goto :finish

:venv_failed
echo.
echo [STOPPED] The private Python environment could not be prepared.
echo Close any other Almanac window, make sure this folder is fully extracted
echo rather than opened inside the ZIP, and double-click START_ALMANAC again.
echo Your ESPN and Google credentials were not requested by this step.
set "FLA_EXIT=2"
goto :finish

:bad_folder
echo Fantasy League Almanac could not open its extracted folder.
echo Extract the ZIP to a normal folder, then double-click START_ALMANAC again.
set "FLA_EXIT=2"

:finish
echo.
pause
popd >nul 2>&1
exit /b %FLA_EXIT%
