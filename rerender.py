"""Re-render an existing transcript from cached segments JSON.
Speeds up iteration on render.py: pipeline.py caches segments as JSON so we
can swap rendering without re-running Whisper.
"""
import json, sys
from pathlib import Path
from abbu_transcriber.render import render_html, render_txt

# Expect cached segments at: test_output/<stem>_segments.json
for stem, audio, title in [
    ("medium_quality", "medium_quality.jpeg", "Medium Quality"),
    ("bad_quality",    "bad_quality.m4a",     "Bad Quality"),
]:
    seg_path = Path(f"test_output/{stem}_segments.json")
    if not seg_path.exists():
        print(f"  skip {stem}: no cached segments at {seg_path}")
        continue
    data = json.loads(seg_path.read_text())
    out_html = f"test_output/{stem}_transcript.html"
    out_txt  = f"test_output/{stem}_transcript.txt"
    render_html(audio, data["segments"], out_html, title, data["file_duration"])
    render_txt(data["segments"], out_txt, title, data["file_duration"])
    print(f"  rendered {stem}")
