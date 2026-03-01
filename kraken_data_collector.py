import os
import time
import pandas as pd
from logging import getLogger
from datetime import datetime, timezone
from typing import Optional, List, Dict

try:
    import schedule
    SCHEDULE_AVAILABLE = True
except ImportError:
    SCHEDULE_AVAILABLE = False

from config_factory import CFG
from kraken_client import KrakenClient

LOG = getLogger("data_collector")

# Kraken pair mapping helpers
_COMMON_MAP = {
    "BTC": "XBT",
    "XBT": "XBT",
    "ETH": "ETH",
    "USD": "USD",
    "EUR": "EUR",
}


def to_kraken_pair(pair: str) -> str:
    """Map BASE/QUOTE like BTC/USD to Kraken altname (e.g., XBTUSD)."""
    try:
        base, quote = pair.split("/")
    except ValueError:
        return pair.replace("/", "")
    base = _COMMON_MAP.get(base, base)
    quote = _COMMON_MAP.get(quote, quote)
    return f"{base}{quote}"


class DataCollector:
    def __init__(self) -> None:
        self.client = KrakenClient()
        os.makedirs(CFG.DATA_DIR, exist_ok=True)
        # Fast trading cadence for active strategy (Golden Cross detection)
        self._due_secs = {
            "1m": 60,    # every 1 minute (for responsive signals)
            "5m": 120,   # every 2 minutes (LTF for micro signals)
            "15m": 300,  # every 5 minutes (main signal timeframe)
            "1h": 900,   # every 15 minutes (HTF macro alignment)
            "1d": 3600,  # every hour (daily context)
        }

    def _csv_path(self, pair: str, tf: str) -> str:
        tag = pair.replace("/", "")
        return os.path.join(CFG.DATA_DIR, f"bars_{tag}_{tf}.csv")

    def _fetch_ohlc(self, pair: str, interval: int) -> pd.DataFrame:
        # Kraken interval in minutes:1,5,15,60,1440
        p = to_kraken_pair(pair)
        res = self.client.public_get("/0/public/OHLC", {"pair": p, "interval": interval})
        # Kraken returns { result: { <pair_key>: [...], last: <ts> }, error: [] }
        result = res.get("result", {})
        # Try to find the key containing the OHLC array
        key = None
        for k in result.keys():
            if k.lower().startswith(p.lower()):
                key = k
                break
        if key is None:
            # Fallback: first non-"last" key
            keys = [k for k in result.keys() if k != "last"]
            if not keys:
                raise RuntimeError(f"OHLC response missing data for {pair}: keys={list(result.keys())}")
            key = keys[0]
        rows = result.get(key, [])
        if not rows:
            raise RuntimeError(f"No OHLC rows for {pair} ({p})")
        cols = ["time", "open", "high", "low", "close", "vwap", "volume", "count"]
        df = pd.DataFrame(rows, columns=cols)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df

    def _is_due(self, path: str, tf: str) -> bool:
        due = self._due_secs.get(tf, 60)
        try:
            mtime = os.path.getmtime(path)
        except FileNotFoundError:
            return True
        return (time.time() - mtime) >= due

    def _append_new_rows(self, path: str, df_new: pd.DataFrame) -> int:
        if not os.path.exists(path):
            df_new.to_csv(path, index=False)
            return len(df_new)
        try:
            last = pd.read_csv(path, usecols=["time"])
            last_time = pd.to_datetime(last["time"].iloc[-1], utc=True)
        except Exception:
            last_time = None
        if last_time is not None:
            df_new = df_new[df_new["time"] > last_time]
        if df_new.empty:
            return 0
        # Append without header
        df_new.to_csv(path, mode="a", header=False, index=False)
        return len(df_new)

    def fetch_incremental(self, pair: str) -> dict:
        """
        Fetch incremental OHLC data for a pair across all timeframes.
        
        Returns:
            dict with stats about what was collected
        """
        mapping = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 1440}
        stats = {"pair": pair, "timeframes": {}, "total_rows": 0}
        
        for tf, iv in mapping.items():
            path = self._csv_path(pair, tf)
            if not self._is_due(path, tf):
                continue
            try:
                new = self._fetch_ohlc(pair, iv)
                # Minimal delay - Kraken public API is generous
                time.sleep(0.15)
            except Exception as e:
                LOG.error("OHLC fetch failed for %s @%s: %s", pair, tf, e)
                time.sleep(0.5)  # Brief delay on error before retry
                continue
            added = self._append_new_rows(path, new)
            if added:
                LOG.info("Saved %s | rows+=%d", path, added)
                stats["timeframes"][tf] = added
                stats["total_rows"] += added
            else:
                LOG.debug("Up-to-date %s", path)

        return stats

    def fetch_5m_data(self, pairs: Optional[List[str]] = None) -> Dict:
        """
        Fetch 5-minute data for all pairs.
        Optimized for probe sensor which uses 5m data.
        """
        pairs = pairs or CFG.PAIRS
        stats = {"timestamp": datetime.now(timezone.utc).isoformat(), "pairs": {}, "total_rows": 0}

        for pair in pairs:
            try:
                path = self._csv_path(pair, "5m")
                new = self._fetch_ohlc(pair, 5)
                time.sleep(0.35)
                added = self._append_new_rows(path, new)
                if added:
                    LOG.info("[5m] %s | +%d rows", pair, added)
                    stats["pairs"][pair] = added
                    stats["total_rows"] += added
            except Exception as e:
                LOG.error("[5m] %s fetch failed: %s", pair, e)
                time.sleep(1.0)

        return stats

    def fetch_15m_data(self, pairs: Optional[List[str]] = None) -> Dict:
        """Fetch 15-minute data for all pairs."""
        pairs = pairs or CFG.PAIRS
        stats = {"timestamp": datetime.now(timezone.utc).isoformat(), "pairs": {}, "total_rows": 0}

        for pair in pairs:
            try:
                path = self._csv_path(pair, "15m")
                new = self._fetch_ohlc(pair, 15)
                time.sleep(0.35)
                added = self._append_new_rows(path, new)
                if added:
                    LOG.info("[15m] %s | +%d rows", pair, added)
                    stats["pairs"][pair] = added
                    stats["total_rows"] += added
            except Exception as e:
                LOG.error("[15m] %s fetch failed: %s", pair, e)
                time.sleep(1.0)

        return stats


# =============================================================================
# SCHEDULED DATA COLLECTION
# =============================================================================

_collector_instance: Optional[DataCollector] = None


def get_data_collector() -> DataCollector:
    """Get or create the data collector singleton."""
    global _collector_instance
    if _collector_instance is None:
        _collector_instance = DataCollector()
    return _collector_instance


def collect_5m_data() -> None:
    """Scheduled job to collect 5-minute data."""
    LOG.info("[SCHEDULE] Running 5-minute data collection...")
    collector = get_data_collector()
    stats = collector.fetch_5m_data()
    LOG.info("[SCHEDULE] 5m collection complete: %d total rows", stats["total_rows"])


def collect_15m_data() -> None:
    """Scheduled job to collect 15-minute data."""
    LOG.info("[SCHEDULE] Running 15-minute data collection...")
    collector = get_data_collector()
    stats = collector.fetch_15m_data()
    LOG.info("[SCHEDULE] 15m collection complete: %d total rows", stats["total_rows"])


def collect_all_timeframes() -> None:
    """Scheduled job to collect all timeframes for all pairs."""
    LOG.info("[SCHEDULE] Running full data collection...")
    collector = get_data_collector()
    for pair in CFG.PAIRS:
        collector.fetch_incremental(pair)
    LOG.info("[SCHEDULE] Full collection complete")


def setup_schedule() -> None:
    """
    Setup scheduled data collection jobs.

    Schedule:
    - 5m data: every 5 minutes
    - 15m data: every 15 minutes  
    - All timeframes: every hour
    """
    if not SCHEDULE_AVAILABLE:
        LOG.warning("[SCHEDULE] schedule library not installed. Run: pip install schedule")
        return

    # Clear any existing jobs
    schedule.clear()

    # 5-minute data every 5 minutes (for probe sensor)
    schedule.every(5).minutes.do(collect_5m_data)

    # 15-minute data every 15 minutes (for main strategy)
    schedule.every(15).minutes.do(collect_15m_data)

    # Full collection every hour
    schedule.every(1).hours.do(collect_all_timeframes)

    LOG.info("[SCHEDULE] Data collection schedule configured:")
    LOG.info("  - 5m data: every 5 minutes")
    LOG.info("  - 15m data: every 15 minutes")
    LOG.info("  - All timeframes: every hour")


def run_scheduled_collector(run_immediately: bool = True) -> None:
    """
    Run the scheduled data collector in a loop.

    Args:
        run_immediately: If True, run collection once before starting schedule
    """
    if not SCHEDULE_AVAILABLE:
        LOG.error("[SCHEDULE] schedule library not installed. Run: pip install schedule")
        return

    setup_schedule()

    # Run immediately if requested
    if run_immediately:
        LOG.info("[SCHEDULE] Running initial data collection...")
        collect_5m_data()
        collect_15m_data()

    LOG.info("[SCHEDULE] Starting scheduled data collector loop...")

    while True:
        try:
            schedule.run_pending()
            time.sleep(10)  # Check every 10 seconds
        except KeyboardInterrupt:
            LOG.info("[SCHEDULE] Stopped by user")
            break
        except Exception as e:
            LOG.error("[SCHEDULE] Error: %s", e)
            time.sleep(60)


def run_once() -> None:
    """Run data collection once (no scheduling)."""
    collector = get_data_collector()
    LOG.info("[COLLECTOR] Running one-time collection for all pairs...")
    for pair in CFG.PAIRS:
        stats = collector.fetch_incremental(pair)
        if stats["total_rows"] > 0:
            LOG.info("[COLLECTOR] %s: +%d rows", pair, stats["total_rows"])
    LOG.info("[COLLECTOR] One-time collection complete")


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import argparse
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    parser = argparse.ArgumentParser(description="Kraken Data Collector")
    parser.add_argument("--schedule", action="store_true", help="Run with scheduling")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--5m", dest="five_min", action="store_true", help="Collect 5m data only")
    parser.add_argument("--15m", dest="fifteen_min", action="store_true", help="Collect 15m data only")
    args = parser.parse_args()

    if args.schedule:
        run_scheduled_collector()
    elif args.once:
        run_once()
    elif args.five_min:
        collect_5m_data()
    elif args.fifteen_min:
        collect_15m_data()
    else:
        # Default: run once
        run_once()