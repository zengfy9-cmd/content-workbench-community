"""Local Whisper on Apple silicon or CPU Windows/Linux, with identical results."""
import importlib.util
import os
import platform
import re
import unicodedata
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


def transcribe(path: Path, initial_prompt: str, word_timestamps: bool = False, *,
               independent_windows: bool = False, vad: bool = True) -> dict:
    with _lock:
        if backend() == "mlx_whisper":
            import mlx_whisper
            return mlx_whisper.transcribe(str(path), path_or_hf_repo=os.environ.get(
                "CW_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo"), language="zh",
                initial_prompt=initial_prompt, word_timestamps=word_timestamps,
                **({"condition_on_previous_text": False} if independent_windows else {}))
        opts = {}
        if independent_windows:
            opts.update(condition_on_previous_text=False, vad_filter=vad, beam_size=5,
                        chunk_length=20 if vad else 15)
            if vad:
                opts["vad_parameters"] = {"min_silence_duration_ms": 500}
        segments, info = _model().transcribe(str(path), language="zh", initial_prompt=initial_prompt,
                                            word_timestamps=word_timestamps, **opts)
        result = [{"text": s.text, "start": s.start, "end": s.end,
                   "words": [{"word": w.word, "start": w.start, "end": w.end} for w in (s.words or [])]}
                  for s in segments]
        return {"text": "".join(s["text"] for s in result), "segments": result,
                "duration": info.duration, "voiced_duration": info.duration_after_vad if independent_windows and vad else None}


def speech_issues(result: dict) -> list[str]:
    """Catch visibly broken ASR without claiming it is a human transcript check."""
    text = unicodedata.normalize("NFKC", result.get("text", "")).casefold()
    chars = ''.join(c for c in text if c.isalnum())
    if not chars:
        return ['未识别到有效人声文字']
    if re.search(r'([^\W\d_])\1{7,}|([^\W_]{2,6})\2{5,}', chars):
        return ['转写出现异常重复']
    voiced = result.get("voiced_duration")
    if not voiced or voiced < 15:
        return []
    ranges = sorted((max(0, s['start']), s['end']) for s in result['segments']
                    if any(c.isalnum() for c in s['text']))
    coverage, end = 0, 0
    for start, stop in ranges:
        coverage += max(0, stop - max(start, end))
        end = max(end, stop)
    if coverage < .60 * voiced or len(chars) < .7 * voiced:
        return ['已识别文字对人声音轨覆盖不足']
    return []


def checked_speech(path: Path) -> tuple[dict, list[dict]]:
    attempts = []
    for vad in (True, False):
        result = transcribe(path, initial_prompt='', independent_windows=True, vad=vad)
        # No-VAD attempt cannot infer speech length on its own. Reuse the estimate
        # from the first pass where available, to avoid accepting the same short read.
        if not vad and attempts:
            result['voiced_duration'] = attempts[0]['result'].get('voiced_duration')
        issues = speech_issues(result)
        attempts.append({'pass': 'vad' if vad else 'short-windows', 'issues': issues, 'result': result})
        if not issues:
            return result, attempts
    return {}, attempts
