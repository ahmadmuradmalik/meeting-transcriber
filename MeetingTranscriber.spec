# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MeetingTranscriber.

Bundles:
  - Python 3.11 interpreter
  - All Python dependencies (torch, faster-whisper, demucs, speechbrain, PySide6...)
  - ffmpeg.exe (downloaded by the CI workflow into the repo root)
  - App icon (resources/icon.ico)

Models (Whisper, Demucs, ECAPA) are NOT bundled — they download on first run
to keep the .exe under ~2 GB. See pipeline.ensure_models_cached().
"""
import os
from pathlib import Path
from PyInstaller.utils.hooks import (
    collect_data_files, collect_submodules, collect_dynamic_libs, collect_all,
)

block_cipher = None
ROOT = Path(os.path.abspath(SPECPATH))

# --- Data files: icons, model configs, etc. ---
datas = []
datas += [(str(ROOT / "abbu_transcriber" / "resources" / "icon.png"),
           "abbu_transcriber/resources")]
datas += [(str(ROOT / "abbu_transcriber" / "resources" / "icon.ico"),
           "abbu_transcriber/resources")]

# ffmpeg.exe + ffprobe.exe live in repo root after the CI step downloads them.
for bin_name in ("ffmpeg.exe", "ffprobe.exe"):
    if (ROOT / bin_name).exists():
        # PyInstaller binaries are tuples (source, dest_dir_in_bundle)
        binaries_root = [(str(ROOT / bin_name), ".")]
        datas += binaries_root

# Libraries that ship YAML configs, .pth files, etc.
for pkg in ("speechbrain", "demucs", "faster_whisper", "ctranslate2",
            "torchaudio", "librosa", "pyannote"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass

# --- Hidden imports: things PyInstaller can't statically detect ---
hiddenimports = []
for pkg in ("faster_whisper", "ctranslate2", "speechbrain", "speechbrain.inference",
            "speechbrain.inference.speaker", "speechbrain.lobes", "speechbrain.lobes.models",
            "demucs", "demucs.pretrained", "demucs.apply", "demucs.audio",
            "sklearn.cluster", "sklearn.metrics", "sklearn.metrics.pairwise",
            "torch", "torchaudio", "torchcodec",
            "huggingface_hub", "huggingface_hub.utils",
            "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
            "soundfile", "numpy", "tqdm"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        hiddenimports.append(pkg)

# --- Dynamic libs: native DLLs for torch, ctranslate2, etc. ---
binaries = []
for pkg in ("torch", "ctranslate2", "numpy", "PySide6", "soundfile"):
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass

# --- Nuclear collect_all for ML libraries with Cython internals ---
# scipy, sklearn, numpy regularly add new private Cython modules between
# versions that PyInstaller's autodetection misses. collect_all pulls
# the entire package — bundle gets bigger but no module-load surprises.
# (Real bug seen: scipy 1.17 added scipy._cyutility, missing from default
#  PyInstaller scipy hook → ImportError on diarization.)
for pkg in ("scipy", "sklearn", "speechbrain", "demucs",
            "faster_whisper", "ctranslate2"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as e:
        print(f"WARN: collect_all({pkg}) failed: {e}")


a = Analysis(
    ["run_app.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Pruning saves ~200 MB. None of these are used by the app.
        "tkinter", "matplotlib", "notebook", "jupyter",
        "IPython", "pandas", "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="MeetingTranscriber",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                  # UPX often breaks PyTorch/ctranslate2 — skip
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,              # no console window — GUI only
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "abbu_transcriber" / "resources" / "icon.ico"),
)
