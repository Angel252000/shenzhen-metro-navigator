"""Natural-language voice interface for the Shenzhen Metro Navigator.

    python3 main.py --voice

A fourth interface over the same Part 1 engine, following the pattern of
``ui.py``, ``gui.py`` and ``web.py``: it never touches route-finding logic,
only wraps it. ``Router.find_route()`` and ``MetroNetwork.resolve()`` already
handle fuzzy station names (English/Chinese, substrings, ids), so the model
only needs to pull two bare strings out of free speech -- it never has to
match them to real stations itself.

Pipeline: microphone -> faster-whisper (STT, local) -> Ollama gemma3:1b
(JSON-schema-constrained extraction of origin/destination) ->
Router.find_route() [unchanged from Part 1] -> InstructionGenerator.render()
-> screen + macOS ``say`` (TTS, local).

These dependencies (sounddevice, faster-whisper, ollama) live in their own
venv (``.venv-voice``, python3.12) -- never in the system python3 that the
rest of the project uses, so ``--gui``/``--from``/``--to`` keep working with
zero extra installs. Run this interface with:

    .venv-voice/bin/python main.py --voice
"""

from __future__ import annotations

import json
import subprocess
from typing import Tuple

import numpy as np
import ollama
import sounddevice as sd
from faster_whisper import WhisperModel

from navigator import InstructionGenerator, MetroError, MetroNetwork, Router

SAMPLE_RATE = 16_000
RECORD_SECONDS = 6
# gemma3:1b rejects Ollama's "tools" parameter outright (HTTP 400, "does not
# support tools") -- that's its chat template, not its capability. Ollama's
# "format" parameter constrains output to a JSON schema independently of
# tool-calling support, and works fine here: 1b stays the model, matching
# the 8 GB RAM budget this was designed around (see Obsidian note 07).
OLLAMA_MODEL = "gemma3:1b"

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "origen": {"type": "string"},
        "destino": {"type": "string"},
    },
    "required": ["origen", "destino"],
}

EXTRACTION_SYSTEM_PROMPT = (
    "You extract metro station names from a tourist's question. Output ONLY "
    "the two station names, exactly as the user said them -- strip verbs, "
    "pronouns, and filler words like 'estoy en' or 'quiero ir a'. Never "
    "translate the station name. JSON only: "
    '{"origen": ..., "destino": ...}. If no route is being asked, use empty '
    "strings."
)


class VoiceError(Exception):
    """Raised when speech can't be turned into a route request."""


def record_audio(seconds: float = RECORD_SECONDS) -> np.ndarray:
    print(f"\U0001F3A4 Listening ({seconds:.0f}s)... ask how to get somewhere.")
    audio = sd.rec(
        int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32"
    )
    sd.wait()
    return audio.reshape(-1)


def transcribe(model: WhisperModel, audio: np.ndarray) -> str:
    segments, _ = model.transcribe(audio, language=None)
    return " ".join(segment.text.strip() for segment in segments).strip()


def extract_stations(text: str) -> Tuple[str, str]:
    """Ask gemma3 for (origin, destination) via schema-constrained JSON.

    ``resolve()`` (navigator.py) does the real name-matching afterwards, so
    the model is only asked to copy out two spans of text, never to identify
    an actual station.
    """
    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        format=EXTRACTION_SCHEMA,
        options={"temperature": 0},
    )
    try:
        payload = json.loads(response["message"]["content"])
        origin, destination = payload["origen"], payload["destino"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise VoiceError(f"could not parse a route request from: {text!r}") from exc
    if not origin or not destination:
        raise VoiceError(f"could not find a route request in: {text!r}")
    return origin, destination


def speak(text: str) -> None:
    """TTS via macOS ``say``. macOS-only, like the spec allows -- the rest
    of the project (stdlib only) stays portable; this one piece doesn't."""
    try:
        subprocess.run(["say", text], check=False)
    except FileNotFoundError:
        pass


def handle_request(network: MetroNetwork, generator: InstructionGenerator, text: str) -> str:
    origin, destination = extract_stations(text)
    route = Router(network).find_route(origin, destination)
    return generator.render(route)


def run_voice(network: MetroNetwork, lang: str = "both") -> int:
    print("Loading local speech model (first run downloads it, ~75 MB)...")
    whisper_model = WhisperModel("tiny", compute_type="int8")
    generator = InstructionGenerator(network, lang)

    print("Ready. Speak a request like 'how do I get from Luohu to Airport "
          "East?'. Ctrl+C to quit.")
    while True:
        try:
            audio = record_audio()
            text = transcribe(whisper_model, audio)
            if not text:
                print("(didn't catch that -- try again)")
                continue
            print(f"> {text}")

            message = handle_request(network, generator, text)
            print(message)
            speak(message)
        except (VoiceError, MetroError) as exc:
            print(f"error: {exc}")
            speak(f"Sorry, {exc}")
        except KeyboardInterrupt:
            print("\nBye!")
            return 0
