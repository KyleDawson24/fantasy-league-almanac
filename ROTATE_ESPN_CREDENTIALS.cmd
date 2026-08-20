@echo off
call "%~dp0START_ALMANAC.cmd" --rotate-credentials
exit /b %ERRORLEVEL%
