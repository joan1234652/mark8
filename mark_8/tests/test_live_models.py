"""
Self-test for core/live_models.py — verifies the model fallback chain
and the API key format heuristic.

Usage:
    python tests/test_live_models.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import live_models as lm


def _ok(label: str, cond: bool) -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}")
    return cond


def test_chain() -> bool:
    print("\n— Model fallback chain —")
    results = []
    chain = lm.get_live_model_chain()
    results.append(_ok(f"chain has >= 5 models (got {len(chain)})", len(chain) >= 5))
    results.append(_ok("default = first entry of chain",
                       lm.DEFAULT_LIVE_MODEL == lm.LIVE_MODEL_CHAIN[0]))
    results.append(_ok("pick_live_model returns a string",
                       isinstance(lm.pick_live_model(), str)))
    results.append(_ok("every chain entry starts with 'models/'",
                       all(m.startswith("models/") for m in chain)))
    return all(results)


def test_key_heuristic() -> bool:
    print("\n— API key format heuristic (soft check) —")
    results = []

    # User's actual key from the bug report — Cloud Console format, 53 chars.
    # The soft check now ACCEPTS this (only rejects empty / too short / bad chars).
    aq_key = "AQ.Ab8RN6I1PaE9JCYzgwlwrhlkIzoNflehIFT04GrdiIZ70quedw"
    results.append(_ok("AQ.* Cloud Console key accepted by soft format check",
                       lm.is_realtime_key(aq_key) is True))
    results.append(_ok("AQ.* labeled as 'Cloud Console'",
                       lm.key_format_label(aq_key) == "Cloud Console"))

    # AI Studio key — accepted, labeled correctly. Must be EXACTLY 39 chars.
    real_key = "AIzaSyDabcdefghijklmnopqrstuvwxyz012345"  # 39 chars exactly
    results.append(_ok("Real 39-char AIza key accepted",
                       lm.is_realtime_key(real_key) is True))
    results.append(_ok("AIzaSy labeled as 'AI Studio'",
                       lm.key_format_label(real_key) == "AI Studio"))

    # Rejections
    results.append(_ok("empty key rejected",
                       lm.is_realtime_key("") is False))
    results.append(_ok("None key rejected",
                       lm.is_realtime_key(None) is False))  # type: ignore[arg-type]
    results.append(_ok("very short key rejected",
                       lm.is_realtime_key("AIzaSy") is False))
    results.append(_ok("key with spaces rejected",
                       lm.is_realtime_key("AIzaSy abc def ghi") is False))

    return all(results)


def test_diagnostic_brief() -> bool:
    print("\n— Diagnostic brief —")
    results = []
    brief = lm.diagnostic_brief("AQ.Ab8RN6I1PaE9JCYzgwlwrhlkIzoNflehIFT04GrdiIZ70quedw")
    results.append(_ok("AQ key brief mentions 'Cloud Console'",
                       "Cloud Console" in brief))
    results.append(_ok("AQ key brief shows masked form (…)",
                       "…" in brief))

    empty_brief = lm.diagnostic_brief("")
    results.append(_ok("empty key brief mentions 'No Gemini API key'",
                       "No Gemini API key" in empty_brief))
    return all(results)


def main() -> int:
    tests = [test_chain, test_key_heuristic, test_diagnostic_brief]
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
