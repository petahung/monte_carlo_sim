#!/usr/bin/env python3
"""
update_btc_data.py — BTC 歷史資料產生器
========================================
從 Yahoo Finance 下載 BTC-USD 歷史價格，模擬 2x/3x 槓桿報酬，
產生與 index.html 手動載入相容的 btc_leveraged.csv。

前置需求：
    pip install yfinance pandas

使用方式：
    python update_btc_data.py              # 全量下載（首次）
    python update_btc_data.py              # 增量更新（已有 btc_leveraged.csv 時）
    python update_btc_data.py --from 2020-01-01  # 指定起始日期

輸出檔案：
    btc_leveraged.csv   可直接用 index.html「手動載入」按鈕載入

欄位說明（與 ndx_leveraged.csv 相容）：
    NDX_DailyReturn       BTC 每日報酬率（%）
    NDX2L_Adj_DailyReturn 模擬 2x BTC 每日淨報酬（扣借貸成本＋費用率）
    NDX3L_Adj_DailyReturn 模擬 3x BTC 每日淨報酬（扣借貸成本＋費用率）
    RF_Daily_Pct          每日無風險利率（%，年化/365）

槓桿模擬說明：
    BTC 全年 365 日交易（非 252 個交易日）
    2x 淨報酬 = 2×r − rf − ER2   (ER2 = BITX 費用率 1.85%/yr)
    3x 淨報酬 = 3×r − 2×rf − ER3 (ER3 = 理論 3x 費用率 1.50%/yr，無主流商品)
    借貸利差 = +2%/yr（同 NDX 模擬設定）

注意：3x BTC 目前無主流 ETF，此為理論模擬值，風險極高。
"""

import os, sys, argparse, io, ssl
from datetime import datetime, timedelta
import urllib.request
import pandas as pd

DIR = os.path.dirname(os.path.abspath(__file__))

# ── 費用率（每日） ─────────────────────────────────────────────
ER2   = 0.0185 / 365.0   # BITX 1.85%/yr
ER3   = 0.0150 / 365.0   # 理論 3x，無主流商品
BORROW_SPREAD = 0.02 / 365.0   # 借貸利差 2%/yr（每日）

COLS = [
    'Date', 'BTC_Close',
    'NDX_DailyReturn',            # BTC 1x 每日報酬（%）
    'RF_Annual_Pct', 'RF_Daily_Pct',
    'NDX2L_DailyReturn',          # BTC 2x 粗報酬（%）
    'NDX3L_DailyReturn',          # BTC 3x 粗報酬（%）
    'NDX2L_Adj_DailyReturn',      # BTC 2x 淨報酬（%，扣費用＋借貸）
    'NDX3L_Adj_DailyReturn',      # BTC 3x 淨報酬（%，扣費用＋借貸）
    'NDX_Indexed',                # BTC 累積指數（基準=100）
    'NDX2L',                      # 2x 粗累積指數
    'NDX3L',                      # 3x 粗累積指數
    'NDX2L_adj',                  # 2x 淨累積指數
    'NDX3L_adj',                  # 3x 淨累積指數
]

# ── 資料抓取 ───────────────────────────────────────────────────

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
        df = df.dropna()
        df = df[df['Price'] > 0]
        return df
    except Exception as e:
        print(f"  yfinance error: {e}")
        return None

def fetch_stooq(ticker, start, end):
    try:
        d1 = start.replace('-', '')
        d2 = end.replace('-', '')
        url = f'https://stooq.com/q/d/l/?s={ticker}&d1={d1}&d2={d2}&i=d'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            text = r.read().decode()
        df = pd.read_csv(io.StringIO(text), parse_dates=['Date'])
        if df.empty or 'Close' not in df.columns:
            return None
        df = df[['Date', 'Close']].rename(columns={'Close': 'Price'}).set_index('Date')
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df
    except Exception as e:
        print(f"  Stooq error: {e}")
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

# ── 建構 RF 插值器 ─────────────────────────────────────────────

def build_rf_table(current_rf_pct: float) -> list:
    """
    使用與 NDX 相同的 RF 錨點，回傳 [(date, pct), ...] 排序列表。
    BTC 使用 365 日換算每日 RF（而非 252）。
    """
    rf_candidates = [f for f in os.listdir(DIR) if '貨幣市場' in f and f.endswith('.csv')]
    anchor = []
    if rf_candidates:
        import re
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
    # 加入最新利率
    anchor.append((pd.Timestamp(datetime.now().date()), current_rf_pct))
    anchor.sort(key=lambda x: x[0])
    return anchor

def make_interp(anchor: list):
    dates = [a[0] for a in anchor]
    pcts  = [a[1] for a in anchor]
    def interp(d):
        prev_i = next_i = None
        for i, ad in enumerate(dates):
            if ad <= d:
                prev_i = i
            elif next_i is None:
                next_i = i
                break
        if prev_i is None: return pcts[0]
        if next_i is None: return pcts[prev_i]
        span = (dates[next_i] - dates[prev_i]).days
        frac = (d - dates[prev_i]).days / max(span, 1)
        return pcts[prev_i] + (pcts[next_i] - pcts[prev_i]) * frac
    return interp

# ── 計算槓桿報酬並建構 rows ────────────────────────────────────

def build_rows(price_df: pd.DataFrame, interp_rf, cum_state: dict) -> list:
    rows = []
    cumBTC   = cum_state['NDX_Indexed']
    cum2L    = cum_state['NDX2L']
    cum3L    = cum_state['NDX3L']
    cum2Ladj = cum_state['NDX2L_adj']
    cum3Ladj = cum_state['NDX3L_adj']

    prev_price = cum_state.get('prev_price', None)

    for date, row in price_df.iterrows():
        price = float(row['Price'])

        # 計算每日報酬
        if prev_price is not None and prev_price > 0:
            r = (price - prev_price) / prev_price
        else:
            r = 0.0
        prev_price = price

        rf_ann = interp_rf(date)
        # BTC 全年 365 天交易
        rf_daily = rf_ann / 365.0 / 100.0
        borrow   = rf_daily + BORROW_SPREAD

        cumBTC   *= (1.0 + r)
        cum2L    *= (1.0 + 2.0 * r)
        cum3L    *= (1.0 + 3.0 * r)
        ret2adj   = 2.0 * r - borrow - ER2
        ret3adj   = 3.0 * r - 2.0 * borrow - ER3
        cum2Ladj *= (1.0 + ret2adj)
        cum3Ladj *= (1.0 + ret3adj)

        rows.append({
            'Date':                  date.strftime('%Y-%m-%d'),
            'BTC_Close':             round(price, 2),
            'NDX_DailyReturn':       round(r * 100.0, 4),
            'RF_Annual_Pct':         round(rf_ann, 4),
            'RF_Daily_Pct':          round(rf_daily * 100.0, 6),
            'NDX2L_DailyReturn':     round(2.0 * r * 100.0, 4),
            'NDX3L_DailyReturn':     round(3.0 * r * 100.0, 4),
            'NDX2L_Adj_DailyReturn': round(ret2adj * 100.0, 4),
            'NDX3L_Adj_DailyReturn': round(ret3adj * 100.0, 4),
            'NDX_Indexed':           round(cumBTC,   6),
            'NDX2L':                 round(cum2L,    6),
            'NDX3L':                 round(cum3L,    6),
            'NDX2L_adj':             round(cum2Ladj, 6),
            'NDX3L_adj':             round(cum3Ladj, 6),
        })
    return rows

# ── Main ───────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--from', dest='start_date', default='2014-01-01',
                    help='起始日期（首次下載用），預設 2014-01-01')
    args = ap.parse_args()

    btc_path = os.path.join(DIR, 'btc_leveraged.csv')
    today    = datetime.now().date()

    # ── 判斷增量 or 全量 ──────────────────────────────────────
    if os.path.exists(btc_path):
        existing   = pd.read_csv(btc_path, comment='#')
        existing['Date'] = pd.to_datetime(existing['Date'])
        last_date  = existing['Date'].iloc[-1]
        fetch_from = (last_date + timedelta(days=1)).strftime('%Y-%m-%d')
        print(f"Existing: {existing['Date'].iloc[0].date()} ~ {last_date.date()} "
              f"({len(existing)} rows)")
        is_incremental = True
    else:
        fetch_from = args.start_date
        existing   = None
        last_date  = None
        is_incremental = False
        print(f"btc_leveraged.csv 不存在，全量下載自 {fetch_from}")

    if last_date and last_date.date() >= today:
        print("Already up to date.")
        return

    # ── 抓取 BTC 價格 ─────────────────────────────────────────
    fetch_end = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    print(f"Fetching BTC-USD  {fetch_from} → {today} ...")

    prices = fetch_yfinance('BTC-USD', fetch_from, fetch_end)
    if prices is None or prices.empty:
        print("  yfinance failed, trying Stooq ...")
        prices = fetch_stooq('btc.v', fetch_from, today.strftime('%Y-%m-%d'))
    if prices is None or prices.empty:
        print("無法取得 BTC 資料，請檢查網路連線。")
        sys.exit(1)

    prices = prices.sort_index()
    print(f"  Got {len(prices)} row(s): "
          f"{prices.index[0].date()} ~ {prices.index[-1].date()}")

    # ── 抓取 RF 利率 ──────────────────────────────────────────
    last_rf = float(existing['RF_Annual_Pct'].iloc[-1]) if existing is not None else 4.0
    print("Fetching RF rate ...")
    current_rf = fetch_rf_rate(last_known=last_rf)
    print(f"  RF: {current_rf:.4f}%/yr")

    anchor   = build_rf_table(current_rf)
    interp_rf = make_interp(anchor)

    # ── 計算累積狀態起點 ──────────────────────────────────────
    if is_incremental:
        last_row  = existing.iloc[-1]
        cum_state = {
            'NDX_Indexed': float(last_row['NDX_Indexed']),
            'NDX2L':       float(last_row['NDX2L']),
            'NDX3L':       float(last_row['NDX3L']),
            'NDX2L_adj':   float(last_row['NDX2L_adj']),
            'NDX3L_adj':   float(last_row['NDX3L_adj']),
            'prev_price':  float(last_row['BTC_Close']),
        }
    else:
        # 全量：用第一筆資料當基準
        first_price = float(prices.iloc[0]['Price'])
        cum_state = {
            'NDX_Indexed': 100.0,
            'NDX2L':       100.0,
            'NDX3L':       100.0,
            'NDX2L_adj':   100.0,
            'NDX3L_adj':   100.0,
            'prev_price':  first_price,
        }
        # 全量時跳過第一筆（當作基準點，報酬=0）
        prices = prices.iloc[1:]

    # ── 建構新 rows ───────────────────────────────────────────
    new_rows = build_rows(prices, interp_rf, cum_state)
    print(f"Computed {len(new_rows)} new row(s).")

    if not new_rows:
        print("No new rows.")
        return

    # ── 儲存 CSV ──────────────────────────────────────────────
    new_df = pd.DataFrame(new_rows, columns=COLS)
    if is_incremental:
        existing_str = existing.copy()
        existing_str['Date'] = existing_str['Date'].dt.strftime('%Y-%m-%d')
        # 確保欄位一致
        for col in COLS:
            if col not in existing_str.columns:
                existing_str[col] = 0.0
        updated = pd.concat([existing_str[COLS], new_df], ignore_index=True)
    else:
        updated = new_df

    # 寫入：第一行為元資料注釋（供 index.html 自動套用標籤）
    with open(btc_path, 'w', encoding='utf-8') as f:
        f.write('# ASSET=BTC-USD,NAME=BTC,LEV2=2x BTC,LEV3=3x BTC\n')
        f.write(updated.to_csv(index=False))
    print(f"Saved btc_leveraged.csv: {len(updated)} rows "
          f"(last: {new_rows[-1]['Date']})")

    # ── 摘要統計 ──────────────────────────────────────────────
    n_years = (pd.to_datetime(new_rows[-1]['Date']) -
               pd.to_datetime(updated['Date'].iloc[0])).days / 365.25
    last    = updated.iloc[-1]
    first   = updated.iloc[0]

    def cagr(end, start, yrs):
        if float(start) <= 0 or yrs <= 0: return 0
        return (float(end) / float(start)) ** (1 / yrs) - 1

    print(f"\n=== 摘要  {updated['Date'].iloc[0]} ~ {new_rows[-1]['Date']} "
          f"({n_years:.1f} 年) ===")
    print(f"  BTC   (1x)       : {float(last['NDX_Indexed']):>12.2f}  "
          f"CAGR {cagr(last['NDX_Indexed'], first['NDX_Indexed'], n_years)*100:.1f}%")
    print(f"  BTC2L (2x gross) : {float(last['NDX2L']):>12.2f}  "
          f"CAGR {cagr(last['NDX2L'], first['NDX2L'], n_years)*100:.1f}%")
    print(f"  BTC2L (2x net)   : {float(last['NDX2L_adj']):>12.2f}  "
          f"CAGR {cagr(last['NDX2L_adj'], first['NDX2L_adj'], n_years)*100:.1f}%")
    print(f"  BTC3L (3x gross) : {float(last['NDX3L']):>12.2f}  "
          f"CAGR {cagr(last['NDX3L'], first['NDX3L'], n_years)*100:.1f}%")
    print(f"  BTC3L (3x net)   : {float(last['NDX3L_adj']):>12.2f}  "
          f"CAGR {cagr(last['NDX3L_adj'], first['NDX3L_adj'], n_years)*100:.1f}%")

    print(f"\n✓ Done. 請用 index.html「手動載入」按鈕載入 btc_leveraged.csv")

if __name__ == '__main__':
    main()
