"""
Avatar + voice manager — lets JARVIS customise its own face and voice.

Surfaces these tools to the Gemini Live API:

  • set_face         — install a PNG as the live avatar
  • preview_face      — describe the current avatar state
  • set_voice         — analyze an audio sample + build a voice profile
  • preview_voice      — show the active voice profile stats
  • clear_voice        — forget the voice profile (revert to base TTS)
  • about              — print the current Mark version (e.g. "Mark 3")

The face/voice files are stored in config/ — `avatar.png` for the image,
`voice_profile.json` for the voice profile, `voice_samples/<label>.wav`
for the raw uploaded sample (kept for re-analysis later).
"""
from __future__ import annotations

import shutil
from pathlib import Path


def _get_base_dir() -> Path:
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = _get_base_dir()
AVATAR_PATH      = BASE_DIR / "config" / "avatar.png"
PROFILE_PATH     = BASE_DIR / "config" / "voice_profile.json"
SAMPLES_DIR      = BASE_DIR / "config" / "voice_samples"


def set_face(image_path: str) -> dict:
    """
    Install a PNG as JARVIS's live avatar.

    The image is copied to config/avatar.png so the avatar widget picks
    it up automatically. OpenCV tries to detect the mouth region for
    jaw-drop animation when JARVIS is speaking; if it can't (no face
    found, or OpenCV missing), the avatar falls back to whole-image
    jitter + scale pulse.

    Args:
      image_path: absolute path to a PNG file (or any image Qt can load).
    """
    src = Path(image_path).expanduser().resolve()
    if not src.exists():
        return {"ok": False, "text": f"I couldn't find an image at {image_path}."}
    AVATAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, AVATAR_PATH)
    # Try to detect mouth region so we can report it back to the user.
    mouth_info = "not detected — using whole-image animation"
    try:
        from core import avatar
        if avatar.is_available():
            # We can't instantiate the widget in a tool context, but the
            # detection logic is static — run it manually.
            rect = avatar.AvatarWidget._detect_mouth(AVATAR_PATH)
            if rect:
                mouth_info = f"detected at x={rect[0]:.2f} y={rect[1]:.2f} w={rect[2]:.2f} h={rect[3]:.2f}"
    except Exception as e:
        mouth_info = f"detection skipped ({e})"
    return {
        "ok":   True,
        "text": (
            f"I've installed your PNG as my face. Mouth region: {mouth_info}. "
            "I'll animate it automatically when I speak, listen, or think."
        ),
        "path": str(AVATAR_PATH),
    }


def preview_face() -> dict:
    """Report on the currently-installed avatar."""
    if AVATAR_PATH.exists():
        return {"ok": True,
                "text": f"My current avatar is at {AVATAR_PATH}.",
                "path": str(AVATAR_PATH)}
    return {"ok": False,
            "text": "I don't have a custom face yet. Give me a PNG and I'll use it."}


def set_voice(audio_path: str, label: str = "user_voice") -> dict:
    """
    Build a voice profile from a 5–30 second audio sample.

    The profile captures average pitch (F0), pitch range, speaking rate,
    and dynamic range. The TTS layer picks it up automatically on the
    next utterance — pitch shift + tempo shift are applied via
    dependency-free resampling (no ML model required).

    For TRUE voice cloning (ElevenLabs-style), set ELEVENLABS_API_KEY in
    config/api_keys.json and call this with a 1-minute sample — the
    cloner will upload to ElevenLabs and switch to the cloned voice.

    Args:
      audio_path: absolute path to a WAV / MP3 / M4A file.
      label:      name to remember this voice by.
    """
    src = Path(audio_path).expanduser().resolve()
    if not src.exists():
        return {"ok": False, "text": f"I couldn't find audio at {audio_path}."}

    # Stash a copy of the raw sample for future re-analysis.
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    dest = SAMPLES_DIR / f"{label}{src.suffix}"
    shutil.copy2(src, dest)

    try:
        from core.voice_cloner import analyze_audio
        prof = analyze_audio(dest, label=label)
    except Exception as e:
        return {"ok": False, "text": f"Voice analysis failed: {e}"}

    f0 = prof.get("mean_f0_hz", 0)
    semis = prof.get("target_pitch_shift_semitones", 0)
    speed = prof.get("target_speed_factor", 1.0)
    return {
        "ok":   True,
        "text": (
            f"I've analysed your voice sample. Average pitch: {f0:.0f} Hz "
            f"(I'll shift my voice by {semis:+.1f} semitones to match). "
            f"Speaking speed factor: {speed:.2f}x. "
            "I'll sound more like you starting on my next reply."
        ),
        "profile": prof,
    }


def preview_voice() -> dict:
    """Show the active voice profile stats."""
    try:
        from core.voice_cloner import load_profile
        prof = load_profile()
    except Exception:
        prof = None
    if not prof:
        return {"ok": True,
                "text": "I'm using my default voice — no profile installed."}
    return {
        "ok":      True,
        "text": (
            f"Voice profile '{prof.get('label','?')}': "
            f"avg pitch {prof.get('mean_f0_hz',0):.0f} Hz, "
            f"pitch shift {prof.get('target_pitch_shift_semitones',0):+.1f} semitones, "
            f"speed {prof.get('target_speed_factor',1.0):.2f}x."
        ),
        "profile": prof,
    }


def clear_voice() -> dict:
    """Forget the user's voice profile — revert to the base TTS voice."""
    try:
        from core.voice_cloner import clear_profile
        removed = clear_profile()
    except Exception:
        removed = False
    if removed:
        return {"ok": True, "text": "Voice profile cleared. I'm back to my default voice."}
    return {"ok": False, "text": "I didn't have a voice profile to clear."}


def about() -> dict:
    """Tell the user which Mark version is currently running."""
    try:
        from core.version import get_version_name
        name = get_version_name()
    except Exception:
        name = "Mark ?"
    return {"ok": True, "text": f"You're running {name}. Every update adds one to the name."}


TOOL_DECLARATION = None  # multi-tool module; main.py picks TOOL_DECLARATIONS up directly.

TOOL_DECLARATIONS = [
    {
        "name":        "set_face",
        "description": (
            "Install a PNG as JARVIS's animated face + body. The avatar "
            "animates automatically when speaking (jaw drop + jitter), "
            "listening (lean forward), thinking (slow rotation), or idle "
            "(gentle breathing). Mouth region is auto-detected via OpenCV."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "image_path": {"type": "STRING",
                                "description": "Absolute path to the PNG image file."},
            },
            "required": ["image_path"],
        },
    },
    {
        "name":        "preview_face",
        "description": "Report whether JARVIS currently has a custom avatar installed.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name":        "set_voice",
        "description": (
            "Build a voice profile from a 5–30 second audio sample. The "
            "TTS layer will pitch-shift + tempo-adjust to match the "
            "sample on the next utterance. For true voice cloning, set "
            "ELEVENLABS_API_KEY first."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "audio_path": {"type": "STRING",
                                "description": "Absolute path to a WAV/MP3/M4A sample."},
                "label":       {"type": "STRING",
                                "description": "Optional name to remember this voice by."},
            },
            "required": ["audio_path"],
        },
    },
    {
        "name":        "preview_voice",
        "description": "Show the active voice profile stats (pitch, tempo, shift).",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name":        "clear_voice",
        "description": "Forget the installed voice profile. JARVIS reverts to the default TTS voice.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
    {
        "name":        "about",
        "description": "Tell the user which Mark version is currently running.",
        "parameters": {"type": "OBJECT", "properties": {}},
    },
]
