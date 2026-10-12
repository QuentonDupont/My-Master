"""Alert-once notifications and heartbeats for scheduled flows.

The Python twin of tools/notify_on_failure.sh, using the same files so either
side can clear the other's marker:

    logs/.markers/<marker>      present = already alerted, stay quiet
    logs/.heartbeat_<name>      UTC timestamp of the last fully successful run

A notification fires once per marker until `clear()` runs for it — without
that, a 5-minute cadence would renotify every 5 minutes for as long as
something stays broken. The mechanism is `osascript`, the only one a headless
launchd job has.
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys

from core import config


def _marker(name: str):
    return config.LOG_DIR / ".markers" / name


def alert_once(marker: str, title: str, message: str) -> bool:
    """Notify unless this marker is already raised. True if it notified."""
    path = _marker(marker)
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    notify(title, message)
    return True


def clear(marker: str) -> None:
    try:
        _marker(marker).unlink()
    except FileNotFoundError:
        pass


def heartbeat(name: str) -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    (config.LOG_DIR / f".heartbeat_{name}").write_text(
        dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") + "\n")


def notify(title: str, message: str) -> None:
    if sys.platform != "darwin":
        return
    script = (f"display notification {_q(message)} with title {_q(title)} "
              f'sound name "Basso"')
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def _q(text: str) -> str:
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'
