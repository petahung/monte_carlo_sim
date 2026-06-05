# download_etf.ps1
#
# 功能：從 Yahoo Finance 下載 QLD（2x NDX ETF）和 TQQQ（3x NDX ETF）
#       的完整歷史調整後收盤價，儲存為 CSV，供 compare_leverage.ps1 使用。
#
# 前置需求：
#   - Windows PowerShell 5.1+ 或 PowerShell 7+
#   - 網路連線（連接 Yahoo Finance API）
#
# 使用方式：
#   cd C:\path\to\monte_carlo_ndx
#   .\download_etf.ps1
#
# 輸出檔案：
#   QLD.csv    QLD 每日調整後收盤價（自 2006-06-22 起）
#   TQQQ.csv   TQQQ 每日調整後收盤價（自 2010-02-12 起）
#
$dir = Split-Path $MyInvocation.MyCommand.Path

$sv = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$sv.UserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"

$now = [int][double]::Parse(([datetime]::UtcNow - [datetime]"1970-01-01").TotalSeconds)

function Download-ETF {
    param($ticker, $outFile)
    $url = "https://query2.finance.yahoo.com/v8/finance/chart/$ticker" +
           "?period1=0&period2=$now&interval=1d&events=history&includeAdjustedClose=true"
    $r   = Invoke-WebRequest -Uri $url -WebSession $sv -UseBasicParsing
    $j   = $r.Content | ConvertFrom-Json
    $res = $j.chart.result[0]

    $timestamps  = $res.timestamp
    $adjClose    = $res.indicators.adjclose[0].adjclose
    $close       = $res.indicators.quote[0].close

    $rows = @()
    for ($i = 0; $i -lt $timestamps.Count; $i++) {
        $dt = [System.DateTimeOffset]::FromUnixTimeSeconds($timestamps[$i]).UtcDateTime
        $ac = $adjClose[$i]
        $cl = $close[$i]
        if ($null -ne $ac -and $ac -ne 0) {
            $rows += [PSCustomObject]@{
                Date     = $dt.ToString("yyyy-MM-dd")
                Close    = [math]::Round($cl, 4)
                AdjClose = [math]::Round($ac, 4)
            }
        }
    }
    $rows | Export-Csv $outFile -NoTypeInformation -Encoding UTF8
    Write-Host ("Downloaded {0}: {1} days -> {2}" -f $ticker, $rows.Count, $outFile)
}

Download-ETF "QLD"  (Join-Path $dir "QLD.csv")
Download-ETF "TQQQ" (Join-Path $dir "TQQQ.csv")
