import os
import json
from dataclasses import dataclass, field
from typing import Dict
from dotenv import load_dotenv

# Load environment variables from .kraken_env (single source of truth)
# This is the ONLY config file - .env is not used
load_dotenv(dotenv_path=".kraken_env", override=True)


@dataclass
class _CFG:
    MODE: str = os.getenv("MODE", "sandbox")
    API_KEY: str = os.getenv("KRAKEN_API_KEY", "")
    API_SECRET: str = os.getenv("KRAKEN_API_SECRET", "")

    # Allow overriding API endpoints from env; fall back to defaults by MODE
    BASE_URL: str = os.getenv(
        "KRAKEN_API_BASE_URL",
        os.getenv("KRAKEN_API_BASE", "https://api.kraken.com")
    )
    WS_URL: str = os.getenv(
        "KRAKEN_WS_URL",
        "wss://ws.kraken.com" if MODE == "live" else "wss://ws-beta.kraken.com"
    )
    QUOTE: str = "USD"

    # Pairs (comma-separated in .env)
    PAIRS: list = tuple(map(str.strip, os.getenv("PAIRS", "BTC/USD,ETH/USD,SOL/USD,LINK/USD,AVAX/USD,ADA/USD,LTC/USD,DOT/USD,POL/USD,XRP/USD").split(",")))

    # Excluded pairs (comma-separated in .env) - for exposure tracking
    EXCLUDED_PAIRS: list = tuple(map(str.strip, os.getenv("EXCLUDED_PAIRS", "CHF/USD,A/USD,ATOM/USD,BABY/USD,FLOW/USD").split(","))) if os.getenv("EXCLUDED_PAIRS") else ()
    
    # Dust threshold for exposure tracking (positions below this are ignored)
    EXPOSURE_DUST_THRESHOLD_USD: float = float(os.getenv("EXPOSURE_DUST_THRESHOLD_USD", 1.0))

    # Profit Target (V2.1)
    TARGET_PROFIT_PCT: float = float(os.getenv("TARGET_PROFIT_PCT", 0.02))

    # ATR-based strategy
    ATR_MULTIPLIER: float = float(os.getenv("ATR_MULTIPLIER", 1.5))

    # ATR Trailing Stop (V3.5)
    USE_ATR_TRAILING: bool = os.getenv("USE_ATR_TRAILING", "false").lower() == "true"
    ATR_TRAILING_MULTIPLIER: float = float(os.getenv("ATR_TRAILING_MULTIPLIER", 2.0))
    ATR_TRAILING_TRIGGER_PCT: float = float(os.getenv("ATR_TRAILING_TRIGGER_PCT", 7.0))
    ATR_TRAILING_TIMEFRAME: str = os.getenv("ATR_TRAILING_TIMEFRAME", "15m")

    # Thresholds
    BUY_CONF_THRESHOLD: float = float(os.getenv("BUY_CONF_THRESHOLD", 0.50))
    SELL_CONF_THRESHOLD: float = float(os.getenv("SELL_CONF_THRESHOLD", 0.42))
    RISK_SCORE_THRESHOLD: float = float(os.getenv("RISK_SCORE_THRESHOLD", 0.35))

    # Exit system (V2.1)
    TRAIL_ENABLE: bool = os.getenv("TRAIL_ENABLE", "true").lower() == "true"
    TRAIL_ARM: float = float(os.getenv("TRAIL_ARM", 0.02))
    TRAIL_PCT: float = float(os.getenv("TRAIL_PCT", 0.015))
    TRAIL_TIGHTEN_AT: float = float(os.getenv("TRAIL_TIGHTEN_AT", 0.05))
    TRAIL_TIGHTEN_PCT: float = float(os.getenv("TRAIL_TIGHTEN_PCT", 0.008))
    PROFIT_GATE_MODE: str = os.getenv("PROFIT_GATE_MODE", "ATR_ADAPTIVE")
    CUT_AT_HARD: float = float(os.getenv("CUT_AT_HARD", 0.08))
    DEGRADE_AT: float = float(os.getenv("DEGRADE_AT", 0.05))
    DEGRADE_REDUCE_FRACTION: float = float(os.getenv("DEGRADE_REDUCE_FRACTION", 0.5))
    RSI_EXIT_OVERBOUGHT: float = float(os.getenv("RSI_EXIT_OVERBOUGHT", 70))
    RSI_EXIT_MIN_PROFIT: float = float(os.getenv("RSI_EXIT_MIN_PROFIT", 0.02))
    MIN_HOLD_HOURS: float = float(os.getenv("MIN_HOLD_HOURS", 2))

    # Multi-Gate Trailing System (V3.6 - ThalesFX-Style)
    USE_MULTI_GATE_TRAILING: bool = os.getenv("USE_MULTI_GATE_TRAILING", "true").lower() == "true"
    GATE_1_ACTIVATION_PCT: float = float(os.getenv("GATE_1_ACTIVATION_PCT", 0.010))  # +1.0% profit
    GATE_1_TRAIL_DISTANCE_PCT: float = float(os.getenv("GATE_1_TRAIL_DISTANCE_PCT", 0.012))  # 1.2% trail (widest, initial protection)
    GATE_2_ACTIVATION_PCT: float = float(os.getenv("GATE_2_ACTIVATION_PCT", 0.020))  # +2.0% profit
    GATE_2_TRAIL_DISTANCE_PCT: float = float(os.getenv("GATE_2_TRAIL_DISTANCE_PCT", 0.009))  # 0.9% trail (tighter)
    GATE_3_ACTIVATION_PCT: float = float(os.getenv("GATE_3_ACTIVATION_PCT", 0.035))  # +3.5% profit
    GATE_3_TRAIL_DISTANCE_PCT: float = float(os.getenv("GATE_3_TRAIL_DISTANCE_PCT", 0.007))  # 0.7% trail (tightest)
    
    # ATR-based gate parameters
    MIN_TRAIL_PCT: float = float(os.getenv("MIN_TRAIL_PCT", 0.006))  # Absolute minimum 0.6%
    GATE_TRAIL_ATR_MULT: float = float(os.getenv("GATE_TRAIL_ATR_MULT", 1.0))  # ATR multiplier for distance
    MIN_ACTIVATION_PCT: float = float(os.getenv("MIN_ACTIVATION_PCT", 0.010))  # Minimum 1.0%
    GATE_ACTIVATION_ATR_MULT: float = float(os.getenv("GATE_ACTIVATION_ATR_MULT", 1.2))  # ATR multiplier for activation
    
    # Early loss ladder thresholds (for risk management before deep drawdowns)
    LOSS_WARNING_PCT: float = float(os.getenv("LOSS_WARNING_PCT", -0.025))  # -2.5% loss stage
    LOSS_CRITICAL_PCT: float = float(os.getenv("LOSS_CRITICAL_PCT", -0.040))  # -4.0% loss stage
    LOSS_EXTREME_PCT: float = float(os.getenv("LOSS_EXTREME_PCT", -0.060))  # -6.0% hard stop

    # New: Fixed stop-loss percent (applies to tracked and untracked when cost basis known)
    STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", 0.03))  # 3% default

    # Buy Stop re-entry guard (percent above stop-loss exit price required to allow buy)
    BUY_STOP_PCT: float = float(os.getenv("BUY_STOP_PCT", 0.0175))  # 1.75% default

    # Anti-overtrading controls (NEW)
    MAX_BUYS_PER_TICK: int = int(os.getenv("MAX_BUYS_PER_TICK", 1))  # Limit buys per evaluation tick
    BUY_COOLDOWN_MIN: int = int(os.getenv("BUY_COOLDOWN_MIN", 10))  # Minutes between same-pair buys

    # RSI-Assisted Rebound Configuration (NEW)
    REBOUND_ENABLED: bool = os.getenv("REBOUND_ENABLED", "true").lower() == "true"
    REBOUND_PCT_TARGET: float = float(os.getenv("REBOUND_PCT_TARGET", 0.0175))  # 1.75% price rebound
    REBOUND_HOLD_BARS: int = int(os.getenv("REBOUND_HOLD_BARS", 2))  # 2-bar confirmation
    REBOUND_TF_SECONDS: int = int(os.getenv("REBOUND_TF_SECONDS", 900))  # 15m timeframe
    CONTINUOUS_REBOUND_MODE: bool = os.getenv("CONTINUOUS_REBOUND_MODE", "false").lower() == "true"
    
    # RSI parameters for rebound tracking
    RSI_PERIOD: int = int(os.getenv("RSI_PERIOD", 14))
    RSI_DELTA_PTS: float = float(os.getenv("RSI_DELTA_PTS", 10.0))  # RSI improvement threshold
    RSI_CROSS_THRESHOLD: float = float(os.getenv("RSI_CROSS_THRESHOLD", 40.0))  # RSI cross-up level

    # Ledger reconciliation knobs - OPTIMIZED FOR API EFFICIENCY
    ORDER_HISTORY_LOOKBACK_DAYS: int = int(os.getenv("ORDER_HISTORY_LOOKBACK_DAYS", 60))  # Reduced from 120
    MAX_BACKFILL_MONTHS: int = int(os.getenv("MAX_BACKFILL_MONTHS", 24))
    LEDGER_TOLERANCE: float = float(os.getenv("LEDGER_TOLERANCE", 1e-8))

    # Entry price loading configuration
    ENTRY_PRICE_HISTORY_PAGES: int = int(os.getenv("ENTRY_PRICE_HISTORY_PAGES", 20))

    # DUST handling (support both env names)
    DUST_USD_THRESHOLD: float = float(os.getenv("DUST_USD_THRESHOLD", os.getenv("DUST_THRESHOLD_USD", 5.00)))
    DUST_MIN_QTY: float = float(os.getenv("DUST_MIN_QTY", 0.0001))

    # API budget controls - NEW
    LEDGER_PAGE_SIZE: int = int(os.getenv("LEDGER_PAGE_SIZE", 50))
    MAX_LEDGER_PAGES_PER_REFRESH: int = int(os.getenv("MAX_LEDGER_PAGES_PER_REFRESH", 4))  # Limit pages per refresh
    API_BUDGET_PER_TICK: int = int(os.getenv("API_BUDGET_PER_TICK", 30))  # Max API calls per tick

    # NEW: Trades history page cap (used by OrderHistoryTracker)
    MAX_TRADES_PAGES_PER_REFRESH: int = int(os.getenv("MAX_TRADES_PAGES_PER_REFRESH", os.getenv("ORDER_HISTORY_MAX_PAGES", 4)))

    # Fee Management (NEW - V3.6)
    # Enable dynamic fee fetching from Kraken account info
    ENABLE_DYNAMIC_FEES: bool = os.getenv("ENABLE_DYNAMIC_FEES", "true").lower() == "true"
    # How long to cache fees before refreshing (seconds)
    FEE_CACHE_TTL_SECONDS: int = int(os.getenv("FEE_CACHE_TTL_SECONDS", 3600))  # 1 hour default
    # Fallback fees if API fails (as percentages)
    FALLBACK_MAKER_FEE_PCT: float = float(os.getenv("FALLBACK_MAKER_FEE_PCT", 0.16))  # 0.16%
    FALLBACK_TAKER_FEE_PCT: float = float(os.getenv("FALLBACK_TAKER_FEE_PCT", 0.26))  # 0.26%

    # Asset normalization mapping - Use field(default_factory=dict) to avoid mutable default
    STAKING_ASSET_MAPPING: Dict[str, str] = field(default_factory=dict)

    # Capital
    MIN_NOTIONAL_BUY: float = float(os.getenv("MIN_NOTIONAL_BUY", 10.0))
    SKIP_BUY_IF_CASH_LT_MIN_NOTIONAL: bool = os.getenv("SKIP_BUY_IF_CASH_LT_MIN_NOTIONAL", "true").lower() == "true"
    MAX_POSITION_USD: float = float(os.getenv("MAX_POSITION_USD", 250.0))
    BUY_PER_TRADE_USD: float = float(os.getenv("BUY_PER_TRADE_USD", 50.0))  # New: cap per individual buy

    # Intervals - OPTIMIZED TO REDUCE API LOAD
    TRADING_INTERVAL_S: int = int(os.getenv("TRADING_INTERVAL_S", 8 * 60))  # 8 minutes (from 5)
    DATA_INTERVAL_S: int = int(os.getenv("DATA_INTERVAL_S", 20 * 60))  # 20 minutes (from 15)
    HEALTH_INTERVAL_S: int = int(os.getenv("HEALTH_INTERVAL_S", 15 * 60))  # 15 minutes

    # Backward-compatibility aliases (used by some modules/tests)
    DATA_COLLECTION_INTERVAL_S: int = int(os.getenv("DATA_COLLECTION_INTERVAL_S", DATA_INTERVAL_S))

    # Decision report retention (auto-reset)
    DECISION_REPORT_TTL_S: int = int(os.getenv("DECISION_REPORT_TTL_S", 3600))  # 1 hour default
    DECISION_REPORT_INTERVAL_S: int = int(os.getenv("DECISION_REPORT_INTERVAL_S", 300))  # 5 minutes
    DECISION_REPORT_TAIL: int = int(os.getenv("DECISION_REPORT_TAIL", 0))  # 0 = full report

    # Decision Report Follower (automatic execution)
    EXECUTOR_FOLLOW_REPORT_ENABLED: bool = os.getenv("EXECUTOR_FOLLOW_REPORT_ENABLED", "true").lower() == "true"
    EXECUTOR_FOLLOW_REPORT_BUY: bool = os.getenv("EXECUTOR_FOLLOW_REPORT_BUY", "false").lower() == "true"

    # Auto-reduce exposure (NK-TRADING-AMOUNT-FIX)
    AUTO_REDUCE_EXPOSURE_ENABLED: bool = os.getenv("AUTO_REDUCE_EXPOSURE_ENABLED", "false").lower() == "true"
    AUTO_REDUCE_EXPOSURE_THRESHOLD_PCT: float = float(os.getenv("AUTO_REDUCE_EXPOSURE_THRESHOLD_PCT", 75.0))  # Trigger at 75% (before hitting 70% hard limit)
    AUTO_REDUCE_EXPOSURE_TARGET_PCT: float = float(os.getenv("AUTO_REDUCE_EXPOSURE_TARGET_PCT", 65.0))  # Reduce to 65% to leave buffer

    # Staking Transfer Configuration
    XFER_ENABLED: bool = os.getenv("XFER_ENABLED", "false").lower() == "true"

    # Paths
    DATA_DIR: str = os.getenv("DATA_DIR", "./data")
    LOG_DIR: str = os.getenv("LOG_DIR", "./logs")
    MODEL_DIR: str = os.getenv("MODEL_DIR", "./models")
    LOG_FILE: str = os.path.join(LOG_DIR, "run.log")
    STATE_DB_PATH: str = os.getenv("KRAKEN_STATE_DB", "./kraken_state.db")

    # Trailing Buy Stop System (nkproduction)
    # Trend classification thresholds for ADX-based speed selection
    TREND_ADX_MEDIUM: float = float(os.getenv("TREND_ADX_MEDIUM", 20.0))
    TREND_ADX_STRONG: float = float(os.getenv("TREND_ADX_STRONG", 30.0))
    
    # Independent signal mode - when True, trailing buy stop uses its own
    # ADX/RSI/Ichimoku indicators instead of requiring buy_manager signals
    TRAILING_BUY_INDEPENDENT_SIGNALS: bool = os.getenv("TRAILING_BUY_INDEPENDENT_SIGNALS", "true").lower() == "true"
    
    # =========================================================================
    # TIER ROUTER (Spacing Ladder) - V4.0
    # =========================================================================
    # Default tier percentages for buy-stop spacing
    TIER_7_PCT: float = float(os.getenv("TIER_7_PCT", 0.07))
    TIER_3_PCT: float = float(os.getenv("TIER_3_PCT", 0.03))
    TIER_175_PCT: float = float(os.getenv("TIER_175_PCT", 0.0175))
    
    # Confidence thresholds for tier stepping
    TIER_CONF_3: float = float(os.getenv("TIER_CONF_3", 0.60))
    TIER_CONF_175: float = float(os.getenv("TIER_CONF_175", 0.80))
    
    # Hysteresis settings to prevent tier flapping
    TIER_SWITCH_COOLDOWN_SECONDS: float = float(os.getenv("TIER_SWITCH_COOLDOWN_SECONDS", 60.0))
    TIER_CONFIRM_TICKS: int = int(os.getenv("TIER_CONFIRM_TICKS", 2))
    
    # =========================================================================
    # NK TRADING AMOUNT RULE
    # =========================================================================
    # Position sizing formula: budget = $20 + (10% of available cash)
    # Example: $400 cash -> $20 + $40 = $60 per trade
    TRADE_PCT_OF_CASH: float = float(os.getenv("TRADE_PCT_OF_CASH", 0.10))
    TRADE_BONUS_USD: float = float(os.getenv("TRADE_BONUS_USD", 20.0))
    MIN_TRADE_USD: float = float(os.getenv("MIN_TRADE_USD", 25.0))
    PORTFOLIO_CAP_PCT: float = float(os.getenv("PORTFOLIO_CAP_PCT", 0.70))

    # =========================================================================
    # THOUGHT JOURNAL SYSTEM (V3.9)
    # =========================================================================
    # Enable thought journal for human-readable thinking + ML training data
    THOUGHT_JOURNAL_ENABLED: bool = os.getenv("THOUGHT_JOURNAL_ENABLED", "true").lower() == "true"
    # Print thoughts to console (for supervision)
    THOUGHT_PRINT_TO_CONSOLE: bool = os.getenv("THOUGHT_PRINT_TO_CONSOLE", "true").lower() == "true"
    # How often to print thought stats summary (in ticks)
    THOUGHT_STATS_INTERVAL_TICKS: int = int(os.getenv("THOUGHT_STATS_INTERVAL_TICKS", 20))
    # Paths for thought storage
    THOUGHT_STATE_PATH: str = os.getenv("THOUGHT_STATE_PATH", "./trailing_buy_stop_system/thought_state.json")
    THOUGHT_HISTORY_DB: str = os.getenv("THOUGHT_HISTORY_DB", "./trailing_buy_stop_system/thought_history.db")

    # =========================================================================
    # NOVA PEAK INDICATOR (Daton's Peak Technology V4.0)
    # =========================================================================
    # Enable/disable Nova peak integration with trailing buy-stop system
    NOVA_PEAK_ENABLED: bool = os.getenv("NOVA_PEAK_ENABLED", "false").lower() in ("true", "1", "yes")
    # Mode: "shadow" (log only, no behavior change) or "enforce" (actually apply changes)
    NOVA_PEAK_MODE: str = os.getenv("NOVA_PEAK_MODE", "shadow").lower()
    # Path to trained model file (.pth)
    NOVA_PEAK_MODEL_PATH: str = os.getenv("NOVA_PEAK_MODEL_PATH", "./models/nova_peak_model.pth")
    # Timeframe for peak analysis (must match data collection timeframe)
    NOVA_PEAK_TF: str = os.getenv("NOVA_PEAK_TF", "15m")
    # Probability threshold to classify as peak (0.0-1.0, default 0.80 = 80%)
    NOVA_PEAK_MIN_PROB: float = float(os.getenv("NOVA_PEAK_MIN_PROB", 0.80))
    # Minimum edge (difference between winning/losing probability)
    NOVA_PEAK_MIN_EDGE: float = float(os.getenv("NOVA_PEAK_MIN_EDGE", 0.20))
    # Lock entries on BUY_PEAK detection (chase/top zone - avoid chasing)
    NOVA_LOCK_ON_BUY_PEAK: bool = os.getenv("NOVA_LOCK_ON_BUY_PEAK", "true").lower() in ("true", "1", "yes")
    # Tighten entries on SELL_PEAK detection (bottomless pit - good buy opportunity)
    NOVA_TIGHTEN_ON_SELL_PEAK: bool = os.getenv("NOVA_TIGHTEN_ON_SELL_PEAK", "true").lower() in ("true", "1", "yes")
    # Multiplier for tightening on SELL_PEAK (0.75 = reduce buffer by 25%)
    NOVA_TIGHTEN_MULT: float = float(os.getenv("NOVA_TIGHTEN_MULT", 0.75))
    # Cooldown after BUY_PEAK lock in seconds (prevents churn)
    NOVA_COOLDOWN_SECONDS: float = float(os.getenv("NOVA_COOLDOWN_SECONDS", 60.0))

    # Unified decision report path
    DECISION_REPORT_PATH: str = os.getenv(
        "DECISION_REPORT_PATH",
        os.path.join(LOG_DIR, "decision_report.json")
    )
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Exits
    EXIT_CONFIG = {
        "crypto": {
            "take_profit_pct": 4.0,     # current hard target
            "soft_take_profit_pct": 2.0,  # optional earlier partial/looser logic
            "soft_stop_loss_pct": -3.0,   # consider cutting around here
            "hard_stop_loss_pct": -6.0,   # emergency "get out" line
            "max_hold_hours": 72,         # after this, allow earlier exits
            "breakeven_band_pct": 1.0,    # +/-1% considered "flat"
        },
        "stocks": {
            "take_profit_pct": 3.0,
            "soft_stop_loss_pct": -2.0,
            "hard_stop_loss_pct": -5.0,
            "max_hold_hours": 120,
            "breakeven_band_pct": 0.5,
        },
    }

    # Small Account Mode
    SMALL_ACCOUNT_CONFIG = {
        "enabled": True,
        "equity_threshold": 500.0,       # under this, we treat it as small account
        "min_cash_per_new_position": 50.0,   # don't even consider new positions unless we have at least this
        "max_positions_small": 5,        # stricter cap for small accounts
        "reserve_cash_pct": 0.05,        # always try to keep 5% equity as cash
        "crypto_take_profit_pct": 3.0,   # slightly lower take profit than big accounts
        "crypto_soft_stop_loss_pct": -4.0,
        "crypto_hard_stop_loss_pct": -8.0,
    }

    def __post_init__(self):
        """Load STAKING_ASSET_MAPPING from environment after object creation."""
        mapping_str = os.getenv("STAKING_ASSET_MAPPING", "{}")
        try:
            self.STAKING_ASSET_MAPPING = json.loads(mapping_str)
            if not isinstance(self.STAKING_ASSET_MAPPING, dict):
                self.STAKING_ASSET_MAPPING = {}
        except (json.JSONDecodeError, TypeError):
            self.STAKING_ASSET_MAPPING = {}

CFG = _CFG()
CFG.__post_init__()  # Initialize the STAKING_ASSET_MAPPING

def get_exit_config(asset_type: str, balances: dict | None = None) -> dict:
    base_cfg = CFG.EXIT_CONFIG.get(asset_type, CFG.EXIT_CONFIG["crypto"]).copy()

    if balances is not None and is_small_account(balances):
        # Override some fields for small account behavior
        base_cfg["take_profit_pct"] = CFG.SMALL_ACCOUNT_CONFIG.get("crypto_take_profit_pct", base_cfg["take_profit_pct"])
        base_cfg["soft_stop_loss_pct"] = CFG.SMALL_ACCOUNT_CONFIG.get("crypto_soft_stop_loss_pct", base_cfg["soft_stop_loss_pct"])
        base_cfg["hard_stop_loss_pct"] = CFG.SMALL_ACCOUNT_CONFIG.get("crypto_hard_stop_loss_pct", base_cfg["hard_stop_loss_pct"])

    return base_cfg

def is_small_account(balances: dict) -> bool:
    if not CFG.SMALL_ACCOUNT_CONFIG.get("enabled", True):
        return False
    equity = balances.get("equity", 0.0)
    return equity <= CFG.SMALL_ACCOUNT_CONFIG.get("equity_threshold", 500.0)

def get_min_cash_for_new_position(balances: dict) -> float:
    equity = balances.get("equity", 0.0)
    base_min = CFG.SMALL_ACCOUNT_CONFIG.get("min_cash_per_new_position", 50.0)
    reserve_pct = CFG.SMALL_ACCOUNT_CONFIG.get("reserve_cash_pct", 0.05)
    reserve = equity * reserve_pct
    return base_min + reserve