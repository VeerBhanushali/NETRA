# scripts/demo_down.ps1
# Shuts down any running python uvicorn or node next.js background servers on :8000 and :3000

Write-Host "[*] Stopping NETRA processes..." -ForegroundColor Yellow

# Terminate process on port 8000
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "[✓] Terminated process on port 8000 (FastAPI)" -ForegroundColor Green
}

# Terminate process on port 3000
Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    Write-Host "[✓] Terminated process on port 3000 (Next.js)" -ForegroundColor Green
}

Write-Host "[✓] NETRA stack stopped." -ForegroundColor Cyan
