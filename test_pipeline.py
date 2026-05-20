"""Test the pipeline end-to-end on both sample files."""
import sys, time
from pathlib import Path
from abbu_transcriber.pipeline import run_pipeline

def progress(stage, pct, msg):
    print(f"  [{stage}] {msg} ({pct:.0f}%)" if pct >= 0 else f"  [{stage}] {msg}")

OUT = Path("test_output")
OUT.mkdir(exist_ok=True)

for src, preset in [
    ("medium_quality.jpeg", "standard"),
    ("bad_quality.m4a", "best"),
]:
    print(f"\n=== {src} ({preset}) ===")
    t0 = time.time()
    r = run_pipeline(src, str(OUT), preset, on_progress=progress)
    print(f"\nDone in {r['elapsed']:.1f}s: {r['html']}")
