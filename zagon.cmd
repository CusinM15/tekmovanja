@echo off
rem ============================================================
rem  zagon.cmd — enostaven dvoklik za zagon.ps1
rem  (zaobide Execution Policy; skripta naredi pull+venv+run)
rem ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0zagon.ps1"
pause
