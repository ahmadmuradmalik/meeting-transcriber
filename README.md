# Meeting Transcriber — local prototype

## Run it (Mac, for now)

```sh
source .venv/bin/activate
python run_app.py
```

A window opens. Drag a recording onto it, pick a quality preset, watch the stages run, then click "Open transcript".

## Quality presets

| Preset | Model | Cleanup | When to use |
|---|---|---|---|
| Quick | small (~250 MB) | none | Clean recordings, speed matters |
| Standard | large-v3 (~3 GB) | none | Default — most meetings |
| Best for noisy | large-v3 | Demucs vocal isolation | Rough recordings |

## What the transcript HTML does

- **Karaoke sync** — current word highlights as audio plays, row scrolls into view
- **Click any word or timestamp** → audio jumps there
- **Click any "Person 1" chip** → type a new name, saves locally
- **Download button** → name + choose .txt or .html
- **Print button** → clean print layout (no controls)
- **Confidence flags** — "low confidence" / "possibly noise" tags on shaky segments

## Files

- `abbu_transcriber/pipeline.py` — orchestrates the stages
- `abbu_transcriber/audio.py` — Demucs vocal isolation
- `abbu_transcriber/transcribe.py` — faster-whisper + VAD + anti-hallucination
- `abbu_transcriber/diarize.py` — ECAPA-TDNN + agglomerative clustering
- `abbu_transcriber/render.py` — HTML + .txt output
- `abbu_transcriber/app.py` — PySide6 GUI
- `run_app.py` — entry point

## Sample data

`bad_quality.m4a` and `medium_quality.jpeg` (mis-extensioned but actually audio) are
test recordings. Outputs land in `test_output/`.
