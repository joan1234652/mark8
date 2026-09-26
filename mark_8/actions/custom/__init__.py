"""
Custom skills directory — JARVIS forges new skills here.

Modules in this folder are auto-discovered by core/skill_registry.py on
startup. Each module must define a function matching its filename and a
module-level `TOOL_DECLARATION` dict. See core/skill_forge.py for the
full contract.

Example: actions/custom/tell_time.py defines `tell_time()` and a
TOOL_DECLARATION with name="tell_time". main.py picks it up automatically.
"""
