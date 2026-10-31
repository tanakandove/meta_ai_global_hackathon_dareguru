"""M0.2: Muse Voice Transcribe on a short WAV (mono, 16-bit PCM, 16/24 kHz)."""
import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

wav_path = sys.argv[1]
settings = {"mode": "DIARIZATION", "model": "muse-voice-transcribe-1.0", "audioEncoding": "WAV"}

with open(wav_path, "rb") as f:
    r = httpx.post(
        f"{os.environ['MODEL_BASE_URL']}/asr/transcribe",
        headers={"Authorization": f"Bearer {os.environ['MODEL_API_KEY']}"},
        files={"request": (None, json.dumps(settings), "application/json"), "audio": (os.path.basename(wav_path), f, "audio/wav")},
        timeout=120,
    )
print(r.status_code)
print(json.dumps(r.json(), indent=2) if r.headers.get("content-type", "").startswith("application/json") else r.text)
