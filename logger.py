"""
Simple file logger for debugging Limitcode.
Writes logs to a local file instead of external service.
"""

import os
import json
import time
from typing import Dict, Any, Optional

# Keep debug logs inside the package so they are easy to inspect from Sublime's
# Packages/Limitcode directory and do not spill into the user profile root.
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "limitcode_debug.log")


def _write_log(level: str, message: str, metadata: Optional[Dict[str, Any]] = None):
    """Write log entry to file."""
    try:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log_entry = {
            "timestamp": timestamp,
            "level": level,
            "message": message,
            "metadata": metadata or {}
        }
        
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
            f.flush()
    except Exception:
        pass  # Silent fail


def log_info(message: str, metadata: Optional[Dict[str, Any]] = None):
    """Log info message (file only; the status bar belongs to agent status updates)."""
    _write_log("info", message, metadata)


def log_error(message: str, metadata: Optional[Dict[str, Any]] = None):
    """Log error message. Errors are rare enough to surface in the status bar."""
    _write_log("error", message, metadata)
    import sublime
    sublime.status_message(f"[Limitcode ERROR] {message}")


def log_debug(message: str, metadata: Optional[Dict[str, Any]] = None):
    """Log debug message."""
    _write_log("debug", message, metadata)
