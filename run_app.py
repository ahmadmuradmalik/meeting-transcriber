"""Entry point for the desktop app."""
import os, sys
from pathlib import Path

# When packaged via PyInstaller --onefile, _MEIPASS is the temp extract dir.
# Prepend it to PATH so bundled ffmpeg.exe / ffprobe.exe are reachable by
# subprocess calls inside the app.
if getattr(sys, "frozen", False):
    bundle_dir = getattr(sys, "_MEIPASS", str(Path(sys.executable).parent))
    os.environ["PATH"] = bundle_dir + os.pathsep + os.environ.get("PATH", "")

from abbu_transcriber.app import main

if __name__ == "__main__":
    main()
