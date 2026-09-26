"""
Skill registry — single source of truth for everything JARVIS can do.

Scans actions/*.py (built-in) and actions/custom/*.py (self-forged) for
TOOL_DECLARATION dicts, loads them, and exposes a uniform API for the
rest of the codebase:

    from core.skill_registry import list_skills, find_skill, call_skill

    all_skills = list_skills()                       # list[dict]
    tell_time  = find_skill("tell_time")              # dict|None
    result     = call_skill("tell_time", some_arg=1)  # str|dict

main.py can subscribe to on_change() to be notified when a new skill is
forged live, so it can re-fetch tool declarations from the Live API.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Optional


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR      = _get_base_dir()
ACTIONS_DIR    = BASE_DIR / "actions"
CUSTOM_DIR     = BASE_DIR / "actions" / "custom"


# ───────────────────────── store ───────────────────────────────────

_skills: dict[str, dict] = {}
_lock = threading.Lock()
_listeners: list[Callable[[], None]] = []


def _normalize(decl: dict, module: Any, path: Path) -> dict:
    return {
        "name":        decl.get("name", path.stem),
        "description": decl.get("description", ""),
        "parameters":  decl.get("parameters", {}),
        "module":      module,
        "path":        str(path),
        "is_custom":   "actions/custom" in str(path).replace("\\", "/"),
    }


def _load_module_from_path(path: Path):
    """Import a .py file as a fresh module. Returns the module object."""
    mod_name = f"mark1_skill_{path.stem}_{id(path)}"
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


# ───────────────────────── discovery ──────────────────────────────

def discover_all() -> int:
    """
    Scan actions/ and actions/custom/ for skills.
    Returns the count of skills loaded.

    Safe to call multiple times — it refreshes the in-memory store.
    """
    found = 0
    errors: list[tuple[str, str]] = []

    # Built-in actions — only top-level .py files (not __init__).
    if ACTIONS_DIR.exists():
        for p in sorted(ACTIONS_DIR.glob("*.py")):
            if p.name == "__init__.py":
                continue
            try:
                mod = _load_module_from_path(p)
                decl = getattr(mod, "TOOL_DECLARATION", None)
                if isinstance(decl, dict) and decl.get("name"):
                    with _lock:
                        _skills[decl["name"]] = _normalize(decl, mod, p)
                    found += 1
            except Exception as e:
                errors.append((p.name, str(e)))

    # Custom skills — same scan on actions/custom/.
    if CUSTOM_DIR.exists():
        for p in sorted(CUSTOM_DIR.glob("*.py")):
            if p.name == "__init__.py":
                continue
            try:
                mod = _load_module_from_path(p)
                decl = getattr(mod, "TOOL_DECLARATION", None)
                if isinstance(decl, dict) and decl.get("name"):
                    with _lock:
                        _skills[decl["name"]] = _normalize(decl, mod, p)
                    found += 1
            except Exception as e:
                errors.append((p.name, str(e)))

    if errors:
        print(f"[skill_registry] {len(errors)} skill(s) failed to load:")
        for name, err in errors[:5]:
            print(f"  • {name}: {err[:120]}")

    _fire_listeners()
    return found


def register_custom(skill_name: str, module: Any, decl: dict, path: Path) -> None:
    """Register a freshly-forged skill (called by core/skill_forge.py)."""
    with _lock:
        _skills[skill_name] = _normalize(decl, module, path)
    _fire_listeners()


def unregister(skill_name: str) -> bool:
    """Remove a skill from the active catalog (used when a custom is deleted)."""
    with _lock:
        existed = skill_name in _skills
        _skills.pop(skill_name, None)
    if existed:
        _fire_listeners()
    return existed


# ───────────────────────── public API ─────────────────────────────

def list_skills() -> list[dict]:
    """Return lightweight info for every loaded skill (no module objects)."""
    with _lock:
        return [
            {
                "name":        s["name"],
                "description": s["description"],
                "parameters":  s["parameters"],
                "path":        s["path"],
                "is_custom":   s["is_custom"],
            }
            for s in _skills.values()
        ]


def find_skill(name: str) -> Optional[dict]:
    """Look up a skill by its TOOL_DECLARATION name."""
    with _lock:
        return _skills.get(name)


def call_skill(name: str, **kwargs) -> Any:
    """
    Invoke a skill's function by name.
    Returns whatever the skill function returns (str or dict).
    Raises KeyError if not registered; whatever the skill raises propagates.
    """
    with _lock:
        s = _skills.get(name)
    if not s:
        raise KeyError(f"Skill {name!r} not registered. Call discover_all() first?")
    fn = getattr(s["module"], name, None)
    if not callable(fn):
        raise RuntimeError(f"Skill {name!r} module has no callable {name}()")
    return fn(**kwargs)


def list_tool_declarations() -> list[dict]:
    """
    Return the OpenAI-style tool-declaration list for every registered skill.
    main.py uses this to register tools with the Gemini Live API session.
    """
    return [
        {
            "name":        s["name"],
            "description": s["description"],
            "parameters":  s["parameters"],
        }
        for s in list_skills()
    ]


def on_change(cb: Callable[[], None]) -> None:
    """Register a listener fired whenever the skill catalog changes."""
    _listeners.append(cb)


def _fire_listeners() -> None:
    for cb in list(_listeners):
        try:
            cb()
        except Exception as e:
            print(f"[skill_registry] on_change listener raised: {e}")


# ───────────────────────── smoke test ──────────────────────────────

if __name__ == "__main__":
    print(f"Discovering skills from {ACTIONS_DIR} + {CUSTOM_DIR}…")
    n = discover_all()
    print(f"Loaded {n} skills.\n")
    for s in list_skills():
        marker = "★" if s["is_custom"] else " "
        print(f"  {marker} {s['name']:25s}  {s['description'][:60]}")
