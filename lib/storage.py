"""Writable paths for Limitcode user data."""

import os
import shutil

import sublime


def data_path(*parts: str) -> str:
    """Return a path outside the read-only installed package archive."""
    return os.path.join(sublime.packages_path(), "User", "Limitcode", *parts)


def _legacy_history_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "history")


def history_dir() -> str:
    """Return the session directory and preserve histories from manual installs."""
    target = data_path("history")
    legacy = _legacy_history_dir()

    if os.path.isdir(legacy) and os.path.normcase(legacy) != os.path.normcase(target):
        os.makedirs(target, exist_ok=True)
        for name in os.listdir(legacy):
            if not name.endswith((".json", ".md")):
                continue
            source = os.path.join(legacy, name)
            destination = os.path.join(target, name)
            if os.path.isfile(source) and not os.path.exists(destination):
                shutil.copy2(source, destination)

    return target
