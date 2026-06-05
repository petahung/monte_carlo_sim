# compare_leverage.ps1
#
# 功能：比較模擬的 NDX2L/NDX3L 與實際 ETF（QLD/TQQQ）的歷史報酬，
#       驗證模擬數據的準確性，輸出相關係數、CAGR 誤差等統計。
#
# 前置需求：
#   - Windows PowerShell 5.1+ 或 PowerShell 7+
#   - 已執行 download_etf.ps1（產生 QLD.csv、TQQQ.csv）
#   - 已執行 build_leveraged_etf.ps1 或 update_data.py（產生 ndx_leveraged.csv）
#
# 使用方式：
#   cd C:\path\to\monte_carlo_ndx
#   .\download_etf.ps1        # 先下載實際 ETF 資料（若尚未下載）
#   .\compare_leverage.ps1    # 執行比較
#
# 輸入檔案：
#   ndx_leveraged.csv   模擬的槓桿指數資料
#   QLD.csv             QLD 實際歷史收盤價
#   TQQQ.csv            TQQQ 實際歷史收盤價
#
# 輸出：
#   終端機印出逐年 CAGR 比對、相關係數、累積誤差等統計
#   compare_NDX2L_vs_QLD.csv    每日對照表
#   compare_NDX3L_vs_TQQQ.csv   每日對照表
#
$scriptDir = Split-Path $MyInvocation.MyCommand.Path
$root      = Split-Path $scriptDir
$dir       = Join-Path $root "data"

# ── Load simulation data ───────────────────────────────────────
$sim = @{}
foreach ($row in (Import-Csv (Join-Path $dir "ndx_leveraged.csv"))) {
    $sim[$row.Date] = $row
}
Write-Host ("Simulation rows: {0}" -f $sim.Count)

# ── Load actual ETF adj-close and compute daily returns ────────
function Load-DailyRet {
    param([string]$file)
    $raw = @(Import-Csv $file | Sort-Object { [datetime]$_.Date })
    $out = @()
    for ($i = 1; $i -lt $raw.Length; $i++) {
        $prev = [double]$raw[$i-1].AdjClose
        $curr = [double]$raw[$i].AdjClose
        if ($prev -gt 0) {
            $out += [PSCustomObject]@{
                Date     = [string]$raw[$i].Date
                AdjClose = $curr
                DailyRet = ($curr - $prev) / $prev * 100.0
            }
        }
    }
    return $out
}

# ── Compare one ETF vs two simulation columns ─────────────────
function Compare-One {
    param(
        [string]$etfFile,
        [string]$simRetCol,      # e.g. NDX2L_DailyReturn
        [string]$simRetAdjCol,   # e.g. NDX2L_Adj_DailyReturn
        [string]$simIdxCol,      # e.g. NDX2L
        [string]$simIdxAdjCol,   # e.g. NDX2L_adj
        [string]$label
    )
    $etfData = Load-DailyRet $etfFile
    Write-Host ("`nLoaded {0}: {1} days" -f (Split-Path $etfFile -Leaf), $etfData.Length)

    # Inner join on date
    $dates=@(); $eRet=@(); $sGRet=@(); $sARet=@()
    $eIdx=100.0; $sGIdx=100.0; $sAIdx=100.0
    $eIdxArr=@(); $sGIdxArr=@(); $sAIdxArr=@()

    foreach ($row in $etfData) {
        $d = $row.Date
        if ($sim.ContainsKey($d)) {
            $er = $row.DailyRet
            $sg = [double]$sim[$d].$simRetCol
            $sa = [double]$sim[$d].$simRetAdjCol
            $eIdx  *= (1.0 + $er / 100.0)
            $sGIdx *= (1.0 + $sg / 100.0)
            $sAIdx *= (1.0 + $sa / 100.0)
            $dates    += $d
            $eRet     += $er
            $sGRet    += $sg
            $sARet    += $sa
            $eIdxArr  += $eIdx
            $sGIdxArr += $sGIdx
            $sAIdxArr += $sAIdx
        }
    }
    $n = $dates.Count
    if ($n -lt 2) { Write-Host "No overlapping dates"; return }

    $ny = ([datetime]$dates[$n-1] - [datetime]$dates[0]).TotalDays / 365.25

    function CAGR3($v, $y) { ([math]::Pow($v/100.0, 1.0/$y) - 1.0)*100.0 }

    # Correlation gross
    $meanE=[double]0; $meanG=[double]0; $meanA=[double]0
    foreach ($v in $eRet)  { $meanE += $v }; $meanE /= $n
    foreach ($v in $sGRet) { $meanG += $v }; $meanG /= $n
    foreach ($v in $sARet) { $meanA += $v }; $meanA /= $n
    $covG=0.0; $varE=0.0; $varG=0.0; $covA=0.0; $varA=0.0
    for ($i=0; $i -lt $n; $i++) {
        $de=$eRet[$i]-$meanE; $dg=$sGRet[$i]-$meanG; $da=$sARet[$i]-$meanA
        $covG += $de*$dg; $varE += $de*$de; $varG += $dg*$dg
        $covA += $de*$da; $varA += $da*$da
    }
    $corrG = $covG/[math]::Sqrt($varE*$varG)
    $corrA = $covA/[math]::Sqrt($varE*$varA)

    # Daily diff stats gross vs adj
    $diffG=[double[]]($eRet | ForEach-Object { $_ - $sGRet[[array]::IndexOf($eRet,$_)] })
    $diffA=[double[]]($eRet | ForEach-Object { $_ - $sARet[[array]::IndexOf($eRet,$_)] })
    # Simpler: compute inline
    $diffGArr=@(); $diffAArr=@()
    for ($i=0; $i -lt $n; $i++) { $diffGArr += ($eRet[$i]-$sGRet[$i]); $diffAArr += ($eRet[$i]-$sARet[$i]) }
    $meanDiffG=($diffGArr|Measure-Object -Average).Average
    $meanDiffA=($diffAArr|Measure-Object -Average).Average
    $ssG=0.0; $ssA=0.0
    foreach ($v in $diffGArr) { $ssG+=($v-$meanDiffG)*($v-$meanDiffG) }
    foreach ($v in $diffAArr) { $ssA+=($v-$meanDiffA)*($v-$meanDiffA) }
    $teG=[math]::Sqrt($ssG/($n-1))*[math]::Sqrt(252)
    $teA=[math]::Sqrt($ssA/($n-1))*[math]::Sqrt(252)

    Write-Host ""
    Write-Host ("=========================================")
    Write-Host ("  {0}" -f $label)
    Write-Host ("  {0} ~ {1}  ({2} days, {3:F1} yrs)" -f $dates[0],$dates[$n-1],$n,[math]::Round($ny,1))
    Write-Host ("=========================================")
    Write-Host ("  Final index (base=100):")
    Write-Host ("    ETF actual  : {0,12:F2}   CAGR {1:F2}%" -f $eIdxArr[$n-1],  (CAGR3 $eIdxArr[$n-1]  $ny))
    Write-Host ("    Sim gross   : {0,12:F2}   CAGR {1:F2}%" -f $sGIdxArr[$n-1], (CAGR3 $sGIdxArr[$n-1] $ny))
    Write-Host ("    Sim adj(net): {0,12:F2}   CAGR {1:F2}%" -f $sAIdxArr[$n-1], (CAGR3 $sAIdxArr[$n-1] $ny))
    Write-Host ("  Daily return correlation:")
    Write-Host ("    ETF vs Gross: {0:F6}   Ann.TrackErr {1:F4}%" -f $corrG, $teG)
    Write-Host ("    ETF vs Adj  : {0:F6}   Ann.TrackErr {1:F4}%" -f $corrA, $teA)
    Write-Host ("  Avg daily diff (ETF - Sim):")
    Write-Host ("    vs Gross: {0:F4}%/day   =>  {1:F2}%/yr drag" -f $meanDiffG, ($meanDiffG*252))
    Write-Host ("    vs Adj  : {0:F4}%/day   =>  {1:F2}%/yr residual" -f $meanDiffA, ($meanDiffA*252))

    # Save comparison CSV
    $outCSV = @()
    for ($i=0; $i -lt $n; $i++) {
        $outCSV += [PSCustomObject]@{
            Date     = $dates[$i]
            ETF_Idx  = [math]::Round($eIdxArr[$i],  4)
            Sim_Gross= [math]::Round($sGIdxArr[$i], 4)
            Sim_Adj  = [math]::Round($sAIdxArr[$i], 4)
        }
    }
    $csvName = "compare_" + $label.Replace(" ","_").Replace("(","").Replace(")","") + ".csv"
    $outCSV | Export-Csv (Join-Path $dir $csvName) -NoTypeInformation -Encoding UTF8
    Write-Host ("  Saved: {0}" -f $csvName)
}

Compare-One (Join-Path $dir "QLD.csv")  "NDX2L_DailyReturn" "NDX2L_Adj_DailyReturn" "NDX2L" "NDX2L_adj" "NDX2L vs QLD"
Compare-One (Join-Path $dir "TQQQ.csv") "NDX3L_DailyReturn" "NDX3L_Adj_DailyReturn" "NDX3L" "NDX3L_adj" "NDX3L vs TQQQ"
