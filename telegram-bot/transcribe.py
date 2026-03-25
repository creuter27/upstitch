"""
Voice transcription via local faster-whisper (runs on-device, no API key needed).

The model is downloaded once on first use to ~/.cache/faster-whisper (outside Tresorit).
  tiny  ~ 75 MB  — fast, good enough for short voice commands
  base  ~ 150 MB — more accurate, switch if tiny misses words
"""
from pathlib import Path
from faster_whisper import WhisperModel

# Loaded lazily on first use so bot startup is instant.
_model: WhisperModel | None = None
_MODEL_SIZE = "tiny"
_CACHE_DIR = str(Path.home() / ".cache" / "faster-whisper")


def is_model_loaded() -> bool:
    return _model is not None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(
            _MODEL_SIZE,
            device="cpu",
            compute_type="int8",
            download_root=_CACHE_DIR,
        )
    return _model


def transcribe(audio_path: Path, language: str = "de") -> str:
    """Transcribe an audio file (OGG/MP3/WAV/…) and return the text."""
    model = _get_model()
    segments, _ = model.transcribe(str(audio_path), language=language, beam_size=5)
    return " ".join(seg.text.strip() for seg in segments).strip()
