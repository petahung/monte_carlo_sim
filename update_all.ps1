$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "========================================"
Write-Host "  Monte Carlo NDX - Update All Data"
Write-Host "========================================"
Write-Host ""

$steps = @(
    @{ label = "[1/8] NDX Nasdaq 100 (embed index.html)";  cmd = "python scripts/update_data.py" },
    @{ label = "[2/8] S&P500 TR  (^SP500TR)";              cmd = 'python scripts/update_ticker_data.py --ticker ^SP500TR  --name "S&P500 TR"  --lev2-name "2x S&P500 TR"  --lev3-name "3x S&P500 TR"' },
    @{ label = "[3/8] TSLA";                               cmd = "python scripts/update_ticker_data.py --ticker TSLA" },
    @{ label = "[4/8] NVDA";                               cmd = "python scripts/update_ticker_data.py --ticker NVDA" },
    @{ label = "[5/8] TAIEX TR   (IR0001.TW)";            cmd = 'python scripts/update_ticker_data.py --ticker IR0001.TW --name "TAIEX TR" --lev2-name "2x TAIEX TR" --lev3-name "3x TAIEX TR"' },
    @{ label = "[6/8] Bitcoin    (BTC-USD)";               cmd = 'python scripts/update_ticker_data.py --ticker BTC-USD   --name "bitcoin"   --lev2-name "2x bitcoin"   --lev3-name "3x bitcoin"' },
    @{ label = "[7/8] VT";                                 cmd = "python scripts/update_ticker_data.py --ticker VT" },
    @{ label = "[8/8] 元大台50 TR (0050.TW)";              cmd = 'python scripts/update_ticker_data.py --ticker 0050.TW --name "元大台50 TR" --lev2-name "2x 元大台50 TR" --lev3-name "3x 元大台50 TR" --total-return' }
)

$failed = @()

foreach ($step in $steps) {
    Write-Host $step.label -ForegroundColor Yellow
    Write-Host ("  > " + $step.cmd) -ForegroundColor DarkGray
    try {
        Invoke-Expression $step.cmd
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "exit $LASTEXITCODE" }
        Write-Host "  OK`n" -ForegroundColor Green
    } catch {
        Write-Host ("  FAILED: " + $_) -ForegroundColor Red
        Write-Host ""
        $failed += $step.label
    }
}

Write-Host "========================================"
if ($failed.Count -eq 0) {
    Write-Host "All done!" -ForegroundColor Green
} else {
    Write-Host "Failed steps:" -ForegroundColor Red
    $failed | ForEach-Object { Write-Host ("  * " + $_) -ForegroundColor Red }
}
Write-Host ""

$ans = Read-Host "Git commit & push? (y/N)"
if ($ans -match '^[Yy]') {
    $today = Get-Date -Format "yyyy-MM-dd"
    git add data/ index.html
    git commit -m "chore: update all preset data to $today"
    git push origin main
    Write-Host "Pushed to GitHub Pages." -ForegroundColor Green
} else {
    Write-Host "Skipped git push." -ForegroundColor Gray
}
Write-Host ""
