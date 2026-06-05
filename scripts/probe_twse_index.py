#!/usr/bin/env python3
"""
probe_twse_index.py — 探測 TWSE 報酬指數 API 端點

在本機執行：
    python scripts/probe_twse_index.py

確認哪個端點可以拿到 IR0001（發行量加權股價報酬指數）資料。
"""

import json, requests
requests.packages.urllib3.disable_warnings()

HDR = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
DATE = '20240104'  # 測試日期（工作日）

candidates = [
    # 猜測：mfi94u.html → MFI94U endpoint（同 STOCK_DAY 命名規律）
    f'https://www.twse.com.tw/indicesReport/MFI94U?response=json&date={DATE}',
    # 備選：MI_INDEX 大盤行情，type=IND 含多指數
    f'https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={DATE}&type=IND',
    # 備選：MI_INDEX type=MS
    f'https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={DATE}&type=MS',
    # 備選：TAIEX report
    f'https://www.twse.com.tw/indicesReport/TAIEX?response=json&date={DATE}',
]

for url in candidates:
    print(f'\n── {url}')
    try:
        r = requests.get(url, headers=HDR, timeout=10, verify=False)
        print(f'   HTTP {r.status_code}')
        if r.status_code == 200:
            data = r.json()
            print(f'   keys: {list(data.keys())[:10]}')
            # 印出 fields（欄位名）
            for k, v in data.items():
                if 'field' in k.lower() and isinstance(v, list):
                    print(f'   {k}: {v}')
            # 印出第一筆 data
            for k, v in data.items():
                if 'data' in k.lower() and isinstance(v, list) and v:
                    print(f'   {k}[0]: {v[0]}')
                    break
        else:
            print(f'   body: {r.text[:200]}')
    except Exception as e:
        print(f'   ERROR: {e}')
