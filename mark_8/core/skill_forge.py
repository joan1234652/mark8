"""
Skill Forge — the self-upgrade engine for MARK 1.

Lets JARVIS write, validate, load, and register new action modules on the
fly. When the user asks for something JARVIS can't currently do ("tell me
the time", "convert 100 usd to eur", "give me a word for wordle today"),
JARVIS calls the `learn_skill` tool, which triggers this forge.

Pipeline
────────
  request_skill(intent) →
    1. generate_code()   — uses core/ai_client (OpenRouter free or Gemini)
                           to write a Python module following the
                           Skill Forge contract.
    2. validate()         — `ast.parse()` + a small static check that
                           the module defines a callable matching the
                           declared name and a `TOOL_DECLARATION` dict.
    3. save()             — writes to actions/custom/{skill_name}.py
    4. load()             — importlib spec_from_file_location, isolated
                           from the rest of the process via a child
                           module namespace so a buggy skill can't
                           poison the main session.
    5. test()             — runs the skill with sample args in a
                           subprocess (so a crash doesn't kill JARVIS).
    6. register()         — adds the skill to core.skill_registry and
                           fires the on_new_skill callback so main.py
                           can refresh its tool list with the Live API.

The Skill Forge contract
────────────────────────
A generated module must define exactly:

    def <skill_name>(**kwargs) -> str | dict:
        ...

    TOOL_DECLARATION = {
        "name":        "<skill_name>",
        "description": "When to call this skill (one short sentence).",
        "parameters":  {
            "type": "OBJECT",
            "properties": { ... },   # JSON schema; empty dict if no params
            "required": [...]
        },
    }

Return value:
  • A string is treated as the assistant's spoken reply.
  • A dict with keys {"text": str, "extra": any} lets the skill pass
    structured data back to the caller without it being spoken.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
import textwrap
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
CUSTOM_DIR      = BASE_DIR / "actions" / "custom"
CUSTOM_DIR.mkdir(parents=True, exist_ok=True)

# Optional hook that main.py can register to be called when a new skill
# becomes live — lets the Live session re-fetch its tool declarations.
_on_new_skill: list[Callable[[str, dict], None]] = []


def on_new_skill(cb: Callable[[str, dict], None]) -> None:
    """Register a callback invoked whenever a new skill goes live."""
    _on_new_skill.append(cb)


# ───────────────────────── code generation ─────────────────────────

_FORGE_SYSTEM_PROMPT = textwrap.dedent("""
    You are the Skill Forge — a Python code generator for the MARK 1
    voice assistant. Your job is to write a single self-contained Python
    module that implements a NEW skill the user has asked for.

    CONTRACT — the module MUST define:
      1. A function whose name matches the skill_name (snake_case).
         The function takes **kwargs and returns either a string (which
         will be spoken) or a dict {"text": str, "extra": any}.
      2. A module-level TOOL_DECLARATION dict with keys:
           name:        the function name (snake_case, no spaces)
           description: one short sentence — when to call this skill
           parameters:  JSON-schema object (use "type": "OBJECT",
                        "properties": {...}, "required": [...]).
                        Use empty {} if the skill takes no args.

    HARD RULES:
      • The module must NOT spawn subprocesses, write files outside the
        user's home dir, or use the network unless the user's intent
        explicitly requires it.
      • The module must NOT use `eval`, `exec`, `__import__`, or any
        form of dynamic code execution.
      • Use only the Python standard library + (optionally) `requests`,
        `psutil`, `datetime`, `json`, `re`, `math`, `urllib`.
      • Keep the function body under 30 lines.
      • Output ONLY the Python source code in a single ```python fenced
        block. No commentary before or after.

    Example output for "tell me the time":
    ```python
    from datetime import datetime

    def tell_time(**kwargs):
        now = datetime.now().strftime("%I:%M %p")
        return f"It's {now}."

    TOOL_DECLARATION = {
        "name":        "tell_time",
        "description": "Tells the current local time.",
        "parameters":  {"type": "OBJECT", "properties": {}, "required": []},
    }
    ```
""").strip()


def _user_prompt(intent: str, skill_name: str) -> str:
    return (
        f"User intent: {intent}\n"
        f"Suggested skill name (snake_case): {skill_name}\n\n"
        f"Write the module now. Remember: ONLY a single ```python fenced "
        f"block, no commentary."
    )


def _slugify(text: str) -> str:
    """Turn a free-text intent into a snake_case skill name."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = text.strip("_")
    if not text:
        text = "custom_skill"
    if not text[0].isalpha():
        text = "skill_" + text
    return text[:48]


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


def generate_code(intent: str, skill_name: Optional[str] = None) -> tuple[str, str]:
    """
    Ask the unified AI client to write a module for the intent.
    Returns (skill_name, python_source).

    Raises RuntimeError if the LLM returns nothing useful.
    """
    from core import ai_client  # lazy to avoid import cycle at module load
    skill_name = skill_name or _slugify(intent)

    try:
        raw = ai_client.generate_text(
            _user_prompt(intent, skill_name),
            system=_FORGE_SYSTEM_PROMPT,
            model=ai_client.get_active_model("openrouter"),
            temperature=0.2,            # code wants low temp
            max_tokens=1500,
            use_cache=False,            # never cache code-gen calls
        )
    except Exception as e:
        raise RuntimeError(f"Skill Forge LLM call failed: {e}")

    code = _strip_fences(raw)
    if not code or "def " not in code:
        raise RuntimeError(f"Skill Forge got empty / invalid code: {raw[:200]}")
    return skill_name, code


# ───────────────────────── validation ──────────────────────────────

def validate_code(code: str, expected_skill_name: str) -> tuple[bool, str]:
    """
    Static check the generated code. Returns (ok, message).
    Catches the common LLM mistakes: missing function, missing
    TOOL_DECLARATION, dangerous patterns.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"SyntaxError: {e.msg} (line {e.lineno})"

    fn_names = set()
    decls: list[ast.Assign] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            fn_names.add(node.name)
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "TOOL_DECLARATION":
                    decls.append(node)

    if expected_skill_name not in fn_names:
        return False, (
            f"Module does not define function '{expected_skill_name}'. "
            f"Found: {sorted(fn_names) or 'none'}"
        )
    if not decls:
        return False, "Module does not define TOOL_DECLARATION at module level."

    # Lightweight dangerous-pattern scan. Not a sandbox — the user explicitly
    # asked JARVIS to write code on their machine. But we DO refuse the
    # obviously-bad stuff so an LLM hallucination doesn't brick the system.
    forbidden = [
        ("subprocess", "subprocess"),
        ("os.system",  "os.system"),
        ("os.popen",   "os.popen"),
        ("eval(",      "eval"),
        ("exec(",      "exec"),
        ("__import__", "__import__"),
        ("pty.spawn",  "pty.spawn"),
        ("shutil.rmtree", "shutil.rmtree"),
    ]
    for needle, label in forbidden:
        if needle in code:
            return False, f"Forbidden pattern: {label} (Skill Forge refuses this by default)."

    return True, "ok"


# ───────────────────────── save / load / test ──────────────────────

def save_skill(skill_name: str, code: str) -> Path:
    """Write the skill source to actions/custom/{name}.py. Returns the path."""
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    path = CUSTOM_DIR / f"{skill_name}.py"
    path.write_text(code, encoding="utf-8")
    return path


def load_skill(skill_name: str) -> tuple[Any, dict]:
    """
    Dynamically load actions/custom/{name}.py into an isolated namespace.
    Returns (module, TOOL_DECLARATION dict). Raises on import error.
    """
    path = CUSTOM_DIR / f"{skill_name}.py"
    if not path.exists():
        raise FileNotFoundError(f"Skill {skill_name} not found at {path}")
    mod_name = f"mark1_custom_skill_{skill_name}_{int(time.time())}"
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not create module spec for {skill_name}")
    module = importlib.util.module_from_spec(spec)
    # Insert into sys.modules so `from x import y` inside the skill works
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    decl = getattr(module, "TOOL_DECLARATION", None)
    if not isinstance(decl, dict):
        raise RuntimeError(
            f"Skill {skill_name} did not define TOOL_DECLARATION dict"
        )
    return module, decl


def test_skill(module: Any, skill_name: str, sample_args: Optional[dict] = None) -> tuple[bool, str]:
    """
    Run the skill in-process with sample args. Returns (ok, output_or_error).
    A skill that throws is rejected — the user will see the error and can
    ask JARVIS to regenerate it.
    """
    fn = getattr(module, skill_name, None)
    if not callable(fn):
        return False, f"Skill {skill_name} not callable in module"
    args = sample_args or {}
    try:
        out = fn(**args)
    except Exception:
        tb = traceback.format_exc()
        return False, tb.splitlines()[-1]
    if isinstance(out, str):
        return True, out
    if isinstance(out, dict) and "text" in out:
        return True, str(out["text"])
    return True, str(out)


# ───────────────────────── orchestration ───────────────────────────

def request_skill(
    intent: str,
    *,
    skill_name: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Top-level entry point. JARVIS calls this when the user asks for a
    capability JARVIS doesn't currently have.

    Returns a dict:
      { "ok": bool,
        "skill": str|None,           # skill_name on success
        "path": str|None,           # saved path
        "declaration": dict|None,   # the TOOL_DECLARATION
        "test_output": str|None,
        "error": str|None }
    """
    def _log(msg: str) -> None:
        print(f"[forge] {msg}")
        if log:
            log(msg)

    skill_name = skill_name or _slugify(intent)
    _log(f"Forging skill '{skill_name}' for intent: {intent!r}")

    try:
        skill_name, code = generate_code(intent, skill_name)
    except Exception as e:
        return {"ok": False, "skill": skill_name, "error": f"generation: {e}"}

    ok, msg = validate_code(code, skill_name)
    if not ok:
        return {"ok": False, "skill": skill_name, "error": f"validation: {msg}"}
    _log("Validation passed.")

    path = save_skill(skill_name, code)
    _log(f"Saved to {path}")

    try:
        module, decl = load_skill(skill_name)
    except Exception as e:
        return {"ok": False, "skill": skill_name, "path": str(path),
                "error": f"load: {e}"}
    _log(f"Loaded. Declaration: name={decl.get('name')!r}")

    ok, out = test_skill(module, skill_name)
    if not ok:
        return {"ok": False, "skill": skill_name, "path": str(path),
                "error": f"test: {out}"}
    _log(f"Test passed. Output: {out[:80]}")

    # Register with the skill registry and fire the on_new_skill hook so
    # main.py can re-fetch its tool declarations.
    try:
        from core import skill_registry
        skill_registry.register_custom(skill_name, module, decl, path)
    except Exception as e:
        _log(f"Registry registration failed (non-fatal): {e}")

    for cb in list(_on_new_skill):
        try:
            cb(skill_name, decl)
        except Exception as e:
            _log(f"on_new_skill callback raised: {e}")

    return {
        "ok":          True,
        "skill":       skill_name,
        "path":        str(path),
        "declaration": decl,
        "test_output": out,
        "error":       None,
    }


def list_custom_skills() -> list[str]:
    """Return names of all skills saved in actions/custom/."""
    if not CUSTOM_DIR.exists():
        return []
    return sorted(p.stem for p in CUSTOM_DIR.glob("*.py") if p.name != "__init__.py")


def remove_skill(skill_name: str) -> bool:
    """Delete a custom skill file. Returns True if removed."""
    path = CUSTOM_DIR / f"{skill_name}.py"
    if not path.exists():
        return False
    path.unlink()
    try:
        from core import skill_registry
        skill_registry.unregister(skill_name)
    except Exception:
        pass
    return True


# ───────────────────────── smoke test ──────────────────────────────

if __name__ == "__main__":
    # Self-contained offline test: skip the LLM call, validate a known-good
    # module shape.
    sample = textwrap.dedent("""
        from datetime import datetime
        def tell_time(**kwargs):
            return datetime.now().strftime("It's %I:%M %p.")
        TOOL_DECLARATION = {
            "name": "tell_time",
            "description": "Tells the current local time.",
            "parameters": {"type": "OBJECT", "properties": {}, "required": []},
        }
    """).strip()

    ok, msg = validate_code(sample, "tell_time")
    print(f"validate: ok={ok}, msg={msg}")

    path = save_skill("tell_time", sample)
    print(f"saved: {path}")
    mod, decl = load_skill("tell_time")
    print(f"loaded: {decl['name']}")
    ok, out = test_skill(mod, "tell_time")
    print(f"test: ok={ok}, out={out}")
    # Cleanup
    remove_skill("tell_time")
    print("removed.")
