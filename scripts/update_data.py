#!/usr/bin/env python3
"""
update_data.py — NDX Monte Carlo Simulator data updater
========================================================
Fetches the latest NDX prices, rebuilds ndx_leveraged.csv,
and embeds it into index.html.

Usage:
    python3 update_data.py            # auto-fetch from Yahoo Finance / Stooq
    python3 update_data.py --rebuild  # skip fetch, rebuild from existing CSVs only

Requirements:
    pip install yfinance pandas
"""

import os, sys, re, argparse, io
from datetime import datetime, timedelta
import urllib.request, ssl
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT       = os.path.dirname(SCRIPT_DIR)   # repo root
DATA_DIR   = os.path.join(ROOT, 'data')
DIR        = DATA_DIR   # backward-compat alias

# ── Constants ──────────────────────────────────────────────────
ER2  = 0.0095 / 252.0   # QLD  0.95%/yr expense ratio
ER3  = 0.0088 / 252.0   # TQQQ 0.88%/yr expense ratio
COLS = [
    'Date', 'NDX_Close', 'NDX_DailyReturn',
    'RF_Annual_Pct', 'RF_Daily_Pct',
    'NDX2L_DailyReturn', 'NDX3L_DailyReturn',
    'NDX2L_Adj_DailyReturn', 'NDX3L_Adj_DailyReturn',
    'NDX_Indexed', 'NDX2L', 'NDX3L', 'NDX2L_adj', 'NDX3L_adj',
]

def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rebuild', action='store_true',
                    help='Skip online fetch; rebuild from local CSVs only')
    return ap.parse_args()

# ── Data fetch helpers ─────────────────────────────────────────

def fetch_yfinance(ticker: str, start: str, end: str) -> pd.DataFrame | None:
    """Try yfinance. Returns DataFrame(Price, Return) or None."""
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
        df['Return'] = df['Price'].pct_change()
        return df.dropna()
    except Exception as e:
        print(f"  yfinance error: {e}")
        return None

def fetch_stooq(ticker: str, start: str, end: str) -> pd.DataFrame | None:
    """Try Stooq CSV API. Returns DataFrame(Price, Return) or None."""
    try:
        d1 = start.replace('-', '')
        d2 = end.replace('-', '')
        url = f'https://stooq.com/q/d/l/?s={ticker}&d1={d1}&d2={d2}&i=d'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            text = resp.read().decode()
        df = pd.read_csv(io.StringIO(text), parse_dates=['Date'])
        if df.empty or 'Close' not in df.columns:
            return None
        df = df[['Date', 'Close']].rename(columns={'Close': 'Price'}).set_index('Date')
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df['Return'] = df['Price'].pct_change()
        return df.dropna()
    except Exception as e:
        print(f"  Stooq error: {e}")
        return None

def fetch_rf_irx() -> float | None:
    """Fetch current RF rate from ^IRX via yfinance. Returns annual % or None."""
    try:
        import yfinance as yf
        irx = yf.download('^IRX', period='5d', auto_adjust=True,
                          progress=False, multi_level_index=False)
        if not irx.empty:
            return float(irx['Close'].iloc[-1])
    except Exception:
        pass
    try:  # Stooq fallback
        url = 'https://stooq.com/q/d/l/?s=%5eirx&c=5d&i=d'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            text = resp.read().decode()
        df = pd.read_csv(io.StringIO(text))
        return float(df['Close'].iloc[-1])
    except Exception:
        return None

# ── Manual CSV loader (Investing.com Nasdaq 100 Historical Data*.csv) ──────────

def load_manual_csvs() -> pd.DataFrame:
    """Load all 'Nasdaq 100 Historical Data*.csv' files in DIR."""
    files = [f for f in os.listdir(DIR)
             if f.startswith('Nasdaq 100 Historical Data') and f.endswith('.csv')]
    if not files:
        return pd.DataFrame()
    frames = []
    for f in files:
        try:
            df = pd.read_csv(os.path.join(DIR, f), thousands=',')
            frames.append(df)
        except Exception as e:
            print(f"  Warning: could not read {f}: {e}")
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    combined['Date'] = pd.to_datetime(combined['Date'], format='%m/%d/%Y')
    combined['Price'] = combined['Price'].astype(str).str.replace(',', '').astype(float)
    combined['Return'] = combined['Change %'].astype(str).str.replace('%', '').astype(float) / 100
    combined = (combined[['Date', 'Price', 'Return']]
                .drop_duplicates('Date')
                .sort_values('Date')
                .set_index('Date'))
    return combined

# ── RF interpolator ────────────────────────────────────────────

def build_rf_interpolator(rf_path: str):
    rf_df = pd.read_csv(rf_path, names=['year_str', 'pct'], skiprows=1)
    anchor_dates, anchor_pcts = [], []
    for _, row in rf_df.iterrows():
        yr_str = str(row['year_str']).strip()
        try:
            pct = float(row['pct'])
        except ValueError:
            continue
        if re.search(r'\(', yr_str):
            anchor_dates.append(pd.Timestamp(datetime.now().date()))
            anchor_pcts.append(pct)
        else:
            try:
                yr = int(yr_str[:4])
                anchor_dates.append(pd.Timestamp(f'{yr}-12-31'))
                anchor_pcts.append(pct)
            except ValueError:
                pass

    def interp(d: pd.Timestamp) -> float:
        prev_i = next_i = None
        for i, ad in enumerate(anchor_dates):
            if ad <= d:
                prev_i = i
            elif next_i is None:
                next_i = i
                break
        if prev_i is None:
            return anchor_pcts[0]
        if next_i is None:
            return anchor_pcts[prev_i]
        span = (anchor_dates[next_i] - anchor_dates[prev_i]).days
        frac = (d - anchor_dates[prev_i]).days / max(span, 1)
        return anchor_pcts[prev_i] + (anchor_pcts[next_i] - anchor_pcts[prev_i]) * frac

    return interp

# ── Build ndx_leveraged rows from price DataFrame ──────────────

def build_rows(price_df: pd.DataFrame, interp_rf,
               cum_state: dict) -> list[dict]:
    """
    price_df: indexed by date, columns=[Price, Return]
    cum_state: {'NDX_Indexed', 'NDX2L', 'NDX3L', 'NDX2L_adj', 'NDX3L_adj'}
    Returns list of row dicts.
    """
    rows = []
    cumNDX   = cum_state['NDX_Indexed']
    cum2L    = cum_state['NDX2L']
    cum3L    = cum_state['NDX3L']
    cum2Ladj = cum_state['NDX2L_adj']
    cum3Ladj = cum_state['NDX3L_adj']

    for date, row in price_df.iterrows():
        r      = float(row['Return'])
        rf_ann = interp_rf(date)
        rf     = rf_ann / 252.0 / 100.0

        cumNDX   *= (1.0 + r)
        cum2L    *= (1.0 + 2.0 * r)
        cum3L    *= (1.0 + 3.0 * r)
        ret2adj   = 2.0 * r - rf - ER2
        ret3adj   = 3.0 * r - 2.0 * rf - ER3
        cum2Ladj *= (1.0 + ret2adj)
        cum3Ladj *= (1.0 + ret3adj)

        rows.append({
            'Date':                  date.strftime('%Y-%m-%d'),
            'NDX_Close':             round(float(row['Price']), 2),
            'NDX_DailyReturn':       round(r * 100.0, 4),
            'RF_Annual_Pct':         round(rf_ann, 4),
            'RF_Daily_Pct':          round(rf * 100.0, 6),
            'NDX2L_DailyReturn':     round(2.0 * r * 100.0, 4),
            'NDX3L_DailyReturn':     round(3.0 * r * 100.0, 4),
            'NDX2L_Adj_DailyReturn': round(ret2adj * 100.0, 4),
            'NDX3L_Adj_DailyReturn': round(ret3adj * 100.0, 4),
            'NDX_Indexed':           round(cumNDX,   6),
            'NDX2L':                 round(cum2L,    6),
            'NDX3L':                 round(cum3L,    6),
            'NDX2L_adj':             round(cum2Ladj, 6),
            'NDX3L_adj':             round(cum3Ladj, 6),
        })
    return rows

# ── Embed CSV into index.html ──────────────────────────────────

def embed_csv_in_html(html_path: str, csv_text: str):
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
    START_TAG = '<script type="text/plain" id="csv-data">'
    END_TAG   = '</script>'
    s = html.find(START_TAG)
    if s == -1:
        raise ValueError('csv-data tag not found in index.html')
    e = html.find(END_TAG, s + len(START_TAG))
    if e == -1:
        raise ValueError('closing </script> not found after csv-data tag')
    new_html = html[:s + len(START_TAG)] + '\n' + csv_text + html[e:]
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(new_html)
    print(f"  Embedded {len(csv_text):,} chars into index.html")

# ── Main ───────────────────────────────────────────────────────

def main():
    args = parse_args()
    today = datetime.now().date()

    # Load existing ndx_leveraged.csv
    lever_path = os.path.join(DIR, 'ndx_leveraged.csv')
    existing   = pd.read_csv(lever_path)
    existing['Date'] = pd.to_datetime(existing['Date'])
    last_date  = existing['Date'].iloc[-1]
    print(f"Existing data: {existing['Date'].iloc[0].date()} ~ {last_date.date()} ({len(existing)} rows)")

    # RF anchor file
    rf_candidates = [f for f in os.listdir(DIR)
                     if f.endswith('.csv') and '貨幣市場' in f]
    rf_path = os.path.join(DIR, rf_candidates[0]) if rf_candidates else None

    # ── A. Determine new price data ────────────────────────────
    new_prices = pd.DataFrame()

    if args.rebuild:
        print("--rebuild: loading all manual NDX CSVs ...")
        all_manual = load_manual_csvs()
        if not all_manual.empty:
            new_prices = all_manual[all_manual.index > last_date]
            print(f"  Manual CSVs cover up to {all_manual.index[-1].date()}, "
                  f"{len(new_prices)} new row(s) after {last_date.date()}")
    else:
        fetch_start = (last_date + timedelta(days=1)).strftime('%Y-%m-%d')
        fetch_end   = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

        if last_date.date() >= today:
            print("Already up to date.")
        else:
            print(f"Fetching ^NDX  {fetch_start} → {today} ...")
            new_prices = fetch_yfinance('^NDX', fetch_start, fetch_end)
            if new_prices is None or new_prices.empty:
                print("  yfinance failed, trying Stooq ...")
                new_prices = fetch_stooq('%5endx', fetch_start, today.strftime('%Y-%m-%d'))
            if new_prices is None or new_prices.empty:
                print("  Both sources failed. Checking for manual CSVs ...")
                all_manual = load_manual_csvs()
                if not all_manual.empty:
                    new_prices = all_manual[all_manual.index > last_date]
                    print(f"  Found {len(new_prices)} new manual row(s).")
            if new_prices is None or new_prices.empty:
                print("\nNo new data available. Run with --rebuild to reprocess existing files.")
                sys.exit(0)
            else:
                print(f"  Got {len(new_prices)} row(s): "
                      f"{new_prices.index[0].date()} ~ {new_prices.index[-1].date()}")

        # Also check for manual CSVs that might extend further
        all_manual = load_manual_csvs()
        if not all_manual.empty:
            manual_new = all_manual[all_manual.index > last_date]
            if not manual_new.empty and (new_prices.empty or
                    manual_new.index[-1] > new_prices.index[-1]):
                print(f"  Manual CSVs extend to {manual_new.index[-1].date()}, merging ...")
                if new_prices.empty:
                    new_prices = manual_new
                else:
                    new_prices = pd.concat([new_prices, manual_new]).sort_index()
                    new_prices = new_prices[~new_prices.index.duplicated(keep='last')]

    if new_prices is None or new_prices.empty:
        print("Nothing to update.")
        # Still re-embed (in case index.html is out of date)
        print("Re-embedding existing data into index.html ...")
        existing_str = existing.copy()
        existing_str['Date'] = existing_str['Date'].dt.strftime('%Y-%m-%d')
        embed_csv_in_html(os.path.join(ROOT, 'index.html'),
                          existing_str.to_csv(index=False))
        print("Done.")
        return

    # ── B. Update RF rate ──────────────────────────────────────
    if rf_path and not args.rebuild:
        print("Fetching RF rate (^IRX) ...")
        current_rf = fetch_rf_irx()
        if current_rf is not None:
            print(f"  Current RF: {current_rf:.4f}%/yr")
            rf_lines = open(rf_path, encoding='utf-8').readlines()
            year_str = f"{today.year} ({today.strftime('%b')})"
            clean    = [l for l in rf_lines if not re.search(r'\d{4}.*\(', l)]
            clean.append(f"{year_str},{current_rf:.2f}\n")
            with open(rf_path, 'w', encoding='utf-8') as f:
                f.writelines(clean)
            print(f"  RF anchor updated: {year_str} → {current_rf:.2f}%")
        else:
            print("  RF fetch failed; using last known rate.")

    # ── C. Build RF interpolator ───────────────────────────────
    if rf_path:
        interp_rf = build_rf_interpolator(rf_path)
    else:
        # Fallback: use last RF from existing data
        last_rf = float(existing['RF_Annual_Pct'].iloc[-1])
        interp_rf = lambda _: last_rf

    # ── D. Compute new rows ────────────────────────────────────
    last_row  = existing.iloc[-1]
    cum_state = {
        'NDX_Indexed': float(last_row['NDX_Indexed']),
        'NDX2L':       float(last_row['NDX2L']),
        'NDX3L':       float(last_row['NDX3L']),
        'NDX2L_adj':   float(last_row['NDX2L_adj']),
        'NDX3L_adj':   float(last_row['NDX3L_adj']),
    }
    new_rows = build_rows(new_prices, interp_rf, cum_state)
    print(f"Computed {len(new_rows)} new row(s).")

    # ── E. Save ndx_leveraged.csv ──────────────────────────────
    existing_str = existing.copy()
    existing_str['Date'] = existing_str['Date'].dt.strftime('%Y-%m-%d')
    updated = pd.concat(
        [existing_str, pd.DataFrame(new_rows, columns=COLS)],
        ignore_index=True,
    )
    updated.to_csv(lever_path, index=False)
    print(f"Saved ndx_leveraged.csv: {len(updated)} rows "
          f"(last: {new_rows[-1]['Date']})")

    # ── F. Embed into index.html ───────────────────────────────
    html_path = os.path.join(ROOT, 'index.html')
    embed_csv_in_html(html_path, updated.to_csv(index=False))

    print(f"\n✓ Done. Data now covers up to {new_rows[-1]['Date']}.")

if __name__ == '__main__':
    main()
