#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mdv — reader GUI per file Markdown.

Funzionalità:
  * apertura di più file in tab dedicate
  * riquadri sorgente e anteprima mostrabili/nascosti in modo indipendente
  * dark mode (interfaccia + anteprima renderizzata)
  * auto-refresh quando il file cambia su disco
  * evidenziazione sintattica della sorgente (attivabile/disattivabile)
  * Impostazioni: vista all'apertura, font, dimensioni e colori dei riquadri
  * salvataggio (Ctrl+S), trascinamento file nella finestra, scorciatoie

Uso:
    python mdv.py [file.md ...] [--dark|--light] [--no-webengine]
"""

from __future__ import annotations

import html as html_lib
import os
import re
import sys
from dataclasses import dataclass

import markdown
from PyQt6.QtCore import (
    QFileSystemWatcher,
    QSettings,
    Qt,
    QRegularExpression,
    QTimer,
    QUrl,
    pyqtSignal as Signal,
)
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QFontDatabase,
    QKeySequence,
    QPalette,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFontComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

# --------------------------------------------------------------------------- #
#  Rendering HTML
# --------------------------------------------------------------------------- #

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    HAVE_WEBENGINE = True
except Exception:  # pragma: no cover - QtWebEngine non disponibile
    QWebEngineView = None
    HAVE_WEBENGINE = False

# Fallback manuale (utile su sistemi senza OpenGL / ambienti headless)
if os.environ.get("MDV_NO_WEBENGINE") == "1":
    HAVE_WEBENGINE = False

MD_EXTENSIONS = ["extra", "toc", "sane_lists", "fenced_code", "codehilite", "pymdownx.tilde"]
MD_EXTENSION_CONFIGS = {
    "codehilite": {"guess_lang": False, "css_class": "highlight"},
}

# --------------------------------------------------------------------------- #
#  Preferenze (persistite su disco con QSettings)
# --------------------------------------------------------------------------- #

DEFAULT_PREVIEW_SIZE = 15  # px, come nel CSS predefinito dell'anteprima


@dataclass
class ViewPrefs:
    """Preferenze di visualizzazione dell'utente."""

    mode: str = "split"        # split | preview | source (vista all'apertura)
    src_family: str = ""       # "" = font monospazio di sistema
    src_size: int = 0          # 0 = dimensione predefinita (px)
    src_color: str = ""        # "" = colore del tema
    src_syntax: bool = True    # colorazione della sintassi della sorgente
    prev_family: str = ""      # "" = stack di font CSS predefinito
    prev_size: int = DEFAULT_PREVIEW_SIZE
    prev_color: str = ""       # "" = colore del tema


def mode_to_panes(mode: str) -> tuple[bool, bool]:
    """Converte la vista iniziale in (mostra_sorgente, mostra_anteprima)."""
    if mode == "preview":
        return False, True
    if mode == "source":
        return True, False
    return True, True


def panes_to_mode(show_source: bool, show_preview: bool) -> str:
    """Ricava la vista iniziale dalla visibilità dei riquadri."""
    if show_source and show_preview:
        return "split"
    if show_preview:
        return "preview"
    if show_source:
        return "source"
    return "split"  # non dovrebbe accadere: almeno un riquadro resta visibile


def default_src_font() -> QFont:
    """Font monospazio di sistema (valore predefinito della sorgente)."""
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)


def font_px(font: QFont) -> int:
    """Dimensione del font in pixel (converte dai punti se necessario)."""
    px = font.pixelSize()
    if px and px > 0:
        return px
    pt = font.pointSizeF()
    if pt and pt > 0:
        screen = QApplication.primaryScreen()
        dpi = screen.logicalDotsPerInchY() if screen else 96.0
        return max(1, round(pt * dpi / 72))
    return 11


def default_prev_family() -> str:
    """Famiglia rappresentativa del font di sistema (predefinito anteprima)."""
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()


def load_prefs() -> ViewPrefs:
    """Legge le preferenze da disco, con i valori predefiniti mancanti."""
    s = QSettings("mdv", "mdv")
    p = ViewPrefs()

    mode = s.value("view/mode", p.mode, type=str)
    if mode in ("split", "preview", "source"):
        p.mode = mode

    p.src_family = s.value("source/font_family", "", type=str) or ""
    p.src_color = s.value("source/font_color", "", type=str) or ""
    p.src_syntax = s.value("source/syntax", True, type=bool)
    p.prev_family = s.value("preview/font_family", "", type=str) or ""
    p.prev_color = s.value("preview/font_color", "", type=str) or ""

    try:
        p.src_size = max(0, int(s.value("source/font_size", 0) or 0))
    except (TypeError, ValueError):
        p.src_size = 0
    try:
        p.prev_size = max(
            0,
            int(
                s.value("preview/font_size", DEFAULT_PREVIEW_SIZE)
                or DEFAULT_PREVIEW_SIZE
            ),
        )
    except (TypeError, ValueError):
        p.prev_size = DEFAULT_PREVIEW_SIZE

    return p


def save_prefs(prefs: ViewPrefs) -> None:
    """Salva le preferenze su disco."""
    s = QSettings("mdv", "mdv")
    s.setValue("view/mode", prefs.mode)
    s.setValue("source/font_family", prefs.src_family)
    s.setValue("source/font_size", prefs.src_size)
    s.setValue("source/font_color", prefs.src_color)
    s.setValue("source/syntax", prefs.src_syntax)
    s.setValue("preview/font_family", prefs.prev_family)
    s.setValue("preview/font_size", prefs.prev_size)
    s.setValue("preview/font_color", prefs.prev_color)
    s.sync()

_MONO = (
    'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, '
    '"Liberation Mono", monospace'
)

LIGHT_PREVIEW_CSS = f"""
:root {{ color-scheme: light; }}
html, body {{ background: #ffffff; }}
body {{
    font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    color: #1f2328; line-height: 1.65; margin: 0; padding: 28px 40px 64px; font-size: 15px;
}}
.content {{ max-width: 920px; margin: 0 auto; }}
h1, h2, h3, h4, h5, h6 {{ line-height: 1.3; margin: 1.4em 0 .6em; font-weight: 650; }}
h1 {{ font-size: 1.9em; border-bottom: 1px solid #d0d7de; padding-bottom: .35em; }}
h2 {{ font-size: 1.5em; border-bottom: 1px solid #d0d7de; padding-bottom: .3em; }}
h3 {{ font-size: 1.25em; }}
p {{ margin: .75em 0; }}
a {{ color: #0969da; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
code {{
    font-family: {_MONO}; font-size: .9em; background: #f6f8fa;
    padding: .18em .4em; border-radius: 6px; color: #cf222e;
}}
pre {{
    font-family: {_MONO}; background: #f6f8fa; border: 1px solid #d0d7de;
    border-radius: 8px; padding: 14px 16px; overflow-x: auto;
}}
pre code {{ background: none; padding: 0; color: #1f2328; font-size: .88em; line-height: 1.5; }}
blockquote {{ margin: 1em 0; padding: .1em 1.1em; border-left: .28em solid #d0d7de; color: #656d76; }}
blockquote p {{ margin: .4em 0; }}
ul, ol {{ padding-left: 1.6em; }}
li {{ margin: .3em 0; }}
hr {{ border: none; border-top: 1px solid #d0d7de; margin: 2em 0; }}
table {{ border-collapse: collapse; margin: 1.2em 0; }}
th, td {{ border: 1px solid #d0d7de; padding: 7px 13px; text-align: left; }}
th {{ background: #f6f8fa; font-weight: 600; }}
tr:nth-child(2n) td {{ background: #f6f8fa; }}
img {{ max-width: 100%; height: auto; border-radius: 6px; }}
.kbd {{ font-family: {_MONO}; font-size: .85em; border: 1px solid #d0d7de;
        border-bottom-width: 2px; border-radius: 5px; padding: 1px 6px; background: #f6f8fa; }}
"""

DARK_PREVIEW_CSS = f"""
:root {{ color-scheme: dark; }}
html, body {{ background: #0d1117; }}
body {{
    font-family: -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    color: #e6edf3; line-height: 1.65; margin: 0; padding: 28px 40px 64px; font-size: 15px;
}}
.content {{ max-width: 920px; margin: 0 auto; }}
h1, h2, h3, h4, h5, h6 {{ line-height: 1.3; margin: 1.4em 0 .6em; font-weight: 650; color: #e6edf3; }}
h1 {{ font-size: 1.9em; border-bottom: 1px solid #30363d; padding-bottom: .35em; }}
h2 {{ font-size: 1.5em; border-bottom: 1px solid #30363d; padding-bottom: .3em; }}
h3 {{ font-size: 1.25em; }}
p {{ margin: .75em 0; }}
a {{ color: #4493f8; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
code {{
    font-family: {_MONO}; font-size: .9em; background: #161b22;
    padding: .18em .4em; border-radius: 6px; color: #ff7b72;
}}
pre {{
    font-family: {_MONO}; background: #161b22; border: 1px solid #30363d;
    border-radius: 8px; padding: 14px 16px; overflow-x: auto;
}}
pre code {{ background: none; padding: 0; color: #e6edf3; font-size: .88em; line-height: 1.5; }}
blockquote {{ margin: 1em 0; padding: .1em 1.1em; border-left: .28em solid #30363d; color: #8b949e; }}
blockquote p {{ margin: .4em 0; }}
ul, ol {{ padding-left: 1.6em; }}
li {{ margin: .3em 0; }}
hr {{ border: none; border-top: 1px solid #30363d; margin: 2em 0; }}
table {{ border-collapse: collapse; margin: 1.2em 0; }}
th, td {{ border: 1px solid #30363d; padding: 7px 13px; text-align: left; }}
th {{ background: #161b22; font-weight: 600; }}
tr:nth-child(2n) td {{ background: #161b22; }}
img {{ max-width: 100%; height: auto; border-radius: 6px; }}
.kbd {{ font-family: {_MONO}; font-size: .85em; border: 1px solid #30363d;
        border-bottom-width: 2px; border-radius: 5px; padding: 1px 6px; background: #161b22; }}
"""


def _highlight_overrides(dark: bool) -> str:
    """Stili per i blocchi generati da Pygments (codehilite)."""
    if dark:
        pre = (
            ".highlight { background: transparent; }\n"
            ".highlight pre { background: #161b22; border: 1px solid #30363d; "
            "border-radius: 8px; padding: 14px 16px; overflow-x: auto; "
            "font-size: .88em; line-height: 1.5; color: #e6edf3; }\n"
        )
    else:
        pre = (
            ".highlight { background: transparent; }\n"
            ".highlight pre { background: #f6f8fa; border: 1px solid #d0d7de; "
            "border-radius: 8px; padding: 14px 16px; overflow-x: auto; "
            "font-size: .88em; line-height: 1.5; color: #1f2328; }\n"
        )
    return pre


def _css_quote(value: str) -> str:
    """Valore CSS string-safe (nomi di font con apici)."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _prefs_css(prefs: ViewPrefs | None) -> str:
    """Override CSS per font e colori personalizzati dell'anteprima."""
    if prefs is None:
        return ""
    rules: list[str] = []
    if prefs.prev_family:
        rules.append(f"body {{ font-family: '{_css_quote(prefs.prev_family)}'; }}")
    if prefs.prev_size:
        rules.append(f"body {{ font-size: {int(prefs.prev_size)}px; }}")
    if prefs.prev_color:
        # Il colore personalizzato vale per il testo; link e codice inline
        # mantengono i loro colori di accento per restare distinguibili.
        color = prefs.prev_color
        rules.append(
            "body, h1, h2, h3, h4, h5, h6, blockquote, pre code, td, th "
            f"{{ color: {color}; }}"
        )
    return "\n".join(rules)


def preview_css(dark: bool, prefs: ViewPrefs | None = None) -> str:
    """CSS dell'anteprima: tema + stili Pygments + preferenze utente."""
    theme = DARK_PREVIEW_CSS if dark else LIGHT_PREVIEW_CSS
    try:
        from pygments.formatters import HtmlFormatter

        style = "monokai" if dark else "default"
        pyg = HtmlFormatter(style=style).get_style_defs(".highlight")
    except Exception:
        pyg = ""
    # Gli override devono venire dopo le regole di Pygments.
    return (
        theme + "\n" + pyg + "\n" + _highlight_overrides(dark)
        + "\n" + _prefs_css(prefs)
    )


def build_html(
    md_text: str,
    title: str,
    dark: bool,
    prefs: ViewPrefs | None = None,
) -> str:
    """Converte il testo Markdown in un documento HTML completo."""
    body = markdown.markdown(
        md_text,
        extensions=MD_EXTENSIONS,
        extension_configs=MD_EXTENSION_CONFIGS,
    )
    css = preview_css(dark, prefs)
    safe_title = html_lib.escape(title)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="it">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{safe_title}</title>\n"
        f"<style>{css}</style>\n"
        "</head>\n"
        f'<body><div class="content">{body}</div></body>\n'
        "</html>\n"
    )


# --------------------------------------------------------------------------- #
#  Evidenziazione sintattica della sorgente
# --------------------------------------------------------------------------- #

class MarkdownHighlighter(QSyntaxHighlighter):
    """Evidenziazione leggera per il testo Markdown."""

    def __init__(self, document, dark: bool = False, custom_color: str = ""):
        super().__init__(document)
        self._dark = dark
        self._custom = (custom_color or "").strip()
        self._enabled = True
        self._fmt: dict[str, QTextCharFormat] = {}
        self._inline: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self._build()

    # -- costruzione delle regole ------------------------------------------ #
    def _build(self) -> None:
        mono = default_src_font().family()

        if self._dark:
            colors = dict(
                heading="#e6edf3",
                strong="#ffffff",
                em="#d2a8ff",
                code_fg="#ff7b72",
                code_bg="#161b22",
                link="#4493f8",
                quote="#8b949e",
                fence="#8b949e",
                list_="#79c0ff",
                strike="#8b949e",
            )
        else:
            colors = dict(
                heading="#1f2328",
                strong="#1f2328",
                em="#8250df",
                code_fg="#cf222e",
                code_bg="#f6f8fa",
                link="#0969da",
                quote="#656d76",
                fence="#6e7781",
                list_="#0550ae",
                strike="#656d76",
            )

        if self._custom:
            # Con un colore personalizzato i token strutturali lo seguono;
            # codice inline e link mantengono il colore di accento.
            for key in ("heading", "strong", "em", "quote", "fence", "list_", "strike"):
                colors[key] = self._custom

        def make(color=None, *, bold=False, italic=False, bg=None, family=None):
            f = QTextCharFormat()
            if color:
                f.setForeground(QBrush(QColor(color)))
            if bg:
                f.setBackground(QBrush(QColor(bg)))
            if bold:
                f.setFontWeight(int(QFont.Weight.Bold))
            if italic:
                f.setFontItalic(True)
            if family:
                f.setFontFamily(family)
            return f

        self._fmt = {
            "heading": make(colors["heading"], bold=True),
            "strong": make(colors["strong"], bold=True),
            "em": make(colors["em"], italic=True),
            "code": make(colors["code_fg"], bg=colors["code_bg"], family=mono),
            "fence": make(colors["fence"], italic=True, family=mono),
            "link": make(colors["link"]),
            "quote": make(colors["quote"], italic=True),
            "list": make(colors["list_"], bold=True),
            "strike": make(colors["strike"], bg=colors["code_bg"]),
        }

        # Ordine = ordine di applicazione: l'ultimo prevale sui range sovrapposti.
        # L'inline code va per ultimo così vince su grassetti/maiuscoli interni.
        self._inline = [
            (QRegularExpression(r"\*\*[^*\n]+\*\*"), self._fmt["strong"]),
            (QRegularExpression(r"(?<![*\w])\*[^*\n]+\*(?!\*)"), self._fmt["em"]),
            (QRegularExpression(r"~~[^~\n]+~~"), self._fmt["strike"]),
            (
                QRegularExpression(r"\[[^\]\n]+\]\([^)\n]+\)"),
                self._fmt["link"],
            ),
            (
                QRegularExpression(r"https?://[^\s<>()\"']+"),
                self._fmt["link"],
            ),
            (QRegularExpression(r"`[^`\n]+`"), self._fmt["code"]),
        ]

    def set_dark(self, dark: bool) -> None:
        if dark == self._dark:
            return
        self._dark = dark
        self._build()
        self.rehighlight()

    def set_custom_color(self, color: str) -> None:
        """Imposta il colore personalizzato del testo ("" = colore del tema)."""
        color = (color or "").strip()
        if color == self._custom:
            return
        self._custom = color
        self._build()
        self.rehighlight()

    def set_enabled(self, enabled: bool) -> None:
        """Attiva/disattiva la colorazione della sintassi."""
        if enabled == self._enabled:
            return
        self._enabled = enabled
        self.rehighlight()

    def is_enabled(self) -> bool:
        return self._enabled

    # -- evidenziazione ----------------------------------------------------- #
    def highlightBlock(self, text: str) -> None:  # noqa: N802 (API Qt)
        if not self._enabled:
            # Nessun formato: il testo resta con lo stile di default
            # (rehighlight() al riattivazione riformatta tutti i blocchi).
            self.setCurrentBlockState(0)
            return

        in_fence = self.previousBlockState() == 1
        stripped = text.strip()

        # Blocchi tra ``` o ~~~
        if in_fence:
            self.setFormat(0, len(text), self._fmt["fence"])
            if stripped.startswith("```") or stripped.startswith("~~~"):
                self.setCurrentBlockState(0)
            else:
                self.setCurrentBlockState(1)
            return

        if stripped.startswith("```") or stripped.startswith("~~~"):
            self.setFormat(0, len(text), self._fmt["fence"])
            self.setCurrentBlockState(1)
            return

        self.setCurrentBlockState(0)

        # Regole a livello di riga
        if re.match(r"^\s{0,3}#{1,6}\s", text):
            self.setFormat(0, len(text), self._fmt["heading"])
        elif re.match(r"^\s{0,3}>", text):
            self.setFormat(0, len(text), self._fmt["quote"])
        elif re.match(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$", text):
            self.setFormat(0, len(text), self._fmt["fence"])
        else:
            m = re.match(r"^(\s*)([-*+]|\d+\.)\s", text)
            if m:
                start = m.start(2)
                self.setFormat(start, m.end(2) - start, self._fmt["list"])

        # Regole inline
        for pattern, fmt in self._inline:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(
                    match.capturedStart(), match.capturedLength(), fmt
                )


# --------------------------------------------------------------------------- #
#  Widget di anteprima (WebEngine con fallback su QTextBrowser)
# --------------------------------------------------------------------------- #

class PreviewWidget(QWidget):
    """Mostra l'HTML renderizzato: QWebEngineView se possibile, altrimenti QTextBrowser."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._web = None
        self._text = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if HAVE_WEBENGINE:
            try:
                self._web = QWebEngineView(self)
                layout.addWidget(self._web)
            except Exception:
                self._web = None

        if self._web is None:
            self._text = QTextBrowser(self)
            self._text.setOpenExternalLinks(True)
            self._text.setFrameShape(QFrame.Shape.NoFrame)
            layout.addWidget(self._text)

    @property
    def is_web(self) -> bool:
        return self._web is not None

    def set_html(self, html: str, base_url=None) -> None:
        if self._web is not None:
            base = base_url if base_url is not None and base_url.isValid() else QUrl("about:blank")
            self._web.setHtml(html, base)
        else:
            self._text.setHtml(html)

    def set_dark(self, dark: bool) -> None:
        bg = "#0d1117" if dark else "#ffffff"
        if self._web is not None:
            try:
                self._web.page().setBackgroundColor(QColor(bg))
            except Exception:
                pass
        else:
            fg = "#e6edf3" if dark else "#1f2328"
            self._text.setStyleSheet(
                f"QTextBrowser {{ background: {bg}; color: {fg}; border: none; }}"
            )


# --------------------------------------------------------------------------- #
#  Tab = un file Markdown aperto
# --------------------------------------------------------------------------- #

class MarkdownTab(QWidget):
    """Sorgente + anteprima per un singolo file, con auto-refresh da disco."""

    notice = Signal(str)                 # messaggio per la barra di stato
    modified_changed = Signal(bool)      # stato "modificato" cambiato

    def __init__(
        self,
        path: str,
        dark: bool = False,
        prefs: ViewPrefs | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.path = os.path.abspath(path)
        self._dark = dark
        self._prefs = prefs if prefs is not None else ViewPrefs()
        self._loading = False

        # Sorgente
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Sorgente Markdown…")

        # Anteprima
        self.preview = PreviewWidget()

        # Vista split
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.editor)
        self.splitter.addWidget(self.preview)
        self.splitter.setSizes([500, 500])

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.splitter)

        # Timer per il render "debounced" mentre si digita
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(280)
        self._render_timer.timeout.connect(self.render)

        # Auto-refresh: osserva il file su disco
        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._on_file_changed)

        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.document().modificationChanged.connect(self.modified_changed.emit)

        self.set_dark(dark)
        self.set_prefs(self._prefs)   # font, colori, sintassi, riquadri
        self.load()
        self.render()
        if os.path.isfile(self.path):
            self.watcher.addPath(self.path)

    # -- stato -------------------------------------------------------------- #
    def is_modified(self) -> bool:
        return self.editor.document().isModified()

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        self._render_timer.start()

    # -- I/O ---------------------------------------------------------------- #
    def load(self) -> None:
        """Legge il file da disco e lo carica nell'editor."""
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            self.notice.emit(f"Impossibile leggere {os.path.basename(self.path)}: {exc}")
            return
        if text == self.editor.toPlainText():
            return
        self._loading = True
        self.editor.setPlainText(text)
        self.editor.document().setModified(False)
        self._loading = False

    def save(self) -> bool:
        """Salva l'editor su disco. Ritorna True in caso di successo."""
        try:
            # Sospende il watch durante la scrittura per evitare loop di refresh
            watching = self.path in self.watcher.files()
            if watching:
                self.watcher.removePath(self.path)
            with open(self.path, "w", encoding="utf-8") as fh:
                fh.write(self.editor.toPlainText())
            self.editor.document().setModified(False)
            if watching or os.path.isfile(self.path):
                self.watcher.addPath(self.path)
            self.notice.emit(f"Salvato {os.path.basename(self.path)}")
            return True
        except OSError as exc:
            QMessageBox.critical(
                self, "Errore di salvataggio",
                f"Impossibile salvare {self.path}:\n{exc}",
            )
            return False

    def reload_from_disk(self, confirm: bool = True) -> None:
        """Ricarica il file dal disco (chiede conferma se ci sono modifiche)."""
        if confirm and self.is_modified():
            ret = QMessageBox.question(
                self, "Ricaricare dal disco?",
                "Hai modifiche non salvate. Ricaricare il file dal disco?\n"
                "Le modifiche non salvate verranno perse.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        self.load()
        self.render()
        self.notice.emit(f"Ricaricato {os.path.basename(self.path)} dal disco")

    def _on_file_changed(self, path: str) -> None:
        """Il file è cambiato su disco: auto-refresh se non ci sono modifiche locali."""
        if path != self.path:
            return
        name = os.path.basename(self.path)

        # Alcuni editor riscrivono il file (replace): ri-aggancia il watch
        if self.path not in self.watcher.files() and os.path.isfile(self.path):
            self.watcher.addPath(self.path)

        if not os.path.exists(self.path):
            self.notice.emit(f"{name} è stato rimosso dal disco")
            return

        if self.is_modified():
            self.notice.emit(
                f"{name} è cambiato sul disco ma hai modifiche non salvate — "
                "premi F5 per ricaricare"
            )
            return

        self.load()
        self.render()
        self.notice.emit(f"{name} aggiornato dal disco (auto-refresh)")

    # -- anteprima ---------------------------------------------------------- #
    def render(self) -> None:
        self._render_timer.stop()
        html = build_html(
            self.editor.toPlainText(),
            os.path.basename(self.path),
            self._dark,
            self._prefs,
        )
        base = QUrl.fromLocalFile(os.path.dirname(self.path) + os.sep)
        self.preview.set_html(html, base)

    # -- temi / preferenze / layout ----------------------------------------- #
    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        if not hasattr(self, "_highlighter"):
            self._highlighter = MarkdownHighlighter(
                self.editor.document(), dark, self._prefs.src_color
            )
            self._highlighter.set_enabled(self._prefs.src_syntax)
        else:
            self._highlighter.set_dark(dark)
        self._update_editor_palette()
        self.preview.set_dark(dark)
        self.render()

    def _update_editor_palette(self) -> None:
        """Applica i colori di base della sorgente (tema o personalizzati)."""
        if self._dark:
            base_bg, placeholder = "#0d1117", "#6e7681"
            highlight, theme_text = "#1f6feb", "#e6edf3"
        else:
            base_bg, placeholder = "#ffffff", "#8b949e"
            highlight, theme_text = "#0969da", "#1f2328"
        pal = self.editor.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor(base_bg))
        pal.setColor(
            QPalette.ColorRole.Text, QColor(self._prefs.src_color or theme_text)
        )
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(placeholder))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(highlight))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        self.editor.setPalette(pal)

    def set_prefs(self, prefs: ViewPrefs) -> None:
        """Applica font, colori, sintassi e riquadri di sorgente/anteprima."""
        self._prefs = prefs

        # Font della sorgente (dimensioni in pixel)
        default = default_src_font()
        font = QFont(prefs.src_family or default.family())
        font.setPixelSize(max(6, int(prefs.src_size or font_px(default))))
        self.editor.setFont(font)

        # Colore del testo e colorazione della sintassi
        if hasattr(self, "_highlighter"):
            self._highlighter.set_custom_color(prefs.src_color)
            self._highlighter.set_enabled(prefs.src_syntax)
        self._update_editor_palette()

        # Riquadri (indipendenti l'uno dall'altro)
        self.set_panes(*mode_to_panes(prefs.mode))

        # L'anteprima dipende da font/colore via CSS
        self.render()

    def set_panes(self, show_source: bool, show_preview: bool) -> None:
        """Mostra/nasconde i due riquadri in modo indipendente."""
        self.editor.setVisible(show_source)
        self.preview.setVisible(show_preview)
        if show_source and show_preview:
            self.splitter.setSizes([500, 500])
        elif show_source:
            self.splitter.setSizes([1, 0])
        else:
            self.splitter.setSizes([0, 1])


# --------------------------------------------------------------------------- #
#  Finestra principale
# --------------------------------------------------------------------------- #

LIGHT_APP_STYLE = """
QMainWindow { background: #f3f4f6; }
QMenuBar { background: #ffffff; color: #1f2328; }
QMenuBar::item:selected { background: #e5e7eb; }
QMenu { background: #ffffff; color: #1f2328; border: 1px solid #d0d7de; }
QMenu::item:selected { background: #e5e7eb; color: #1f2328; }
QMenu::separator { background: #d0d7de; height: 1px; }
QTabWidget::pane { border: 1px solid #d0d7de; background: #ffffff; top: -1px; }
QTabBar::tab {
    background: #e5e7eb; color: #424a53; padding: 7px 16px; margin-right: 2px;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
}
QTabBar::tab:selected { background: #ffffff; color: #1f2328; }
QTabBar::tab:hover:!selected { background: #d8dde3; }
QSplitter::handle { background: #d0d7de; }
QStatusBar { background: #ffffff; color: #424a53; border-top: 1px solid #d0d7de; }
QLabel#emptyLabel { color: #656d76; font-size: 15px; }
"""

DARK_APP_STYLE = """
QMainWindow { background: #010409; }
QMenuBar { background: #0d1117; color: #e6edf3; }
QMenuBar::item:selected { background: #21262d; }
QMenu { background: #0d1117; color: #e6edf3; border: 1px solid #30363d; }
QMenu::item:selected { background: #21262d; color: #e6edf3; }
QMenu::separator { background: #30363d; height: 1px; }
QTabWidget::pane { border: 1px solid #30363d; background: #0d1117; top: -1px; }
QTabBar::tab {
    background: #161b22; color: #8b949e; padding: 7px 16px; margin-right: 2px;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
}
QTabBar::tab:selected { background: #0d1117; color: #e6edf3; }
QTabBar::tab:hover:!selected { background: #21262d; }
QSplitter::handle { background: #30363d; }
QStatusBar { background: #0d1117; color: #8b949e; border-top: 1px solid #30363d; }
QLabel#emptyLabel { color: #8b949e; font-size: 15px; }
"""

FILE_FILTER = (
    "File Markdown (*.md *.markdown *.mkd *.mdown *.txt);;Tutti i file (*)"
)


def system_dark() -> bool:
    """Rileva il tema di sistema (se supportato da Qt)."""
    try:
        hints = QApplication.styleHints()
        return hints.colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Dialog "Impostazioni"
# --------------------------------------------------------------------------- #

class SettingsDialog(QDialog):
    """Vista all'apertura, font/colori dei due riquadri, sintassi della sorgente."""

    def __init__(self, prefs: ViewPrefs, dark: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Impostazioni")
        self.setModal(True)
        self.prefs = prefs  # aggiornato in accept()

        # Valori predefiniti (quelli attuali dell'applicazione)
        self._def_src_font = default_src_font()
        self._def_src_px = font_px(self._def_src_font)
        self._def_prev_family = default_prev_family()
        self._def_color = QColor("#e6edf3" if dark else "#1f2328")

        root = QVBoxLayout(self)

        # --- Vista all'apertura -------------------------------------------- #
        view_box = QGroupBox("Vista all'apertura dei file")
        view_lay = QGridLayout(view_box)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Split view (sorgente + anteprima)", "split")
        self.mode_combo.addItem("Solo anteprima formattata", "preview")
        self.mode_combo.addItem("Solo sorgente", "source")
        idx = self.mode_combo.findData(prefs.mode)
        self.mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        view_lay.addWidget(QLabel("Vista predefinita:"), 0, 0)
        view_lay.addWidget(self.mode_combo, 0, 1)
        root.addWidget(view_box)

        # --- Sorgente -------------------------------------------------------- #
        src_box = QGroupBox("Sorgente (editor Markdown)")
        src_lay = QGridLayout(src_box)

        self.src_family = QFontComboBox()
        self.src_family.setCurrentFont(
            QFont(prefs.src_family or self._def_src_font.family())
        )
        self.src_size = QSpinBox()
        self.src_size.setRange(6, 96)
        self.src_size.setSuffix(" px")
        self.src_size.setValue(prefs.src_size or self._def_src_px)

        self.src_color_check = QCheckBox("Colore personalizzato")
        self.src_color_check.setChecked(bool(prefs.src_color))
        self.src_color = QColor(prefs.src_color or self._def_color)
        self.src_color_btn = QPushButton()
        self._swatch(self.src_color_btn, self.src_color)

        self.src_syntax_check = QCheckBox("Colorazione della sintassi")
        self.src_syntax_check.setChecked(prefs.src_syntax)
        self.src_syntax_check.setToolTip(
            "Evidenziazione di grassetti, titoli, codice, link, ecc."
        )

        src_lay.addWidget(QLabel("Font:"), 0, 0)
        src_lay.addWidget(self.src_family, 0, 1)
        src_lay.addWidget(QLabel("Dimensione:"), 1, 0)
        src_lay.addWidget(self.src_size, 1, 1)
        src_lay.addWidget(self.src_color_check, 2, 0)
        src_lay.addWidget(self.src_color_btn, 2, 1, Qt.AlignmentFlag.AlignLeft)
        src_lay.addWidget(self.src_syntax_check, 3, 0, 1, 2)
        src_lay.setColumnStretch(1, 1)
        root.addWidget(src_box)

        # --- Anteprima -------------------------------------------------------- #
        prev_box = QGroupBox("Anteprima (Markdown formattato)")
        prev_lay = QGridLayout(prev_box)

        self.prev_family = QFontComboBox()
        self.prev_family.setCurrentFont(
            QFont(prefs.prev_family or self._def_prev_family)
        )
        self.prev_size = QSpinBox()
        self.prev_size.setRange(6, 96)
        self.prev_size.setSuffix(" px")
        self.prev_size.setValue(prefs.prev_size or DEFAULT_PREVIEW_SIZE)

        self.prev_color_check = QCheckBox("Colore personalizzato")
        self.prev_color_check.setChecked(bool(prefs.prev_color))
        self.prev_color = QColor(prefs.prev_color or self._def_color)
        self.prev_color_btn = QPushButton()
        self._swatch(self.prev_color_btn, self.prev_color)

        prev_lay.addWidget(QLabel("Font:"), 0, 0)
        prev_lay.addWidget(self.prev_family, 0, 1)
        prev_lay.addWidget(QLabel("Dimensione:"), 1, 0)
        prev_lay.addWidget(self.prev_size, 1, 1)
        prev_lay.addWidget(self.prev_color_check, 2, 0)
        prev_lay.addWidget(self.prev_color_btn, 2, 1, Qt.AlignmentFlag.AlignLeft)
        prev_lay.setColumnStretch(1, 1)
        root.addWidget(prev_box)

        note = QLabel(
            "Il colore personalizzato vale per il testo: link e codice "
            "mantengono il loro colore. Le modifiche si applicano subito e "
            "vengono ricordate alla prossima apertura."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #656d76;")
        root.addWidget(note)

        # --- Pulsanti --------------------------------------------------------- #
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btn_cancel = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        if btn_cancel is not None:
            btn_cancel.setText("Annulla")
        self.btn_reset = self.button_box.addButton(
            "Ripristina predefiniti", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.btn_reset.clicked.connect(self._reset_defaults)
        root.addWidget(self.button_box)

        # Abilitazione pulsanti colore
        self.src_color_check.toggled.connect(self.src_color_btn.setEnabled)
        self.prev_color_check.toggled.connect(self.prev_color_btn.setEnabled)
        self.src_color_btn.setEnabled(self.src_color_check.isChecked())
        self.prev_color_btn.setEnabled(self.prev_color_check.isChecked())
        self.src_color_btn.clicked.connect(self._pick_src_color)
        self.prev_color_btn.clicked.connect(self._pick_prev_color)

        self.resize(500, self.sizeHint().height())

    # -- helper -------------------------------------------------------------- #
    @staticmethod
    def _swatch(btn: QPushButton, color: QColor) -> None:
        btn.setFixedSize(96, 24)
        btn.setStyleSheet(
            f"background-color: {color.name()}; "
            "border: 1px solid #8a8f98; border-radius: 3px;"
        )

    def _pick_src_color(self) -> None:
        color = QColorDialog.getColor(
            self.src_color, self, "Colore del testo — sorgente"
        )
        if color.isValid():
            self.src_color = color
            self._swatch(self.src_color_btn, color)

    def _pick_prev_color(self) -> None:
        color = QColorDialog.getColor(
            self.prev_color, self, "Colore del testo — anteprima"
        )
        if color.isValid():
            self.prev_color = color
            self._swatch(self.prev_color_btn, color)

    def _reset_defaults(self) -> None:
        """Riporta tutti i campi ai valori predefiniti."""
        self.mode_combo.setCurrentIndex(0)  # split
        self.src_family.setCurrentFont(QFont(self._def_src_font.family()))
        self.src_size.setValue(self._def_src_px)
        self.src_color_check.setChecked(False)
        self.src_color = QColor(self._def_color)
        self._swatch(self.src_color_btn, self.src_color)
        self.src_syntax_check.setChecked(True)
        self.prev_family.setCurrentFont(QFont(self._def_prev_family))
        self.prev_size.setValue(DEFAULT_PREVIEW_SIZE)
        self.prev_color_check.setChecked(False)
        self.prev_color = QColor(self._def_color)
        self._swatch(self.prev_color_btn, self.prev_color)

    # -- raccolta dei valori ------------------------------------------------- #
    def _collect(self) -> ViewPrefs:
        src_family = self.src_family.currentFont().family()
        if src_family == self._def_src_font.family():
            src_family = ""  # resta il font di sistema predefinito
        prev_family = self.prev_family.currentFont().family()
        if prev_family == self._def_prev_family:
            prev_family = ""  # resta lo stack CSS predefinito
        return ViewPrefs(
            mode=str(self.mode_combo.currentData()),
            src_family=src_family,
            src_size=int(self.src_size.value()),
            src_color=self.src_color.name() if self.src_color_check.isChecked() else "",
            src_syntax=self.src_syntax_check.isChecked(),
            prev_family=prev_family,
            prev_size=int(self.prev_size.value()),
            prev_color=self.prev_color.name() if self.prev_color_check.isChecked() else "",
        )

    def accept(self) -> None:  # noqa: N802 (API Qt)
        self.prefs = self._collect()
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self, dark: bool | None = None, split: bool | None = None):
        super().__init__()
        self._dark = system_dark() if dark is None else dark

        # Preferenze: la vista iniziale vale anche per i tab correnti
        self.prefs = load_prefs()
        if split is not None:  # sovrascritte per uso programmatico/test
            self.prefs.mode = "split" if split else "preview"
        self._show_source, self._show_preview = mode_to_panes(self.prefs.mode)

        self.resize(1180, 780)
        self.setAcceptDrops(True)

        # Stato vuoto (nessun file aperto)
        self._empty = QLabel(
            "Nessun file aperto\n\n"
            "Trascina qui dei file Markdown\noppure premi Ctrl+O per aprirli"
        )
        self._empty.setObjectName("emptyLabel")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(True)
        self._tabs.setDocumentMode(True)
        self._tabs.tabCloseRequested.connect(self.close_tab)
        self._tabs.currentChanged.connect(self._on_tab_changed)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self._tabs)
        self.setCentralWidget(self._stack)

        self._build_menus()
        self._apply_theme()
        self._update_state()

    # -- menu e azioni ------------------------------------------------------ #
    def _build_menus(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")

        self.act_open = QAction("&Apri…", self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)
        self.act_open.triggered.connect(self.open_dialog)
        file_menu.addAction(self.act_open)

        self.act_save = QAction("&Salva", self)
        self.act_save.setShortcut(QKeySequence.StandardKey.Save)
        self.act_save.triggered.connect(self.save_current)
        file_menu.addAction(self.act_save)

        self.act_reload = QAction("&Ricarica dal disco", self)
        self.act_reload.setShortcuts([QKeySequence("F5"), QKeySequence("Ctrl+R")])
        self.act_reload.triggered.connect(self.reload_current)
        file_menu.addAction(self.act_reload)

        file_menu.addSeparator()

        self.act_close = QAction("&Chiudi tab", self)
        self.act_close.setShortcut(QKeySequence("Ctrl+W"))
        self.act_close.triggered.connect(lambda: self.close_tab(self._tabs.currentIndex()))
        file_menu.addAction(self.act_close)

        file_menu.addSeparator()

        self.act_quit = QAction("&Esci", self)
        self.act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self.act_quit.triggered.connect(self.close)
        file_menu.addAction(self.act_quit)

        view_menu = menubar.addMenu("&Visualizza")

        self.act_show_source = QAction("Mostra &sorgente", self)
        self.act_show_source.setCheckable(True)
        self.act_show_source.setChecked(self._show_source)
        self.act_show_source.setShortcut(QKeySequence("Ctrl+Shift+E"))
        self.act_show_source.toggled.connect(lambda v: self._set_pane("source", v))
        view_menu.addAction(self.act_show_source)

        self.act_show_preview = QAction("Mostra &anteprima", self)
        self.act_show_preview.setCheckable(True)
        self.act_show_preview.setChecked(self._show_preview)
        self.act_show_preview.setShortcut(QKeySequence("Ctrl+Shift+P"))
        self.act_show_preview.toggled.connect(lambda v: self._set_pane("preview", v))
        view_menu.addAction(self.act_show_preview)

        view_menu.addSeparator()

        self.act_dark = QAction("&Dark mode", self)
        self.act_dark.setCheckable(True)
        self.act_dark.setChecked(self._dark)
        self.act_dark.setShortcut(QKeySequence("Ctrl+Shift+D"))
        self.act_dark.toggled.connect(self._set_dark)
        view_menu.addAction(self.act_dark)

        view_menu.addSeparator()

        self.act_next = QAction("Tab &successiva", self)
        self.act_next.setShortcut(QKeySequence("Ctrl+Tab"))
        self.act_next.triggered.connect(
            lambda: self._tabs.setCurrentIndex(
                (self._tabs.currentIndex() + 1) % max(self._tabs.count(), 1)
            )
        )
        view_menu.addAction(self.act_next)

        self.act_prev = QAction("Tab &precedente", self)
        self.act_prev.setShortcut(QKeySequence("Ctrl+Shift+Tab"))
        self.act_prev.triggered.connect(
            lambda: self._tabs.setCurrentIndex(
                (self._tabs.currentIndex() - 1) % max(self._tabs.count(), 1)
            )
        )
        view_menu.addAction(self.act_prev)

        settings_menu = menubar.addMenu("&Impostazioni")
        self.act_settings = QAction("&Preferenze…", self)
        self.act_settings.setShortcut(QKeySequence("Ctrl+,"))
        self.act_settings.triggered.connect(self.open_settings)
        settings_menu.addAction(self.act_settings)

        help_menu = menubar.addMenu("&Aiuto")
        act_about = QAction("&Informazioni su mdv", self)
        act_about.triggered.connect(self._about)
        help_menu.addAction(act_about)

    # -- apertura file ------------------------------------------------------ #
    def open_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Apri file Markdown", "", FILE_FILTER
        )
        for path in paths:
            self.open_file(path)

    def open_file(self, path: str) -> MarkdownTab | None:
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            self.statusBar().showMessage(f"File non trovato: {path}", 5000)
            return None

        # Se già aperto, porta in primo piano
        for i in range(self._tabs.count()):
            tab = self._tabs.widget(i)
            if isinstance(tab, MarkdownTab) and tab.path == path:
                self._tabs.setCurrentIndex(i)
                return tab

        tab = MarkdownTab(path, dark=self._dark, prefs=self.prefs)
        tab.notice.connect(lambda msg: self.statusBar().showMessage(msg, 6000))
        tab.modified_changed.connect(
            lambda _mod, t=tab: self._on_modified(t)
        )

        idx = self._tabs.addTab(tab, self._tab_label(tab))
        self._tabs.setTabToolTip(idx, path)
        self._tabs.setCurrentIndex(idx)
        self._stack.setCurrentWidget(self._tabs)
        self._update_state()
        self.statusBar().showMessage(f"Aperto {path}", 4000)
        return tab

    def current_tab(self) -> MarkdownTab | None:
        w = self._tabs.currentWidget()
        return w if isinstance(w, MarkdownTab) else None

    # -- operazioni sulla tab corrente -------------------------------------- #
    def save_current(self) -> None:
        tab = self.current_tab()
        if tab:
            tab.save()

    def reload_current(self) -> None:
        tab = self.current_tab()
        if tab:
            tab.reload_from_disk()

    def close_tab(self, index: int) -> None:
        if not (0 <= index < self._tabs.count()):
            return
        tab = self._tabs.widget(index)
        if not isinstance(tab, MarkdownTab):
            return

        if tab.is_modified():
            name = os.path.basename(tab.path)
            ret = QMessageBox.question(
                self, "Modifiche non salvate",
                f"Vuoi salvare le modifiche di {name} prima di chiudere?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if ret == QMessageBox.StandardButton.Cancel:
                return
            if ret == QMessageBox.StandardButton.Save and not tab.save():
                return

        self._tabs.removeTab(index)
        tab.deleteLater()
        self._update_state()

    def _on_modified(self, tab: MarkdownTab) -> None:
        idx = self._tabs.indexOf(tab)
        if idx >= 0:
            self._tabs.setTabText(idx, self._tab_label(tab))
        if tab is self.current_tab():
            self._update_window_title()

    @staticmethod
    def _tab_label(tab: MarkdownTab) -> str:
        label = os.path.basename(tab.path)
        return f"{label} *" if tab.is_modified() else label

    def _on_tab_changed(self, _index: int) -> None:
        self._update_state()

    def _update_window_title(self) -> None:
        tab = self.current_tab()
        if tab:
            self.setWindowTitle(f"{self._tab_label(tab)} — mdv")
        else:
            self.setWindowTitle("mdv")

    def _update_state(self) -> None:
        has_tabs = self._tabs.count() > 0
        self._stack.setCurrentWidget(self._tabs if has_tabs else self._empty)
        self.act_save.setEnabled(has_tabs)
        self.act_reload.setEnabled(has_tabs)
        self.act_close.setEnabled(has_tabs)
        self.act_next.setEnabled(has_tabs)
        self.act_prev.setEnabled(has_tabs)
        self._update_window_title()

    # -- temi / layout ------------------------------------------------------ #
    def _apply_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(DARK_APP_STYLE if self._dark else LIGHT_APP_STYLE)

    def _set_dark(self, dark: bool) -> None:
        self._dark = dark
        self._apply_theme()
        for i in range(self._tabs.count()):
            tab = self._tabs.widget(i)
            if isinstance(tab, MarkdownTab):
                tab.set_dark(dark)

    # -- preferenze e riquadri ---------------------------------------------- #
    def open_settings(self) -> None:
        """Apre il dialog Impostazioni e applica i valori confermati."""
        dlg = SettingsDialog(self.prefs, self._dark, self)
        if dlg.exec():
            self.apply_prefs(dlg.prefs)

    def apply_prefs(self, prefs: ViewPrefs) -> None:
        """Applica e salva le preferenze su tutti i tab aperti."""
        self.prefs = prefs
        save_prefs(self.prefs)
        self._show_source, self._show_preview = mode_to_panes(prefs.mode)
        self._sync_pane_actions()
        for i in range(self._tabs.count()):
            tab = self._tabs.widget(i)
            if isinstance(tab, MarkdownTab):
                tab.set_prefs(self.prefs)
        self.statusBar().showMessage("Impostazioni applicate e salvate", 4000)

    def _set_pane(self, which: str, visible: bool) -> None:
        """Mostra/nasconde un riquadro in modo indipendente dall'altro."""
        show_source = visible if which == "source" else self._show_source
        show_preview = visible if which == "preview" else self._show_preview

        if not show_source and not show_preview:
            self.statusBar().showMessage(
                "Almeno un riquadro deve restare visibile", 4000
            )
            self._sync_pane_actions()
            return

        self._show_source, self._show_preview = show_source, show_preview
        self.prefs.mode = panes_to_mode(show_source, show_preview)
        save_prefs(self.prefs)
        self._sync_pane_actions()
        self._apply_panes()

    def _sync_pane_actions(self) -> None:
        """Allinea le voci di menu allo stato reale dei riquadri."""
        for act, value in (
            (self.act_show_source, self._show_source),
            (self.act_show_preview, self._show_preview),
        ):
            if act.isChecked() != value:
                act.blockSignals(True)
                act.setChecked(value)
                act.blockSignals(False)

    def _apply_panes(self) -> None:
        for i in range(self._tabs.count()):
            tab = self._tabs.widget(i)
            if isinstance(tab, MarkdownTab):
                tab.set_panes(self._show_source, self._show_preview)

    def _about(self) -> None:
        engine = "QWebEngineView" if HAVE_WEBENGINE else "QTextBrowser (fallback)"
        QMessageBox.information(
            self, "Informazioni su mdv",
            "<b>mdv</b> — reader GUI per file Markdown<br><br>"
            "• più file in tab dedicate<br>"
            "• riquadri sorgente/anteprima indipendenti "
            "(Ctrl+Shift+E / Ctrl+Shift+P)<br>"
            "• Impostazioni: vista all'apertura, font, colori, sintassi "
            "(Ctrl+,)<br>"
            "• dark mode (Ctrl+Shift+D)<br>"
            "• auto-refresh al cambio su disco (F5 per ricaricare)<br>"
            "• salvataggio con Ctrl+S, apertura con Ctrl+O<br><br>"
            f"Motore di anteprima: {engine}",
        )

    # -- drag & drop -------------------------------------------------------- #
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local:
                self.open_file(local)
        event.acceptProposedAction()

    # -- chiusura ----------------------------------------------------------- #
    def closeEvent(self, event) -> None:  # noqa: N802
        for i in range(self._tabs.count()):
            tab = self._tabs.widget(i)
            if not isinstance(tab, MarkdownTab) or not tab.is_modified():
                continue
            name = os.path.basename(tab.path)
            ret = QMessageBox.question(
                self, "Modifiche non salvate",
                f"Vuoi salvare le modifiche di {name} prima di uscire?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if ret == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if ret == QMessageBox.StandardButton.Save and not tab.save():
                event.ignore()
                return
        event.accept()


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #

def parse_args(argv: list[str]):
    global HAVE_WEBENGINE
    dark: bool | None = None
    files: list[str] = []
    for arg in argv:
        if arg in ("--dark", "-d"):
            dark = True
        elif arg in ("--light", "-l"):
            dark = False
        elif arg == "--no-webengine":
            HAVE_WEBENGINE = False
        elif arg in ("-h", "--help"):
            print(__doc__)
            raise SystemExit(0)
        else:
            files.append(arg)
    return dark, files


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Da impostare prima della creazione di QApplication (richiesto da QtWebEngine)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)

    dark, files = parse_args(argv)

    app = QApplication(sys.argv)
    app.setApplicationName("mdv")
    app.setApplicationDisplayName("mdv — Markdown reader")

    win = MainWindow(dark=dark)
    for path in files:
        win.open_file(path)
    win.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
