"""
Example custom skill — bundled so new users can see the contract.

This is what JARVIS would generate if the user asked "tell me the time".
You can delete this file; it serves as documentation + a self-test for
core/skill_registry.discover_all().
"""
from datetime import datetime


def tell_time(**kwargs) -> str:
    """Return the current local time in 12-hour format."""
    now = datetime.now()
    return f"It's {now.strftime('%I:%M %p')} on {now.strftime('%A, %B %d')}."


TOOL_DECLARATION = {
    "name":        "tell_time",
    "description": "Tells the current local time and date.",
    "parameters":  {"type": "OBJECT", "properties": {}, "required": []},
}
