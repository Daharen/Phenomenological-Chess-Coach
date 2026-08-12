# Run-Qwen-Server.ps1 -- launch + keep-alive for the local Qwen3.5 9B on
# 127.0.0.1:8080 (OpenAI-compatible), the "Local 9B" brain for the Chess Coach.
# Uses the newer llama.cpp build (b10092): the older build 8661 cannot load
# Qwen3.5's hybrid SSM architecture ("missing tensor blk.NN.ssm_conv1d.weight").
# Restarts on crash, but STOPS after 3 fast failures instead of looping.
$ErrorActionPreference = 'Continue'
$Llama = 'F:\My_Programs\LifeOrchestrator-Refresh_Large_Data\_engines\llama.cpp-b10092\bin\llama-server.exe'
$Model = 'F:\My_Programs\LifeOrchestrator-Refresh_Large_Data\07-model-gateway\llm\Qwen3.5-9B-Q4_K_M\Qwen_Qwen3.5-9B-Q4_K_M.gguf'
$Bind = '127.0.0.1'; $Port = 8080; $Ngl = 99; $Ctx = 8192
if (-not (Test-Path $Llama)) { Write-Host "[!] llama-server not found: $Llama" -ForegroundColor Red; Read-Host 'Enter to close'; exit 1 }
if (-not (Test-Path $Model)) { Write-Host "[!] model not found: $Model" -ForegroundColor Red; Read-Host 'Enter to close'; exit 1 }
$fails = 0
while ($true) {
  Write-Host ''
  Write-Host ("[{0}] Starting Qwen3.5 9B on http://{1}:{2}  (all layers on GPU)" -f (Get-Date -Format 'HH:mm:ss'), $Bind, $Port) -ForegroundColor Cyan
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  & $Llama --model $Model --host $Bind --port $Port --n-gpu-layers $Ngl --ctx-size $Ctx --alias qwen3.5-9b
  $code = $LASTEXITCODE; $sw.Stop(); $secs = [int]$sw.Elapsed.TotalSeconds
  Write-Host ("[{0}] llama-server exited (code {1}) after {2}s." -f (Get-Date -Format 'HH:mm:ss'), $code, $secs) -ForegroundColor Yellow
  if ($secs -lt 30) {
    $fails++
    Write-Host ("  Fast exit -- likely a model/binary problem. Consecutive fast failures: {0}/3" -f $fails) -ForegroundColor Yellow
    if ($fails -ge 3) {
      Write-Host ''
      Write-Host "[!] Stopped after 3 fast failures. Check the error above." -ForegroundColor Red
      Read-Host 'Press Enter to close'
      exit 1
    }
  } else { $fails = 0 }
  Write-Host '  Restarting in 3s...  (close this window to stop)' -ForegroundColor DarkGray
  Start-Sleep -Seconds 3
}
