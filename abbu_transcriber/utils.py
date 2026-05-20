"""Cross-platform helpers: subprocess that doesn't flash console windows,
and a writable user cache directory.
"""
import os, sys, subprocess
from pathlib import Path

# On Windows, prevents a console window from briefly flashing each time we
# spawn a subprocess (ffmpeg/ffprobe). No-op on Mac/Linux.
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _silent_kwargs(kwargs: dict) -> dict:
    """Inject the no-window creationflags on Windows."""
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
    return kwargs


def run_silent(cmd, **kwargs):
    """subprocess.run that hides console windows on Windows.
    Defaults text=True with utf-8 + replace so non-ASCII output never crashes.
    """
    if kwargs.get("text") and "encoding" not in kwargs:
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    return subprocess.run(cmd, **_silent_kwargs(kwargs))


def popen_silent(cmd, **kwargs):
    """subprocess.Popen that hides console windows on Windows."""
    if kwargs.get("text") and "encoding" not in kwargs:
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    return subprocess.Popen(cmd, **_silent_kwargs(kwargs))


def user_cache_dir(app_name: str = "AbbuTranscriber") -> Path:
    """Return a writable per-user cache directory, following platform conventions.

    Windows: %LOCALAPPDATA%\\AbbuTranscriber       (e.g. C:/Users/Dad/AppData/Local/AbbuTranscriber)
    macOS:   ~/Library/Caches/AbbuTranscriber
    Linux:   ~/.cache/AbbuTranscriber (or $XDG_CACHE_HOME)
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    out = base / app_name
    out.mkdir(parents=True, exist_ok=True)
    return out
