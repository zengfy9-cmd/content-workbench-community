"""Local Whisper on Apple silicon or CPU Windows/Linux, with identical results."""
import importlib.util
import os
import platform
import threading
from functools import lru_cache
from pathlib import Path

_lock = threading.Lock()


def backend() -> str:
    return "mlx_whisper" if platform.system() == "Darwin" and platform.machine() == "arm64" else "faster_whisper"


def available() -> bool:
    return importlib.util.find_spec(backend()) is not None


@lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel
    return WhisperModel(os.environ.get("CW_WHISPER_MODEL", "small"), device="cpu", compute_type="int8",
                        download_root=os.environ.get("CW_WHISPER_CACHE"))


def transcribe(path: Path, initial_prompt: str, word_timestamps: bool = False) -> dict:
    with _lock:
        if backend() == "mlx_whisper":
            import mlx_whisper
            return mlx_whisper.transcribe(str(path), path_or_hf_repo=os.environ.get(
                "CW_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo"), language="zh",
                initial_prompt=initial_prompt, word_timestamps=word_timestamps)
        segments, _ = _model().transcribe(str(path), language="zh", initial_prompt=initial_prompt,
                                         word_timestamps=word_timestamps)
        result = [{"text": s.text, "start": s.start, "end": s.end,
                   "words": [{"word": w.word, "start": w.start, "end": w.end} for w in (s.words or [])]}
                  for s in segments]
        return {"text": "".join(s["text"] for s in result), "segments": result}
