"""
Curated catalog of strong *free* OpenRouter models (as of 2025).

OpenRouter exposes a generous set of free-tier models that cost $0 to call
but are rate-limited. This module centralises the catalog so the rest of the
codebase can pick the right model for a given task without hard-coding IDs.

Usage:
    from core.openrouter_models import pick_model, FREE_MODELS, BEST_FOR_TASK

    model = pick_model("reasoning")        # -> DeepSeek R1 (free)
    model = pick_model("fast")              # -> Llama 3.3 70B (free)
    model = pick_model("code")              # -> Qwen 2.5 72B (free)
    model = pick_model("long-context")      # -> Gemini 2.0 Flash (1M ctx, free)

These IDs are stable; if OpenRouter retires one, replace it here and the rest
of the codebase picks it up automatically.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ModelInfo:
    id: str               # full slug used in API requests
    label: str            # human-readable
    context_window: int   # tokens
    tags: tuple[str, ...]
    description: str


# Source: https://openrouter.ai/models?max_price=0
FREE_MODELS: tuple[ModelInfo, ...] = (
    ModelInfo(
        id="meta-llama/llama-3.3-70b-instruct:free",
        label="Llama 3.3 70B",
        context_window=131_072,
        tags=("fast", "general", "chat"),
        description="Meta's flagship 70B — best all-rounder free model.",
    ),
    ModelInfo(
        id="deepseek/deepseek-r1:free",
        label="DeepSeek R1",
        context_window=65_536,
        tags=("reasoning",),
        description="Chain-of-thought reasoning model. Slower but very smart.",
    ),
    ModelInfo(
        id="google/gemini-2.0-flash-exp:free",
        label="Gemini 2.0 Flash (via OR)",
        context_window=1_048_576,
        tags=("fast", "long-context", "vision", "multimodal"),
        description="1M-token context via OpenRouter. Multimodal.",
    ),
    ModelInfo(
        id="meta-llama/llama-3.1-8b-instruct:free",
        label="Llama 3.1 8B",
        context_window=131_072,
        tags=("fast", "lightweight"),
        description="Lightweight, ultra-fast. Good for short Q&A.",
    ),
    ModelInfo(
        id="qwen/qwen-2.5-72b-instruct:free",
        label="Qwen 2.5 72B",
        context_window=32_768,
        tags=("code", "multilingual"),
        description="Strong on code + multilingual. Limited context.",
    ),
    ModelInfo(
        id="mistralai/mistral-7b-instruct:free",
        label="Mistral 7B",
        context_window=32_768,
        tags=("fast", "lightweight"),
        description="Classic compact model. Great latency.",
    ),
    ModelInfo(
        id="google/gemma-2-9b-it:free",
        label="Gemma 2 9B",
        context_window=8_192,
        tags=("fast", "lightweight"),
        description="Google's open Gemma. Solid for very short prompts.",
    ),
    ModelInfo(
        id="mistralai/mistral-nemo:free",
        label="Mistral Nemo",
        context_window=131_072,
        tags=("fast", "long-context"),
        description="Mistral + NVIDIA collab. 128k context, fast.",
    ),
    ModelInfo(
        id="qwen/qwen-2.5-coder-32b-instruct:free",
        label="Qwen 2.5 Coder 32B",
        context_window=32_768,
        tags=("code",),
        description="Best free coder — 32B specialised on code.",
    ),
    ModelInfo(
        id="nousresearch/hermes-3-llama-3.1-405b:free",
        label="Hermes 3 405B",
        context_window=131_072,
        tags=("reasoning", "long-context", "general"),
        description="Nous' 405B fine-tune of Llama 3.1. Huge, slow, very smart.",
    ),
)

# Sensible per-task defaults (first matching tag wins).
BEST_FOR_TASK: dict[str, str] = {
    "fast":          "meta-llama/llama-3.3-70b-instruct:free",
    "general":       "meta-llama/llama-3.3-70b-instruct:free",
    "reasoning":     "deepseek/deepseek-r1:free",
    "code":          "qwen/qwen-2.5-coder-32b-instruct:free",
    "long-context":  "google/gemini-2.0-flash-exp:free",
    "lightweight":   "meta-llama/llama-3.1-8b-instruct:free",
    "vision":        "google/gemini-2.0-flash-exp:free",
    "multilingual":  "qwen/qwen-2.5-72b-instruct:free",
}

# Default when nothing else fits.
DEFAULT_MODEL = BEST_FOR_TASK["general"]


def pick_model(task: str) -> str:
    """Return the model id best suited for `task` (one of BEST_FOR_TASK keys)."""
    return BEST_FOR_TASK.get(task, DEFAULT_MODEL)


def model_info(model_id: str) -> Optional[ModelInfo]:
    """Look up ModelInfo by id. Returns None if not in catalog."""
    for m in FREE_MODELS:
        if m.id == model_id:
            return m
    return None


def is_free_model(model_id: str) -> bool:
    """True if `model_id` is in our free catalog."""
    return model_info(model_id) is not None


def list_free_models() -> list[dict]:
    """Return the catalog as plain dicts (for UI / dashboard / debug)."""
    return [
        {
            "id": m.id,
            "label": m.label,
            "context_window": m.context_window,
            "tags": list(m.tags),
            "description": m.description,
        }
        for m in FREE_MODELS
    ]


# ─────────────────────────── runtime discovery ─────────────────────
#
# OpenRouter rotates free-tier models every few weeks — the static
# catalog above can go stale. discover_free_models() hits the live API
# and returns whatever's currently free, cached for 1 hour so we don't
# query on every call.

import json as _json
import sys as _sys
import time as _time
from pathlib import Path as _Path

_CACHE_PATH = (
    _Path(_sys.executable).parent if getattr(_sys, "frozen", False)
    else _Path(__file__).resolve().parent.parent
) / "memory" / "openrouter_free_cache.json"
_CACHE_TTL_SECONDS = 3600  # 1 hour


def discover_free_models(*, force_refresh: bool = False, timeout: int = 10) -> list[ModelInfo]:
    """
    Query the OpenRouter API for currently-free models.

    Returns a list of ModelInfo objects (auto-classified by name heuristic
    into 'general' / 'code' / 'reasoning' / 'long-context' / 'lightweight'
    tags). Cached for 1 hour to avoid hitting the API on every call.

    Args:
      force_refresh: if True, ignore the cache and re-query the API.
      timeout: seconds to wait for the HTTP response.

    Returns:
      List of ModelInfo. Empty list if the API can't be reached.
    """
    # Try the cache first
    if not force_refresh and _CACHE_PATH.exists():
        try:
            cache = _json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            if _time.time() - cache.get("fetched_at", 0) < _CACHE_TTL_SECONDS:
                return [_ModelInfo_from_dict(d) for d in cache.get("models", [])]
        except Exception:
            pass  # cache corrupt — re-fetch

    # Query the live API
    try:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"User-Agent": "Mark-JARVIS/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = _json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"[openrouter_models] discover_free_models failed: {type(e).__name__}: {e}")
        return []

    free = []
    for m in data.get("data", []):
        pricing = m.get("pricing", {})
        try:
            prompt_price = float(pricing.get("prompt", "1") or "1")
            completion_price = float(pricing.get("completion", "1") or "1")
        except (ValueError, TypeError):
            continue
        if prompt_price != 0 or completion_price != 0:
            continue
        mid = m.get("id", "")
        if not mid:
            continue

        # Filter to text-capable models only. OpenRouter exposes
        # architecture.input_modalities + output_modalities per model.
        # We need text-in → text-out (drops vision-only, audio-only, etc).
        arch = m.get("architecture", {}) or {}
        input_mods = arch.get("input_modalities", []) or []
        output_mods = arch.get("output_modalities", []) or []
        if input_mods and "text" not in input_mods:
            continue
        if output_mods and "text" not in output_mods:
            continue

        free.append(ModelInfo(
            id=mid,
            label=m.get("name", mid),
            context_window=int(m.get("context_length", 0) or 0),
            tags=_classify_model(mid),
            description="Discovered free model from OpenRouter API.",
        ))

    # Persist to cache
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(_json.dumps({
            "fetched_at": _time.time(),
            "count": len(free),
            "models": [
                {"id": m.id, "label": m.label,
                 "context_window": m.context_window,
                 "tags": list(m.tags), "description": m.description}
                for m in free
            ],
        }, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[openrouter_models] cache write failed: {e}")

    return free


def _classify_model(model_id: str) -> tuple[str, ...]:
    """Heuristically tag a model based on its slug."""
    mid = model_id.lower()
    tags = ["general"]
    if any(x in mid for x in ("coder", "code", "deepseek-coder", "qwen-coder")):
        tags = ["code"]
    elif any(x in mid for x in ("reason", "r1", "o1", "thinking", "deepseek-r1")):
        tags = ["reasoning"]
    elif any(x in mid for x in ("mini", "nano", "small", "8b", "1b", "2.6b")):
        tags = ["lightweight"]
    elif "gemma" in mid or "flash" in mid:
        tags = ["fast", "general"]
    # Context-length based
    if "1m" in mid or "1000000" in mid or "1048576" in mid:
        tags = tags + ("long-context",)
    return tuple(tags)


def _ModelInfo_from_dict(d: dict) -> ModelInfo:
    return ModelInfo(
        id=d["id"],
        label=d.get("label", d["id"]),
        context_window=int(d.get("context_window", 0) or 0),
        tags=tuple(d.get("tags", ("general",))),
        description=d.get("description", ""),
    )


def get_discovered_or_catalog(*, force_refresh: bool = False) -> list[ModelInfo]:
    """
    Return the discovered-free list if available; fall back to the static
    catalog if the API can't be reached.

    This is what the unified AI client should use as the priority order
    for picking a model.
    """
    discovered = discover_free_models(force_refresh=force_refresh)
    if discovered:
        return discovered
    return list(FREE_MODELS)


def pick_discovered_model(task: str = "general", *, force_refresh: bool = False) -> str:
    """
    Pick a currently-free model for a task. Tries discovered models first;
    falls back to the static catalog if discovery fails.

    Args:
      task: 'general' / 'reasoning' / 'code' / 'fast' / 'lightweight' /
            'long-context' / 'vision' / 'multilingual'
      force_refresh: bypass the cache when discovering

    Returns:
      A model id string (e.g. 'meta-llama/llama-3.3-70b-instruct:free').
    """
    models = get_discovered_or_catalog(force_refresh=force_refresh)
    # First, try to find a discovered model that matches the task tag
    tag = task
    for m in models:
        if tag in m.tags:
            return m.id
    # No tag match — return the first discovered model (or catalog default)
    if models:
        return models[0].id
    return DEFAULT_MODEL


if __name__ == "__main__":
    # Quick smoke test
    import json
    print("=== Static catalog ===")
    print(json.dumps(list_free_models(), indent=2))
    print("\n=== Runtime discovery ===")
    discovered = discover_free_models(force_refresh="--refresh" in _sys.argv)
    print(f"Discovered {len(discovered)} free models live on OpenRouter:")
    for m in discovered[:15]:
        print(f"  • {m.id:60s} (ctx={m.context_window}, tags={m.tags})")
    if discovered:
        print(f"\nDefault discovered: {discovered[0].id}")
        print(f"Pick code:    {pick_discovered_model('code')}")
        print(f"Pick reason:  {pick_discovered_model('reasoning')}")
        print(f"Pick fast:    {pick_discovered_model('fast')}")
