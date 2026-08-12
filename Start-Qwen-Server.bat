@echo off
title Qwen 9B - Chess Coach LLM (127.0.0.1:8080)
REM ==================================================================
REM  Start-Qwen-Server.bat  --  single click to launch AND keep up the
REM  local Qwen3.5 9B (llama-server, OpenAI-compatible, 127.0.0.1:8080).
REM  This is the "Local 9B" engine brain for the Phenomenological Chess
REM  Coach. Leave this window open; close it (or Ctrl+C) to stop the model.
REM  Logic (with crash-restart + fast-failure backoff) lives in
REM  tools\Run-Qwen-Server.ps1 and uses the b10092 llama.cpp build.
REM ==================================================================
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\Run-Qwen-Server.ps1"
echo.
echo Qwen server stopped.
pause
