"""Build the demo meeting recording (fixtures/meeting.wav) from its script.

The University Circle installation-preparation meeting, spoken by three distinct text-to-speech voices so
Muse Voice Transcribe's diarization has separate speakers to label. Output is mono 16-bit PCM at 16 kHz,
which is what POST /v1/asr/transcribe accepts.

Run with a Python that has piper-tts installed:
    python scripts/make_meeting_audio.py --voices VOICE_DIR --out fixtures/meeting.wav
"""
import argparse
import audioop
import wave
from pathlib import Path

from piper import PiperVoice

TARGET_RATE = 16000
PAUSE_SECONDS = 0.7

VOICES = {
    "John": "en_US-ryan-medium",
    "Adam": "en_US-joe-medium",
    "Matthew": "en_GB-alan-medium",
}

# The meeting is held on November 13, 2026, the day before Alex installs the display. It is the script as
# written, plus one word: Adam's "Yes, John." lets John, whom nobody else addresses, be identified from context.
DIALOGUE = [
    ("John", "Thanks for joining. Before Alex installs the display tomorrow, I want to make sure all the preparation work is complete. Adam, can you give us an update on the NUC?"),
    ("Adam", "Yes, John. I've finished configuring the NUC. I've connected it to the network, tested the dashboard, and confirmed that it boots and launches correctly. It's ready for installation."),
    ("John", "Great. Matthew, what about the mounting points?"),
    ("Matthew", "I've drilled the mounting holes at the agreed locations. I also checked the spacing against the display bracket. The wall is ready for the screen."),
    ("John", "Perfect. So the display has been delivered, the NUC is configured, and the mounting points are ready. Alex is still scheduled to install the display tomorrow, November 14."),
    ("Adam", "Everything on my end is ready."),
    ("Matthew", "Same here."),
    ("John", "Great. That's all for today."),
]


def synthesize(voice: PiperVoice, text: str) -> bytes:
    """Return 16 kHz mono 16-bit PCM for one line."""
    pcm = b""
    rate = voice.config.sample_rate
    for chunk in voice.synthesize(text):
        pcm += chunk.audio_int16_bytes
    resampled, _ = audioop.ratecv(pcm, 2, 1, rate, TARGET_RATE, None)
    return resampled


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voices", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    loaded = {name: PiperVoice.load(args.voices / f"{model}.onnx") for name, model in VOICES.items()}
    silence = b"\x00\x00" * int(TARGET_RATE * PAUSE_SECONDS)

    audio = b""
    for speaker, line in DIALOGUE:
        audio += synthesize(loaded[speaker], line) + silence

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(args.out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TARGET_RATE)
        w.writeframes(audio)
    print(f"wrote {args.out} ({len(audio) / 2 / TARGET_RATE:.1f}s)")


if __name__ == "__main__":
    main()
