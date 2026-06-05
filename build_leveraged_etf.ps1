# build_leveraged_etf.ps1
#
# 功能：從 Investing.com 下載的 NDX 原始 CSV，結合無風險利率資料，
#       產生包含模擬 2x/3x 槓桿指數的 ndx_leveraged.csv。
#
# 前置需求：
#   - Windows PowerShell 5.1+ 或 PowerShell 7+
#
# 使用方式：
#   cd C:\path\to\monte_carlo_ndx
#   .\build_leveraged_etf.ps1
#
# 輸入檔案：
#   Nasdaq 100 Historical Data*.csv   從 Investing.com 手動下載的原始 NDX 價格資料
#   美元貨幣市場基金利率趨勢.csv        無風險利率年度錨點（用於插值計算每日 RF）
#
# 輸出檔案：
#   ndx_leveraged.csv   包含 NDX、NDX2L、NDX3L 每日報酬與累積指數（含費用率調整）
#
# 欄位說明：
#   NDX2L_Adj / NDX3L_Adj  扣除借貸成本（RF + 2%）與費用率後的淨報酬
#   RF_Annual_Pct           當日年化無風險利率（線性插值自年底錨點）
#
# 注意：日常更新請改用 update_data.py（自動抓取，無需手動下載）
#
$dir = Split-Path $MyInvocation.MyCommand.Path

# ── 1. Load NDX data ──────────────────────────────────────────
$csvFiles = Get-ChildItem $dir -Filter "Nasdaq 100 Historical Data*.csv"
$allRows  = @()
foreach ($f in $csvFiles) { $allRows += Import-Csv $f.FullName }

$parsed = @()
foreach ($row in $allRows) {
    $ds = $row."Date".Trim('"')
    $cs = $row."Change %".Trim('"').Replace('%','').Trim()
    $ps = $row."Price".Trim('"').Replace(',','').Trim()
    try {
        $d = [datetime]::ParseExact($ds, "MM/dd/yyyy",
                 [System.Globalization.CultureInfo]::InvariantCulture)
        $parsed += [PSCustomObject]@{
            Date   = $d
            Price  = [double]$ps
            Change = [double]$cs / 100.0
        }
    } catch { }
}

$seen   = @{}
$unique = @()
foreach ($row in ($parsed | Sort-Object Date)) {
    $k = $row.Date.ToString("yyyyMMdd")
    if (-not $seen.ContainsKey($k)) { $seen[$k] = $true; $unique += $row }
}
$N = $unique.Count
Write-Host ("NDX trading days: {0}  ({1} ~ {2})" -f $N,
    $unique[0].Date.ToString("yyyy-MM-dd"), $unique[$N-1].Date.ToString("yyyy-MM-dd"))

# ── 2. Load money-market anchor rates (year-end %) ────────────
$mmFile = Get-ChildItem $dir -Filter "*.csv" |
    Where-Object { $_.Name -notlike "Nasdaq*" -and $_.Name -notlike "QLD*" -and
                   $_.Name -notlike "TQQQ*"   -and $_.Name -notlike "ndx_*" -and
                   $_.Name -notlike "compare_*" } |
    Select-Object -First 1
$mmLines = [System.IO.File]::ReadAllLines($mmFile.FullName, [System.Text.Encoding]::UTF8)

$anchorDates = @()
$anchorPcts  = @()
foreach ($line in ($mmLines | Select-Object -Skip 1)) {
    $parts = $line.Trim() -split ","
    if ($parts.Count -lt 2) { continue }
    $yr  = $parts[0].Trim().Trim('"')
    $pct = [double]$parts[1].Trim().Trim('"')
    if ($yr -match "(\d{4})") {
        $yearNum = [int]$Matches[1]
        if ($yr -match "Apr") { $aDate = $unique[$N-1].Date }
        else                  { $aDate = [datetime]("$yearNum-12-31") }
        $anchorDates += $aDate
        $anchorPcts  += $pct
    }
}
# Sort by date
$sortIdx   = 0..($anchorDates.Count-1) | Sort-Object { $anchorDates[$_] }
$anchorDates = $sortIdx | ForEach-Object { $anchorDates[$_] }
$anchorPcts  = $sortIdx | ForEach-Object { $anchorPcts[$_]  }
$nA = $anchorDates.Count
Write-Host ("Anchor points: {0}  first={1:F2}%  last={2:F2}%" -f $nA, $anchorPcts[0], $anchorPcts[$nA-1])

# ── 3. Precompute daily RF for each trading day (inline interp) ──
$rfAnnual = New-Object double[] $N
$rfDaily  = New-Object double[] $N

for ($i = 0; $i -lt $N; $i++) {
    $d = $unique[$i].Date
    # Binary linear interpolation between anchor points
    $prev = -1; $next = -1
    for ($j = 0; $j -lt $nA; $j++) {
        if ($anchorDates[$j] -le $d) { $prev = $j }
        elseif ($next -lt 0)         { $next = $j }
    }
    if ($prev -lt 0) {
        $ann = $anchorPcts[0]
    } elseif ($next -lt 0) {
        $ann = $anchorPcts[$prev]
    } else {
        $span = ($anchorDates[$next] - $anchorDates[$prev]).TotalDays
        $frac = ($d - $anchorDates[$prev]).TotalDays / $span
        $ann  = $anchorPcts[$prev] + ($anchorPcts[$next] - $anchorPcts[$prev]) * $frac
    }
    $rfAnnual[$i] = $ann
    $rfDaily[$i]  = $ann / 252.0 / 100.0   # decimal per trading day
}
Write-Host ("RF precomputed. day[0]={0:F4}%/yr  day[5000]={1:F4}%/yr  day[N-1]={2:F4}%/yr" -f `
    $rfAnnual[0], $rfAnnual[5000], $rfAnnual[$N-1])

# ── 4. Expense ratios (daily fraction) ────────────────────────
$ER2 = 0.0095 / 252.0    # QLD  0.95%/yr
$ER3 = 0.0088 / 252.0    # TQQQ 0.88%/yr

# ── 5. Accumulate indices ──────────────────────────────────────
$BASE     = 100.0
$cumNDX   = $BASE
$cum2L    = $BASE
$cum3L    = $BASE
$cum2Ladj = $BASE
$cum3Ladj = $BASE

$rows = New-Object object[] $N
for ($i = 0; $i -lt $N; $i++) {
    $r   = $unique[$i].Change      # daily return (decimal)
    $rf  = $rfDaily[$i]            # daily RF (decimal)
    $ann = $rfAnnual[$i]           # annual RF (%)

    $cumNDX   *= (1.0 + $r)
    $cum2L    *= (1.0 + 2.0 * $r)
    $cum3L    *= (1.0 + 3.0 * $r)

    $ret2adj   = 2.0 * $r - $rf - $ER2
    $ret3adj   = 3.0 * $r - 2.0 * $rf - $ER3
    $cum2Ladj *= (1.0 + $ret2adj)
    $cum3Ladj *= (1.0 + $ret3adj)

    $rows[$i] = [PSCustomObject]@{
        Date                  = $unique[$i].Date.ToString("yyyy-MM-dd")
        NDX_Close             = [math]::Round($unique[$i].Price, 2)
        NDX_DailyReturn       = [math]::Round($r * 100.0, 4)
        RF_Annual_Pct         = [math]::Round($ann, 4)
        RF_Daily_Pct          = [math]::Round($rf * 100.0, 6)
        NDX2L_DailyReturn     = [math]::Round(2.0 * $r * 100.0, 4)
        NDX3L_DailyReturn     = [math]::Round(3.0 * $r * 100.0, 4)
        NDX2L_Adj_DailyReturn = [math]::Round($ret2adj * 100.0, 4)
        NDX3L_Adj_DailyReturn = [math]::Round($ret3adj * 100.0, 4)
        NDX_Indexed           = [math]::Round($cumNDX,   6)
        NDX2L                 = [math]::Round($cum2L,    6)
        NDX3L                 = [math]::Round($cum3L,    6)
        NDX2L_adj             = [math]::Round($cum2Ladj, 6)
        NDX3L_adj             = [math]::Round($cum3Ladj, 6)
    }
}

# ── 6. Export CSV ─────────────────────────────────────────────
$outPath = Join-Path $dir "ndx_leveraged.csv"
$rows | Export-Csv $outPath -NoTypeInformation -Encoding UTF8
Write-Host ("Saved: {0}  ({1} rows)" -f $outPath, $rows.Length)

# ── 7. Summary ────────────────────────────────────────────────
$last   = $rows[$N-1]
$first  = $rows[0]
$nYears = ($unique[$N-1].Date - $unique[0].Date).TotalDays / 365.25

function CAGR2($v, $y) {
    if ($v -le 0 -or $y -le 0) { return 0 }
    ([math]::Pow($v / $BASE, 1.0 / $y) - 1.0) * 100.0
}

Write-Host ""
Write-Host ("=== Summary {0} ~ {1}  ({2:F1} yrs) ===" -f $first.Date, $last.Date, $nYears)
Write-Host ("NDX      (1x)        : {0,12:F2}  CAGR {1:F2}%" -f ([double]$last.NDX_Indexed), (CAGR2 ([double]$last.NDX_Indexed) $nYears))
Write-Host ("NDX2L    (2x, gross) : {0,12:F2}  CAGR {1:F2}%" -f ([double]$last.NDX2L),       (CAGR2 ([double]$last.NDX2L)       $nYears))
Write-Host ("NDX2L_adj(2x, net)   : {0,12:F2}  CAGR {1:F2}%" -f ([double]$last.NDX2L_adj),   (CAGR2 ([double]$last.NDX2L_adj)   $nYears))
Write-Host ("NDX3L    (3x, gross) : {0,12:F2}  CAGR {1:F2}%" -f ([double]$last.NDX3L),       (CAGR2 ([double]$last.NDX3L)       $nYears))
Write-Host ("NDX3L_adj(3x, net)   : {0,12:F2}  CAGR {1:F2}%" -f ([double]$last.NDX3L_adj),   (CAGR2 ([double]$last.NDX3L_adj)   $nYears))

# Sub-period CAGR for QLD / TQQQ comparison
$qldStart  = "2006-06-22"
$tqqqStart = "2010-02-12"

function PeriodCAGR($allRows, $col, $startDate) {
    $sub = @($allRows | Where-Object { $_.Date -ge $startDate })
    if ($sub.Count -lt 2) { return 0 }
    $v0 = [double]$sub[0].$col
    $v1 = [double]$sub[$sub.Count-1].$col
    $ny = ([datetime]$sub[$sub.Count-1].Date - [datetime]$sub[0].Date).TotalDays / 365.25
    ([math]::Pow($v1/$v0, 1.0/$ny) - 1.0) * 100.0
}

Write-Host ""
Write-Host ("--- QLD period ({0} ~) ---" -f $qldStart)
Write-Host ("NDX2L    gross CAGR : {0:F2}%" -f (PeriodCAGR $rows "NDX2L"     $qldStart))
Write-Host ("NDX2L_adj  net CAGR : {0:F2}%  (QLD actual ~24.84%%)" -f (PeriodCAGR $rows "NDX2L_adj" $qldStart))

Write-Host ""
Write-Host ("--- TQQQ period ({0} ~) ---" -f $tqqqStart)
Write-Host ("NDX3L    gross CAGR : {0:F2}%" -f (PeriodCAGR $rows "NDX3L"     $tqqqStart))
Write-Host ("NDX3L_adj  net CAGR : {0:F2}%  (TQQQ actual ~42.32%%)" -f (PeriodCAGR $rows "NDX3L_adj" $tqqqStart))
