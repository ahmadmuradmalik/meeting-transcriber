"""Audio cleanup via Demucs vocal isolation.

Uses Demucs's in-process Python API so this works inside a PyInstaller .exe
(no shelling out to `python -m demucs`).
"""
import subprocess
from pathlib import Path

_model_cache = {}


def _get_demucs_model(name="htdemucs"):
    if name not in _model_cache:
        from demucs.pretrained import get_model
        _model_cache[name] = get_model(name)
    return _model_cache[name]


def _resample_for_demucs(input_path: str, sr: int):
    """Demucs wants float32 audio at a specific sample rate (44.1kHz typically)."""
    import torch, torchaudio
    wav, source_sr = torchaudio.load(input_path)
    if source_sr != sr:
        wav = torchaudio.functional.resample(wav, source_sr, sr)
    # Demucs expects (channels, samples). Force stereo (duplicate if mono).
    if wav.shape[0] == 1:
        wav = torch.cat([wav, wav], dim=0)
    elif wav.shape[0] > 2:
        wav = wav[:2]
    return wav, sr


def demucs_isolate(input_path: str, workdir: str, on_progress=None, cancel_check=None) -> str:
    """Run Demucs htdemucs and return path to isolated vocals.wav.

    on_progress(pct, message): called periodically.
    cancel_check(): if returns True, raises InterruptedError.
    """
    on_progress = on_progress or (lambda p, m: None)
    cancel_check = cancel_check or (lambda: False)

    import torch, torchaudio
    from demucs.apply import apply_model

    on_progress(0, "Loading separation model...")
    model = _get_demucs_model("htdemucs")
    if cancel_check(): raise InterruptedError()

    on_progress(10, "Loading audio...")
    wav, sr = _resample_for_demucs(input_path, model.samplerate)
    # apply_model expects (batch, channels, samples)
    mix = wav.unsqueeze(0)

    on_progress(20, "Isolating voices...")
    device = "cpu"
    # Try MPS on Mac (we test there); CPU fallback on Windows. CUDA if available.
    if torch.cuda.is_available():
        device = "cuda"
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        device = "mps"
    model.to(device)
    mix = mix.to(device)

    with torch.no_grad():
        stems = apply_model(model, mix, device=device, progress=False,
                            split=True, overlap=0.25)
    # stems shape: (batch, source, channels, samples)
    sources = model.sources  # e.g. ['drums', 'bass', 'other', 'vocals']
    if "vocals" not in sources:
        raise RuntimeError(f"Demucs model {model} has no 'vocals' stem")
    vocals = stems[0, sources.index("vocals")].cpu()

    if cancel_check(): raise InterruptedError()
    on_progress(95, "Saving cleaned audio...")
    out_dir = Path(workdir) / "demucs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "vocals.wav"
    torchaudio.save(str(out_path), vocals, sample_rate=sr)
    on_progress(100, "Audio cleaned")
    return str(out_path)
