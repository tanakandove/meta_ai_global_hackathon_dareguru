"""Build the synthetic demo meeting (fixtures/meeting.wav) from a scripted dialogue.

Each speaker gets a distinct Piper voice so Muse Voice Transcribe's diarization
has separate speakers to label. Output is mono 16-bit PCM at 16 kHz, which is
what POST /v1/asr/transcribe accepts.

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
    "Dana": "en_US-amy-medium",
    "Sam": "en_US-ryan-medium",
    "Priya": "en_US-lessac-medium",
    "Marcus": "en_GB-alan-medium",
    "Alex": "en_US-joe-medium",
}

DIALOGUE = [
    ("Dana", "Okay, quick sync on the dashboard install. Before we wrap, a few updates."),
    ("Dana", "I'll send the revised hardware specifications to Facilities by Friday."),
    ("Sam", "We could consider contacting the vendor about the rack mounts, but I'm not sure that's needed yet."),
    ("Priya", "On the cabling plan, I sent it to Facilities yesterday. They have everything they need."),
    ("Marcus", "I've started measuring the display mounting locations. I'm about halfway through, but I'm blocked on access to the second floor."),
    ("Dana", "Alex, can you get the NUC configuration checklist ready before next Wednesday?"),
    ("Alex", "Yes, I'll have it done by then."),
    ("Dana", "Great, that's it. Thanks everyone."),
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
