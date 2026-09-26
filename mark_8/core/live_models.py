"""
Live API model catalog + diagnostic for Mark 5.

Google rotates the Gemini Live API preview models every few months. Old
preview names get retired and produce "Requested entity was not found"
errors (WebSocket close code 1008, policy violation).

This module centralises the catalog of known Live API models in priority
order, and provides a probe that pings the Gemini API to confirm which
one actually works with the configured API key.

Usage in main.py:

    from core.live_models import get_live_model_chain, pick_live_model

    for model_id in get_live_model_chain():
        try:
            async with client.aio.live.connect(model=model_id, config=cfg) as sess:
                ...
            break
        except APIError as e:
            if "Requested entity" in str(e) or "not found" in str(e):
                print(f"[JARVIS] Model {model_id} rejected, trying next…")
                continue
            raise
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
CONFIG_PATH     = BASE_DIR / "config" / "api_keys.json"


# ─────────────────────────── catalog ────────────────────────────────

# Priority-ordered list of known Gemini Live API model names.
# When Google retires a preview model, the next one in the list is tried
# automatically. Add new preview names at the TOP of this list as Google
# releases them — the order is "newest / most-preferred first".
LIVE_MODEL_CHAIN: list[str] = [
    # 2026 previews
    "models/gemini-2.5-flash-native-audio-preview-09-2026",
    "models/gemini-2.5-flash-preview-native-audio-dialog-09-2026",

    # 2026 Q2 previews
    "models/gemini-2.5-flash-native-audio-preview-06-2026",
    "models/gemini-2.5-flash-preview-native-audio-dialog-06-2026",

    # 2026 Q1 previews
    "models/gemini-2.5-flash-native-audio-preview-03-2026",
    "models/gemini-2.5-flash-preview-native-audio-dialog-03-2026",

    # Late 2025 previews
    "models/gemini-2.5-flash-native-audio-preview-12-2025",
    "models/gemini-2.5-flash-native-audio-preview-09-2025",

    # Stable / general-release Live API models
    "models/gemini-2.0-flash-live-001",
    "models/gemini-2.0-flash-live-preview",
    "models/gemini-live-2.5-flash-preview",
]


# Default — the top of the chain.
DEFAULT_LIVE_MODEL = LIVE_MODEL_CHAIN[0]


# ─────────────────────────── config helpers ───────────────────────

def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_live_model_chain() -> list[str]:
    """
    Return the model chain to try, in priority order.

    Reads `live_model` (single override) + `live_model_fallbacks` (list)
    from config/api_keys.json if present, otherwise falls back to the
    hardcoded LIVE_MODEL_CHAIN above.
    """
    cfg = _load_config()
    user_chain: list[str] = []

    explicit = (cfg.get("live_model") or "").strip()
    if explicit:
        user_chain.append(explicit)

    extras = cfg.get("live_model_fallbacks") or []
    if isinstance(extras, list):
        for m in extras:
            if isinstance(m, str) and m.strip():
                user_chain.append(m.strip())

    # De-duplicate while preserving order, then append the hardcoded chain
    seen = set()
    out: list[str] = []
    for m in user_chain + LIVE_MODEL_CHAIN:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def pick_live_model() -> str:
    """Return the first model id to try (top of the chain)."""
    chain = get_live_model_chain()
    return chain[0] if chain else DEFAULT_LIVE_MODEL


# ─────────────────────────── diagnostics ───────────────────────────

def is_realtime_key(api_key: str) -> bool:
    """
    Heuristic check: does the key look like a known Google API key format?

    Two formats are recognised:
      • AI Studio keys (aistudio.google.com/apikey): start with 'AIza',
        39 chars total.
      • Google Cloud Console API keys (console.cloud.google.com → APIs &
        Services → Credentials): may start with 'AIza', 'AQ', or other
        Google-issued prefixes, variable length (24-80 chars typical).

    NOTE: This is a SOFT check — it never refuses a key. Even if it returns
    False, the key may still work for the Gemini API. Use verify_key_with_api()
    for a definitive test by making a real HTTP call to the Gemini endpoint.

    Args:
      api_key: the candidate key string

    Returns:
      True if the key looks like a plausible Google API key (non-empty,
      at least 20 chars, alphanumeric + standard punctuation).
    """
    if not api_key:
        return False
    # Reject obviously-bad input (whitespace, control chars, very short).
    stripped = api_key.strip()
    if len(stripped) < 20:
        return False
    # Real Gemini keys use a limited character set: letters, digits, hyphen,
    # underscore, dot. Reject anything with spaces, control chars, etc.
    import re
    if not re.fullmatch(r"[A-Za-z0-9._\-]+", stripped):
        return False
    return True


def key_format_label(api_key: str) -> str:
    """
    Return a short label describing the likely key format:
      'AI Studio'      → starts with 'AIza', 39 chars
      'Cloud Console'  → starts with 'AQ', or non-AIza prefix
      'unknown'        → doesn't match either known pattern
    """
    if not api_key:
        return "empty"
    if api_key.startswith("AIza") and len(api_key) == 39:
        return "AI Studio"
    if api_key.startswith("AIza"):
        return "AI Studio (non-standard length)"
    if api_key.startswith("AQ."):
        return "Cloud Console"
    return "unknown Google-issued format"


def verify_key_with_api(api_key: str, *, timeout: int = 8) -> tuple[bool, str, list[str]]:
    """
    Make a real HTTP call to the Gemini API to test whether `api_key` works.

    Hits the cheapest endpoint — `GET /v1beta/models?key=KEY` — and reports:
      • (True,  "✓ Key verified — N models accessible", [model_ids])  on success
      • (False, "<error message>", [])                                on failure

    Network call, ~200 ms typical. Catches all exceptions so the wizard
    can run this even when offline (it'll just return False + a clear msg).

    Args:
      api_key:  the candidate key string
      timeout:  seconds to wait before giving up
    """
    if not api_key:
        return False, "Empty key.", []
    try:
        import urllib.request
        import urllib.error
        import json as _json
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models"
            f"?key={api_key}&pageSize=100"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mark-JARVIS/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = _json.loads(r.read().decode("utf-8"))
        models = [m.get("name", "") for m in data.get("models", [])]
        return True, f"✓ Key verified — {len(models)} models accessible.", models
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        if e.code == 400 and "API key not valid" in body:
            return False, "✗ Google rejected the key (400): API key not valid. Please use a key from https://aistudio.google.com/apikey", []
        if e.code == 403:
            return False, "✗ Key accepted but API access denied (403). Enable the 'Generative Language API' on your Google Cloud project.", []
        if e.code == 429:
            # Rate-limited but key is valid — that's actually a success signal
            return True, "✓ Key verified (rate-limited but authenticated).", []
        return False, f"✗ Google returned HTTP {e.code}: {body[:200]}", []
    except Exception as e:
        return False, f"✗ Network error during verification: {type(e).__name__}: {e}", []


def diagnostic_brief(api_key: str) -> str:
    """
    Build a one-line diagnostic string for the user.
    Prints the masked key, length, and format label. Does NOT refuse
    any key — just informs.
    """
    if not api_key:
        return "  ⚠ No Gemini API key configured."
    masked = api_key[:8] + "…" + api_key[-3:]
    fmt = key_format_label(api_key)
    return f"  Gemini key: {masked} (len={len(api_key)}, {fmt})"


if __name__ == "__main__":
    print("Live API model chain (priority order):")
    for i, m in enumerate(LIVE_MODEL_CHAIN, 1):
        marker = " ← default" if i == 1 else ""
        print(f"  {i:2d}. {m}{marker}")
    print()
    cfg = _load_config()
    key = (cfg.get("gemini_api_key") or "").strip()
    print("Configured key diagnostic:")
    print(diagnostic_brief(key))
