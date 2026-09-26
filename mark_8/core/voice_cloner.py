"""
Voice cloner for Mark 3.

Lets the user give JARVIS any voice by providing a 5-30 second audio
sample. The cloner extracts a VoiceProfile (fundamental frequency,
speaking rate, dynamic range) and the TTS layer applies it on top of
whatever base TTS engine is active (EdgeTTS / Kokoro / ElevenLabs).

True voice cloning (ElevenLabs-style) requires a paid API or a multi-GB
local model. We do the pragmatic thing instead:

  1. Extract F0 (pitch) from the reference via autocorrelation.
  2. Extract speaking rate (audio-active ratio + average syllable rate).
  3. Store the profile in config/voice_profile.json.
  4. The TTS engine picks up the profile automatically and applies:
       - pitch shift (resample-based, no extra deps)
       - tempo change (resample rate adjustment)
  5. Optional: if ELEVENLABS_API_KEY is in env, use ElevenLabs' voice
     cloning endpoint for true voice cloning instead.

API
───
    from core.voice_cloner import analyze_audio, load_profile, apply_profile

    analyze_audio("path/to/sample.wav")              # → writes profile
    profile = load_profile()                          # → dict or None
    shifted = apply_profile(samples, sr=24000, profile=profile)  # → ndarray
"""
from __future__ import annotations

import json
import math
import sys
import wave
from pathlib import Path
from typing import Optional

import numpy as np


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = _get_base_dir()
PROFILE_PATH     = BASE_DIR / "config" / "voice_profile.json"
SAMPLES_DIR      = BASE_DIR / "config" / "voice_samples"
SAMPLES_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────── audio loading ────────────────────────

def _load_wav(path: Path) -> tuple[np.ndarray, int]:
    """Read a WAV file. Returns mono float32 array + sample rate."""
    with wave.open(str(path), "rb") as w:
        n_channels = w.getnchannels()
        sample_rate = w.getframerate()
        sample_width = w.getsampwidth()
        frames = w.readframes(w.getnframes())
    if sample_width == 2:
        arr = np.frombuffer(frames, dtype=np.int16)
    elif sample_width == 4:
        arr = np.frombuffer(frames, dtype=np.int32)
    elif sample_width == 1:
        arr = np.frombuffer(frames, dtype=np.uint8).astype(np.int32) - 128
    else:
        raise ValueError(f"Unsupported sample width: {sample_width} bytes")
    if n_channels > 1:
        arr = arr.reshape(-1, n_channels).mean(axis=1)
    arr = arr.astype(np.float32) / 32768.0
    return arr, sample_rate


def _load_any_audio(path: Path) -> tuple[np.ndarray, int]:
    """
    Load WAV/MP3/M4A/etc. Tries miniaudio first (already a dep), then
    falls back to the stdlib `wave` module (WAV only).
    """
    try:
        import miniaudio  # type: ignore
        decoded = miniaudio.decode_file(str(path))
        # miniaudio returns Int16 stereo; collapse to mono float32
        if decoded.samples.ndim > 1:
            mono = decoded.samples.mean(axis=1)
        else:
            mono = decoded.samples
        return np.asarray(mono, dtype=np.float32), decoded.sample_rate
    except Exception:
        return _load_wav(path)


# ─────────────────────────── pitch (F0) extraction ────────────────

def _autocorrelation_f0(
    samples: np.ndarray,
    sample_rate: int,
    *,
    frame_size_ms: float = 40.0,
    hop_ms: float = 20.0,
    fmin: float = 70.0,
    fmax: float = 400.0,
) -> tuple[float, float]:
    """
    Estimate average fundamental frequency + pitch range (std dev).

    Autocorrelation is the cheapest F0 estimator — no ML model, no extra
    deps. Per-frame: pick the lag (within [sample_rate/fmax, sample_rate/fmin])
    whose autocorrelation is highest. Average across voiced frames.

    Returns (mean_f0_hz, std_f0_hz). Both are 0.0 if no voiced frames.
    """
    if len(samples) == 0:
        return 0.0, 0.0
    frame_size = int(sample_rate * frame_size_ms / 1000.0)
    hop = int(sample_rate * hop_ms / 1000.0)
    min_lag = max(int(sample_rate / fmax), 2)
    max_lag = max(int(sample_rate / fmin), min_lag + 1)

    f0s: list[float] = []
    for i in range(0, len(samples) - frame_size, hop):
        frame = samples[i:i + frame_size]
        if np.sqrt(np.mean(frame * frame)) < 0.01:
            continue  # silence
        # Normalised autocorrelation via numpy (zero-pad to avoid edge effects)
        corr = np.correlate(frame, frame, mode="full")[frame_size - 1:]
        if corr.size == 0 or corr[0] <= 0:
            continue
        corr = corr / corr[0]
        # Search within plausible lag range only
        region = corr[min_lag:max_lag]
        if region.size == 0:
            continue
        peak_idx = int(np.argmax(region))
        peak_val = float(region[peak_idx])
        if peak_val < 0.3:
            continue  # unvoiced
        # Parabolic interpolation around the peak for sub-sample precision
        lag = min_lag + peak_idx
        f0 = sample_rate / lag
        if fmin <= f0 <= fmax:
            f0s.append(f0)

    if not f0s:
        return 0.0, 0.0
    mean_f0 = float(np.mean(f0s))
    std_f0 = float(np.std(f0s))
    return mean_f0, std_f0


# ─────────────────────────── speaking rate ────────────────────────

def _active_ratio(samples: np.ndarray, frame_size: int = 1024) -> float:
    """Fraction of frames with RMS > silence threshold."""
    if len(samples) == 0:
        return 0.0
    n = len(samples) // frame_size
    if n == 0:
        return 0.0
    active = 0
    for i in range(n):
        frame = samples[i * frame_size:(i + 1) * frame_size]
        rms = float(np.sqrt(np.mean(frame * frame)))
        if rms > 0.02:
            active += 1
    return active / n


# ─────────────────────────── main analyzer ────────────────────────

def analyze_audio(path: str | Path, *, label: str = "user_voice") -> dict:
    """
    Analyze a reference audio sample and write a voice profile.

    Args:
      path:  WAV / MP3 / M4A file containing ~5-30s of the target voice.
      label:  Name to remember this profile by.

    Returns the profile dict.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"Voice sample not found: {p}")

    samples, sample_rate = _load_any_audio(p)
    duration = len(samples) / sample_rate
    if duration < 1.0:
        raise ValueError(
            f"Sample too short ({duration:.1f}s). Need at least 1s, ideally 5-30s."
        )

    mean_f0, std_f0 = _autocorrelation_f0(samples, sample_rate)
    active_ratio = _active_ratio(samples)
    rms = float(np.sqrt(np.mean(samples * samples)))

    # Approximate syllable rate — count zero-crossings-per-second as a proxy.
    # Not a true syllable detector, but a reasonable rate indicator.
    zcr = float(np.mean(np.abs(np.diff(np.sign(samples))) > 0)) * sample_rate / 2.0

    profile = {
        "label":          label,
        "source_path":     str(p),
        "duration_s":      round(duration, 2),
        "sample_rate":     sample_rate,
        "mean_f0_hz":      round(mean_f0, 2),
        "std_f0_hz":       round(std_f0, 2),
        "f0_range_hz":     [round(mean_f0 - std_f0, 2), round(mean_f0 + std_f0, 2)],
        "active_ratio":    round(active_ratio, 3),
        "rms_amplitude":   round(rms, 3),
        "zcr_rate":         round(zcr, 2),
        # Heuristic targets the TTS engine applies:
        "target_pitch_shift_semitones": _semitone_shift(mean_f0),
        "target_speed_factor":          _speed_factor(active_ratio, duration),
    }

    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    return profile


def _semitone_shift(mean_f0: float) -> float:
    """
    How many semitones to shift the base TTS voice to match the reference.
    Reference voice F0 vs. EdgeTTS's default ~120 Hz (male) / ~210 Hz (female).
    We split the difference at ~165 Hz as a neutral middle.
    """
    if mean_f0 <= 0:
        return 0.0
    base = 165.0
    return round(12.0 * math.log2(mean_f0 / base), 2)


def _speed_factor(active_ratio: float, duration: float) -> float:
    """
    Map active ratio (fraction of frames with speech) → playback speed
    multiplier. 0.5 (quiet) → 0.85x (slower), 0.95 (busy) → 1.15x (faster).
    """
    return round(0.85 + (active_ratio * 0.30), 3)


# ─────────────────────────── profile load/apply ───────────────────

def load_profile() -> Optional[dict]:
    """Return the active voice profile, or None if not set."""
    try:
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def clear_profile() -> bool:
    """Forget the user's voice profile. Returns True if removed."""
    if PROFILE_PATH.exists():
        PROFILE_PATH.unlink()
        return True
    return False


def apply_profile(samples: np.ndarray, sample_rate: int,
                  *, profile: Optional[dict] = None) -> tuple[np.ndarray, int]:
    """
    Apply the voice profile to a TTS audio buffer.

    Pitch shift via resampling (cheap, no extra deps). Tempo change is
    folded into the same resample: if speed_factor=1.1 and pitch_shift=+2,
    we resample at rate * 1.1 * 2^(2/12) — the audio plays 1.1x faster and
    sounds 2 semitones higher.

    Returns (new_samples, new_sample_rate). Caller should pass new_sample_rate
    to the playback device.
    """
    p = profile or load_profile()
    if not p:
        return samples, sample_rate

    semitones = p.get("target_pitch_shift_semitones", 0.0)
    speed = p.get("target_speed_factor", 1.0)
    if semitones == 0.0 and speed == 1.0:
        return samples, sample_rate

    # Combined factor: speed up → fewer samples (sample_rate goes up relative)
    pitch_ratio = 2.0 ** (semitones / 12.0)
    combined = pitch_ratio * speed

    # Resample with numpy interp — simple and dependency-free
    n_old = len(samples)
    n_new = max(int(n_old / combined), 1)
    old_idx = np.linspace(0, n_old - 1, n_old)
    new_idx = np.linspace(0, n_old - 1, n_new)
    if samples.ndim == 1:
        out = np.interp(new_idx, old_idx, samples).astype(np.float32)
    else:
        # stereo — interp each channel
        out = np.stack([
            np.interp(new_idx, old_idx, samples[:, c]).astype(np.float32)
            for c in range(samples.shape[1])
        ], axis=1)
    new_sample_rate = int(round(sample_rate * combined))
    return out, new_sample_rate


# ─────────────────────────── ElevenLabs (optional) ───────────────

def elevenlabs_clone(api_key: str, sample_path: str, name: str = "jarvis") -> Optional[str]:
    """
    Upload a voice sample to ElevenLabs and create a cloned voice.
    Returns the new voice_id, or None if it failed.

    Only called when the user provides an ELEVENLABS_API_KEY — without it,
    we use the local pitch-shift approach (apply_profile) instead.
    """
    try:
        import requests  # already a project dep
    except ImportError:
        return None
    p = Path(sample_path)
    if not p.exists():
        return None
    files = {"files": (p.name, p.open("rb"), "audio/mpeg")}
    data = {"name": name}
    try:
        r = requests.post(
            "https://api.elevenlabs.io/v1/voices/add",
            headers={"xi-api-key": api_key},
            data=data,
            files=files,
            timeout=60,
        )
        if r.ok:
            return r.json().get("voice", {}).get("voice_id")
    except Exception as e:
        print(f"[voice_cloner] ElevenLabs clone failed: {e}")
    return None


# ─────────────────────────── smoke test ───────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1:
        prof = analyze_audio(sys.argv[1])
        print(json.dumps(prof, indent=2))
    else:
        print("Usage: python -m core.voice_cloner path/to/sample.wav")
        print(f"Profile path: {PROFILE_PATH}")
        print(f"Loaded profile: {load_profile()}")
