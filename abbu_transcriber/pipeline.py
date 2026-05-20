"""End-to-end pipeline: audio -> (clean) -> transcribe -> diarize -> render.

Designed to be called from a GUI thread. Accepts a progress callback:
  on_progress(stage: str, pct: float, message: str)
where pct is 0..100 within the stage, or -1 for indeterminate.
"""
import os, time, tempfile, shutil, json, html, base64
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional


def _hf_cache_has(repo_id: str) -> bool:
    """Best-effort check whether a HuggingFace repo is already cached."""
    try:
        from huggingface_hub import try_to_load_from_cache
        # Sentinel files for the two repos we care about
        sentinels = ["config.json", "model.bin", "hyperparams.yaml"]
        for s in sentinels:
            if try_to_load_from_cache(repo_id, s) not in (None, False):
                return True
    except Exception:
        pass
    return False


def will_need_setup(preset: str) -> bool:
    """Predict whether ensure_models_cached() will actually do any downloads.
    Used by the GUI to decide whether to show the setup row.

    Errs on the side of True — better to show a 'setup' row that resolves
    instantly than to silently hang for minutes on a hidden download.
    """
    cfg = PRESETS[preset]
    if not _hf_cache_has(f"Systran/faster-whisper-{cfg['model']}"):
        return True
    if not _hf_cache_has("speechbrain/spkrec-ecapa-voxceleb"):
        return True
    if cfg["demucs"]:
        # Demucs caches as hash-named .th files (e.g. 955717e8-8726e21a.th).
        # htdemucs is a single ~80MB checkpoint — at least one must be present.
        demucs_cache = Path.home() / ".cache" / "torch" / "hub" / "checkpoints"
        if not demucs_cache.exists():
            return True
        if not any(p.stat().st_size > 50_000_000 for p in demucs_cache.glob("*.th")):
            return True
    return False


def ensure_models_cached(cfg: dict, on_progress: Callable[[float, str], None]):
    """Pre-download any missing models with visible progress. No-op if cached."""
    from tqdm.auto import tqdm

    class CallbackTqdm(tqdm):
        """tqdm subclass that re-emits progress to our GUI callback."""
        def __init__(self, *args, **kwargs):
            # In windowed PyInstaller bundles sys.stdout can be None, which
            # makes tqdm's default file=sys.stderr fall over. Force a sink.
            if kwargs.get("file") is None:
                kwargs["file"] = open(os.devnull, "w", encoding="utf-8")
            super().__init__(*args, **kwargs)
            self._label = (self.desc or "Downloading").split("/")[-1][:40]
            self._emit()
        def update(self, n=1):
            super().update(n)
            self._emit()
        def _emit(self):
            if self.total and self.total > 0:
                pct = 100 * self.n / self.total
                mb_done = self.n / (1024 * 1024)
                mb_total = self.total / (1024 * 1024)
                on_progress(pct, f"Downloading {self._label} ({mb_done:.0f}/{mb_total:.0f} MB)")

    from huggingface_hub import snapshot_download

    whisper_repo = f"Systran/faster-whisper-{cfg['model']}"
    spk_repo = "speechbrain/spkrec-ecapa-voxceleb"

    if not _hf_cache_has(whisper_repo):
        on_progress(-1, f"Setting up: downloading speech model ({cfg['model']})...")
        snapshot_download(repo_id=whisper_repo, tqdm_class=CallbackTqdm)

    if not _hf_cache_has(spk_repo):
        on_progress(-1, "Setting up: downloading speaker model...")
        snapshot_download(repo_id=spk_repo, tqdm_class=CallbackTqdm)

    if cfg["demucs"]:
        # Demucs uses torch.hub which has its own download progress; we just
        # trigger it. The indeterminate spinner covers it.
        try:
            import demucs.pretrained
            demucs_cache = Path.home() / ".cache" / "torch" / "hub" / "checkpoints"
            if not any(demucs_cache.glob("*htdemucs*")):
                on_progress(-1, "Setting up: downloading audio cleanup model...")
            demucs.pretrained.get_model("htdemucs")
        except Exception:
            pass  # will surface during the cleanup stage if it fails

# Quality presets: model size, demucs on/off, compute type
PRESETS = {
    # NOTE on compute_type: large-v3 produces garbage under int8 quantization
    # (verified May 2026). We use float32 for the large model. Smaller models
    # tolerate int8 well so quick mode can use it for speed.
    "quick":    {"model": "small",     "demucs": False, "compute": "int8"},
    "standard": {"model": "large-v3",  "demucs": False, "compute": "float32"},
    "best":     {"model": "large-v3",  "demucs": True,  "compute": "float32"},
}

Progress = Callable[[str, float, str], None]


def _noop(stage, pct, msg): pass


def run_pipeline(
    audio_path: str,
    output_dir: str,
    preset: str = "standard",
    on_progress: Progress = _noop,
    cancel_check: Callable[[], bool] = lambda: False,
) -> dict:
    """Run the full pipeline. Returns dict with output paths and stats."""
    cfg = PRESETS[preset]
    audio_path = Path(audio_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = audio_path.stem
    html_out = output_dir / f"{stem}_transcript.html"
    txt_out = output_dir / f"{stem}_transcript.txt"

    t_total = time.time()
    workdir = Path(tempfile.mkdtemp(prefix="abbu_"))
    try:
        # ---- Stage 0: ensure models cached (first run downloads ~3 GB)
        ensure_models_cached(cfg,
            on_progress=lambda p, m: on_progress("setup", p, m))
        if cancel_check(): raise InterruptedError()

        # ---- Stage 1: optional vocal isolation
        speech_audio = str(audio_path)
        if cfg["demucs"]:
            on_progress("cleanup", -1, "Cleaning audio (isolating voices)...")
            from .audio import demucs_isolate
            speech_audio = demucs_isolate(str(audio_path), str(workdir),
                                          on_progress=lambda p, m: on_progress("cleanup", p, m),
                                          cancel_check=cancel_check)
        if cancel_check(): raise InterruptedError()

        # ---- Stage 2: transcribe
        on_progress("transcribe", -1, f"Loading {cfg['model']} model...")
        from .transcribe import transcribe_file
        segments, duration = transcribe_file(
            speech_audio,
            model_size=cfg["model"],
            compute_type=cfg["compute"],
            on_progress=lambda p, m: on_progress("transcribe", p, m),
            cancel_check=cancel_check,
        )
        if cancel_check(): raise InterruptedError()

        # ---- Stage 3: diarize (only if multiple segments)
        if len(segments) >= 2:
            on_progress("diarize", -1, "Identifying speakers...")
            from .diarize import diarize
            segments = diarize(speech_audio, segments,
                               on_progress=lambda p, m: on_progress("diarize", p, m))
        else:
            for s in segments: s["speaker"] = "S1"
        if cancel_check(): raise InterruptedError()

        # ---- Stage 4: render
        on_progress("render", -1, "Generating transcript...")
        from .render import render_html, render_txt
        # Audio for embed: use original (small enough for ≤1hr at AAC 128k)
        title = audio_path.stem.replace("_", " ").title()
        # Cache segments + duration so we can re-render without re-transcribing
        seg_cache = output_dir / f"{stem}_segments.json"
        seg_cache.write_text(__import__("json").dumps(
            {"segments": segments, "file_duration": duration}, indent=2))
        render_html(str(audio_path), segments, str(html_out), title, duration)
        render_txt(segments, str(txt_out), title, duration)

        on_progress("done", 100, "Done")
        return {
            "html": str(html_out),
            "txt": str(txt_out),
            "elapsed": time.time() - t_total,
            "duration": duration,
            "n_segments": len(segments),
            "n_speakers": len({s["speaker"] for s in segments}),
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
