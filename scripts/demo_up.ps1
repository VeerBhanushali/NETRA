# scripts/demo_up.ps1
# Starts the NETRA Command Centre Stack (FastAPI Gateway on :8000 & Next.js Console on :3000)

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  NETRA // SPATIO-TEMPORAL COMMAND CENTRE LAUNCHER" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Run Preflight Diagnostics
Write-Host "[*] Running 10-Point Preflight Check..." -ForegroundColor Yellow
python scripts\preflight.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "[-] Preflight failed. Halting startup." -ForegroundColor Red
    exit 1
}

# 2. Launch FastAPI Gateway
Write-Host "[*] Launching FastAPI Backend on http://127.0.0.1:8000 ..." -ForegroundColor Green
$apiProcess = Start-Process -FilePath "python" -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8000" -WorkingDirectory "apps\api" -PassThru

# 3. Launch Next.js Web Console
Write-Host "[*] Launching Next.js Web Console on http://localhost:3000 ..." -ForegroundColor Green
$webProcess = Start-Process -FilePath "npm.cmd" -ArgumentList "start" -WorkingDirectory "apps\web" -PassThru

Write-Host "`n[✓] NETRA Stack is LIVE!" -ForegroundColor Cyan
Write-Host "  - Command Centre Dashboard: http://localhost:3000/dashboard" -ForegroundColor White
Write-Host "  - Tactical Map:             http://localhost:3000/map" -ForegroundColor White
Write-Host "  - Incident Alerts:          http://localhost:3000/alerts" -ForegroundColor White
Write-Host "  - Camera Wall:              http://localhost:3000/cameras" -ForegroundColor White
Write-Host "  - API Documentation:        http://127.0.0.1:8000/docs" -ForegroundColor White
Write-Host "`nTo trigger live demo scenarios on stage:" -ForegroundColor Yellow
Write-Host "  python scripts\force_event.py --event cloned_plate" -ForegroundColor Yellow
Write-Host "  python scripts\force_event.py --event overspeed" -ForegroundColor Yellow
Write-Host "  python scripts\force_event.py --event watchlist_hit" -ForegroundColor Yellow
