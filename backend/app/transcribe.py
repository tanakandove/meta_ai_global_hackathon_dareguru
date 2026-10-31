"""Muse Voice Transcribe: file transcription with speaker diarization."""
import json
import wave
from dataclasses import dataclass
from pathlib import Path

import httpx

from app import config

MAX_BYTES = 32 * 1024 * 1024
MAX_SECONDS = 10 * 60
ALLOWED_RATES = {16000, 24000}


class AudioError(ValueError):
    """The audio can't be sent as is; the message says how to fix it."""


@dataclass(frozen=True)
class Turn:
    index: int
    speaker: str
    text: str
    start_ms: int
    end_ms: int


def check_wav(path: Path) -> None:
    """Reject audio the API would refuse, with an actionable message instead of a 400."""
    if path.stat().st_size > MAX_BYTES:
        raise AudioError("Audio is over 32 MB. Split it into shorter clips.")
    try:
        with wave.open(str(path), "rb") as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            seconds = w.getnframes() / rate
    except (wave.Error, EOFError) as e:
        raise AudioError(f"Not a readable WAV file ({e}). Convert to mono 16-bit PCM WAV at 16 or 24 kHz.") from e
    if channels != 1 or width != 2 or rate not in ALLOWED_RATES:
        raise AudioError(f"WAV must be mono, 16-bit, 16 or 24 kHz; got {channels} ch, {width * 8}-bit, {rate} Hz.")
    if seconds > MAX_SECONDS:
        raise AudioError(f"Audio is {seconds / 60:.1f} minutes; the limit is 10. Split it into shorter clips.")


def transcribe_wav(path: Path) -> list[Turn]:
    check_wav(path)
    settings = {"mode": "DIARIZATION", "model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV"}
    with path.open("rb") as f:
        r = httpx.post(
            f"{config.MODEL_BASE_URL}/asr/transcribe",
            headers={"Authorization": f"Bearer {config.MODEL_API_KEY}"},
            files={"request": (None, json.dumps(settings), "application/json"), "audio": (path.name, f, "audio/wav")},
            timeout=180,
        )
    r.raise_for_status()
    return [Turn(t["turnId"], t.get("speaker", "?"), t["transcript"], t["startMs"], t["endMs"]) for t in r.json()["turns"]]
