"""PySide6 GUI for Abbu Transcriber."""
import sys, os, platform, traceback, warnings, webbrowser
from datetime import datetime
from pathlib import Path

# PySide6 emits a noisy warning when we proactively try to .disconnect() a
# signal that hasn't been connected yet (used to safely reconnect handlers).
warnings.filterwarnings("ignore", message=".*Failed to disconnect.*")

from PySide6.QtCore import Qt, QThread, Signal, QSize, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QProgressBar, QStackedWidget, QFrame,
    QRadioButton, QButtonGroup, QMessageBox, QSizePolicy, QTextEdit,
    QSystemTrayIcon, QStyle, QDialog
)

APP_VERSION = "0.1.0"


def _classify_error(msg: str) -> str:
    """Best-effort: return a short friendly hint based on the error text."""
    low = msg.lower()
    if "out of memory" in low or "cuda out of memory" in low or "memoryerror" in low:
        return ("Your computer ran out of memory mid-process. Try the 'Quick' or 'Standard' "
                "quality preset, or close other programs and try again.")
    if "no space left" in low or "disk full" in low:
        return "Your hard drive is full. Free up some space and try again."
    if any(k in low for k in ("connection", "timeout", "network", "max retries", "name resolution")):
        return ("Couldn't reach the model download servers. Check your internet connection "
                "and try again. Once models are downloaded, you won't need internet again.")
    if "permission denied" in low:
        return ("The app couldn't write the transcript. Try saving to a different folder, "
                "or check the file isn't already open in another program.")
    if "unsupported" in low and "format" in low:
        return "That file format isn't supported. Try .m4a, .mp3, .wav, or .mp4."
    return ("This is an unexpected error. Save the report and send it to Ahmad — "
            "it has the technical details he needs to debug.")


def _build_report(error_text: str) -> str:
    """Compose an error report for emailing/saving."""
    try:
        import psutil
        ram_gb = psutil.virtual_memory().total / (1024 ** 3)
        ram_str = f"{ram_gb:.1f} GB"
    except Exception:
        ram_str = "unknown"
    cpu = platform.processor() or platform.machine() or "unknown"
    return (
        f"Abbu Transcriber error report\n"
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"App version: {APP_VERSION}\n\n"
        f"System\n======\n"
        f"OS: {platform.system()} {platform.release()} ({platform.version()})\n"
        f"CPU: {cpu}\n"
        f"RAM: {ram_str}\n"
        f"Python: {platform.python_version()}\n\n"
        f"Error\n=====\n"
        f"{error_text}\n"
    )

from .pipeline import run_pipeline, PRESETS

AUDIO_EXTS = {".m4a", ".mp4", ".mp3", ".wav", ".aac", ".ogg", ".webm", ".flac", ".mov", ".jpeg"}


# ---------- Background worker ----------

class PipelineWorker(QThread):
    progress = Signal(str, float, str)   # stage, pct, message
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, audio_path: str, output_dir: str, preset: str):
        super().__init__()
        self.audio_path = audio_path
        self.output_dir = output_dir
        self.preset = preset
        self._cancel = False

    def cancel(self): self._cancel = True

    def run(self):
        try:
            result = run_pipeline(
                self.audio_path, self.output_dir, self.preset,
                on_progress=lambda stage, pct, msg: self.progress.emit(stage, pct, msg),
                cancel_check=lambda: self._cancel,
            )
            self.finished_ok.emit(result)
        except InterruptedError:
            self.failed.emit("Cancelled")
        except Exception as e:
            self.failed.emit(f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}")


# ---------- Custom drop zone ----------

class DropZone(QFrame):
    fileDropped = Signal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumHeight(340)
        self.setStyleSheet("""
            QFrame {
                border: 3px dashed #c4c4c4;
                border-radius: 16px;
                background: #fafafa;
            }
            QFrame:hover { border-color: #2563eb; background: #eff6ff; }
        """)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(10)
        icon = QLabel("⬇")
        icon.setStyleSheet("font-size: 88px; color: #9ca3af; border: none; background: transparent;")
        icon.setAlignment(Qt.AlignCenter)
        title = QLabel("Drop a recording here")
        title.setStyleSheet("font-size: 22px; font-weight: 600; color: #111827; border: none; background: transparent;")
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("or click to choose a file")
        sub.setStyleSheet("font-size: 15px; color: #6b7280; border: none; background: transparent;")
        sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(icon)
        lay.addWidget(title)
        lay.addWidget(sub)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            path, _ = QFileDialog.getOpenFileName(self, "Choose a recording", "",
                "Audio/Video (*.m4a *.mp3 *.mp4 *.wav *.aac *.ogg *.webm *.flac *.mov *.jpeg);;All files (*)")
            if path:
                self.fileDropped.emit(path)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if not urls: return
        path = urls[0].toLocalFile()
        if Path(path).suffix.lower() in AUDIO_EXTS:
            self.fileDropped.emit(path)
        else:
            QMessageBox.warning(self, "Unsupported file",
                f"File type not supported: {Path(path).suffix}\nSupported: {', '.join(sorted(AUDIO_EXTS))}")


# ---------- Pages ----------

class IdlePage(QWidget):
    """Idle screen. Just the drop zone + a small settings link that opens
    quality choices in a dialog. Defaults to Standard."""
    startRequested = Signal(str, str)  # path, preset

    def __init__(self, settings_holder):
        super().__init__()
        self.settings_holder = settings_holder
        lay = QVBoxLayout(self)
        lay.setSpacing(16)
        lay.setContentsMargins(24, 24, 24, 16)

        self.drop = DropZone()
        self.drop.fileDropped.connect(self._on_file)
        lay.addWidget(self.drop, stretch=1)

        # Bottom: tiny settings link (only thing visible — quality is hidden)
        bottom = QHBoxLayout()
        bottom.addStretch()
        self.settings_link = QPushButton("⚙ Settings")
        self.settings_link.setFlat(True)
        self.settings_link.setCursor(Qt.PointingHandCursor)
        self.settings_link.setStyleSheet(
            "QPushButton { color: #6b7280; padding: 4px 8px; }"
            "QPushButton:hover { color: #111827; }"
        )
        self.settings_link.clicked.connect(self._open_settings)
        bottom.addWidget(self.settings_link)
        lay.addLayout(bottom)

    def _on_file(self, path):
        self.startRequested.emit(path, self.settings_holder.preset)

    def _open_settings(self):
        SettingsDialog(self.settings_holder, self).exec()


class SettingsHolder:
    """Simple value holder for app preferences (in-memory for v1)."""
    def __init__(self):
        self.preset = "standard"


class SettingsDialog(QWidget):
    """Modal-style settings dialog. Quality lives here so the main screen
    stays one big drop zone."""
    def __init__(self, holder: SettingsHolder, parent=None):
        from PySide6.QtWidgets import QDialog
        self._dialog = QDialog(parent)
        self._dialog.setWindowTitle("Settings")
        self._dialog.setMinimumWidth(400)
        self.holder = holder

        lay = QVBoxLayout(self._dialog)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(14)

        q_label = QLabel("Transcription quality")
        q_label.setStyleSheet("font-weight: 600; color: #111827; font-size: 14px;")
        lay.addWidget(q_label)

        self.radios = {}
        for key, label, desc in [
            ("quick",    "Quick",          "Smaller model. Use for clean recordings when speed matters."),
            ("standard", "Standard",       "Recommended. Good for most meetings."),
            ("best",     "Best for noisy", "Cleans up rough audio first. Slower but recovers more."),
        ]:
            rb = QRadioButton(label)
            rb.setStyleSheet("font-size: 13px; font-weight: 500;")
            sub = QLabel(desc)
            sub.setStyleSheet("color: #6b7280; font-size: 12px; margin-left: 22px;")
            sub.setWordWrap(True)
            lay.addWidget(rb)
            lay.addWidget(sub)
            self.radios[key] = rb
        self.radios[holder.preset].setChecked(True)

        lay.addStretch()
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self._dialog.reject)
        save = QPushButton("Save"); save.clicked.connect(self._save)
        save.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; border: none; "
            "border-radius: 6px; padding: 6px 14px; font-weight: 600; }"
            "QPushButton:hover { background: #1d4ed8; }"
        )
        btn_row.addWidget(cancel); btn_row.addWidget(save)
        lay.addLayout(btn_row)

    def _save(self):
        for k, rb in self.radios.items():
            if rb.isChecked():
                self.holder.preset = k
                break
        self._dialog.accept()

    def exec(self):
        return self._dialog.exec()


class ProcessingPage(QWidget):
    cancelRequested = Signal()

    STAGES = [
        ("setup",      "First-time setup"),
        ("cleanup",    "Cleaning audio"),
        ("transcribe", "Transcribing speech"),
        ("diarize",    "Identifying speakers"),
        ("render",     "Generating transcript"),
    ]

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        lay.setContentsMargins(20, 20, 20, 20)

        self.file_label = QLabel("—")
        self.file_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.detail_label = QLabel("")
        self.detail_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        lay.addWidget(self.file_label)
        lay.addWidget(self.detail_label)

        # Stage rows. Wrap each in a QWidget so we can show/hide whole rows.
        self.stage_widgets = {}
        for key, name in self.STAGES:
            row_w = QWidget()
            row = QHBoxLayout(row_w)
            row.setContentsMargins(0, 0, 0, 0)
            mark = QLabel("○")
            mark.setFixedWidth(20)
            mark.setStyleSheet("color: #9ca3af;")
            text = QLabel(name)
            text.setStyleSheet("color: #9ca3af;")
            row.addWidget(mark); row.addWidget(text); row.addStretch()
            lay.addWidget(row_w)
            self.stage_widgets[key] = (mark, text, row_w)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(True)
        lay.addWidget(self.bar)

        self.msg = QLabel("")
        self.msg.setStyleSheet("color: #6b7280; font-size: 12px;")
        lay.addWidget(self.msg)

        lay.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.cancelRequested.emit)
        btn_row.addWidget(self.cancel_btn)
        lay.addLayout(btn_row)

    def start(self, path: str, preset: str):
        from .pipeline import PRESETS, will_need_setup
        self.file_label.setText(Path(path).name)
        self.detail_label.setText(f"Quality: {preset.title()}")
        cfg = PRESETS[preset]
        needs_setup = will_need_setup(preset)
        needs_cleanup = cfg["demucs"]
        for key, name in self.STAGES:
            mark, text, row_w = self.stage_widgets[key]
            mark.setText("○"); mark.setStyleSheet("color: #9ca3af;")
            text.setStyleSheet("color: #9ca3af;")
            # Hide rows that won't run this time
            show = True
            if key == "setup" and not needs_setup: show = False
            if key == "cleanup" and not needs_cleanup: show = False
            row_w.setVisible(show)
        self.bar.setValue(0)
        self.msg.setText("Starting...")

    def update_stage(self, stage: str, pct: float, message: str):
        # Walk stages in order. Earlier visible stages get ✓, current gets ⏳, later are ○.
        active_idx = None
        for i, (k, _) in enumerate(self.STAGES):
            mark, text, row_w = self.stage_widgets[k]
            if not row_w.isVisible():
                continue  # ignore hidden rows for marking purposes
            if k == stage:
                active_idx = i
                mark.setText("⏳"); mark.setStyleSheet("color: #2563eb;")
                text.setStyleSheet("color: #111827; font-weight: 600;")
            elif active_idx is None:
                mark.setText("✓"); mark.setStyleSheet("color: #16a34a;")
                text.setStyleSheet("color: #6b7280;")
            else:
                mark.setText("○"); mark.setStyleSheet("color: #9ca3af;")
                text.setStyleSheet("color: #9ca3af;")
        if pct < 0:
            self.bar.setRange(0, 0)  # indeterminate
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(int(pct))
        self.msg.setText(message)


class DonePage(QWidget):
    againRequested = Signal()

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setSpacing(14)
        lay.setContentsMargins(20, 20, 20, 20)

        self.title = QLabel("✓ Done")
        self.title.setStyleSheet("font-size: 22px; font-weight: 700; color: #16a34a;")
        lay.addWidget(self.title)

        self.detail = QLabel("")
        self.detail.setStyleSheet("color: #6b7280;")
        self.detail.setWordWrap(True)
        lay.addWidget(self.detail)

        self.outputs = QLabel("")
        self.outputs.setStyleSheet("font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; color: #374151;")
        self.outputs.setWordWrap(True)
        lay.addWidget(self.outputs)

        lay.addStretch()

        self.open_btn = QPushButton("Open transcript")
        self.open_btn.setMinimumHeight(48)
        self.open_btn.setStyleSheet("""
            QPushButton { background: #2563eb; color: white; border: none; border-radius: 8px; font-size: 15px; font-weight: 600; }
            QPushButton:hover { background: #1d4ed8; }
        """)
        lay.addWidget(self.open_btn)

        again_btn = QPushButton("↩ Transcribe another")
        again_btn.setFlat(True)
        again_btn.setStyleSheet("color: #2563eb; padding: 6px;")
        again_btn.clicked.connect(self.againRequested.emit)
        lay.addWidget(again_btn)

    def show_result(self, result: dict):
        mins = result["elapsed"] / 60
        dur_mins = result["duration"] / 60
        spk_word = "speaker" if result["n_speakers"] == 1 else "speakers"
        self.detail.setText(
            f"Transcribed {dur_mins:.1f} min of audio in {mins:.1f} min. "
            f"{result['n_segments']} segments, {result['n_speakers']} {spk_word}."
        )
        self.outputs.setText(f"{result['html']}\n{result['txt']}")
        # Hold the result so the lambda doesn't get stale across runs
        self._result = result
        # Reconnect cleanly (PySide6 warns on first disconnect if never connected,
        # so we just disconnect by reference rather than blindly)
        try: self.open_btn.clicked.disconnect()
        except (RuntimeError, TypeError): pass
        self.open_btn.clicked.connect(self._open_html)

    def _open_html(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._result["html"]))


class ErrorPage(QWidget):
    againRequested = Signal()

    def __init__(self):
        super().__init__()
        self._last_error = ""
        self._details_open = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        self.title = QLabel("Something went wrong")
        self.title.setStyleSheet("font-size: 20px; font-weight: 700; color: #dc2626;")
        lay.addWidget(self.title)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #374151; font-size: 14px;")
        lay.addWidget(self.hint)

        # Collapsible details
        self.details_toggle = QPushButton("▸ Show technical details")
        self.details_toggle.setFlat(True)
        self.details_toggle.setStyleSheet(
            "QPushButton { color: #6b7280; text-align: left; padding: 4px; }"
            "QPushButton:hover { color: #111827; }"
        )
        self.details_toggle.clicked.connect(self._toggle_details)
        lay.addWidget(self.details_toggle)

        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setStyleSheet(
            "QTextEdit { background: #f9fafb; border: 1px solid #e5e7eb; "
            "border-radius: 6px; font-family: ui-monospace, Menlo, Consolas, monospace; "
            "font-size: 11px; color: #374151; }"
        )
        self.details.setMaximumHeight(180)
        self.details.hide()
        lay.addWidget(self.details)

        lay.addStretch()

        btn_row = QHBoxLayout()
        self.report_btn = QPushButton("💾 Save error report")
        self.report_btn.clicked.connect(self._save_report)
        again_btn = QPushButton("Try another file")
        again_btn.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; border: none; "
            "border-radius: 6px; padding: 8px 16px; font-weight: 600; }"
            "QPushButton:hover { background: #1d4ed8; }"
        )
        again_btn.clicked.connect(self.againRequested.emit)
        btn_row.addWidget(self.report_btn)
        btn_row.addStretch()
        btn_row.addWidget(again_btn)
        lay.addLayout(btn_row)

    def _toggle_details(self):
        self._details_open = not self._details_open
        self.details.setVisible(self._details_open)
        self.details_toggle.setText("▾ Hide technical details" if self._details_open
                                    else "▸ Show technical details")

    def _save_report(self):
        default = str(Path.home() / "Desktop" /
                      f"transcriber_error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        path, _ = QFileDialog.getSaveFileName(self, "Save error report",
                                              default, "Text (*.txt)")
        if not path:
            return
        Path(path).write_text(_build_report(self._last_error))
        QMessageBox.information(self, "Saved",
            f"Error report saved to:\n{path}\n\nYou can email this file to Ahmad.")

    def show_error(self, text: str):
        self._last_error = text
        self.hint.setText(_classify_error(text))
        self.details.setText(text)
        # Reset to collapsed state for each new error
        self._details_open = False
        self.details.hide()
        self.details_toggle.setText("▸ Show technical details")


# ---------- Main window ----------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Meeting Transcriber")
        self.resize(600, 560)
        self.worker = None
        self.settings = SettingsHolder()

        # App icon (bundled with PyInstaller via the spec file).
        icon_path = Path(__file__).parent / "resources" / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        else:
            self.setWindowIcon(self.style().standardIcon(QStyle.SP_DriveDVDIcon))

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        self.idle = IdlePage(self.settings)
        self.proc = ProcessingPage()
        self.done = DonePage()
        self.err  = ErrorPage()
        for w in (self.idle, self.proc, self.done, self.err):
            self.stack.addWidget(w)

        self.idle.startRequested.connect(self._start)
        self.proc.cancelRequested.connect(self._cancel)
        self.done.againRequested.connect(lambda: self.stack.setCurrentWidget(self.idle))
        self.err.againRequested.connect(lambda: self.stack.setCurrentWidget(self.idle))

        # System tray icon — required for Windows toast notifications.
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("Meeting Transcriber")
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

        self.stack.setCurrentWidget(self.idle)

    def _start(self, path: str, preset: str):
        out_dir = str(Path(path).parent)
        self.proc.start(path, preset)
        self.stack.setCurrentWidget(self.proc)
        self.worker = PipelineWorker(path, out_dir, preset)
        self.worker.progress.connect(self.proc.update_stage)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(self._on_fail)
        self.worker.start()

    def _cancel(self):
        if self.worker:
            self.worker.cancel()

    def _on_done(self, result):
        self.done.show_result(result)
        self.stack.setCurrentWidget(self.done)
        # Notify if the window is in the background (he tabbed away).
        if not self.isActiveWindow() and QSystemTrayIcon.supportsMessages():
            mins = result["elapsed"] / 60
            self.tray.showMessage(
                "Transcript ready",
                f"Finished in {mins:.0f} min. Click to open the app.",
                QSystemTrayIcon.Information,
                5000,
            )

    def _on_fail(self, msg):
        if msg == "Cancelled":
            self.stack.setCurrentWidget(self.idle)
            return
        self.err.show_error(msg)
        self.stack.setCurrentWidget(self.err)
        # Also ping him if errored in the background.
        if not self.isActiveWindow() and QSystemTrayIcon.supportsMessages():
            self.tray.showMessage(
                "Transcript failed",
                "Click the app to see what happened.",
                QSystemTrayIcon.Warning,
                5000,
            )


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Meeting Transcriber")
    app.setOrganizationName("Abbu Transcriber")
    # Set the app icon at the QApplication level too, so the taskbar shows it
    # on Windows even before MainWindow appears.
    icon_path = Path(__file__).parent / "resources" / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
