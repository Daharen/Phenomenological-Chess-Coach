@echo off
REM ==================================================================
REM  Start-Qwen-Server.bat
REM  Launches the local Qwen3.5 9B (Q4_K_M) via llama-server on
REM  127.0.0.1:8080 (OpenAI-compatible) and KEEPS IT UP: if the server
REM  ever exits, it auto-restarts. This is the "Local 9B" engine brain
REM  for the Phenomenological Chess Coach. Leave this window open.
REM  Close the window (or Ctrl+C) to stop the model.
REM ==================================================================
setlocal
title Qwen 9B - Chess Coach LLM (127.0.0.1:8080)

set "LLAMA=F:\My_Programs\LifeOrchestrator-Refresh_Large_Data\_engines\llama.cpp\bin\llama-server.exe"
set "MODEL=F:\My_Programs\LifeOrchestrator-Refresh_Large_Data\07-model-gateway\llm\Qwen3.5-9B-Q4_K_M\Qwen_Qwen3.5-9B-Q4_K_M.gguf"
set "HOST=127.0.0.1"
set "PORT=8080"
REM  -ngl 99 offloads all layers to the GPU (RTX 2080 Ti, 11 GB: the
REM  5.75 GB model fits fully). Lower NGL or CTX if you ever hit VRAM OOM.
set "NGL=99"
set "CTX=8192"

if not exist "%LLAMA%" ( echo [!] llama-server.exe not found: "%LLAMA%" & pause & exit /b 1 )
if not exist "%MODEL%" ( echo [!] model not found: "%MODEL%" & pause & exit /b 1 )

:loop
echo.
echo [%date% %time%] Starting Qwen 9B on http://%HOST%:%PORT%  (first load ~a few minutes)
"%LLAMA%" --model "%MODEL%" --host %HOST% --port %PORT% --n-gpu-layers %NGL% --ctx-size %CTX% --alias qwen3.5-9b
echo.
echo [%date% %time%] llama-server exited (code %errorlevel%). Restarting in 3s...  (close this window to stop)
timeout /t 3 /nobreak >nul
goto loop
