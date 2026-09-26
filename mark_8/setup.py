"""
Mark 6 — First-run setup script.

Run this ONCE before starting JARVIS for the first time:
    python setup.py

It will:
  1. Install Python dependencies from requirements.txt
  2. Install Playwright browser binaries (used by actions/browser_control.py)
  3. Run the interactive setup wizard, which:
     • Asks for your Gemini API key (required)
       - Validates the format (must start with 'AIza' and be 39 chars)
       - Refuses to write a bad key — you'll be re-prompted
     • Asks for an OpenRouter API key (optional, free text AI)
     • Asks for an avatar PNG path (optional, JARVIS's animated face)
     • Asks for a voice sample path (optional, JARVIS's voice)
     • Saves everything to config/api_keys.json

Re-running `python setup.py` is safe — it re-prompts with current
values pre-filled so you can update individual fields without losing
the rest.

Get a free Gemini API key at: https://aistudio.google.com/apikey
Get a free OpenRouter API key at: https://openrouter.ai/keys
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _install_requirements() -> bool:
    """Install requirements.txt via pip. Returns True on success."""
    req_path = Path(__file__).resolve().parent / "requirements.txt"
    if not req_path.exists():
        print("[setup] requirements.txt not found — skipping pip install.")
        return True
    print("[setup] Installing Python dependencies (this may take a few minutes)…")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req_path)],
            check=True,
        )
        print("[setup] ✓ Python dependencies installed.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[setup] ⚠ pip install failed (exit {e.returncode}).")
        print("       Some optional dependencies may be missing — JARVIS will")
        print("       still run, but some action modules may be disabled.")
        return False


def _install_playwright() -> bool:
    """Install Playwright browser binaries. Returns True on success."""
    print("[setup] Installing Playwright browser binaries (~300 MB download)…")
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install"],
            check=True,
        )
        print("[setup] ✓ Playwright browsers installed.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"[setup] ⚠ Playwright install failed (exit {e.returncode}).")
        print("       Browser-control actions will be disabled.")
        return False
    except FileNotFoundError:
        print("[setup] ⚠ Playwright not installed — skipping browser setup.")
        print("       Run `pip install playwright` if you want browser control.")
        return False


def _run_wizard() -> None:
    """Run the first-run config wizard."""
    print()
    print("[setup] ─── Configuration wizard ───")
    try:
        from core.setup_wizard import run_wizard
        run_wizard()
    except Exception as e:
        print(f"[setup] ⚠ Wizard failed: {e}")
        print("       You can configure manually by editing config/api_keys.json")
        raise


def main() -> int:
    print()
    print("=" * 60)
    print("  MARK 6 — Setup")
    print("=" * 60)
    print()

    _install_requirements()
    print()
    _install_playwright()
    print()
    _run_wizard()
    return 0


if __name__ == "__main__":
    sys.exit(main())
