"""Entry point for the desktop app."""
import os, sys
from pathlib import Path

# --- CRITICAL: stdout/stderr fix for windowed PyInstaller bundles ---
# In PyInstaller --windowed mode (no console), sys.stdout and sys.stderr are
# None. Any library that tries to write progress bars (tqdm, demucs, etc.)
# crashes with "'NoneType' object has no attribute 'write'". Replace them
# with /dev/null sinks before any library imports.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# When packaged via PyInstaller --onefile, _MEIPASS is the temp extract dir.
# Prepend it to PATH so bundled ffmpeg.exe / ffprobe.exe are reachable by
# subprocess calls inside the app.
if getattr(sys, "frozen", False):
    bundle_dir = getattr(sys, "_MEIPASS", str(Path(sys.executable).parent))
    os.environ["PATH"] = bundle_dir + os.pathsep + os.environ.get("PATH", "")

from abbu_transcriber.app import main

if __name__ == "__main__":
    main()
