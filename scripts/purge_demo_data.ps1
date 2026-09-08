# scripts/purge_demo_data.ps1
# Purges all seeded demo sightings, demo alerts, demo criminals, and demo watchlist entries
Write-Host "============================================================" -ForegroundColor Red
Write-Host "  NETRA // PURGE ALL DEMO DATA & CLEAN SLATE INITIALIZER   " -ForegroundColor Red
Write-Host "============================================================" -ForegroundColor Red
Write-Host ""
Write-Host "[*] Contacting NETRA Spatio-Temporal API on port 8000..." -ForegroundColor Yellow

try {
    $res = Invoke-RestMethod -Uri "http://127.0.0.1:8000/v1/rules/purge-demo" -Method Post -ContentType "application/json"
    Write-Host "[OK] Clean slate activated successfully!" -ForegroundColor Green
    Write-Host "     Message: $($res.message)" -ForegroundColor Gray
    Write-Host "     Sightings Cleared: $($res.purged.sightings)" -ForegroundColor Cyan
    Write-Host "     Alerts Cleared:    $($res.purged.alerts)" -ForegroundColor Cyan
    Write-Host "     Criminals Cleared: $($res.purged.criminals)" -ForegroundColor Cyan
    Write-Host "     Watchlist Cleared: $($res.purged.watchlist)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "[*] Surveillance camera grid remains online and ready for 100% real live camera and mobile data." -ForegroundColor Green
} catch {
    Write-Host "[!] Error connecting to API: $_" -ForegroundColor Red
}
