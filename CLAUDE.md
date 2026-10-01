# Shenzhen Metro Smart Navigator

Offline route planner for the Shenzhen Metro. Stdlib-only core; one optional
interface breaks that rule on purpose (see Voice below).

## Stack

- Python 3.8+. Core (`navigator.py`, `ui.py`, `web.py`, `main.py`,
  `test_navigator.py`) has **zero third-party dependencies**.
- `gui.py` needs `tkinter` — the Homebrew `python3` (3.14) lacks `_tkinter`.
  Run it with `/opt/homebrew/bin/python3.13` or `/usr/bin/python3`.
- `voice.py` needs `sounddevice`, `numpy`, `faster-whisper`, `ollama` — all
  isolated in `.venv-voice/` (python3.12, gitignored). Never install these in
  the system `python3`.

## File map

| File | Role |
|---|---|
| `metro_data.json` | The map: lines, ordered stations, EN/中文 names, timing model. Data, not code. |
| `navigator.py` | The engine: `MetroNetwork`, `Router` (Dijkstra + 0-1 BFS), `InstructionGenerator`. Prints nothing. Computes everything. **This is the only file that may change routing behavior.** |
| `ui.py` | CLI, numbered menus. |
| `gui.py` | tkinter window, dependent combo boxes. |
| `web.py` | `http.server` front end, port 8731. |
| `voice.py` | Natural-language voice front end (STT → Ollama extraction → engine → TTS). |
| `main.py` | `argparse` entry point; lazy-imports `gui`/`voice` so missing optional deps never break the CLI. |
| `test_navigator.py` | 23 unit tests, toy network + real map. |

## Architecture rule (do not break)

**The engine never prints; the interfaces never compute.** Every interface
(`ui.py`, `gui.py`, `web.py`, `voice.py`) is a thin wrapper that calls
`Router.find_route()` and `InstructionGenerator.render()` and nothing else.
Adding a fifth interface should never require touching `navigator.py`. If it
does, something is wrong with the request, not with the engine.

Key design decision: the graph node is `(station, line)`, not `station` —
required to charge transfers correctly. See the graph in `navigator.py`
around `MetroNetwork.neighbors()`.

## Commands

```bash
python3 main.py                                   # interactive CLI
python3 main.py --from Luohu --to "Airport East"  # one-shot
python3 main.py --from 罗湖 --to 会展中心 --lang zh
python3.13 main.py --gui                          # needs tkinter-capable python
python3 web.py                                    # http://127.0.0.1:8731
.venv-voice/bin/python main.py --voice             # needs `ollama serve` + gemma3:1b pulled
python3 -m unittest -q                            # 23 tests, should stay green
```

## Gotchas

- `gemma3:1b` (used by `voice.py`) rejects Ollama's `tools` parameter outright
  (HTTP 400). Extraction uses the `format` JSON-schema parameter instead —
  works with any model, doesn't need `tools` support. Don't "fix" this by
  switching back to `tools`.
- `resolve()` in `navigator.py` only matches English/Chinese station names.
  Spanish or other-language station names will not resolve — this is a Part 1
  design boundary, not a bug to patch inside `voice.py`.
- Network covers 6 of ~17 real Shenzhen Metro lines (141 stations). Extending
  it is pure data entry in `metro_data.json` — no code changes needed.

## Deeper context

Full rationale, decisions-and-alternatives, and the backlog live in Obsidian:
`Shenzhen-Metro.md` (cerebro) in the user's vault, with notes 01–11 covering
architecture, data model, algorithm, decisions, state, and the backlog
ordered by value/effort. Read that before making non-trivial changes — it
has the "why", this file only has the "what".
