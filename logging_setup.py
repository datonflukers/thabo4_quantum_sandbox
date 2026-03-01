"""
Windows VPS Safe Logging Setup

On Windows Server/RDP sessions, console stdout can fail with OSError: [Errno 22].
This module provides file-only logging by default (most reliable), with optional
safe console output for debugging.
"""
import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from config_factory import CFG


os.makedirs(CFG.LOG_DIR, exist_ok=True)


FMT = "[%(asctime)s] %(levelname)s | %(name)s | %(module)s:%(lineno)d | %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"


_file_handler = None
_error_handler = None
_console_handler = None


class SafeStreamHandler(logging.StreamHandler):
    """StreamHandler that never crashes on Windows console issues."""
    
    def emit(self, record):
        try:
            super().emit(record)
        except OSError:
            pass  # Ignore Windows Errno 22
    
    def flush(self):
        try:
            super().flush()
        except OSError:
            pass  # Ignore Windows Errno 22


def setup_logging(enable_console: bool = None):
    """
    Setup logging for the trading bot.
    
    Args:
        enable_console: If True, enable console output. If None, auto-detect
                       (disabled on Windows VPS / OneDrive paths).
    """
    global _file_handler, _error_handler, _console_handler
    
    root = logging.getLogger()
    root.setLevel(getattr(logging, CFG.LOG_LEVEL if hasattr(CFG, 'LOG_LEVEL') else 'INFO'))
    
    # Clear ALL existing handlers to prevent duplicates
    root.handlers.clear()
    
    fmt = logging.Formatter(FMT, DATEFMT)
    
    # Main rotating log file (daily, keep 14 days)
    run_path = os.path.join(CFG.LOG_DIR, "run.log")
    _file_handler = TimedRotatingFileHandler(
        run_path, when="midnight", backupCount=14, encoding="utf-8"
    )
    _file_handler.setFormatter(fmt)
    _file_handler.setLevel(logging.INFO)
    root.addHandler(_file_handler)
    
    # Error-only log file (daily, keep 30 days)
    err_path = os.path.join(CFG.LOG_DIR, "error.log")
    _error_handler = TimedRotatingFileHandler(
        err_path, when="midnight", backupCount=30, encoding="utf-8"
    )
    _error_handler.setFormatter(fmt)
    _error_handler.setLevel(logging.ERROR)
    root.addHandler(_error_handler)
    
    # Auto-detect if we should enable console
    if enable_console is None:
        # Disable console on Windows if running from OneDrive (VPS sync issues)
        cwd = os.getcwd().lower()
        on_windows_vps = sys.platform == "win32" and "onedrive" in cwd
        enable_console = not on_windows_vps
    
    # Optional console handler (safe on Windows)
    if enable_console:
        _console_handler = SafeStreamHandler(sys.stdout)
        _console_handler.setFormatter(fmt)
        _console_handler.setLevel(logging.INFO)
        root.addHandler(_console_handler)
    
    # Prevent logging internal errors from causing more errors
    root.propagate = False
    logging.raiseExceptions = False
    
    return logging.getLogger("b4kraken")


def setup_root_logging(log_file: str = None, enable_console: bool = None):
    """
    Setup root logging with a specific log file.
    
    Args:
        log_file: Path to log file (defaults to CFG.LOG_FILE)
        enable_console: If True, enable console output
    """
    if log_file is None:
        log_file = CFG.LOG_FILE
    
    global _file_handler, _error_handler, _console_handler
    
    root = logging.getLogger()
    root.setLevel(getattr(logging, CFG.LOG_LEVEL if hasattr(CFG, 'LOG_LEVEL') else 'INFO'))
    
    # Clear ALL existing handlers
    root.handlers.clear()
    
    fmt = logging.Formatter(FMT, DATEFMT)
    
    # Main log file
    _file_handler = logging.FileHandler(log_file, encoding="utf-8")
    _file_handler.setFormatter(fmt)
    _file_handler.setLevel(logging.INFO)
    root.addHandler(_file_handler)
    
    # Error log file
    log_dir = os.path.dirname(log_file)
    err_path = os.path.join(log_dir, "error.log")
    _error_handler = logging.FileHandler(err_path, encoding="utf-8")
    _error_handler.setFormatter(fmt)
    _error_handler.setLevel(logging.ERROR)
    root.addHandler(_error_handler)
    
    # Auto-detect console
    if enable_console is None:
        cwd = os.getcwd().lower()
        on_windows_vps = sys.platform == "win32" and "onedrive" in cwd
        enable_console = not on_windows_vps
    
    if enable_console:
        _console_handler = SafeStreamHandler(sys.stdout)
        _console_handler.setFormatter(fmt)
        _console_handler.setLevel(logging.INFO)
        root.addHandler(_console_handler)
    
    root.propagate = False
    logging.raiseExceptions = False
    
    return logging.getLogger("b4kraken")