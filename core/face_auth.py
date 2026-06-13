"""
Face authentication — MediaPipe Face Landmarker + cosine similarity.
Inspired by ada_v2 FaceAuthManager.

Usage:
  auth = FaceAuth()
  auth.enroll()            # capture + save profile
  ok = auth.verify()       # True if face matches stored profile
  auth.start_session()     # verify once at startup; called from main.py
"""
import json
import math
import sys
import time
from pathlib import Path


_PROFILE_PATH = Path.home() / ".jarvis" / "face_profile.json"
_DEFAULT_THRESHOLD = 0.15   # cosine distance — lower = stricter


def _cosine_distance(a: list[float], b: list[float]) -> float:
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 1.0
    return 1.0 - dot / (mag_a * mag_b)


def _landmarks_to_vector(landmarks) -> list[float]:
    """Flatten (x, y, z) landmarks to a single list."""
    vec = []
    for lm in landmarks:
        vec.extend([lm.x, lm.y, lm.z])
    return vec


class FaceAuth:
    def __init__(self, threshold: float | None = None):
        try:
            import mediapipe as mp
            self._mp      = mp
            self._options = mp.tasks.vision.FaceLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(
                    model_asset_path=str(self._model_path())
                ),
                num_faces=1,
            )
            self._available = True
        except (ImportError, Exception) as e:
            print(f"[FaceAuth] MediaPipe unavailable: {e}")
            self._available = False

        self._threshold = threshold or _DEFAULT_THRESHOLD

    def _model_path(self) -> Path:
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) \
               else Path(__file__).resolve().parent.parent
        model = base / "models" / "face_landmarker.task"
        if not model.exists():
            self._download_model(model)
        return model

    def _download_model(self, dest: Path) -> None:
        import urllib.request
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = (
            "https://storage.googleapis.com/mediapipe-models/"
            "face_landmarker/face_landmarker/float16/latest/face_landmarker.task"
        )
        print(f"[FaceAuth] Downloading face landmarker model…")
        urllib.request.urlretrieve(url, dest)
        print(f"[FaceAuth] Model saved to {dest}")

    def _capture_landmarks(self, duration_sec: float = 3.0) -> list[float] | None:
        if not self._available:
            return None
        try:
            import cv2
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision as mp_vision

            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                print("[FaceAuth] No camera found.")
                return None

            landmarker = mp_vision.FaceLandmarker.create_from_options(self._options)
            deadline   = time.time() + duration_sec
            best: list[float] | None = None

            while time.time() < deadline:
                ok, frame = cap.read()
                if not ok:
                    break
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                result = landmarker.detect(mp_img)
                if result.face_landmarks:
                    best = _landmarks_to_vector(result.face_landmarks[0])

            cap.release()
            landmarker.close()
            return best
        except Exception as e:
            print(f"[FaceAuth] Capture error: {e}")
            return None

    def enroll(self) -> bool:
        """Capture face and save profile. Returns True on success."""
        if not self._available:
            print("[FaceAuth] MediaPipe not available — enroll skipped.")
            return False
        print("[FaceAuth] Look at the camera for 5 seconds…")
        vec = self._capture_landmarks(5.0)
        if not vec:
            print("[FaceAuth] No face detected during enrollment.")
            return False
        _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PROFILE_PATH.write_text(
            json.dumps({"vector": vec, "threshold": self._threshold}),
            encoding="utf-8",
        )
        print(f"[FaceAuth] Profile saved ({len(vec)} landmarks).")
        return True

    def verify(self) -> bool:
        """Capture face and compare to stored profile. Returns True if match."""
        if not self._available:
            return True   # no camera — allow by default
        if not _PROFILE_PATH.exists():
            print("[FaceAuth] No profile found — run enroll() first.")
            return False

        profile = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
        stored  = profile["vector"]
        thresh  = profile.get("threshold", self._threshold)

        vec = self._capture_landmarks(3.0)
        if not vec:
            print("[FaceAuth] No face detected during verification.")
            return False

        dist = _cosine_distance(stored, vec)
        print(f"[FaceAuth] Cosine distance: {dist:.4f} (threshold: {thresh})")
        return dist <= thresh

    def start_session(self, speak=None) -> bool:
        """
        Verify at session start. Called from main.py if face_auth_enabled=true.
        Returns True if verified (or auth disabled). speak callback for TTS feedback.
        """
        try:
            cfg_path = Path(sys.executable).parent if getattr(sys, "frozen", False) \
                       else Path(__file__).resolve().parent.parent
            cfg = json.loads((cfg_path / "config" / "api_keys.json").read_text())
        except Exception:
            cfg = {}

        if not cfg.get("face_auth_enabled", False):
            return True

        if not _PROFILE_PATH.exists():
            if speak:
                speak("No face profile found. Please enroll first, sir.")
            print("[FaceAuth] face_auth_enabled but no profile. Enrolling now.")
            return self.enroll()

        if speak:
            speak("Scanning face for authentication, sir.")
        ok = self.verify()
        if ok:
            if speak:
                speak("Identity confirmed. Welcome back, sir.")
        else:
            if speak:
                speak("Face not recognized. Access denied, sir.")
        return ok
