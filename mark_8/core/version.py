"""
Version helpers for the Mark series.

Convention: every release increments the version number in the name.
Read this module to display the active version in the UI / logs.

    >>> from core.version import get_version_name, get_version_number
    >>> get_version_name()
    'Mark 3'
    >>> get_version_number()
    3
"""
from __future__ import annotations
from pathlib import Path
import re


_VERSION_FILE = Path(__file__).resolve().parent / "VERSION"


def get_version_name() -> str:
    """Return the human-readable version name, e.g. 'Mark 3'."""
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return "Mark ?"


def get_version_number() -> int:
    """Return the integer version number, e.g. 3."""
    name = get_version_name()
    m = re.search(r"\d+", name)
    return int(m.group(0)) if m else 0


def bump_version() -> int:
    """
    Increment the version. Returns the new number.
    Call this at the start of every release that adds user-visible features.
    """
    n = get_version_number()
    new_name = f"Mark {n + 1}"
    _VERSION_FILE.write_text(new_name + "\n", encoding="utf-8")
    return n + 1


if __name__ == "__main__":
    print(f"Active version: {get_version_name()}")
    print(f"Active number: {get_version_number()}")
