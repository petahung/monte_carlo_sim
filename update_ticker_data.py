#!/usr/bin/env python3
"""
update_ticker_data.py — 任意標的歷史資料產生器
=================================================
從 Yahoo Finance 下載任意股票/指數/加密貨幣歷史價格，模擬 2x/3x 槓桿報酬，
產生與 index.html 手動載入相容的 CSV 檔案。

前置需求：
    pip install yfinance pandas

使用方式：
    python update_ticker_data.py --ticker NVDA
    python update_ticker_data.py --ticker ^GSPC --name "S&P 500"
    python update_ticker_data.py --ticker BTC-USD --name BTC --start 2014-01-01
    python update_ticker_data.py --ticker TSLA --er2 0.95 --er3 0.88 --rebuild

常用 Yahoo Finance 標的代碼：
    股票  : NVDA  TSLA  MSFT  AAPL  TSM（台積電 ADR）  2330.TW（台積電原股）
    指數  : ^GSPC（S&P 500）  ^IXIC（Nasdaq Comp）  ^DJI（Dow Jones）  ^NDX（NDX 100）
    加密  : BTC-USD  ETH-USD  SOL-USD
    債券  : TLT  IEF  HYG

輸入參數：
    --ticker     Yahoo Finance 標的代碼（必填）
    --name       顯示名稱（預設與 ticker 相同）
    --lev2-name  2x 版本顯示名稱（預設 "2x {name}"）
    --lev3-name  3x 版本顯示名稱（預設 "3x {name}"）
    --er2        2x 費用率 %/yr（預設 0.95，同 QLD）
    --er3        3x 費用率 %/yr（預設 0.88，同 TQQQ）
    --start      起始日期（預設 2000-01-01）
    --rebuild    忽略現有檔案，重新全量下載

輸出檔案：
    {ticker}_leveraged.csv   可直接用 index.html「手動載入」按鈕載入
                             載入後介面標籤自動切換為指定標的名稱

槓桿模擬說明：
    2x 淨報酬 = 2×r − rf*(1+借貸利差) − ER2/yr
    3x 淨報酬 = 3×r − 2×rf*(1+借貸利差) − ER3/yr
    借貸利差固定 2%/yr（同 NDX 設定）
    加密貨幣（-USD 結尾）自動採用 365 日年化；其他標的採用 252 交易日。
"""

import os, sys, argparse, re
from datetime import datetime, timedelta
import pandas as pd

DIR = os.path.dirname(os.path.abspath(__file__))

CRYPTO_SUFFIXES = ('-USD', '-EUR', '-GBP', '-USDT')

def is_crypto(ticker: str) -> bool:
    return any(ticker.upper().endswith(s) for s in CRYPTO_SUFFIXES)

def sanitize_filename(ticker: str) -> str:
    return re.sub(r'[^\w\-]', '_', ticker).lower()

# ── 資料抓取 ────────────────────────────────────────────────────

def fetch_yfinance(ticker, start, end):
    try:
        import yfinance as yf
        raw = yf.download(ticker, start=start, end=end,
                          auto_adjust=True, progress=False,
                          multi_level_index=False)
        if raw.empty:
            return None
        df = raw[['Close']].copy()
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df.columns = ['Price']
        df = df.dropna()           # 移除 NaN 價格（IPO 日、分割日、停牌日）
        df = df[df['Price'] > 0]   # 移除零或負值
        return df
    except Exception as e:
        print(f"  yfinance error: {e}")
        return None

def fetch_rf_rate(last_known=4.0):
    """抓取 ^IRX（13 週 T-Bill 年化 %）作為無風險利率。"""
    try:
        import yfinance as yf
        irx = yf.download('^IRX', period='5d', auto_adjust=True,
                          progress=False, multi_level_index=False)
        if not irx.empty:
            return float(irx['Close'].iloc[-1])
    except Exception:
        pass
    return last_known

# ── RF 插值 ─────────────────────────────────────────────────────

def build_rf_table(current_rf_pct: float) -> list:
    rf_candidates = [f for f in os.listdir(DIR) if '貨幣市場' in f and f.endswith('.csv')]
    anchor = []
    if rf_candidates:
        rf_df = pd.read_csv(os.path.join(DIR, rf_candidates[0]),
                            names=['yr', 'pct'], skiprows=1)
        for _, row in rf_df.iterrows():
            yr_str = str(row['yr']).strip()
            try:
                pct = float(row['pct'])
            except ValueError:
                continue
            if re.search(r'\(', yr_str):
                anchor.append((pd.Timestamp(datetime.now().date()), pct))
            else:
                try:
                    yr = int(yr_str[:4])
                    anchor.append((pd.Timestamp(f'{yr}-12-31'), pct))
                except ValueError:
                    pass
    anchor.append((pd.Timestamp(datetime.now().date()), current_rf_pct))
    anchor.sort(key=lambda x: x[0])
    return anchor

def make_interp(anchor: list):
    dates = [a[0] for a in anchor]
    pcts  = [a[1] for a in anchor]
    def interp(d):
        prev_i = next_i = None
        for i, ad in enumerate(dates):
            if ad <= d: prev_i = i
            elif next_i is None: next_i = i; break
        if prev_i is None: return pcts[0]
        if next_i is None: return pcts[prev_i]
        span = (dates[next_i] - dates[prev_i]).days
        frac = (d - dates[prev_i]).days / max(span, 1)
        return pcts[prev_i] + (pcts[next_i] - pcts[prev_i]) * frac
    return interp

# ── 計算槓桿報酬 ────────────────────────────────────────────────

COLS = [
    'Date', 'Asset_Close',
    'NDX_DailyReturn',
    'RF_Annual_Pct', 'RF_Daily_Pct',
    'NDX2L_DailyReturn', 'NDX3L_DailyReturn',
    'NDX2L_Adj_DailyReturn', 'NDX3L_Adj_DailyReturn',
    'NDX_Indexed', 'NDX2L', 'NDX3L', 'NDX2L_adj', 'NDX3L_adj',
]

def build_rows(price_df, interp_rf, cum_state, er2_daily, er3_daily, trading_days):
    rows = []
    cumBase  = cum_state['NDX_Indexed']
    cum2L    = cum_state['NDX2L']
    cum3L    = cum_state['NDX3L']
    cum2Ladj = cum_state['NDX2L_adj']
    cum3Ladj = cum_state['NDX3L_adj']
    prev_price = cum_state.get('prev_price')
    borrow_spread = 0.02 / trading_days

    for date, row in price_df.iterrows():
        price = float(row['Price'])
        r = (price - prev_price) / prev_price if (prev_price and prev_price > 0) else 0.0
        prev_price = price

        rf_ann   = interp_rf(date)
        rf_daily = rf_ann / trading_days / 100.0
        borrow   = rf_daily + borrow_spread

        cumBase  *= (1.0 + r)
        cum2L    *= (1.0 + 2.0 * r)
        cum3L    *= (1.0 + 3.0 * r)
        ret2adj   = 2.0 * r - borrow - er2_daily
        ret3adj   = 3.0 * r - 2.0 * borrow - er3_daily
        cum2Ladj *= (1.0 + ret2adj)
        cum3Ladj *= (1.0 + ret3adj)

        rows.append({
            'Date':                  date.strftime('%Y-%m-%d'),
            'Asset_Close':           round(price, 4),
            'NDX_DailyReturn':       round(r * 100.0, 4),
            'RF_Annual_Pct':         round(rf_ann, 4),
            'RF_Daily_Pct':          round(rf_daily * 100.0, 6),
            'NDX2L_DailyReturn':     round(2.0 * r * 100.0, 4),
            'NDX3L_DailyReturn':     round(3.0 * r * 100.0, 4),
            'NDX2L_Adj_DailyReturn': round(ret2adj * 100.0, 4),
            'NDX3L_Adj_DailyReturn': round(ret3adj * 100.0, 4),
            'NDX_Indexed':           round(cumBase,   6),
            'NDX2L':                 round(cum2L,    6),
            'NDX3L':                 round(cum3L,    6),
            'NDX2L_adj':             round(cum2Ladj, 6),
            'NDX3L_adj':             round(cum3Ladj, 6),
        })
    return rows

# ── Main ────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description='任意標的槓桿 CSV 產生器')
    ap.add_argument('--ticker',    required=True,
                    help='Yahoo Finance 標的代碼，例如 NVDA、^GSPC、BTC-USD')
    ap.add_argument('--name',      default=None,
                    help='顯示名稱（預設與 ticker 相同）')
    ap.add_argument('--lev2-name', default=None, dest='lev2_name',
                    help='2x 版本顯示名稱，預設 "2x {name}"')
    ap.add_argument('--lev3-name', default=None, dest='lev3_name',
                    help='3x 版本顯示名稱，預設 "3x {name}"')
    ap.add_argument('--er2',       type=float, default=0.95,
                    help='2x 費用率 %%/yr（預設 0.95，同 QLD）')
    ap.add_argument('--er3',       type=float, default=0.88,
                    help='3x 費用率 %%/yr（預設 0.88，同 TQQQ）')
    ap.add_argument('--start',     default='2000-01-01',
                    help='起始日期（全量下載用），預設 2000-01-01')
    ap.add_argument('--rebuild',   action='store_true',
                    help='忽略現有檔案，重新全量下載')
    args = ap.parse_args()

    ticker       = args.ticker
    display_name = args.name or ticker
    lev2_name    = args.lev2_name or f'2x {display_name}'
    lev3_name    = args.lev3_name or f'3x {display_name}'
    trading_days = 365 if is_crypto(ticker) else 252
    er2_daily    = args.er2 / 100.0 / trading_days
    er3_daily    = args.er3 / 100.0 / trading_days

    out_name  = sanitize_filename(ticker) + '_leveraged.csv'
    out_path  = os.path.join(DIR, out_name)
    today     = datetime.now().date()

    meta_line = (f'# ASSET={ticker},NAME={display_name},'
                 f'LEV2={lev2_name},LEV3={lev3_name}')

    # ── 判斷增量 or 全量 ──────────────────────────────────────
    existing = None
    if os.path.exists(out_path) and not args.rebuild:
        existing = pd.read_csv(out_path, comment='#')
        existing['Date'] = pd.to_datetime(existing['Date'])
        last_date  = existing['Date'].iloc[-1]
        fetch_from = (last_date + timedelta(days=1)).strftime('%Y-%m-%d')
        print(f"Existing {out_name}: "
              f"{existing['Date'].iloc[0].date()} ~ {last_date.date()} "
              f"({len(existing)} rows)")
        is_incremental = True
    else:
        fetch_from = args.start
        last_date  = None
        is_incremental = False
        print(f"Full download for {ticker} from {fetch_from}")

    if last_date and last_date.date() >= today:
        print("Already up to date.")
        return

    # ── 抓取價格 ─────────────────────────────────────────────
    fetch_end = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    print(f"Fetching {ticker}  {fetch_from} → {today} ...")
    prices = fetch_yfinance(ticker, fetch_from, fetch_end)
    if prices is None or prices.empty:
        print(f"無法取得 {ticker} 資料。請確認：")
        print(f"  1. ticker 代碼是否正確（可在 finance.yahoo.com 搜尋確認）")
        print(f"  2. 網路連線是否正常")
        print(f"  3. yfinance 版本：pip install --upgrade yfinance")
        sys.exit(1)

    prices = prices.sort_index()
    print(f"  Got {len(prices)} row(s): "
          f"{prices.index[0].date()} ~ {prices.index[-1].date()}")

    # ── 抓取 RF 利率 ─────────────────────────────────────────
    last_rf = float(existing['RF_Annual_Pct'].iloc[-1]) if existing is not None else 4.0
    print("Fetching RF rate (^IRX) ...")
    current_rf = fetch_rf_rate(last_known=last_rf)
    print(f"  RF: {current_rf:.4f}%/yr")

    anchor    = build_rf_table(current_rf)
    interp_rf = make_interp(anchor)

    # ── 計算累積狀態起點 ──────────────────────────────────────
    if is_incremental:
        last_row  = existing.iloc[-1]
        cum_state = {k: float(last_row[k])
                     for k in ['NDX_Indexed', 'NDX2L', 'NDX3L', 'NDX2L_adj', 'NDX3L_adj']}
        cum_state['prev_price'] = float(last_row['Asset_Close'])
    else:
        first_price = float(prices.iloc[0]['Price'])
        cum_state = {
            'NDX_Indexed': 100.0, 'NDX2L': 100.0, 'NDX3L': 100.0,
            'NDX2L_adj':   100.0, 'NDX3L_adj': 100.0,
            'prev_price':  first_price,
        }
        prices = prices.iloc[1:]  # 首行作為基準點，報酬=0

    # ── 建構新 rows ───────────────────────────────────────────
    new_rows = build_rows(prices, interp_rf, cum_state, er2_daily, er3_daily, trading_days)
    print(f"Computed {len(new_rows)} new row(s).")

    if not new_rows:
        print("No new rows.")
        return

    # ── 儲存 CSV ──────────────────────────────────────────────
    new_df = pd.DataFrame(new_rows, columns=COLS)
    if is_incremental:
        ex_str = existing.copy()
        ex_str['Date'] = ex_str['Date'].dt.strftime('%Y-%m-%d')
        for col in COLS:
            if col not in ex_str.columns:
                ex_str[col] = 0.0
        updated = pd.concat([ex_str[COLS], new_df], ignore_index=True)
    else:
        updated = new_df

    # 寫入：第一行為元資料注釋，之後為標準 CSV
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(meta_line + '\n')
        f.write(updated.to_csv(index=False))

    print(f"Saved {out_name}: {len(updated)} rows "
          f"(last: {new_rows[-1]['Date']})")

    # ── 摘要統計 ──────────────────────────────────────────────
    n_years = (pd.to_datetime(new_rows[-1]['Date']) -
               pd.to_datetime(updated['Date'].iloc[0])).days / 365.25
    last  = updated.iloc[-1]
    first = updated.iloc[0]

    def cagr(end, start, yrs):
        if float(start) <= 0 or yrs <= 0: return 0
        return (float(end) / float(start)) ** (1 / yrs) - 1

    pad = max(len(display_name), len(lev2_name), len(lev3_name))
    print(f"\n=== 摘要  {updated['Date'].iloc[0]} ~ {new_rows[-1]['Date']} "
          f"({n_years:.1f} 年) ===")
    print(f"  {display_name:<{pad}} (1x)      : "
          f"{float(last['NDX_Indexed']):>12.2f}  "
          f"CAGR {cagr(last['NDX_Indexed'], first['NDX_Indexed'], n_years)*100:.1f}%")
    print(f"  {lev2_name:<{pad}} (2x gross): "
          f"{float(last['NDX2L']):>12.2f}  "
          f"CAGR {cagr(last['NDX2L'], first['NDX2L'], n_years)*100:.1f}%")
    print(f"  {lev2_name:<{pad}} (2x net)  : "
          f"{float(last['NDX2L_adj']):>12.2f}  "
          f"CAGR {cagr(last['NDX2L_adj'], first['NDX2L_adj'], n_years)*100:.1f}%")
    print(f"  {lev3_name:<{pad}} (3x gross): "
          f"{float(last['NDX3L']):>12.2f}  "
          f"CAGR {cagr(last['NDX3L'], first['NDX3L'], n_years)*100:.1f}%")
    print(f"  {lev3_name:<{pad}} (3x net)  : "
          f"{float(last['NDX3L_adj']):>12.2f}  "
          f"CAGR {cagr(last['NDX3L_adj'], first['NDX3L_adj'], n_years)*100:.1f}%")

    print(f"\n✓ Done. 請用 index.html「手動載入」按鈕載入 {out_name}")
    print(f"  載入後介面標籤將自動切換為「{display_name}」相關顯示。")

if __name__ == '__main__':
    main()
