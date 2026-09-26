"""
Self-test for the Mark 3 voice_cloner + avatar_manager modules.
Runs without an audio device or Qt — generates a synthetic WAV in-memory.

Usage:
    python tests/test_voice_avatar.py
"""
from __future__ import annotations

import math
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import voice_cloner, version


def _ok(label: str, cond: bool) -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}")
    return cond


# ─────────────────────────── synthetic audio ──────────────────────

def _synth_wav(path: Path, duration_s: float = 5.0, f0: float = 150.0,
               sample_rate: int = 16000) -> None:
    """Generate a synthetic voice-like WAV (amplitude-modulated sine)."""
    n = int(duration_s * sample_rate)
    t = np.arange(n) / sample_rate
    # Sine at f0 + amplitude modulation at 5 Hz (mimics syllable rate)
    carrier = np.sin(2 * math.pi * f0 * t)
    mod = 0.5 + 0.5 * np.sin(2 * math.pi * 5.0 * t)
    sig = 0.3 * carrier * mod
    # add a few silences to vary active_ratio
    sig[:sample_rate] = 0  # 1s silence at start
    sig = (sig * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(sig.tobytes())


# ─────────────────────────── tests ────────────────────────────────

def test_version_bump() -> bool:
    print("\n— Version helpers —")
    results = []
    name = version.get_version_name()
    num = version.get_version_number()
    results.append(_ok(f"current version is Mark N (got {name})", name.startswith("Mark")))
    results.append(_ok(f"version number is an int (got {num})", isinstance(num, int) and num > 0))
    return all(results)


def test_voice_profile_round_trip() -> bool:
    print("\n— Voice cloner: profile extraction + apply —")
    results = []

    # Write synthetic WAV into a temp file.
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        wav = td_path / "sample.wav"
        _synth_wav(wav, duration_s=5.0, f0=150.0)

        # Redirect the profile output path so we don't clobber the user's real one.
        original_profile_path = voice_cloner.PROFILE_PATH
        voice_cloner.PROFILE_PATH = td_path / "voice_profile.json"
        try:
            prof = voice_cloner.analyze_audio(wav, label="test_voice")
            results.append(_ok(f"profile has mean_f0_hz > 0 (got {prof['mean_f0_hz']})",
                                prof["mean_f0_hz"] > 0))
            results.append(_ok(f"profile has duration_s ≈ 5 (got {prof['duration_s']})",
                                4.5 <= prof["duration_s"] <= 5.5))
            results.append(_ok("profile saved to disk",
                              voice_cloner.PROFILE_PATH.exists()))

            loaded = voice_cloner.load_profile()
            results.append(_ok("load_profile returns the saved dict",
                              loaded is not None and loaded.get("label") == "test_voice"))

            # apply_profile on a synthetic buffer
            samples = np.random.randn(24000).astype(np.float32) * 0.1
            out, new_sr = voice_cloner.apply_profile(samples, 24000, profile=loaded)
            results.append(_ok("apply_profile returns ndarray",
                              isinstance(out, np.ndarray)))
            results.append(_ok("apply_profile may shift sample rate",
                              new_sr > 0))
        finally:
            voice_cloner.PROFILE_PATH = original_profile_path

    return all(results)


def test_voice_apply_no_profile_passthrough() -> bool:
    print("\n— Voice cloner: passthrough when no profile —")
    results = []
    # Stash profile path to a non-existent file
    original = voice_cloner.PROFILE_PATH
    voice_cloner.PROFILE_PATH = Path("/tmp/__nonexistent_profile_test.json")
    try:
        samples = np.random.randn(24000).astype(np.float32) * 0.1
        out, new_sr = voice_cloner.apply_profile(samples, 24000)
        results.append(_ok("no profile → samples returned unchanged",
                          len(out) == len(samples) and new_sr == 24000))
    finally:
        voice_cloner.PROFILE_PATH = original
    return all(results)


def test_avatar_manager_actions() -> bool:
    print("\n— avatar_manager action smoke test —")
    results = []
    # We can't run the live PyQt widget without a display, but we CAN
    # verify the action functions return well-formed dicts.
    from actions import avatar_manager

    # about() should mention "Mark"
    about = avatar_manager.about()
    results.append(_ok(f"about() returns 'Mark N' (got {about.get('text','')[:20]})",
                       "Mark" in about.get("text", "")))

    # preview_face when no avatar set
    pf = avatar_manager.preview_face()
    results.append(_ok("preview_face returns dict with 'ok' key",
                       isinstance(pf, dict) and "ok" in pf))

    # set_face with non-existent path
    bad = avatar_manager.set_face("/tmp/__nonexistent_face_xyz.png")
    results.append(_ok("set_face with bad path returns ok=False",
                       bad.get("ok") is False))

    # preview_voice with no profile
    pv = avatar_manager.preview_voice()
    results.append(_ok("preview_voice returns dict with 'ok' key",
                       isinstance(pv, dict) and "ok" in pv))

    return all(results)


def test_avatar_module_imports_headless() -> bool:
    print("\n— avatar module imports cleanly without PyQt —")
    results = []
    # is_available() should return False on this headless container
    from core import avatar
    avail = avatar.is_available()
    print(f"  Qt available: {avail}")
    results.append(_ok("is_available() returns a boolean", isinstance(avail, bool)))
    # list_available_pngs should return a list (even if empty)
    pngs = avatar.list_available_pngs()
    results.append(_ok("list_available_pngs returns list", isinstance(pngs, list)))
    return all(results)


# ─────────────────────────── runner ───────────────────────────────

def main() -> int:
    tests = [
        test_version_bump,
        test_voice_profile_round_trip,
        test_voice_apply_no_profile_passthrough,
        test_avatar_manager_actions,
        test_avatar_module_imports_headless,
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
            import traceback
            print(f"  → {t.__name__} raised {type(e).__name__}: {e}")
            traceback.print_exc()

    print(f"\n=== {passed}/{len(tests)} test groups passed ===")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
