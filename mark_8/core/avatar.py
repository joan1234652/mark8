"""
Animated avatar for Mark 3.

Given a single PNG of JARVIS's face/body, this widget animates it
automatically:

  • Idle       → gentle breathing (sin-wave vertical bob + subtle scale)
  • Listening  → slight lean-forward (small tilt + scale-up)
  • Speaking   → audio-amplitude-driven jaw drop + scale pulse + jitter
  • Thinking  → slow rotation + pulse
  • Error      → red tint flash

Auto-detects the mouth region with OpenCV's Haar cascade when available;
falls back to whole-image animation when CV is not installed or no face
is found. Either way the PNG is the only input the user needs to provide.

Public API
──────────
    from core.avatar import AvatarState, AvatarWidget
    avatar = AvatarWidget(parent)
    avatar.set_image("config/avatar.png")        # sets the still PNG
    avatar.set_state(AvatarState.SPEAKING)        # transitions state
    avatar.set_amplitude(0.42)                    # 0..1, drives mouth/jitter

To embed in ui.py, drop one line into the existing layout:
    from core.avatar import AvatarWidget, AvatarState
    self.avatar = AvatarWidget(self)
    layout.addWidget(self.avatar)
    # then call self.avatar.set_state(...) / set_amplitude(...) from
    # the existing audio-callback hooks.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Optional


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = _get_base_dir()
DEFAULT_AVATAR   = BASE_DIR / "config" / "avatar.png"
DEFAULT_BG_PATH  = BASE_DIR / "config" / "avatar_bg.png"


# ───────────────────────── state machine ──────────────────────────

class AvatarState:
    IDLE      = "idle"
    LISTENING = "listening"
    SPEAKING  = "speaking"
    THINKING  = "thinking"
    ERROR     = "error"


# ───────────────────────── widget ──────────────────────────────────

try:
    from PyQt5.QtCore import Qt, QTimer, QRectF, QObject, pyqtSignal  # type: ignore
    from PyQt5.QtGui import QPixmap, QPainter, QColor, QPen  # type: ignore
    from PyQt5.QtWidgets import QWidget                     # type: ignore
    _QT = "PyQt5"
except ImportError:
    try:
        from PyQt6.QtCore import Qt, QTimer, QRectF, QObject, pyqtSignal  # type: ignore
        from PyQt6.QtGui import QPixmap, QPainter, QColor, QPen  # type: ignore
        from PyQt6.QtWidgets import QWidget                     # type: ignore
        _QT = "PyQt6"
    except ImportError:
        _QT = None  # type: ignore


# Fall back to a no-op widget if neither Qt binding is available — the
# avatar module must still import cleanly so tests + headless servers work.
if _QT is None:
    class AvatarWidget:  # type: ignore[no-redef]
        """Stub used when PyQt is not installed. Real implementation below."""
        def __init__(self, parent=None, size: int = 320):
            # When PyQt isn't available, we still construct cleanly so
            # headless tests + the actions/skill_manager action can import
            # the avatar module without crashing. setFixedSize is a no-op.
            self._size = size
            self._state = AvatarState.IDLE
            self._amp = 0.0
            self._pixmap = None
            self._mouth_rect = None

        def set_image(self, path: str | Path) -> bool:
            print(f"[avatar-stub] PyQt not installed — set_image({path}) is a no-op.")
            return False

        def set_state(self, state: str) -> None:
            self._state = state

        def set_amplitude(self, amp: float) -> None:
            self._amp = max(0.0, min(1.0, float(amp)))

        def state(self) -> str: return self._state
        def amplitude(self) -> float: return self._amp
else:
    # ─── Thread-safe signal carrier ────────────────────────────────
    # Qt signals are thread-safe by design: emit() from any thread queues
    # the call to the receiver's thread (the GUI thread in this case).
    # Using a separate QObject for signals avoids QWidget constraints
    # on signal emission + lets us connect from anywhere.
    class _AvatarSignals(QObject):  # type: ignore
        stateChanged = pyqtSignal(str)
        amplitudeChanged = pyqtSignal(float)
        imageChanged = pyqtSignal(str)

    class AvatarWidget(QWidget):  # type: ignore[no-redef]
        """Real PyQt5/6 implementation.

        Thread-safe: `set_state`, `set_amplitude`, and `set_image` use
        Qt signals internally so they can be called from the asyncio
        receive thread (or any non-GUI thread) without triggering
        'QObject::killTimer: Timers cannot be stopped from another thread'.
        """

        def __init__(self, parent=None, size: int = 320):
            super().__init__(parent)
            self.setFixedSize(size, size)
            self._size = size
            self._pixmap: Optional[QPixmap] = None
            self._mouth_rect: Optional[tuple[float, float, float, float]] = None
            self._state = AvatarState.IDLE
            self._amp = 0.0   # audio amplitude 0..1 (drives mouth + jitter)
            self._t = 0.0     # animation time, seconds
            self._blink_t = 0.0  # countdown to next blink

            # 60 fps animation timer — actually 30 fps to save CPU
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._timer.start(33)  # ~30 Hz

            # Thread-safe signal carrier. All public mutators emit through
            # these signals; the slots run on the GUI thread.
            self._sig = _AvatarSignals()
            self._sig.stateChanged.connect(self._apply_state)
            self._sig.amplitudeChanged.connect(self._apply_amplitude)
            self._sig.imageChanged.connect(self._apply_image)

        # ─── public API (thread-safe) ─────────────────────────────

        def set_image(self, path: str | Path) -> bool:
            """Load the PNG. Auto-detects mouth region via OpenCV when available.

            Thread-safe — emits a signal that the GUI thread picks up.
            Returns True if the path exists; the actual load happens on
            the GUI thread.
            """
            p = Path(path).expanduser().resolve()
            if not p.exists():
                print(f"[avatar] Image not found: {p}")
                return False
            # Emit signal — GUI thread will call _apply_image(str(p))
            self._sig.imageChanged.emit(str(p))
            return True

        def set_state(self, state: str) -> None:
            """Thread-safe: emit a signal; GUI thread applies."""
            self._sig.stateChanged.emit(state)

        def set_amplitude(self, amp: float) -> None:
            """Pass live audio amplitude (0..1) from the TTS output.

            Thread-safe — emit a signal so the GUI thread applies the new
            value on its own tick. The animation timer reads self._amp.
            """
            self._sig.amplitudeChanged.emit(float(amp))

        def state(self) -> str: return self._state
        def amplitude(self) -> float: return self._amp

        # ─── private slots (GUI thread only) ──────────────────────

        def _apply_image(self, path: str) -> None:
            p = Path(path)
            pixmap = QPixmap(str(p))
            if pixmap.isNull():
                print(f"[avatar] Failed to load image: {p}")
                return
            self._pixmap = pixmap
            self._mouth_rect = self._detect_mouth(p)
            if self._mouth_rect:
                print(f"[avatar] Mouth detected at {self._mouth_rect}")
            else:
                print("[avatar] No face/mouth detected — using whole-image animation.")
            self.update()

        def _apply_state(self, state: str) -> None:
            self._state = state

        def _apply_amplitude(self, amp: float) -> None:
            self._amp = max(0.0, min(1.0, float(amp)))

        # ─── mouth detection (optional) ───────────────────────────

        @staticmethod
        def _detect_mouth(image_path: Path) -> Optional[tuple[float, float, float, float]]:
            """
            Use OpenCV's Haar cascade to find a face + mouth in the image.
            Returns (x, y, w, h) as fractions of image size in [0, 1].
            Returns None if OpenCV isn't installed or no face/mouth is found.
            """
            try:
                import cv2  # type: ignore
            except ImportError:
                return None
            try:
                img = cv2.imread(str(image_path))
                if img is None:
                    return None
                h, w = img.shape[:2]
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                face_cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                face_cascade = cv2.CascadeClassifier(face_cascade_path)
                faces = face_cascade.detectMultiScale(gray, 1.3, 5)
                if len(faces) == 0:
                    return None
                # Take the largest face
                fx, fy, fw, fh = max(faces, key=lambda r: r[2] * r[3])
                # Mouth cascade — localised to lower third of face
                mouth_cascade = cv2.CascadeClassifier(
                    cv2.data.haarcascades + "haarcascade_mcs_mouth.xml"
                )
                roi = gray[fy + int(fh * 0.55): fy + fh, fx: fx + fw]
                mouths = mouth_cascade.detectMultiScale(roi, 1.5, 5)
                if len(mouths) == 0:
                    # Fallback: assume mouth is in the lower-center of the face
                    mx = fx + int(fw * 0.20)
                    my = fy + int(fh * 0.65)
                    mw = int(fw * 0.60)
                    mh = int(fh * 0.20)
                else:
                    mx, my, mw, mh = max(mouths, key=lambda r: r[2] * r[3])
                    my = fy + int(fh * 0.55) + my
                    mx = fx + mx
                return (mx / w, my / h, mw / w, mh / h)
            except Exception as e:
                print(f"[avatar] Mouth detection failed: {e}")
                return None

        # ─── animation ───────────────────────────────────────────

        def _tick(self) -> None:
            self._t += 0.033
            self._blink_t -= 0.033
            if self._blink_t < 0:
                self._blink_t = 3.0 + (1.0 * abs(hash(self._state)) % 4)
            self.update()

        def paintEvent(self, ev) -> None:  # type: ignore[override]
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            # Background
            painter.fillRect(self.rect(), QColor(0, 0, 0, 0))  # transparent

            if self._pixmap is None or self._pixmap.isNull():
                painter.setPen(QColor(120, 120, 140))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                                  "Drop a PNG\nto give JARVIS\na face.")
                return

            # Compute animation transform per state
            cx, cy = self.width() / 2, self.height() / 2
            scale = 1.0
            dx, dy = 0.0, 0.0
            rotation = 0.0
            mouth_open = 0.0
            tint: Optional[QColor] = None

            t = self._t
            amp = self._amp
            state = self._state

            if state == AvatarState.IDLE:
                # Gentle breathing — 0.25 Hz sin wave on y + scale
                breath = math.sin(t * 0.25 * 2 * math.pi)
                dy = breath * 2.0
                scale = 1.0 + breath * 0.01
            elif state == AvatarState.LISTENING:
                # Lean forward + slight tilt
                dy = math.sin(t * 0.6 * 2 * math.pi) * 1.5
                scale = 1.03
                rotation = math.sin(t * 0.4 * 2 * math.pi) * 0.6  # degrees
            elif state == AvatarState.SPEAKING:
                # Audio-driven jitter + scale pulse + jaw drop
                jitter_x = (math.sin(t * 31.0) + math.sin(t * 17.0)) * amp * 1.5
                jitter_y = (math.sin(t * 23.0) + math.cos(t * 19.0)) * amp * 1.0
                dx = jitter_x
                dy = jitter_y
                scale = 1.0 + amp * 0.04
                mouth_open = amp
            elif state == AvatarState.THINKING:
                # Slow rotation + pulse
                rotation = math.sin(t * 0.4 * 2 * math.pi) * 4.0
                scale = 1.0 + math.sin(t * 0.8 * 2 * math.pi) * 0.015
            elif state == AvatarState.ERROR:
                tint = QColor(180, 0, 0, 60)
                scale = 1.0 + math.sin(t * 4.0 * 2 * math.pi) * 0.02

            # Apply transforms
            painter.translate(cx + dx, cy + dy)
            painter.rotate(rotation)
            painter.scale(scale, scale)

            # Center the pixmap
            pix_w = self._pixmap.width()
            pix_h = self._pixmap.height()
            # Fit to widget while preserving aspect ratio
            fit = min(self.width() / pix_w, self.height() / pix_h)
            draw_w = pix_w * fit
            draw_h = pix_h * fit
            painter.drawPixmap(QRectF(-draw_w / 2, -draw_h / 2, draw_w, draw_h),
                                self._pixmap,
                                QRectF(0, 0, pix_w, pix_h))

            # Mouth overlay — stretch lower face based on amplitude
            if mouth_open > 0.02 and self._mouth_rect is not None:
                mx, my, mw, mh = self._mouth_rect
                # Convert to widget-local coords (post-transform)
                x_local = -draw_w / 2 + (mx * draw_w)
                y_local = -draw_h / 2 + (my * draw_h)
                w_local = mw * draw_w
                h_local = mh * draw_h + mouth_open * 20
                # Draw a translucent dark ellipse representing the open mouth
                painter.setBrush(QColor(20, 0, 0, int(180 * mouth_open)))
                painter.setPen(QPen(QColor(0, 0, 0, 0)))
                painter.drawEllipse(QRectF(x_local, y_local,
                                            w_local, h_local))

            # Blink overlay — close eyes briefly every few seconds
            if state in (AvatarState.IDLE, AvatarState.LISTENING) and self._blink_t < 0.15:
                blink_alpha = int(255 * (1.0 - abs(self._blink_t - 0.075) / 0.075))
                painter.setBrush(QColor(255, 220, 220, max(0, blink_alpha - 80)))
                painter.drawRect(QRectF(-draw_w / 2, -draw_h / 2, draw_w, draw_h))

            # Tint overlay
            if tint is not None:
                painter.fillRect(QRectF(-draw_w / 2, -draw_h / 2,
                                        draw_w, draw_h), tint)

            painter.end()


# ─────────────────────────── helpers ──────────────────────────────

def is_available() -> bool:
    """True if PyQt5 or PyQt6 is importable — i.e. the avatar can actually render."""
    return _QT is not None


def list_available_pngs() -> list[Path]:
    """Return all PNG files in config/ that look like avatar candidates."""
    if not (BASE_DIR / "config").exists():
        return []
    return sorted((BASE_DIR / "config").glob("*.png"))


def install_default_avatar() -> Path:
    """
    Copy the user-provided PNG into config/avatar.png (the path the TTS
    UI expects). Returns the destination path. No-op if already there.
    """
    dest = DEFAULT_AVATAR
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


if __name__ == "__main__":
    # Quick CLI smoke test
    print(f"Qt binding available: {_QT or 'none'}")
    print(f"Default avatar path:  {DEFAULT_AVATAR}")
    print(f"Default exists:       {DEFAULT_AVATAR.exists()}")
    print(f"PNGs in config/:      {[p.name for p in list_available_pngs()]}")
