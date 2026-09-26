"""
First-run setup wizard for Mark 6.

Walks the user through configuring:
  • Gemini API key (required, validated with core.live_models.is_realtime_key)
  • OpenRouter API key (optional — enables free-text-AI routing)
  • Avatar PNG path (optional — copies to config/avatar.png)
  • Voice sample path (optional — analyses + writes config/voice_profile.json)

The wizard is idempotent: re-running `python setup.py` re-prompts with
the existing values pre-filled, so users can update one field without
re-entering the rest.

Public API (used by setup.py and by main.py's re-config flow):
    from core.setup_wizard import run_wizard, validate_gemini_key

    run_wizard()                       # interactive
    validate_gemini_key("AIza...")     # bool — used to refuse bad keys
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Optional


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = _get_base_dir()
CONFIG_PATH      = BASE_DIR / "config" / "api_keys.json"
AVATAR_PATH      = BASE_DIR / "config" / "avatar.png"
PROFILE_PATH     = BASE_DIR / "config" / "voice_profile.json"
SAMPLES_DIR      = BASE_DIR / "config" / "voice_samples"
SAMPLES_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────── config helpers ───────────────────────

def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def validate_gemini_key(key: str) -> tuple[bool, str]:
    """
    Validate a Gemini API key.

    Two-stage check:
      1. SOFT format check via is_realtime_key() — non-empty, plausible
         length, plausible character set. This never refuses a key.
      2. HARD API verification via verify_key_with_api() — actually pings
         the Gemini API to confirm the key works.

    Returns (ok, message). The message is human-friendly — print it.
    """
    from core.live_models import is_realtime_key, verify_key_with_api, key_format_label
    key = (key or "").strip()
    if not key:
        return False, "Empty key."
    if not is_realtime_key(key):
        # Reject only obvious garbage (whitespace, control chars, way too short)
        return False, (
            "Key doesn't look like a Google API key. "
            "Real keys use letters, digits, hyphen, underscore, dot — "
            "and are at least 20 chars."
        )
    # Soft format label — informational only, never blocks.
    fmt = key_format_label(key)
    # Hard API verification.
    ok, msg, _models = verify_key_with_api(key, timeout=10)
    if ok:
        return True, f"✓ Key verified ({fmt}). {msg}"
    return False, f"✗ Key not verified ({fmt}). {msg}"


def _quick_format_check(key: str) -> tuple[bool, str]:
    """
    Fast local-only check — used in the wizard BEFORE making the API call.
    Catches obvious garbage instantly (no network round-trip).
    """
    from core.live_models import is_realtime_key, key_format_label
    key = (key or "").strip()
    if not key:
        return False, "Empty key."
    if not is_realtime_key(key):
        return False, (
            "Key doesn't look like a Google API key. "
            "Real keys use letters, digits, hyphen, underscore, dot — "
            "and are at least 20 chars."
        )
    fmt = key_format_label(key)
    return True, f"Format looks plausible ({fmt}). Verifying against the API…"


# ─────────────────────────── interactive prompts ─────────────────

def _prompt(label: str, default: str = "", *, required: bool = False,
            help_text: str = "") -> str:
    """One-line input prompt with optional default + help text."""
    if help_text:
        print(f"\n  {help_text}")
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            value = input(f"  {label}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Setup cancelled.")
            sys.exit(1)
        if not value and default:
            return default
        if not value and required:
            print("  ⚠ This field is required. Please enter a value.")
            continue
        return value


def _prompt_gemini_key(existing: str = "") -> str:
    """
    Prompt for a Gemini key.

    Logic:
      • Empty / obviously-garbage input → re-prompt.
      • Otherwise: show format label, attempt API verification, accept
        whatever the user gives even if verification fails (with a clear
        warning — they may want to fix it later or they may be offline).
    """
    print("\n[1/4] Gemini API key (required for voice / Live API)")
    print("  Accepts keys from:")
    print("    • AI Studio:        https://aistudio.google.com/apikey  (starts with 'AIza', 39 chars)")
    print("    • Cloud Console:    console.cloud.google.com → APIs & Services → Credentials")
    print("  I'll verify the key against Google's API before saving.")

    if existing:
        masked = existing[:8] + "…" + existing[-3:]
        print(f"  Current: {masked}")

    while True:
        try:
            value = input("  Enter your Gemini API key (or 'skip' to abort): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Setup cancelled.")
            sys.exit(1)

        if value.lower() == "skip":
            print("  ⚠ Skipping — JARVIS will fail to connect to the Live API.")
            print("    You can re-run `python setup.py` later to add a key.")
            return existing  # keep whatever was there

        if not value:
            print("  ⚠ Key cannot be empty. Try again or type 'skip'.")
            continue

        # Quick local format check (instant)
        ok, msg = _quick_format_check(value)
        if not ok:
            print(f"  ⚠ {msg}")
            continue
        print(f"  {msg}")

        # Real API verification (~200 ms)
        print("  Verifying against Google's API…")
        ok, vmsg = validate_gemini_key(value)
        if ok:
            print(f"  {vmsg}")
            return value

        # API verification failed — but the format is plausible. Let the
        # user decide: try again, force-save anyway, or abort.
        print(f"  {vmsg}")
        print("  Options: [r]etry  [f]orce-save anyway  [s]kip")
        try:
            choice = input("  Choice: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Setup cancelled.")
            sys.exit(1)
        if choice == "f":
            print("  ⚠ Saving the key as-is. JARVIS may fail to start — fix it later by re-running setup.")
            return value
        if choice == "s":
            return existing
        # Anything else → loop and try again


def _prompt_openrouter_key(existing: str = "") -> str:
    """Prompt for OpenRouter key. Optional — empty is fine."""
    print("\n[2/4] OpenRouter API key (optional — for free text AI)")
    print("  Get a free one at: https://openrouter.ai/keys")
    print("  Free models route text actions through Llama 3.3 / DeepSeek R1 / etc.")
    if existing:
        masked = existing[:10] + "…" + existing[-3:]
        print(f"  Current: {masked}")
    try:
        value = input("  Enter OpenRouter key (or press Enter to skip): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Setup cancelled.")
        sys.exit(1)
    return value


def _prompt_avatar_png(existing_path: Optional[Path]) -> Optional[Path]:
    """Prompt for an avatar PNG path. Copies to config/avatar.png."""
    print("\n[3/4] Avatar PNG (optional — JARVIS's animated face)")
    print("  Drop in any PNG — JARVIS auto-detects the mouth for lip-sync.")
    if existing_path and existing_path.exists():
        print(f"  Current: {existing_path}")
    try:
        value = input("  Path to PNG (or press Enter to skip): ").strip().strip('"').strip("'")
    except (EOFError, KeyboardInterrupt):
        print("\n  Setup cancelled.")
        sys.exit(1)
    if not value:
        return None
    p = Path(value).expanduser().resolve()
    if not p.exists():
        print(f"  ⚠ File not found: {p}")
        return None
    if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        print(f"  ⚠ Not a recognised image format: {p.suffix}")
        return None
    try:
        shutil.copy2(p, AVATAR_PATH)
        print(f"  ✓ Copied to {AVATAR_PATH}")
        return AVATAR_PATH
    except Exception as e:
        print(f"  ⚠ Copy failed: {e}")
        return None


def _prompt_voice_sample() -> Optional[str]:
    """Prompt for a voice sample. Runs analyze_audio() if provided."""
    print("\n[4/4] Voice sample (optional — JARVIS's voice)")
    print("  5-30s WAV/MP3/M4A of the voice you want JARVIS to use.")
    print("  JARVIS will pitch-shift + tempo-adjust to match.")
    try:
        value = input("  Path to audio (or press Enter to skip): ").strip().strip('"').strip("'")
    except (EOFError, KeyboardInterrupt):
        print("\n  Setup cancelled.")
        sys.exit(1)
    if not value:
        return None
    p = Path(value).expanduser().resolve()
    if not p.exists():
        print(f"  ⚠ File not found: {p}")
        return None
    # Stash a copy
    dest = SAMPLES_DIR / f"wizard{p.suffix}"
    try:
        shutil.copy2(p, dest)
    except Exception as e:
        print(f"  ⚠ Copy failed: {e}")
        return None
    try:
        from core.voice_cloner import analyze_audio
        prof = analyze_audio(dest, label="wizard_voice")
        print(f"  ✓ Voice profile built. Avg pitch: {prof['mean_f0_hz']:.0f} Hz, "
              f"shift {prof['target_pitch_shift_semitones']:+.1f} semitones, "
              f"speed {prof['target_speed_factor']:.2f}x.")
        return str(dest)
    except Exception as e:
        print(f"  ⚠ Voice analysis failed: {e}")
        print("  You can re-run `python setup.py` later to retry, or call set_voice from the Live API.")
        return None


# ─────────────────────────── main entry ───────────────────────────

def run_wizard(*, non_interactive: bool = False) -> dict:
    """
    Run the full first-run setup wizard.

    Args:
      non_interactive: if True, only prints what would be asked and
        exits without prompting — used by tests + CI.

    Returns the final config dict that was written to disk.
    """
    print()
    print("=" * 60)
    print("  MARK 6 — First-run setup wizard")
    print("=" * 60)

    cfg = _load_config()
    if cfg:
        print("  Existing config detected — pre-filling with current values.")
        print("  Press Enter to keep each value as-is, or type a new one.")

    if non_interactive:
        print("\n  [non-interactive mode — would prompt for:]")
        print("    1. Gemini API key (required, validated)")
        print("    2. OpenRouter API key (optional)")
        print("    3. Avatar PNG path (optional)")
        print("    4. Voice sample path (optional)")
        return cfg

    # 1. Gemini key (required, validated)
    gemini_key = _prompt_gemini_key(existing=cfg.get("gemini_api_key", ""))
    cfg["gemini_api_key"] = gemini_key

    # 2. OpenRouter key (optional)
    or_value = _prompt_openrouter_key(existing=cfg.get("openrouter_api_key", ""))
    cfg["openrouter_api_key"] = or_value
    if or_value:
        cfg["llm_provider"] = "openrouter"
        print("  ✓ llm_provider set to 'openrouter' (free text AI).")

    # 3. Avatar PNG (optional)
    existing_avatar = AVATAR_PATH if AVATAR_PATH.exists() else None
    _prompt_avatar_png(existing_avatar)

    # 4. Voice sample (optional)
    _prompt_voice_sample()

    # Default model fields (user can edit later)
    cfg.setdefault("llm_provider", "openrouter")
    cfg.setdefault("gemini_model", "gemini-2.5-flash")
    cfg.setdefault("openrouter_model", "meta-llama/llama-3.3-70b-instruct:free")
    cfg.setdefault("os_system", "auto")

    _save_config(cfg)
    print()
    print(f"  ✓ Configuration saved to {CONFIG_PATH}")
    if AVATAR_PATH.exists():
        print(f"  ✓ Avatar installed at {AVATAR_PATH}")
    if PROFILE_PATH.exists():
        print(f"  ✓ Voice profile at {PROFILE_PATH}")

    print()
    print("  " + "=" * 56)
    print("  ✓ Setup complete! Run `python main.py` to start JARVIS.")
    print("  " + "=" * 56)
    print()
    return cfg


if __name__ == "__main__":
    run_wizard(non_interactive="--non-interactive" in sys.argv)
