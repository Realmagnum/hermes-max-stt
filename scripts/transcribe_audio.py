#!/usr/bin/env python3
"""Transcribe audio file using faster-whisper (CPU, Russian/auto-detect).

Usage:
    transcribe_audio.py <audio_file> [--model tiny|base|small] [--language ru|auto]
    transcribe_audio.py --latest  # transcribe most recent file in audio_cache

Output: transcription text to stdout
"""

import sys
import os
from pathlib import Path

STT_VENV = Path(os.environ.get("STT_VENV", str(Path.home() / ".hermes/stt-venv")))
AUDIO_CACHE = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / "audio_cache"


def find_latest_audio() -> str | None:
    """Return path to most recently modified audio file in cache."""
    if not AUDIO_CACHE.exists():
        return None
    files = sorted(AUDIO_CACHE.glob("*"), key=lambda f: f.stat().st_mtime, reverse=True)
    for f in files:
        if f.suffix.lower() in (".ogg", ".opus", ".mp3", ".m4a", ".wav", ".flac"):
            return str(f)
    return None


def transcribe(file_path: str, model_name: str = "base", language: str | None = "ru") -> str:
    """Transcribe audio file and return text."""
    venv_python = STT_VENV / "bin" / "python3"
    if not venv_python.exists():
        print(
            "ERROR: STT venv not found. "
            "Run: python3 -m venv ~/.hermes/stt-venv && "
            "~/.hermes/stt-venv/bin/pip install faster-whisper",
            file=sys.stderr,
        )
        sys.exit(1)

    import subprocess

    import shlex

    script = f"""
from faster_whisper import WhisperModel

model = WhisperModel({shlex.quote(model_name)}, device='cpu', compute_type='int8')
segments, info = model.transcribe({shlex.quote(file_path)}, language={language!r})
for seg in segments:
    print(seg.text.strip())
"""
    cmd = [str(venv_python), "-c", script]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"ERROR: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Transcribe audio with faster-whisper")
    parser.add_argument("audio_file", nargs="?", help="Path to audio file")
    parser.add_argument("--latest", action="store_true",
                        help="Transcribe most recent audio file in cache")
    parser.add_argument("--model", default="base", choices=["tiny", "base", "small"],
                        help="Model size (default: base)")
    parser.add_argument("--language", default="ru",
                        help="Language code or 'auto' (default: ru)")
    args = parser.parse_args()

    if args.latest:
        file_path = find_latest_audio()
        if not file_path:
            print("ERROR: No audio files found in cache", file=sys.stderr)
            sys.exit(1)
    elif args.audio_file:
        file_path = args.audio_file
        if not os.path.exists(file_path):
            print(f"ERROR: File not found: {file_path}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)

    lang = args.language if args.language != "auto" else None
    text = transcribe(file_path, args.model, lang)
    print(text)


if __name__ == "__main__":
    main()
