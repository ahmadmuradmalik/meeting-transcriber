"""faster-whisper wrapper with VAD + anti-hallucination flags."""
from typing import Callable

# Cache model across calls in same process
_model_cache = {}


def _get_model(model_size: str, compute_type: str):
    key = (model_size, compute_type)
    if key not in _model_cache:
        from faster_whisper import WhisperModel
        _model_cache[key] = WhisperModel(model_size, device="auto", compute_type=compute_type)
    return _model_cache[key]


def transcribe_file(
    path: str,
    model_size: str = "large-v3-turbo",
    compute_type: str = "int8",
    language: str = "en",
    on_progress: Callable[[float, str], None] = lambda p, m: None,
    cancel_check: Callable[[], bool] = lambda: False,
):
    """Transcribe a file. Returns (segments, duration_sec)."""
    model = _get_model(model_size, compute_type)
    on_progress(0, "Transcribing...")
    segments_iter, info = model.transcribe(
        path,
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        condition_on_previous_text=False,
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8],
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
        word_timestamps=True,           # enable for karaoke-style highlighting
    )
    duration = info.duration
    segs = []
    for s in segments_iter:
        if cancel_check():
            raise InterruptedError()
        words = []
        if s.words:
            for w in s.words:
                words.append({
                    "start": round(w.start, 3),
                    "end":   round(w.end, 3),
                    "word":  w.word,
                })
        segs.append({
            "start": round(s.start, 2),
            "end": round(s.end, 2),
            "avg_logprob": round(s.avg_logprob, 2),
            "no_speech": round(s.no_speech_prob, 2),
            "text": s.text.strip(),
            "words": words,
        })
        # progress = fraction of duration covered
        if duration > 0:
            on_progress(min(99, 100 * s.end / duration), f"Transcribing... {int(100*s.end/duration)}%")
    on_progress(100, "Transcribed")
    return segs, duration
