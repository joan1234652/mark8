"""
Skill Manager — a built-in action that lets JARVIS forge new skills by voice.

Exposes three tools to the Live session:
  • learn_skill  — generate + validate + load + register a new skill
                   from a free-text user intent.
  • list_skills  — read back every registered skill, built-in and custom.
  • forget_skill — delete a previously-forged custom skill.

This module IS the loop-closer: when the user says "can you tell me the
time?" and JARVIS has no `tell_time` skill, the LLM calls learn_skill
with intent="tell me the current local time", Skill Forge writes a
`actions/custom/tell_time.py` module, registers it, and the new tool
becomes available on the next turn.
"""
from __future__ import annotations


def learn_skill(intent: str, suggested_name: str = "") -> dict:
    """
    Forge a new skill from a free-text user intent.

    Args:
      intent:           Plain-English description of what the skill should do.
      suggested_name:   Optional snake_case name; auto-derived if empty.

    Returns a JSON-serialisable dict the LLM can narrate to the user.
    """
    # Lazy import to avoid a circular dependency at module load.
    from core import skill_forge, skill_registry

    result = skill_forge.request_skill(
        intent=intent,
        skill_name=suggested_name or None,
    )

    if not result.get("ok"):
        return {
            "ok":   False,
            "text": (
                f"I tried to learn how to {intent}, but the forge rejected "
                f"the result: {result.get('error', 'unknown error')}. "
                "I can try again if you rephrase it."
            ),
        }

    name = result["skill"]
    # Re-discover in case anything else changed (cheap — just refreshes)
    skill_registry.discover_all()
    return {
        "ok":   True,
        "text": (
            f"I just learned a new skill: {name}. "
            f"Test output: {result.get('test_output', '')}. "
            "It's now available — ask me to use it."
        ),
        "skill":       name,
        "declaration": result["declaration"],
    }


def list_skills(filter_custom: bool = False) -> dict:
    """
    Return every registered skill. Set filter_custom=True to see only
    skills JARVIS forged itself.
    """
    from core import skill_registry
    skills = skill_registry.list_skills()
    if filter_custom:
        skills = [s for s in skills if s["is_custom"]]
    return {
        "ok":    True,
        "text":  f"I have {len(skills)} skill(s) loaded.",
        "skills": [
            {"name": s["name"], "description": s["description"],
             "custom": s["is_custom"]}
            for s in skills
        ],
    }


def forget_skill(skill_name: str) -> dict:
    """Delete a previously-forged custom skill. Built-in skills cannot be removed."""
    from core import skill_forge, skill_registry
    if not skill_name:
        return {"ok": False, "text": "I need a skill name to forget."}
    s = skill_registry.find_skill(skill_name)
    if not s:
        return {"ok": False, "text": f"I don't have a skill called {skill_name}."}
    if not s["is_custom"]:
        return {"ok": False, "text": f"{skill_name} is a built-in skill — I can't remove it."}
    if skill_forge.remove_skill(skill_name):
        skill_registry.unregister(skill_name)
        return {"ok": True, "text": f"Forgot {skill_name}."}
    return {"ok": False, "text": f"Could not delete {skill_name}."}


def recall_memory(query: str, k: int = 5) -> dict:
    """
    Semantic search over JARVIS's vector memory.
    Returns the top-k matching memories with similarity scores.
    """
    from core.memory_vector import vstore
    hits = vstore.recall(query, k=min(int(k) or 5, 20))
    if not hits:
        return {"ok": True, "text": "I don't remember anything about that yet.",
                "hits": []}
    lines = [f"  [{h['score']:.2f}] {h['text']}" for h in hits]
    return {
        "ok":   True,
        "text": f"I recall {len(hits)} thing(s) about that:\n" + "\n".join(lines),
        "hits": [{"text": h["text"], "score": h["score"],
                  "metadata": h["metadata"]} for h in hits],
    }


def remember_memory(text: str, category: str = "notes", tags: str = "") -> dict:
    """
    Save a free-text memory to the vector store for later semantic recall.
    Args:
      text:     what to remember (a sentence, a fact, an event)
      category: identity / preferences / projects / relationships / wishes / notes
      tags:     comma-separated tag words to filter by later
    """
    from core.memory_vector import vstore
    if not text or not text.strip():
        return {"ok": False, "text": "Nothing to remember — text was empty."}
    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]
    mid = vstore.remember(text, metadata={"category": category, "tags": tag_list})
    return {
        "ok": True,
        "text": f"Remembered under {category}.",
        "id":  mid,
    }


# ─── Tool declarations surfaced to the Gemini Live API ───────────

TOOL_DECLARATION = None  # this module exposes MULTIPLE tools; main.py picks them up directly.

# Inline list so main.py can splice them into TOOL_DECLARATIONS without
# walking module attributes.
TOOL_DECLARATIONS = [
    {
        "name":        "learn_skill",
        "description": (
            "Forge a NEW skill on the fly. Use this when the user asks for "
            "something you currently can't do — 'tell me the time', "
            "'convert 100 usd to eur', 'roll a 20-sided die'. The forge "
            "generates a Python module, validates it, tests it, and "
            "registers it as a tool you can call from the next turn."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "intent":         {"type": "STRING",
                                    "description": "Plain-English description of what the skill should do."},
                "suggested_name":  {"type": "STRING",
                                    "description": "Optional snake_case name. Auto-derived from intent if empty."},
            },
            "required": ["intent"],
        },
    },
    {
        "name":        "list_skills",
        "description": "List every skill currently available (built-in + custom).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "filter_custom": {"type": "BOOLEAN",
                                  "description": "If true, only list skills JARVIS forged itself."},
            },
        },
    },
    {
        "name":        "forget_skill",
        "description": "Delete a previously-forged custom skill. Built-in skills cannot be removed.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "skill_name": {"type": "STRING",
                               "description": "Snake_case name of the custom skill to forget."},
            },
            "required": ["skill_name"],
        },
    },
    {
        "name":        "recall_memory",
        "description": (
            "Semantic search over JARVIS's long-term vector memory. Use when "
            "the user asks 'do you remember what I told you about X?' or "
            "'what did we discuss last week?' Returns the most similar "
            "memories with similarity scores."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING",
                           "description": "Free-text query to match against stored memories."},
                "k":     {"type": "INTEGER",
                          "description": "Max results to return (default 5, max 20)."},
            },
            "required": ["query"],
        },
    },
    {
        "name":        "remember_memory",
        "description": (
            "Save a fact / preference / event / project note to long-term "
            "vector memory for later semantic recall. Use this proactively "
            "when the user tells you something personal or important."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text":     {"type": "STRING",
                              "description": "The text content to remember."},
                "category": {"type": "STRING",
                             "description": "identity | preferences | projects | relationships | wishes | notes"},
                "tags":     {"type": "STRING",
                             "description": "Comma-separated tag words, e.g. 'car,honda,maintenance'."},
            },
            "required": ["text"],
        },
    },
]
