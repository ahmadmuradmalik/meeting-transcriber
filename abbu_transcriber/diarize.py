"""Speaker diarization via ECAPA-TDNN embeddings + agglomerative clustering."""
from typing import Callable

from .utils import user_cache_dir

_enc_cache = {}


def _get_encoder():
    if "enc" not in _enc_cache:
        from speechbrain.inference.speaker import EncoderClassifier
        # SpeechBrain defaults to SYMLINK strategy when materialising the model
        # into savedir. Creating symlinks on Windows requires admin rights or
        # Developer Mode — neither of which non-technical users have. Force
        # COPY so the app works for any user account.
        try:
            from speechbrain.utils.fetching import LocalStrategy
            strategy = LocalStrategy.COPY
        except Exception:
            # Older speechbrain: accepts the string form
            strategy = "copy"

        savedir = user_cache_dir() / "spkrec"
        _enc_cache["enc"] = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(savedir),
            run_opts={"device": "cpu"},
            local_strategy=strategy,
        )
    return _enc_cache["enc"]


def diarize(wav_path: str, segments: list, max_speakers: int = 6,
            on_progress: Callable[[float, str], None] = lambda p, m: None) -> list:
    import numpy as np
    import torch, torchaudio
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics import silhouette_score

    on_progress(0, "Loading speaker model...")
    enc = _get_encoder()
    wav, sr = torchaudio.load(wav_path)
    if wav.shape[0] > 1:
        wav = wav.mean(0, keepdim=True)
    if sr != 16000:
        wav = torchaudio.functional.resample(wav, sr, 16000)
        sr = 16000

    embs, keep = [], []
    for i, s in enumerate(segments):
        on_progress(20 + 50 * i / max(1, len(segments)), "Embedding speakers...")
        a, b = int(s["start"] * sr), int(s["end"] * sr)
        clip = wav[:, a:b]
        if clip.shape[1] < sr * 0.5:
            continue
        with torch.no_grad():
            e = enc.encode_batch(clip).squeeze().cpu().numpy()
        embs.append(e)
        keep.append(i)
    embs = np.array(embs)

    if len(embs) < 2:
        for s in segments: s["speaker"] = "Person 1"
        return segments

    on_progress(80, "Clustering speakers...")
    best_k, best_score = 1, -1
    for k in range(2, min(max_speakers, len(embs)) + 1):
        labels = AgglomerativeClustering(n_clusters=k, metric="cosine", linkage="average").fit_predict(embs)
        if len(set(labels)) < 2: continue
        score = silhouette_score(embs, labels, metric="cosine")
        if score > best_score:
            best_score, best_k = score, k

    if best_k == 1:
        for s in segments: s["speaker"] = "Person 1"
        return segments

    labels = AgglomerativeClustering(n_clusters=best_k, metric="cosine", linkage="average").fit_predict(embs)
    spk_map = {idx: int(labels[j]) for j, idx in enumerate(keep)}

    # Merge tiny clusters (≤1 segment) into nearest neighbor by previous speaker
    from collections import Counter
    counts = Counter(spk_map.values())
    tiny = {k for k, c in counts.items() if c <= 1}

    last = 0
    for i, s in enumerate(segments):
        if i in spk_map and spk_map[i] not in tiny:
            label = spk_map[i]
            last = label
        else:
            label = last
        s["speaker"] = f"__P{label}"  # temp tag; renumbered below

    # Renumber in order of appearance: Person 1, Person 2, ...
    seen = {}
    for s in segments:
        if s["speaker"] not in seen:
            seen[s["speaker"]] = f"Person {len(seen) + 1}"
    for s in segments:
        s["speaker"] = seen[s["speaker"]]

    on_progress(100, f"Found {len(seen)} speaker(s)")
    return segments
