"""
Unified AI client for MARK XLVIII.

A single drop-in surface that routes text-generation calls between:

  • Gemini  (google-genai SDK)  — for the voice / Live API and as a fallback
  • OpenRouter (free models)    — for cost-saving text-only calls

Why this exists
───────────────
Before, every action module re-implemented its own `genai.Client(...)`
boilerplate and we had no way to swap providers. This client:

  1. Gives every action the same `generate_text()` / `stream_text()` /
     `generate_with_tools()` surface.
  2. Reads provider config from `config/api_keys.json`:
        {
          "gemini_api_key":     "AIza…",
          "openrouter_api_key": "sk-or-v1-…",      # optional
          "llm_provider":       "openrouter"        # or "gemini"
        }
  3. Routes OpenRouter calls as raw HTTP (no `openai` SDK dependency needed).
  4. Adds three efficiency layers:
        - TTL response cache        (skip duplicate calls within 5 min)
        - Connection pool singleton (reuse TCP connections across calls)
        - Retry with backoff        (429 / 5xx → 0.5s, 1s, 2s, 4s)

Tool calling is supported on both providers; the surface is intentionally
narrow so action modules don't need to know which backend served them.
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Generator, Optional

import requests


# ───────────────────────── paths / constants ────────────────────────────

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
CONFIG_PATH     = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"

# OpenRouter HTTP endpoint (OpenAI-compatible chat completions).
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_REFERER = "https://github.com/hasan8123/Mark-XLVIII"
OPENROUTER_TITLE   = "Mark XLVIII"

# Sensible defaults if api_keys.json is missing fields.
_DEFAULT_GEMINI_MODEL    = "gemini-2.5-flash"
_DEFAULT_OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"


# ───────────────────────── config helpers ────────────────────────────────

def _load_config() -> dict:
    """Read config/api_keys.json. Returns {} on any error (never raises)."""
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_provider() -> str:
    """
    Returns the configured text-LLM provider: 'openrouter' or 'gemini'.

    Default is 'openrouter' (free AI) when an OpenRouter key is present,
    else 'gemini'.
    """
    cfg = _load_config()
    raw = (cfg.get("llm_provider") or "").strip().lower()
    if raw in ("openrouter", "gemini"):
        return raw
    # Auto-pick: prefer OpenRouter if a key is set, otherwise Gemini.
    if (cfg.get("openrouter_api_key") or "").strip():
        return "openrouter"
    return "gemini"


def get_gemini_key() -> str:
    return (_load_config().get("gemini_api_key") or "").strip()


def get_openrouter_key() -> str:
    return (_load_config().get("openrouter_api_key") or "").strip()


def get_active_model(provider: Optional[str] = None) -> str:
    """
    Active model id for the given provider (or the active one).
    Reads `openrouter_model` / `gemini_model` from config, falls back to
    curated defaults from `core.openrouter_models`.
    """
    cfg = _load_config()
    p = provider or get_provider()
    if p == "openrouter":
        # Lazy import to avoid pulling the catalog when not needed.
        from core.openrouter_models import DEFAULT_MODEL
        return (cfg.get("openrouter_model") or DEFAULT_MODEL).strip()
    return (cfg.get("gemini_model") or _DEFAULT_GEMINI_MODEL).strip()


# ───────────────────────── connection pool ───────────────────────────────

# Reuse a single Session across all OpenRouter requests. This keeps TCP
# connections warm and eliminates the TLS handshake cost on repeat calls
# (typically shaves 100-300 ms per call after the first).
_session_lock = threading.Lock()
_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            # Disable urllib3's warn-on-keepalive noise on Python 3.12+
            s.headers.update({
                "User-Agent": "Mark-XLVIII/1.0 (+https://github.com/hasan8123/Mark-XLVIII)",
            })
            _session = s
        return _session


# ───────────────────────── response cache ────────────────────────────────

class TTLCache:
    """
    Tiny in-process TTL cache. Saves the cost of identical repeat calls
    (e.g. dev_agent calling the planner twice on the same source diff).
    Capped at 256 entries — LRU-eviction happens implicitly via dict ordering.
    """
    def __init__(self, ttl_seconds: int = 300, max_entries: int = 256):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def _key(self, payload: dict) -> str:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, payload: dict) -> Optional[Any]:
        k = self._key(payload)
        with self._lock:
            entry = self._store.get(k)
            if not entry:
                return None
            ts, value = entry
            if time.time() - ts > self.ttl:
                self._store.pop(k, None)
                return None
            # Move to end (LRU refresh).
            self._store.pop(k, None)
            self._store[k] = (ts, value)
            return value

    def set(self, payload: dict, value: Any) -> None:
        k = self._key(payload)
        with self._lock:
            self._store[k] = (time.time(), value)
            if len(self._store) > self.max_entries:
                # Drop oldest (FIFO since Python 3.7 dict preserves insertion order).
                oldest = next(iter(self._store))
                self._store.pop(oldest, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_cache = TTLCache(ttl_seconds=300, max_entries=256)


# ───────────────────────── retry helpers ────────────────────────────────

def _is_retryable(status: int) -> bool:
    """HTTP statuses we consider transient."""
    return status in (408, 409, 425, 429, 500, 502, 503, 504)


def _retry_with_backoff(
    fn: Callable[[], requests.Response],
    max_attempts: int = 4,
) -> requests.Response:
    """
    Call `fn` up to `max_attempts` times. Retry on transient HTTP errors
    with exponential backoff: 0.5s, 1s, 2s, 4s (capped at 4s).

    Re-raises the last response (so caller can read its body / status).
    """
    last: Optional[requests.Response] = None
    for attempt in range(max_attempts):
        resp = fn()
        last = resp
        if resp.ok or not _is_retryable(resp.status_code):
            return resp
        # Honor Retry-After if present (mostly for 429)
        ra = resp.headers.get("Retry-After")
        try:
            delay = float(ra) if ra else 0.5 * (2 ** attempt)
        except ValueError:
            delay = 0.5 * (2 ** attempt)
        delay = min(delay, 4.0)
        time.sleep(delay)
    assert last is not None
    return last


# ───────────────────────── OpenRouter backend ────────────────────────────

def _or_headers() -> dict:
    return {
        "Authorization": f"Bearer {get_openrouter_key()}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_REFERER,
        "X-Title":      OPENROUTER_TITLE,
    }


def _or_call(
    messages: list[dict],
    model: str,
    *,
    tools: Optional[list[dict]] = None,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    stream: bool = False,
    timeout: int = 120,
    use_cache: bool = True,
) -> requests.Response:
    """
    Raw OpenRouter chat-completions call. Honours cache for non-streaming
    calls and applies retry-with-backoff for transient errors.
    """
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    if use_cache and not stream:
        cached = _cache.get(payload)
        if cached is not None:
            # Reconstruct a Response-like object from the cached body.
            fake = requests.Response()
            fake.status_code = 200
            fake._content = json.dumps(cached).encode("utf-8")
            fake.headers["content-type"] = "application/json"
            return fake

    session = _get_session()
    resp = _retry_with_backoff(
        lambda: session.post(
            OPENROUTER_URL,
            headers=_or_headers(),
            json=payload,
            timeout=timeout,
            stream=stream,
        )
    )

    # If the model slug is retired from the free tier (HTTP 404), try the
    # next discovered-free model once before giving up. OpenRouter rotates
    # free models every few weeks, so this is a common case.
    if resp.status_code == 404 and not stream:
        try:
            body = resp.json()
            err = (body.get("error") or {}).get("message", "").lower()
            if "unavailable for free" in err or "no endpoints found" in err:
                print(f"[ai_client] Model {model} retired from free tier — auto-finding replacement…")
                from core.openrouter_models import pick_discovered_model
                replacement = pick_discovered_model("general", force_refresh=True)
                if replacement and replacement != model:
                    print(f"[ai_client] Switching to {replacement}.")
                    # Invalidate the stale cache + retry with the new model
                    _cache.clear()
                    payload["model"] = replacement
                    resp = _retry_with_backoff(
                        lambda: session.post(
                            OPENROUTER_URL,
                            headers=_or_headers(),
                            json=payload,
                            timeout=timeout,
                            stream=stream,
                        )
                    )
                    # If the replacement also 404s, fall through to the caller.
                    if resp.ok and use_cache:
                        try:
                            _cache.set(payload, resp.json())
                        except Exception:
                            pass
                    return resp
        except Exception as e:
            print(f"[ai_client] Auto-fallback failed: {e}")

    if use_cache and not stream and resp.ok:
        try:
            _cache.set(payload, resp.json())
        except Exception:
            pass

    return resp


# ───────────────────────── Gemini backend (fallback) ────────────────────

def _gemini_client():
    """
    Lazily build a google-genai Client using the configured Gemini key.
    Lazy import so the `google-genai` package is only required when actually
    using the Gemini backend — keeps startup fast when OpenRouter is the
    active provider.
    """
    from google import genai  # type: ignore
    return genai.Client(api_key=get_gemini_key())


# ───────────────────────── unified public API ───────────────────────────

def generate_text(
    prompt: str,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    timeout: int = 120,
    use_cache: bool = True,
) -> str:
    """
    Simple text generation. Routes to OpenRouter or Gemini based on config.

    This is the primary entry point for action modules that just need a
    quick text completion (dev_agent planner, code_helper reviewer, etc.).
    """
    provider = get_provider()
    model = model or get_active_model(provider)

    if provider == "openrouter":
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        # Build a list of models to try in order: the requested model first,
        # then every discovered free model as fallback. Some discovered
        # models return HTTP 200 but empty content (vision-only, audio-only,
        # or experimental) — we walk the list until one returns real text.
        from core.openrouter_models import get_discovered_or_catalog
        candidates: list[str] = [model]
        for m in get_discovered_or_catalog():
            if m.id not in candidates:
                candidates.append(m.id)

        last_error: Optional[Exception] = None
        for model_id in candidates[:8]:  # cap at 8 attempts to bound latency
            try:
                resp = _or_call(
                    messages,
                    model=model_id,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    use_cache=use_cache,
                )
                if not resp.ok:
                    last_error = RuntimeError(f"HTTP {resp.status_code}")
                    continue
                data = resp.json()
                # Walk choices for first non-null content
                content = ""
                for choice in data.get("choices", []):
                    msg = choice.get("message", {})
                    c = msg.get("content")
                    if c:
                        content = c
                        break
                if content.strip():
                    return content.strip()
                # 200 but empty → try next model
                print(f"[ai_client] {model_id} returned 200 but no content — trying next.")
                last_error = RuntimeError("empty content")
                continue
            except Exception as e:
                last_error = e
                print(f"[ai_client] {model_id} failed ({e}) — trying next.")
                continue

        # All candidates failed → fall back to Gemini
        print(f"[ai_client] All OpenRouter candidates failed (last: {last_error}) — falling back to Gemini.")
        return _gemini_generate_text(prompt, system=system,
                                     temperature=temperature,
                                     max_tokens=max_tokens,
                                     timeout=timeout)

    # Gemini
    return _gemini_generate_text(prompt, system=system,
                                 temperature=temperature,
                                 max_tokens=max_tokens,
                                 timeout=timeout)


def _gemini_generate_text(
    prompt: str,
    *,
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    timeout: int = 120,
) -> str:
    """Fallback path through the google-genai SDK."""
    cfg: dict = {"temperature": temperature, "max_output_tokens": max_tokens}
    if system:
        cfg["system_instruction"] = system
    client = _gemini_client()
    model = get_active_model("gemini")
    try:
        r = client.models.generate_content(
            model=model,
            contents=prompt,
            config=cfg,  # type: ignore[arg-type]
        )
        # genai SDK returns an object with .text or .candidates
        return (getattr(r, "text", "") or "").strip()
    except Exception as e:
        raise RuntimeError(f"Gemini text call failed: {e}")


def stream_text(
    prompt: str,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    timeout: int = 120,
) -> Generator[str, None, None]:
    """
    Streaming text generation. Yields successive chunks as they arrive.

    For OpenRouter: parses the SSE stream chunk-by-chunk.
    For Gemini: the SDK doesn't expose a public SSE stream for text-only
    calls; we fall back to non-streaming and yield the whole result.
    """
    provider = get_provider()
    model = model or get_active_model(provider)

    if provider == "openrouter":
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        # Streaming: don't use the cache or retry-with-backoff (both are
        # incompatible with a chunked response body). One shot, iterate SSE.
        try:
            resp = _get_session().post(
                OPENROUTER_URL,
                headers=_or_headers(),
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True,
                },
                timeout=timeout,
                stream=True,
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"[ai_client] OpenRouter stream failed ({e}) — falling back to Gemini.")
            yield _gemini_generate_text(prompt, system=system,
                                       temperature=temperature,
                                       max_tokens=max_tokens, timeout=timeout)
            return

        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                continue
            text = (chunk.get("choices", [{}])[0]
                    .get("delta", {})
                    .get("content", ""))
            if text:
                yield text
        return

    # Gemini: no public streaming for text-only — yield once.
    yield _gemini_generate_text(prompt, system=system,
                                temperature=temperature,
                                max_tokens=max_tokens, timeout=timeout)


def generate_with_tools(
    messages: list[dict],
    tools: list[dict],
    *,
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    timeout: int = 120,
) -> dict:
    """
    Tool-calling completion. Returns a normalised dict:

        {
          "content":    str,
          "tool_calls": [{"id": str, "function": {"name", "arguments"}}, ...]
        }

    Tool-call schema follows the OpenAI / OpenRouter convention. Gemini's
    native schema differs but we normalise here so callers don't care.
    """
    provider = get_provider()
    model = model or get_active_model(provider)

    if provider == "openrouter":
        try:
            resp = _or_call(
                messages,
                model=model,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                use_cache=False,  # tool calls are inherently stateful
            )
            resp.raise_for_status()
            data = resp.json()
            msg = (data.get("choices", [{}])[0]
                   .get("message", {}))
            tc_list: list[dict] = []
            for t in (msg.get("tool_calls") or []):
                fn = t.get("function", {})
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        pass
                tc_list.append({
                    "id": t.get("id", ""),
                    "function": {"name": fn.get("name", ""), "arguments": args},
                })
            return {
                "content": (msg.get("content") or "").strip(),
                "tool_calls": tc_list,
            }
        except Exception as e:
            print(f"[ai_client] OpenRouter tool call failed ({e}) — falling back to Gemini.")
            return _gemini_generate_with_tools(messages, tools, model=model,
                                                temperature=temperature,
                                                max_tokens=max_tokens,
                                                timeout=timeout)

    return _gemini_generate_with_tools(messages, tools, model=model,
                                       temperature=temperature,
                                       max_tokens=max_tokens,
                                       timeout=timeout)


def _gemini_generate_with_tools(
    messages: list[dict],
    tools: list[dict],
    *,
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    timeout: int = 120,
) -> dict:
    """
    Gemini's tool-call format is *similar but not identical* to OpenAI.
    For now we delegate to core.llm_client.call_llm when present so we don't
    duplicate that mapping logic. Falls back to plain text if anything breaks.
    """
    try:
        from core.llm_client import call_llm  # type: ignore
        # call_llm routes between Ollama / OpenAI-compatible / OpenRouter.
        return call_llm(messages, tools=tools, timeout=timeout)
    except Exception as e:
        # Last-ditch: synthesize a no-op tool call result.
        print(f"[ai_client] Gemini tool fallback failed ({e}) — returning plain text.")
        # Strip tools and just generate text from the messages.
        prompt = "\n\n".join(
            f"[{m.get('role','user')}] {m.get('content','')}" for m in messages
        )
        text = _gemini_generate_text(prompt, temperature=temperature,
                                     max_tokens=max_tokens, timeout=timeout)
        return {"content": text, "tool_calls": []}


# ───────────────────────── housekeeping ─────────────────────────────────

def clear_cache() -> None:
    """Flush the in-process response cache (e.g. after a config change)."""
    _cache.clear()


def health() -> dict:
    """
    Lightweight health probe. Reports which providers are configured.
    Does NOT make any network calls — purely local config inspection.
    """
    cfg = _load_config()
    return {
        "active_provider": get_provider(),
        "active_model": get_active_model(),
        "gemini_configured": bool((cfg.get("gemini_api_key") or "").strip()),
        "openrouter_configured": bool((cfg.get("openrouter_api_key") or "").strip()),
    }


# ───────────────────────── smoke test ───────────────────────────────────

if __name__ == "__main__":
    print("=== Mark XLVIII ai_client health probe ===")
    print(json.dumps(health(), indent=2))
    print()
    print("Free OpenRouter models in catalog:")
    from core.openrouter_models import list_free_models
    for m in list_free_models():
        print(f"  {m['label']:30s}  {m['id']}")
