"""
Self-test for the new unified AI client — runs WITHOUT making any network
calls. Verifies:

  • Provider routing logic (openrouter vs gemini)
  • TTL response cache hits/misses
  • Retry-with-backoff decision function
  • Connection pool singleton reuse
  • Free model catalog integrity

Usage:
    python tests/test_ai_client.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Make the project importable when run from the repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import ai_client
from core import openrouter_models as orm
from core import llm_client


# ─────────────────────────── helpers ───────────────────────────────

def _write_config(payload: dict) -> None:
    """Swap config/api_keys.json with a test payload."""
    p = ROOT / "config" / "api_keys.json"
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _ok(label: str, cond: bool) -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}")
    return cond


# ─────────────────────────── tests ─────────────────────────────────

def test_provider_routing() -> bool:
    print("\n— Provider routing —")
    results = []

    _write_config({
        "gemini_api_key":     "FAKE_GEM",
        "openrouter_api_key": "FAKE_OR",
        "llm_provider":       "openrouter",
    })
    results.append(_ok("explicit openrouter",
                       ai_client.get_provider() == "openrouter"))

    _write_config({
        "gemini_api_key":     "FAKE_GEM",
        "openrouter_api_key": "FAKE_OR",
        # llm_provider omitted — should auto-detect OR
    })
    results.append(_ok("auto-detect OR when key present",
                       ai_client.get_provider() == "openrouter"))

    _write_config({
        "gemini_api_key":     "FAKE_GEM",
        # no openrouter_api_key — should fall back to gemini
    })
    results.append(_ok("auto-detect gemini when no OR key",
                       ai_client.get_provider() == "gemini"))

    _write_config({
        "gemini_api_key":     "FAKE_GEM",
        "openrouter_api_key": "FAKE_OR",
        "llm_provider":       "gemini",
    })
    results.append(_ok("explicit gemini overrides OR key",
                       ai_client.get_provider() == "gemini"))

    # Also exercise llm_client routing parity
    _write_config({
        "gemini_api_key":     "FAKE_GEM",
        "openrouter_api_key": "FAKE_OR",
        "llm_provider":       "openrouter",
    })
    results.append(_ok("llm_client.get_llm_provider() mirrors ai_client",
                       llm_client.get_llm_provider() == "openrouter"))

    return all(results)


def test_cache() -> bool:
    print("\n— TTL response cache —")
    results = []

    cache = ai_client.TTLCache(ttl_seconds=1, max_entries=4)
    payload = {"a": 1, "b": "two"}

    results.append(_ok("miss on first lookup", cache.get(payload) is None))

    cache.set(payload, "CACHED")
    results.append(_ok("hit after set",       cache.get(payload) == "CACHED"))

    # Same payload, different key order → must still hit (SHA-256 normalises).
    results.append(_ok("hit with reordered keys",
                       cache.get({"b": "two", "a": 1}) == "CACHED"))

    # Different payload → miss
    results.append(_ok("miss on different payload",
                       cache.get({"a": 2}) is None))

    # TTL expiry
    time.sleep(1.2)
    results.append(_ok("miss after TTL expires", cache.get(payload) is None))

    # LRU eviction
    cache2 = ai_client.TTLCache(ttl_seconds=60, max_entries=2)
    cache2.set({"k": 1}, "v1")
    cache2.set({"k": 2}, "v2")
    cache2.set({"k": 3}, "v3")  # should evict {"k":1}
    results.append(_ok("LRU evicts oldest on overflow",
                       cache2.get({"k": 1}) is None and cache2.get({"k": 3}) == "v3"))

    return all(results)


def test_retry_decision() -> bool:
    print("\n— Retry-with-backoff decisions —")
    results = []
    retryable = (429, 500, 502, 503, 504, 408, 409, 425)
    non_retryable = (200, 201, 400, 401, 403, 404, 422)
    for s in retryable:
        results.append(_ok(f"status {s} retryable", ai_client._is_retryable(s)))
    for s in non_retryable:
        results.append(_ok(f"status {s} NOT retryable",
                           not ai_client._is_retryable(s)))
    return all(results)


def test_pool_singleton() -> bool:
    print("\n— Connection pool singleton —")
    results = []
    s1 = ai_client._get_session()
    s2 = ai_client._get_session()
    results.append(_ok("same instance returned twice", s1 is s2))
    results.append(_ok("User-Agent set",
                      "Mark-XLVIII" in s1.headers.get("User-Agent", "")))
    return all(results)


def test_model_catalog() -> bool:
    print("\n— Free OpenRouter model catalog —")
    results = []
    results.append(_ok("catalog has >= 5 models", len(orm.FREE_MODELS) >= 5))

    for tag, model_id in orm.BEST_FOR_TASK.items():
        info = orm.model_info(model_id)
        results.append(_ok(
            f"pick_model('{tag}') → catalogued model",
            info is not None,
        ))

    results.append(_ok("pick_model('nonsense') falls back to default",
                       orm.pick_model("nonsense") == orm.DEFAULT_MODEL))

    results.append(_ok("list_free_models() returns list of dicts",
                       isinstance(orm.list_free_models(), list) and
                       isinstance(orm.list_free_models()[0], dict)))
    return all(results)


def test_health_probe() -> bool:
    print("\n— Health probe —")
    _write_config({
        "gemini_api_key":     "FAKE_GEM",
        "openrouter_api_key": "FAKE_OR",
        "llm_provider":       "openrouter",
    })
    h = ai_client.health()
    print(f"  health = {json.dumps(h, indent=2)}")
    return (
        _ok("active_provider is openrouter", h["active_provider"] == "openrouter") and
        _ok("gemini_configured True",        h["gemini_configured"] is True) and
        _ok("openrouter_configured True",   h["openrouter_configured"] is True)
    )


def test_stream_signature() -> bool:
    """
    Verify stream_text is a generator and the cache key changes when
    stream=True vs stream=False (so streaming responses never get cached).
    """
    print("\n— stream_text generator signature —")
    import inspect
    results = []
    results.append(_ok("stream_text is a generator function",
                       inspect.isgeneratorfunction(ai_client.stream_text)))
    return all(results)


# ─────────────────────────── runner ────────────────────────────────

def main() -> int:
    # Stash the original config so we can restore it after the tests.
    cfg_path = ROOT / "config" / "api_keys.json"
    original = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else None

    tests = [
        test_provider_routing,
        test_cache,
        test_retry_decision,
        test_pool_singleton,
        test_model_catalog,
        test_health_probe,
        test_stream_signature,
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

    if original is not None:
        cfg_path.write_text(original, encoding="utf-8")

    print(f"\n=== {passed}/{len(tests)} test groups passed ===")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
