@echo off
REM Prints diagnostics (no secrets) and tests the AI connection. Send the output if something does not work.
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
python -m mrx --doctor
pause
