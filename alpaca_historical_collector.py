# -*- coding: utf-8 -*-
"""
Alpaca Historical Data Collector - 2 Years of Crypto Data
==========================================================

Fetches historical OHLC data from Alpaca Markets API.
"""

import os
import time
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests
from logging import getLogger, basicConfig, INFO

basicConfig(level=INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
LOG = getLogger("alpaca_collector")

# Alpaca API credentials
ALPACA_API_KEY = "AK4QVOVIEGU3FN6IZKI3GKE4HB"
ALPACA_SECRET_KEY = "6EbU3UpFENmoSNwU9B27YZCXij32fymyMX5BAiyPhzpi"
ALPACA_BASE_URL = "https://data.alpaca.markets"

# Crypto pairs to fetch (Alpaca format)
CRYPTO_PAIRS = [
    "BTC/USD",
    "ETH/USD", 
    "SOL/USD",
    "LINK/USD",
    "AVAX/USD",
    "ADA/USD",
    "LTC/USD",
    "DOT/USD",
    "XRP/USD",
    "MATIC/USD",  # POL was MATIC
]

OUTPUT_DIR = "data"


def fetch_crypto_bars(symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
    """
    Fetch crypto bars from Alpaca.
    
    Args:
        symbol: Crypto pair (e.g., "BTC/USD")
        timeframe: "1Hour", "1Day", etc.
        start: Start datetime
        end: End datetime
    
    Returns:
        DataFrame with OHLCV data
    """
    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }
    
    all_bars = []
    page_token = None
    
    while True:
        params = {
            "symbols": symbol,
            "timeframe": timeframe,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": 10000,
        }
        if page_token:
            params["page_token"] = page_token
        
        url = f"{ALPACA_BASE_URL}/v1beta3/crypto/us/bars"
        
        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            bars = data.get("bars", {}).get(symbol, [])
            if not bars:
                break
            
            all_bars.extend(bars)
            LOG.info(f"  Fetched {len(bars)} bars, total: {len(all_bars)}")
            
            page_token = data.get("next_page_token")
            if not page_token:
                break
            
            time.sleep(0.2)  # Rate limit
            
        except Exception as e:
            LOG.error(f"Error fetching {symbol}: {e}")
            break
    
    if not all_bars:
        return pd.DataFrame()
    
    # Convert to DataFrame
    df = pd.DataFrame(all_bars)
    df = df.rename(columns={
        "t": "time",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
        "vw": "vwap",
        "n": "count",
    })
    
    # Parse time
    df["time"] = pd.to_datetime(df["time"])
    df = df.sort_values("time").reset_index(drop=True)
    
    return df


def collect_all_pairs(years: float = 2.0, timeframe: str = "1Hour"):
    """Collect historical data for all crypto pairs."""
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(years * 365))
    
    LOG.info(f"Collecting {years} years of {timeframe} data")
    LOG.info(f"Period: {start.date()} to {end.date()}")
    LOG.info("=" * 60)
    
    results = {}
    
    for pair in CRYPTO_PAIRS:
        LOG.info(f"\n[{pair}]")
        
        df = fetch_crypto_bars(pair, timeframe, start, end)
        
        if df.empty:
            LOG.warning(f"  No data for {pair}")
            continue
        
        # Save to CSV - keep ISO 8601 format for proper parsing
        tag = pair.replace("/", "")
        tf_tag = "1h" if timeframe == "1Hour" else timeframe.lower()
        filename = f"bars_{tag}_{tf_tag}.csv"
        path = Path(OUTPUT_DIR) / filename

        # Keep time as ISO 8601 string (Alpaca native format)
        df_save = df.copy()
        df_save["time"] = df_save["time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        df_save.to_csv(path, index=False)
        
        LOG.info(f"  Saved {len(df)} bars to {path}")
        LOG.info(f"  Range: {df['time'].min()} to {df['time'].max()}")
        
        results[pair] = {
            "bars": len(df),
            "start": str(df["time"].min()),
            "end": str(df["time"].max()),
        }
        
        time.sleep(0.3)
    
    LOG.info("\n" + "=" * 60)
    LOG.info("COLLECTION COMPLETE")
    LOG.info("=" * 60)
    
    for pair, info in results.items():
        LOG.info(f"  {pair}: {info['bars']} bars")
    
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=float, default=2.0)
    parser.add_argument("--timeframe", type=str, default="1Hour")
    args = parser.parse_args()
    
    collect_all_pairs(years=args.years, timeframe=args.timeframe)
