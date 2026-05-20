"""Render diarized transcript to HTML (with sidecar audio reference) and plain text."""
import html, json, shutil
from pathlib import Path
from datetime import datetime

from .utils import run_silent


def _probe_audio_format(src: Path) -> str:
    """Return a sensible audio extension based on actual codec/container."""
    try:
        out = run_silent(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=codec_name:format=format_name",
             "-of", "default=nw=1", str(src)],
            check=True, capture_output=True, text=True,
        ).stdout
    except Exception:
        return src.suffix.lower() or ".m4a"
    codec = next((l.split("=")[1] for l in out.splitlines() if l.startswith("codec_name=")), "")
    fmt = next((l.split("=")[1] for l in out.splitlines() if l.startswith("format_name=")), "")
    if "mp4" in fmt or "mov" in fmt or "3gp" in fmt or codec == "aac": return ".m4a"
    if codec == "mp3": return ".mp3"
    if codec in ("vorbis", "opus") or "ogg" in fmt: return ".ogg"
    if codec == "flac": return ".flac"
    if codec in ("pcm_s16le", "pcm_s24le") or "wav" in fmt: return ".wav"
    if "webm" in fmt or "matroska" in fmt: return ".webm"
    return src.suffix.lower() or ".m4a"


def _copy_audio_for_web(src: Path, base_out_path: Path) -> Path:
    """Copy/remux audio next to the HTML. For MP4-family, use +faststart so the
    browser can read duration immediately. Returns the destination Path."""
    real_ext = _probe_audio_format(src)
    dst = base_out_path.with_suffix(real_ext)
    if src.resolve() == dst.resolve():
        return dst
    if real_ext == ".m4a":
        try:
            run_silent(
                ["ffmpeg", "-y", "-i", str(src), "-c", "copy",
                 "-movflags", "+faststart", "-f", "mp4", str(dst)],
                check=True, capture_output=True,
            )
            return dst
        except Exception:
            pass
    shutil.copy2(src, dst)
    return dst

# A friendlier palette — softer and higher contrast than primary RGB
SPEAKER_COLORS = [
    ("#1d4ed8", "#dbeafe"),  # blue
    ("#b91c1c", "#fee2e2"),  # red
    ("#047857", "#d1fae5"),  # green
    ("#a16207", "#fef3c7"),  # amber
    ("#7c3aed", "#ede9fe"),  # purple
    ("#0e7490", "#cffafe"),  # teal
]


def fmt_time(s: float) -> str:
    m, s = divmod(int(s), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def render_html(audio_path: str, segments: list, out_path: str, title: str, file_duration: float):
    audio_path = Path(audio_path)
    out_path = Path(out_path)
    # Copy audio alongside HTML so the player works (and avoid huge base64).
    audio_dst = _copy_audio_for_web(audio_path, out_path)
    audio_ref = audio_dst.name

    mime_map = {".m4a": "audio/mp4", ".mp4": "audio/mp4", ".jpeg": "audio/mp4",
                ".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg",
                ".webm": "audio/webm", ".aac": "audio/aac", ".flac": "audio/flac",
                ".mov": "audio/mp4"}
    mime = mime_map.get(audio_dst.suffix.lower(), "audio/mpeg")

    speakers = sorted({s["speaker"] for s in segments},
                      key=lambda x: int(x.split()[-1]) if x.split()[-1].isdigit() else 999)
    spk_color = {sp: SPEAKER_COLORS[i % len(SPEAKER_COLORS)] for i, sp in enumerate(speakers)}

    n_total = len(segments)
    n_low = sum(1 for s in segments if s.get("avg_logprob", 0) < -0.6)
    speech_duration = max((s["end"] for s in segments), default=0)
    default_name = out_path.stem  # transcript filename without extension

    # Render rows. If we have word-level timestamps, wrap each word in a
    # span so JS can highlight them in sync with the audio.
    rows = []
    last_spk = None
    for ri, s in enumerate(segments):
        flags = ""
        if s.get("avg_logprob", 0) < -0.6:
            flags += '<span class="flag low" title="Model was unsure here. Click the timestamp to listen back.">low confidence</span>'
        if s.get("no_speech", 0) > 0.5:
            flags += '<span class="flag noise" title="Likely background noise rather than speech.">possibly noise</span>'
        spk = s["speaker"]
        fg, bg = spk_color[spk]
        if spk != last_spk:
            chip = (f'<div class="chip" data-speaker="{html.escape(spk)}" '
                    f'style="background:{bg};color:{fg}" title="Click to rename">'
                    f'{html.escape(spk)}</div>')
        else:
            chip = '<div class="chip empty"></div>'
        last_spk = spk
        cls = "row" + (" lowconf" if s.get("avg_logprob", 0) < -0.6 else "")

        words = s.get("words") or []
        if words:
            # Emit per-word spans. Whisper's `word` includes leading whitespace.
            text_html = "".join(
                f'<span class="w" data-s="{w["start"]}" data-e="{w["end"]}">{html.escape(w["word"])}</span>'
                for w in words
            )
        else:
            text_html = html.escape(s["text"])

        rows.append(
            f'<div class="{cls}" id="row-{ri}" data-row-start="{s["start"]}" data-row-end="{s["end"]}">{chip}'
            f'<a class="ts" data-t="{s["start"]}" href="#" title="Jump to {fmt_time(s["start"])}">{fmt_time(s["start"])}</a>'
            f'<div class="text">{text_html}{flags}</div></div>'
        )

    # Legend — chips here are also clickable to rename
    legend_items = "".join(
        f'<span class="leg-chip chip" data-speaker="{html.escape(sp)}" '
        f'style="background:{bg};color:{fg}" title="Click to rename">{html.escape(sp)}</span>'
        for sp, (fg, bg) in spk_color.items()
    )

    # Serialize transcript for JS download (compact)
    plain_text_lines = [title, "=" * len(title), ""]
    last_spk = None
    for s in segments:
        if s["speaker"] != last_spk:
            plain_text_lines.append("")
            plain_text_lines.append(f"{s['speaker']}:")
            last_spk = s["speaker"]
        marks = ""
        if s.get("avg_logprob", 0) < -0.6: marks += " (low confidence)"
        if s.get("no_speech", 0) > 0.5: marks += " (possibly noise)"
        plain_text_lines.append(f"  [{fmt_time(s['start'])}]  {s['text']}{marks}")
    plain_text = "\n".join(plain_text_lines)

    meta_bits = [
        f"<b>File length:</b> {fmt_time(file_duration)}",
        f"<b>Speech:</b> {fmt_time(speech_duration)}",
        f"<b>Segments:</b> {n_total}",
        f"<b>Speakers:</b> {len(speakers)}",
    ]
    if n_low:
        meta_bits.append(f"<b>Low confidence:</b> {n_low}")
    meta_bits.append(f"<b>Processed:</b> {datetime.now().strftime('%Y-%m-%d')}")
    meta_html = "  ·  ".join(meta_bits)

    # Stable storage key per transcript file, so renames persist across reloads
    # but don't bleed across different meetings.
    storage_key = f"abbu-spk:{audio_ref}:{title}"

    doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{
    --ink: #111827;
    --ink-soft: #4b5563;
    --ink-mute: #9ca3af;
    --bg: #fbfbfc;
    --card: #ffffff;
    --border: #e5e7eb;
    --accent: #2563eb;
  }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; }}
  body {{
    font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    color: var(--ink); background: var(--bg);
  }}
  .wrap {{ max-width: 880px; margin: 0 auto; padding: 32px 24px 80px; }}
  header h1 {{ font-size: 26px; margin: 0 0 6px; letter-spacing: -0.01em; }}
  .meta {{ color: var(--ink-soft); font-size: 13px; }}

  .toolbar {{
    margin: 20px 0 16px; display: flex; gap: 8px; flex-wrap: wrap;
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 10px;
  }}
  .btn {{
    appearance: none; border: 1px solid var(--border); background: #fff;
    color: var(--ink); font: inherit; font-size: 13px; font-weight: 500;
    padding: 7px 12px; border-radius: 7px; cursor: pointer;
    display: inline-flex; align-items: center; gap: 6px;
  }}
  .btn:hover {{ background: #f3f4f6; border-color: #d1d5db; }}
  .btn.primary {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
  .btn.primary:hover {{ background: #1d4ed8; }}

  .player {{
    position: sticky; top: 0; z-index: 10; background: var(--bg);
    padding: 12px 0 14px; border-bottom: 1px solid var(--border); margin-bottom: 18px;
  }}
  .player audio {{ width: 100%; }}

  .legend {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0 22px; }}
  .leg-chip {{
    font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 999px;
  }}

  .row {{
    display: grid; grid-template-columns: 96px 64px 1fr; gap: 12px;
    padding: 7px 0; align-items: baseline;
    border-bottom: 1px solid transparent;
    transition: background-color .15s ease;
  }}
  .row:hover {{ background: #f9fafb; border-bottom-color: var(--border); }}
  .row.lowconf .text {{ color: var(--ink-mute); }}
  .row.active {{ background: #fffbeb; border-bottom-color: #fde68a; }}
  .row.active:hover {{ background: #fff3c4; }}

  .w {{ transition: background-color .08s linear, color .08s linear; padding: 0 1px; border-radius: 3px; }}
  .w.past {{ color: var(--ink-soft); }}
  .w.now {{ background: #fde68a; color: #111827; font-weight: 500; }}
  .text:hover .w {{ cursor: pointer; }}

  .chip {{
    font-size: 11px; font-weight: 600; padding: 2px 9px; border-radius: 999px;
    text-align: center; white-space: nowrap; justify-self: end;
    line-height: 1.5;
    cursor: pointer; user-select: none;
    transition: filter .12s;
  }}
  .chip:hover {{ filter: brightness(.92); }}
  .chip.empty {{ background: transparent; cursor: default; }}
  .chip.empty:hover {{ filter: none; }}
  .chip.editing {{
    cursor: text; outline: 2px solid var(--accent); outline-offset: 1px;
  }}
  .chip-input {{
    font-size: 11px; font-weight: 600; padding: 2px 9px; border-radius: 999px;
    border: none; outline: 2px solid var(--accent); outline-offset: 1px;
    width: 100px; text-align: center; line-height: 1.5;
    font-family: inherit;
  }}

  .ts {{
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 12px; color: var(--ink-mute); text-decoration: none;
    padding: 2px 4px; border-radius: 4px;
  }}
  .ts:hover {{ color: var(--accent); background: #eff6ff; }}

  .text {{ font-size: 15.5px; }}
  .flag {{
    display: inline-block; font-size: 10.5px; font-weight: 500;
    padding: 1px 7px; margin-left: 8px; border-radius: 999px; vertical-align: middle;
  }}
  .flag.low {{ background: #fef3c7; color: #92400e; }}
  .flag.noise {{ background: #fee2e2; color: #991b1b; }}

  .modal-bg {{
    display: none; position: fixed; inset: 0; background: rgba(0,0,0,.4);
    align-items: center; justify-content: center; z-index: 100;
  }}
  .modal-bg.show {{ display: flex; }}
  .modal {{
    background: #fff; border-radius: 12px; padding: 22px; width: 380px;
    box-shadow: 0 20px 50px rgba(0,0,0,.2);
  }}
  .modal h3 {{ margin: 0 0 12px; }}
  .modal label {{ display: block; font-size: 12px; color: var(--ink-soft); margin-bottom: 6px; }}
  .modal input, .modal select {{
    width: 100%; padding: 8px 10px; border: 1px solid var(--border);
    border-radius: 7px; font: inherit; font-size: 14px;
  }}
  .modal .actions {{ display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; }}

  @media print {{
    .player, .toolbar, .modal-bg {{ display: none !important; }}
    body {{ background: white; }}
    .row:hover {{ background: transparent; }}
  }}
</style>
</head><body>
<div class="wrap">

<header>
  <h1>{html.escape(title)}</h1>
  <div class="meta">{meta_html}</div>
</header>

<div class="player">
  <audio controls preload="auto" id="player" src="{html.escape(audio_ref)}"></audio>
</div>

<div class="toolbar">
  <button class="btn primary" id="downloadBtn">⬇ Download transcript…</button>
  <button class="btn" onclick="window.print()">🖨 Print</button>
  <span style="flex:1"></span>
  <span class="meta" style="align-self:center">Click any timestamp to jump in audio</span>
</div>

<div class="legend">{legend_items}</div>

<main>
{''.join(rows)}
</main>

</div>

<div class="modal-bg" id="dlModal">
  <div class="modal">
    <h3>Download transcript</h3>
    <label for="dlName">File name</label>
    <input id="dlName" type="text" value="{html.escape(default_name)}">
    <label style="margin-top:14px" for="dlFmt">Format</label>
    <select id="dlFmt">
      <option value="txt">Plain text (.txt) — opens in Word, Notes, anywhere</option>
      <option value="html">Web page (.html) — keeps speakers and audio</option>
    </select>
    <div class="actions">
      <button class="btn" id="dlCancel">Cancel</button>
      <button class="btn primary" id="dlSave">Download</button>
    </div>
  </div>
</div>

<script>
const p = document.getElementById('player');
const STORAGE_KEY = {json.dumps(storage_key)};

// --- Speaker rename (persists in localStorage) ---
function loadRenames() {{
  try {{ return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{{}}'); }}
  catch (e) {{ return {{}}; }}
}}
function saveRename(orig, newName) {{
  const map = loadRenames();
  if (newName && newName !== orig) map[orig] = newName;
  else delete map[orig];
  localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
}}
function applyRenames() {{
  const map = loadRenames();
  document.querySelectorAll('.chip[data-speaker]').forEach(el => {{
    const orig = el.dataset.speaker;
    if (map[orig] !== undefined && map[orig] !== '') el.textContent = map[orig];
    // else: leave textContent alone — could be the original from render, or a
    // rename baked into the file from a previous download.
  }});
}}
function startEdit(chip) {{
  if (chip.classList.contains('empty')) return;
  const orig = chip.dataset.speaker;
  const current = chip.textContent;
  // Snapshot chip's identity so we can restore an equivalent element after edit
  const saved = {{
    tagName: chip.tagName,
    className: chip.className,
    style: chip.style.cssText,
    title: chip.title || '',
    dataset: Object.assign({{}}, chip.dataset),
  }};
  const input = document.createElement('input');
  input.className = 'chip-input';
  input.value = current;
  input.style.background = chip.style.background;
  input.style.color = chip.style.color;
  chip.replaceWith(input);
  input.focus(); input.select();
  let done = false;
  const finish = (commit) => {{
    if (done) return; done = true;
    if (commit) {{
      const newName = input.value.trim();
      saveRename(orig, newName || orig);
    }}
    // Recreate chip element with same identity
    const newChip = document.createElement(saved.tagName);
    newChip.className = saved.className;
    newChip.style.cssText = saved.style;
    newChip.title = saved.title;
    for (const [k, v] of Object.entries(saved.dataset)) newChip.dataset[k] = v;
    input.replaceWith(newChip);
    applyRenames();
  }};
  input.addEventListener('blur', () => finish(true));
  input.addEventListener('keydown', e => {{
    if (e.key === 'Enter') {{ e.preventDefault(); finish(true); }}
    if (e.key === 'Escape') {{ e.preventDefault(); finish(false); }}
  }});
}}
document.addEventListener('click', e => {{
  const chip = e.target.closest('.chip[data-speaker]');
  if (chip) startEdit(chip);
}});
applyRenames();

// Click timestamp → jump
document.querySelectorAll('.ts').forEach(a => {{
  a.addEventListener('click', e => {{
    e.preventDefault();
    p.currentTime = parseFloat(a.dataset.t);
    p.play();
  }});
}});

// Click any word → jump to that word
document.querySelectorAll('.w').forEach(w => {{
  w.addEventListener('click', () => {{
    p.currentTime = parseFloat(w.dataset.s);
    p.play();
  }});
}});

// --- Karaoke-style sync ---
// Precompute a sorted index of word spans for fast binary search
const wordSpans = Array.from(document.querySelectorAll('.w')).map(el => ({{
  el,
  s: parseFloat(el.dataset.s),
  e: parseFloat(el.dataset.e),
  row: el.closest('.row'),
}}));
const rowEls = Array.from(document.querySelectorAll('[id^="row-"]')).map(el => ({{
  el,
  s: parseFloat(el.dataset.rowStart),
  e: parseFloat(el.dataset.rowEnd),
}}));

function findWordIndex(t) {{
  // binary search the latest word whose start <= t
  let lo = 0, hi = wordSpans.length - 1, ans = -1;
  while (lo <= hi) {{
    const mid = (lo + hi) >> 1;
    if (wordSpans[mid].s <= t) {{ ans = mid; lo = mid + 1; }}
    else hi = mid - 1;
  }}
  return ans;
}}

let currentWordIdx = -1;
let currentRow = null;

// User-scroll detection: pause auto-scroll briefly when user scrolls themselves
let userScrolledAt = 0;
let autoScrolling = false;
window.addEventListener('scroll', () => {{
  if (!autoScrolling) userScrolledAt = Date.now();
}}, {{ passive: true }});

function shouldAutoScroll() {{
  // Don't fight user for 4 seconds after they scroll
  return Date.now() - userScrolledAt > 4000;
}}

function rowInView(row) {{
  const r = row.getBoundingClientRect();
  const margin = window.innerHeight * 0.15;
  return r.top >= margin && r.bottom <= window.innerHeight - margin;
}}

function tick() {{
  if (p.paused) return;
  const t = p.currentTime;
  const idx = findWordIndex(t);
  if (idx !== currentWordIdx) {{
    // Update word classes
    if (currentWordIdx >= 0) {{
      const prev = wordSpans[currentWordIdx];
      if (prev) prev.el.classList.remove('now');
    }}
    if (idx >= 0) {{
      const cur = wordSpans[idx];
      cur.el.classList.add('now');
      // Mark all earlier words as past (cheaply: only flip the ones around the boundary)
      for (let i = Math.max(0, Math.min(currentWordIdx, idx) - 1);
           i <= Math.max(currentWordIdx, idx); i++) {{
        if (i < 0 || i >= wordSpans.length) continue;
        if (i < idx) wordSpans[i].el.classList.add('past');
        else wordSpans[i].el.classList.remove('past');
      }}
      // Activate row
      if (cur.row !== currentRow) {{
        if (currentRow) currentRow.classList.remove('active');
        cur.row.classList.add('active');
        currentRow = cur.row;
        if (shouldAutoScroll() && !rowInView(cur.row)) {{
          autoScrolling = true;
          cur.row.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
          setTimeout(() => {{ autoScrolling = false; }}, 800);
        }}
      }}
    }}
    currentWordIdx = idx;
  }}
}}

// Drive at ~20fps via timeupdate (which fires ~4Hz) + rAF for smoothness
p.addEventListener('timeupdate', tick);
function rafLoop() {{ tick(); requestAnimationFrame(rafLoop); }}
p.addEventListener('play', rafLoop);

// On seek, recompute past/now classes from scratch (cheap on a fresh load)
p.addEventListener('seeked', () => {{
  const t = p.currentTime;
  const idx = findWordIndex(t);
  wordSpans.forEach((w, i) => {{
    w.el.classList.toggle('past', i < idx);
    w.el.classList.toggle('now', i === idx);
  }});
  currentWordIdx = idx;
  if (idx >= 0 && currentRow !== wordSpans[idx].row) {{
    if (currentRow) currentRow.classList.remove('active');
    currentRow = wordSpans[idx].row;
    currentRow.classList.add('active');
  }}
}});

const TRANSCRIPT_TXT = {json.dumps(plain_text)};

const modal = document.getElementById('dlModal');
const nameInput = document.getElementById('dlName');
const fmtSel = document.getElementById('dlFmt');
document.getElementById('downloadBtn').onclick = () => {{ modal.classList.add('show'); nameInput.focus(); nameInput.select(); }};
document.getElementById('dlCancel').onclick = () => modal.classList.remove('show');
modal.addEventListener('click', e => {{ if (e.target === modal) modal.classList.remove('show'); }});
document.addEventListener('keydown', e => {{ if (e.key === 'Escape') modal.classList.remove('show'); }});

// Rebuild plain text from the current DOM, so any speaker renames are picked up.
function liveTextDownload() {{
  const titleEl = document.querySelector('h1');
  const title = titleEl ? titleEl.textContent : 'Transcript';
  const lines = [title, '='.repeat(title.length), ''];
  let lastSpk = null;
  document.querySelectorAll('[id^="row-"]').forEach(row => {{
    // Find the speaker chip: either an empty chip (use previous) or one with data-speaker
    const chip = row.querySelector('.chip[data-speaker]');
    const spk = chip ? chip.textContent : lastSpk;
    const ts = row.querySelector('.ts')?.textContent || '';
    // Text without flag tags
    const textNode = row.querySelector('.text');
    const text = textNode ? Array.from(textNode.childNodes)
      .filter(n => !(n.classList && n.classList.contains('flag')))
      .map(n => n.textContent).join('').trim() : '';
    if (spk && spk !== lastSpk) {{
      lines.push('');
      lines.push(spk + ':');
      lastSpk = spk;
    }}
    const flags = Array.from(textNode?.querySelectorAll('.flag') || [])
      .map(f => ' (' + f.textContent + ')').join('');
    lines.push('  [' + ts + ']  ' + text + flags);
  }});
  return lines.join('\\n');
}}

document.getElementById('dlSave').onclick = async () => {{
  const name = (nameInput.value || 'transcript').replace(/[\\\\/:*?"<>|]/g, '_');
  const fmt = fmtSel.value;
  let blob, fname;
  if (fmt === 'txt') {{
    blob = new Blob([liveTextDownload()], {{type: 'text/plain;charset=utf-8'}});
    fname = name + '.txt';
  }} else {{
    blob = new Blob([document.documentElement.outerHTML], {{type: 'text/html;charset=utf-8'}});
    fname = name + '.html';
  }}
  // Modern browsers: let user pick location + name properly
  if (window.showSaveFilePicker) {{
    try {{
      const handle = await window.showSaveFilePicker({{
        suggestedName: fname,
        types: [{{description: fmt.toUpperCase(), accept: {{[blob.type]: ['.' + fmt]}}}}],
      }});
      const w = await handle.createWritable();
      await w.write(blob); await w.close();
      modal.classList.remove('show');
      return;
    }} catch (err) {{
      if (err.name === 'AbortError') return;
      // fall through to anchor fallback
    }}
  }}
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = fname;
  document.body.appendChild(a); a.click();
  setTimeout(() => {{ URL.revokeObjectURL(a.href); a.remove(); }}, 1000);
  modal.classList.remove('show');
}};
</script>
</body></html>
"""
    # Explicit utf-8 — the HTML contains Unicode glyphs (⬇ ✓ ⏳ etc.)
    # and segment text can contain any language. Windows default cp1252
    # cannot encode these.
    out_path.write_text(doc, encoding="utf-8")


def render_txt(segments: list, out_path: str, title: str, file_duration: float):
    lines = [title, "=" * len(title), ""]
    last_spk = None
    for s in segments:
        if s["speaker"] != last_spk:
            lines.append("")
            lines.append(f"{s['speaker']}:")
            last_spk = s["speaker"]
        marks = ""
        if s.get("avg_logprob", 0) < -0.6: marks += " (low confidence)"
        if s.get("no_speech", 0) > 0.5: marks += " (possibly noise)"
        lines.append(f"  [{fmt_time(s['start'])}]  {s['text']}{marks}")
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
