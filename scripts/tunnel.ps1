# scripts/tunnel.ps1
# Free Zero-Config HTTPS Tunnel for NETRA Smartphone Live Camera Scanner
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  NETRA // FREE PUBLIC HTTPS TUNNEL FOR SMARTPHONE PAIRING " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "[*] Launching free Cloudflare Quick Tunnel on port 3000..." -ForegroundColor Yellow
Write-Host "[*] Mobile browsers (Chrome/Safari) require HTTPS for camera access." -ForegroundColor Gray
Write-Host "[*] No signup or account required. You will receive a live public HTTPS URL." -ForegroundColor Gray
Write-Host ""

# Run cloudflared or localtunnel
npx.cmd -y cloudflared tunnel --url http://localhost:3000
