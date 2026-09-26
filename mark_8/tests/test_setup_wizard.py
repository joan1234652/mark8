"""
Self-test for core/setup_wizard.py — verifies the key-validation logic
without prompting for input (uses non-interactive mode).

Note: the actual API verification is NOT run here (no network calls in
tests). We monkey-patch verify_key_with_api to return canned results so
we can exercise the wizard's branching logic offline.

Usage:
    python tests/test_setup_wizard.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import setup_wizard
from core import live_models


def _ok(label: str, cond: bool) -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}")
    return cond


# Use 39-char AIza keys for AI Studio tests (the real AI Studio length).
_AIZA_KEY = "AIzaSyDabcdefghijklmnopqrstuvwxyz012345"  # exactly 39 chars
_AQ_KEY = "AQ.Ab8RN6I1PaE9JCYzgwlwrhlkIzoNflehIFT04GrdiIZ70quedw"  # Cloud Console format


def test_validate_gemini_key_format() -> bool:
    """Verify the soft format check accepts both AI Studio + Cloud Console keys."""
    print("\n— Soft format check (both AI Studio + Cloud Console accepted) —")
    results = []

    # Cloud Console key — the user's actual format
    results.append(_ok("AQ.* Cloud Console key accepted by format check",
                       live_models.is_realtime_key(_AQ_KEY) is True))

    # AI Studio key — 39 chars
    results.append(_ok("AIza* AI Studio key accepted",
                       live_models.is_realtime_key(_AIZA_KEY) is True))

    # Format labels
    results.append(_ok("AQ.* labeled as 'Cloud Console'",
                       live_models.key_format_label(_AQ_KEY) == "Cloud Console"))
    results.append(_ok("AIzaSy labeled as 'AI Studio'",
                       live_models.key_format_label(_AIZA_KEY) == "AI Studio"))

    # Bad input
    results.append(_ok("empty key rejected", live_models.is_realtime_key("") is False))
    results.append(_ok("whitespace-only rejected",
                       live_models.is_realtime_key("   ") is False))
    results.append(_ok("very short key rejected",
                       live_models.is_realtime_key("AIzaSy") is False))
    results.append(_ok("key with spaces rejected",
                       live_models.is_realtime_key("AIzaSy abc def ghi") is False))

    return all(results)


def test_validate_gemini_key_with_api_mocked() -> bool:
    """Verify the 2-stage validate_gemini_key() with the API call mocked."""
    print("\n— 2-stage validation (API mocked) —")
    results = []

    # Mock verify_key_with_api to return success for any 20+ char key
    original_verify = live_models.verify_key_with_api

    def mock_verify(key, *, timeout=8):
        if not key:
            return False, "Empty key.", []
        if len(key) >= 20:
            return True, "✓ Key verified — 42 models accessible.", ["gemini-2.5-flash"]
        return False, "✗ API error", []

    live_models.verify_key_with_api = mock_verify  # type: ignore
    try:
        # AQ key — should be accepted (format + API both pass)
        ok, msg = setup_wizard.validate_gemini_key(_AQ_KEY)
        results.append(_ok("AQ key accepted when API mock returns success",
                           ok is True and "verified" in msg.lower()))

        # AIza key — accepted
        ok, msg = setup_wizard.validate_gemini_key(_AIZA_KEY)
        results.append(_ok("AIza key accepted",
                           ok is True and "verified" in msg.lower()))

        # Mock to return failure
        live_models.verify_key_with_api = lambda key, *, timeout=8: (False, "Google rejected the key (400)", [])  # type: ignore
        ok, msg = setup_wizard.validate_gemini_key(_AQ_KEY)
        results.append(_ok("AQ key rejected when API mock returns failure",
                           ok is False and "Google rejected" in msg))

        # Empty key still rejected by format check (before API call)
        ok, msg = setup_wizard.validate_gemini_key("")
        results.append(_ok("empty key rejected before API call",
                           ok is False and "Empty" in msg))

    finally:
        live_models.verify_key_with_api = original_verify  # type: ignore
    return all(results)


def test_quick_format_check() -> bool:
    """The fast local-only check used before the API call."""
    print("\n— _quick_format_check (no network) —")
    results = []

    ok, msg = setup_wizard._quick_format_check(_AQ_KEY)
    results.append(_ok("AQ key passes quick check",
                       ok is True and "Cloud Console" in msg))

    ok, _msg = setup_wizard._quick_format_check(_AIZA_KEY)
    results.append(_ok("AIza key passes quick check", ok is True))

    ok, _msg = setup_wizard._quick_format_check("")
    results.append(_ok("empty key fails quick check", ok is False))

    ok, _msg = setup_wizard._quick_format_check("x")  # too short
    results.append(_ok("short key fails quick check", ok is False))

    return all(results)


def test_non_interactive_mode() -> bool:
    print("\n— Non-interactive mode (CI-safe) —")
    results = []
    cfg = setup_wizard.run_wizard(non_interactive=True)
    results.append(_ok("non-interactive mode returns a dict",
                       isinstance(cfg, dict)))
    return all(results)


def test_config_round_trip() -> bool:
    print("\n— Config save / load round-trip —")
    results = []
    original = setup_wizard.CONFIG_PATH.read_text(encoding="utf-8") \
        if setup_wizard.CONFIG_PATH.exists() else None

    try:
        fake_cfg = {
            "gemini_api_key": "AQ.Ab8RTestKeyForRoundTripVerification00000",
            "llm_provider": "openrouter",
        }
        setup_wizard._save_config(fake_cfg)
        loaded = setup_wizard._load_config()
        results.append(_ok("saved config reloads",
                           loaded.get("gemini_api_key", "").startswith("AQ.")))
        results.append(_ok("config preserved llm_provider",
                           loaded.get("llm_provider") == "openrouter"))
    finally:
        if original is not None:
            setup_wizard.CONFIG_PATH.write_text(original, encoding="utf-8")
        elif setup_wizard.CONFIG_PATH.exists():
            setup_wizard.CONFIG_PATH.unlink()

    return all(results)


def main() -> int:
    tests = [
        test_validate_gemini_key_format,
        test_validate_gemini_key_with_api_mocked,
        test_quick_format_check,
        test_non_interactive_mode,
        test_config_round_trip,
    ]
    passed = 0
    for t in tests:
        try:
            if t():
                passed += 1
                print(f"  → {t.__name__} OK")
            else:
                print(f"  → {t.__name__} had failures")
        except Exception as e:
            print(f"  → {t.__name__} raised {type(e).__name__}: {e}")
    print(f"\n=== {passed}/{len(tests)} test groups passed ===")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
