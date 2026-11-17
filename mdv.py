#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mdv — reader GUI per file Markdown.

Funzionalità:
  * apertura di più file in tab dedicate, file nuovi e aperti di recente
  * riquadri sorgente e anteprima mostrabili/nascosti in modo indipendente
  * dark mode (interfaccia + anteprima renderizzata)
  * auto-refresh quando il file cambia su disco
  * evidenziazione sintattica della sorgente (attivabile/disattivabile)
  * menu Modifica: annulla/riprendi, taglia/copia/incolla, cerca e
    sostituisci con barra stile Kate (Ctrl+F / Ctrl+H, F3 / Shift+F3)
  * menu Selezione: riformattazione ASCII delle tabelle selezionate
  * Impostazioni: vista all'apertura, font, dimensioni e colori dei riquadri,
    colorazione sintassi, larghezza massima delle righe di tabella
  * salvataggio (Ctrl+S) e salva con nome (Ctrl+Shift+S), drag & drop,
    scorciatoie

Uso:
    python mdv.py [file.md ...] [--dark|--light] [--no-webengine]
"""

from __future__ import annotations

import html as html_lib
import os
import re
import sys
import unicodedata
from dataclasses import dataclass

import markdown
from PyQt6.QtCore import (
    QEvent,
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
    QShortcut,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
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
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QToolButton,
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
DEFAULT_TABLE_MAX_WIDTH = 80  # caratteri: righe di tabella più lunghe restano inalterate
TABLE_MIN_COL_WIDTH = 3      # minima larghezza di una colonna (serve per "---")


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
    table_max_width: int = DEFAULT_TABLE_MAX_WIDTH  # larghezza max riga tabella


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
    try:
        p.table_max_width = max(
            40,
            int(
                s.value(
                    "edit/table_max_width", DEFAULT_TABLE_MAX_WIDTH
                )
                or DEFAULT_TABLE_MAX_WIDTH
            ),
        )
    except (TypeError, ValueError):
        p.table_max_width = DEFAULT_TABLE_MAX_WIDTH

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
    s.setValue("edit/table_max_width", prefs.table_max_width)
    s.sync()


# --------------------------------------------------------------------------- #
#  Riformattazione tabelle ASCII (menu Selezione)
# --------------------------------------------------------------------------- #

def display_width(text: str) -> int:
    """Larghezza a video: i caratteri larghi (W/F) valgono 2, gli accentati 0."""
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _has_unescaped_pipe(text: str) -> bool:
    """True se la stringa contiene una '|' non preceduta da backslash."""
    i, n = 0, len(text)
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "|":
            return True
        i += 1
    return False


def _ends_with_unescaped_pipe(text: str) -> bool:
    """True se la stringa termina con una '|' non escape-ata."""
    if not text.endswith("|"):
        return False
    backslashes, i = 0, len(text) - 2
    while i >= 0 and text[i] == "\\":
        backslashes += 1
        i -= 1
    return backslashes % 2 == 0


def _split_cells(body: str) -> list[str]:
    """Divide il corpo di una riga sulle '|' non escape-ate ('\\|' resta testo)."""
    cells: list[str] = []
    cur: list[str] = []
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch == "\\" and i + 1 < n:
            cur.append(body[i : i + 2])
            i += 2
            continue
        if ch == "|":
            cells.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    cells.append("".join(cur))
    return cells


def _parse_row(line: str) -> tuple[str, list[str] | None]:
    """Separa prefisso (indentazione / '>') e celle di una riga.

    Ritorna (prefisso, celle); celle è None se la riga non contiene '|'.
    """
    expanded = line.expandtabs(4)
    m = re.match(r"[\s>]*", expanded)
    prefix = m.group(0) if m else ""
    body = expanded[len(prefix):]
    if not _has_unescaped_pipe(body):
        return prefix, None
    cells = _split_cells(body)
    if body[:1] == "|":          # bordo iniziale -> cella fantasma
        cells = cells[1:]
    if _ends_with_unescaped_pipe(body) and cells:  # bordo finale
        cells = cells[:-1]
    return prefix, cells


_DELIM_CELL_RE = re.compile(r"\s*:?-+:?\s*")


def _is_table_delimiter(cells: list[str]) -> bool:
    """True se tutte le celle sono del tipo '---', ':---', '---:' o ':---:'."""
    return bool(cells) and all(_DELIM_CELL_RE.fullmatch(c) for c in cells)


def _delimiter_alignment(cell: str) -> str | None:
    """Allineamento dichiarato dalla cella di delimitazione."""
    text = cell.strip()
    left = text.startswith(":")
    right = text.endswith(":")
    if left and right:
        return "center"
    if left:
        return "left"
    if right:
        return "right"
    return None


def is_inside_fence(lines: list[str], index: int) -> bool:
    """True se la riga `index` cade dentro un blocco fenced code (``` o ~~~)."""
    if index < 0 or index >= len(lines):
        return False
    char, length = "", 0
    for line in lines[: index + 1]:
        m = re.match(r" {0,3}(`{3,}|~{3,})", line)
        if not m:
            continue
        marker = m.group(1)
        if not char:
            # Per i fence con '`' l'info string non può contenere '`'.
            if marker[0] == "`" and "`" in line[m.end():]:
                continue
            char, length = marker[0], len(marker)
        elif marker[0] == char and len(marker) >= length and not line[m.end():].strip():
            char, length = "", 0  # chiusura
    return bool(char)


def find_table_block(
    lines: list[str], start: int, end: int | None = None
) -> tuple[int, int, str | None]:
    """Individua il blocco di tabella attorno al cursore o alla selezione.

    Ritorna (prima_riga, ultima_riga, errore): le righe contigue con '|'
    che partono da `start` (o dall'intervallo start..end), rifiutando i
    blocchi di codice fenced.
    """
    if not lines:
        return 0, 0, "documento vuoto"
    if end is None or end < start:
        end = start
    start = max(0, min(start, len(lines) - 1))
    end = max(start, min(end, len(lines) - 1))

    if is_inside_fence(lines, start) or is_inside_fence(lines, end):
        return start, end, "il cursore è dentro un blocco di codice"
    if not _has_unescaped_pipe(lines[start].expandtabs(4)):
        return start, end, "il cursore non è su una riga di tabella"

    first = start
    while first > 0 and _has_unescaped_pipe(lines[first - 1].expandtabs(4)):
        first -= 1
    last = end
    while (
        last + 1 < len(lines)
        and _has_unescaped_pipe(lines[last + 1].expandtabs(4))
    ):
        last += 1
    for i in range(first, last + 1):
        if is_inside_fence(lines, i):
            return first, last, "il blocco selezionato contiene codice fenced"

    # I parser richiedono l'intestazione come prima riga del blocco: se ci
    # sono righe con '|' sopra, il blocco inizia alla riga sopra il delimiter.
    delim = next(
        (
            i
            for i in range(first, last + 1)
            if (c := _parse_row(lines[i])[1]) is not None
            and _is_table_delimiter(c)
        ),
        None,
    )
    if delim is not None and delim - 1 > first:
        first = delim - 1
    return first, last, None


def _pad_cell(content: str, width: int, align: str | None) -> str:
    pad = max(0, width - display_width(content))
    if align == "right":
        return " " * pad + content
    if align == "center":
        left = pad // 2
        return " " * left + content + " " * (pad - left)
    return content + " " * pad


def _render_row(
    prefix: str, cells: list[str], widths: list[int], aligns: list[str | None]
) -> str:
    parts = []
    for j, width in enumerate(widths):
        content = cells[j].strip() if j < len(cells) else ""
        parts.append(" " + _pad_cell(content, width, aligns[j]) + " ")
    return prefix + "|" + "|".join(parts) + "|"


def _render_delimiter(
    prefix: str, widths: list[int], aligns: list[str | None]
) -> str:
    parts = []
    for width, align in zip(widths, aligns):
        if align == "left":
            text = ":" + "-" * (width - 1)
        elif align == "right":
            text = "-" * (width - 1) + ":"
        elif align == "center":
            text = ":" + "-" * (width - 2) + ":"
        else:
            text = "-" * width
        parts.append(" " + text + " ")
    return prefix + "|" + "|".join(parts) + "|"


def format_table(
    lines: list[str], max_width: int
) -> tuple[list[str], str | None]:
    """Riformatta un blocco di tabella Markdown allineando le colonne in ASCII.

    Ritorna (righe, messaggio): `messaggio` è None a formattazione avvenuta,
    altrimenti le righe sono quelle originali e il messaggio spiega il motivo.

    Corner case gestiti:
      * righe la cui resa supererebbe `max_width` restano inalterate
        (prima quelle che non ci starebbero mai, poi — in modo iterativo —
        quelle che allargano troppo le colonne per tutte le altre);
      * celle mancanti (riga corta) vengono integrate, celle in eccesso
        rispetto alla delimitazione lasciano la riga invariata;
      * '|'-escape-ate ("\\|") non separano le celle;
      * tab espansi a 4 spazi, prefissi '> '/indentazione conservati e
        riattaccati allineati;
      * intestazione o delimitazione mancanti/non corrispondenti -> errore;
      * blocchi dentro fenced code -> rifiutati (vedi find_table_block).
    """
    original = list(lines)
    if not lines:
        return original, "nessuna tabella da formattare"
    if max_width < 6:
        return original, "larghezza massima troppo piccola"

    parsed = [_parse_row(line) for line in lines]

    delim_idx = next(
        (
            i
            for i, (_prefix, cells) in enumerate(parsed)
            if cells is not None and _is_table_delimiter(cells)
        ),
        -1,
    )
    if delim_idx < 0:
        return original, "manca la riga di delimitazione (es. |---|---|)"
    if delim_idx == 0:
        return original, "manca la riga di intestazione sopra la delimitazione"

    delim_cells = parsed[delim_idx][1] or []
    ncols = len(delim_cells)
    aligns = [_delimiter_alignment(c) for c in delim_cells]

    header_cells = parsed[delim_idx - 1][1]
    if header_cells is None or len(header_cells) != ncols:
        return original, "l'intestazione non corrisponde alla riga di delimitazione"

    # Righe renderabili: intestazione, delimitazione e corpo con <= ncols celle.
    renderable = {delim_idx - 1, delim_idx}
    for i in range(delim_idx + 1, len(parsed)):
        cells = parsed[i][1]
        if cells and len(cells) <= ncols:
            renderable.add(i)

    def widths_over(indices: set[int]) -> list[int]:
        """Larghezze di colonna (min 3) dati gli indici delle righe ammesse."""
        widths = [TABLE_MIN_COL_WIDTH] * ncols
        for i in indices:
            if i == delim_idx:
                continue  # la delimitazione viene rigenerata: non conta
            cells = parsed[i][1] or []
            for j in range(ncols):
                content = cells[j].strip() if j < len(cells) else ""
                w = display_width(content)
                if w > widths[j]:
                    widths[j] = w
        return widths

    def fits(widths: list[int]) -> bool:
        # Tutte le righe renderabili hanno la stessa larghezza finale:
        # ncols celle + (ncols + 1) barre + 2 spazi per cella.
        return sum(widths) + 3 * ncols + 1 <= max_width

    def own_fits(i: int) -> bool:
        """True se la riga ci starebbe anche da sola (colonne al minimo)."""
        cells = parsed[i][1] or []
        widths = [TABLE_MIN_COL_WIDTH] * ncols
        for j in range(ncols):
            content = cells[j].strip() if j < len(cells) else ""
            widths[j] = max(widths[j], display_width(content))
        return fits(widths)

    # 1) Righe "impossibili": non ci starebbero mai -> inalterate subito.
    excluded = {
        i
        for i in renderable
        if i != delim_idx and not own_fits(i)
    }

    # 2) Se il totale supera il limite, togliere la riga che contribuisce
    #    di più finché non entra (in modo monotono: mai rientri).
    widths = widths_over(renderable - excluded)
    while not fits(widths):
        best, best_key = None, (0, -1)
        for i in sorted(renderable - excluded - {delim_idx}):
            gain = sum(widths) - sum(
                widths_over(renderable - excluded - {i})
            )
            # Preferenza alle righe di corpo (indice più alto) a parità di gain.
            key = (gain, i)
            if gain > 0 and key > best_key:
                best, best_key = i, key
        if best is None:
            break
        excluded.add(best)
        widths = widths_over(renderable - excluded)

    if not fits(widths):
        return original, (
            f"la tabella supera la larghezza massima di {max_width} caratteri"
        )

    # Prefissi (indentazione / '>') allineati sulla stessa colonna.
    rendered = renderable - excluded
    max_prefix = max(
        (display_width(parsed[i][0]) for i in rendered), default=0
    )

    out: list[str] = []
    for i, line in enumerate(original):
        if i not in rendered:
            out.append(line)  # invariata (fuori tabella, non allineabile, esclusa)
            continue
        prefix, cells = parsed[i]
        prefix = prefix + " " * (max_prefix - display_width(prefix))
        if i == delim_idx:
            out.append(_render_delimiter(prefix, widths, aligns))
        else:
            out.append(_render_row(prefix, cells or [], widths, aligns))
    return out, None


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
#  Barra "Trova/Sostituisci" (stile Kate/KWrite)
# --------------------------------------------------------------------------- #

MATCH_COLOR = "#b5d6ff"      # evidenziazione dei risultati
CURRENT_COLOR = "#ffcc66"    # risultato corrente (selezione nell'editor)


class _FindLineEdit(QLineEdit):
    """Campo che distingue Return (successivo) da Shift+Return (precedente)."""

    submitted = Signal(bool)  # True se Shift era premuto

    def keyPressEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.submitted.emit(
                bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            )
            event.accept()
            return
        super().keyPressEvent(event)


class FindReplaceBar(QWidget):
    """Barra di ricerca/sostituzione con evidenziazione live dei match.

    Layout stile Kate/KWrite: riga "Trova" con opzioni (maiuscole/minuscole,
    parola intera, regex), frecce per scorrere, contatore e chiusura; riga
    "Sostituisci" con i pulsanti di sostituzione.
    """

    notice = Signal(str)  # messaggi per la barra di stato
    closed = Signal()     # l'utente ha chiuso la barra (Esc o ✕)

    def __init__(self, editor: QPlainTextEdit, parent=None):
        super().__init__(parent)
        self.editor = editor
        self._matches: list[tuple[int, int]] = []
        self._last_error = ""

        # --- campi --------------------------------------------------------- #
        self.find_edit = _FindLineEdit()
        self.find_edit.setPlaceholderText("Trova…")
        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("Sostituisci con…")

        # --- opzioni ------------------------------------------------------- #
        self.opt_case = QToolButton()
        self.opt_case.setText("Aa")
        self.opt_case.setCheckable(True)
        self.opt_case.setToolTip("Distingui maiuscole/minuscole")
        self.opt_word = QToolButton()
        self.opt_word.setText("ab|")
        self.opt_word.setCheckable(True)
        self.opt_word.setToolTip("Solo parole intere")
        self.opt_regex = QToolButton()
        self.opt_regex.setText(".*")
        self.opt_regex.setCheckable(True)
        self.opt_regex.setToolTip("Espressione regolare")
        for opt in (self.opt_case, self.opt_word, self.opt_regex):
            opt.setAutoRaise(True)
            opt.setFixedWidth(28)

        # --- navigazione ---------------------------------------------------- #
        self.btn_prev = QToolButton()
        self.btn_prev.setText("↑")
        self.btn_prev.setToolTip("Risultato precedente (Shift+Enter)")
        self.btn_next = QToolButton()
        self.btn_next.setText("↓")
        self.btn_next.setToolTip("Risultato successivo (Enter)")
        self.btn_close = QToolButton()
        self.btn_close.setText("✕")
        self.btn_close.setToolTip("Chiudi la barra (Esc)")
        self.lbl_count = QLabel()

        self.btn_replace = QPushButton("Sostituisci")
        self.btn_replace_all = QPushButton("Tutti")

        # --- layout ---------------------------------------------------------- #
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        row1.addWidget(QLabel("Trova:"))
        row1.addWidget(self.find_edit, 1)
        for widget in (
            self.opt_case, self.opt_word, self.opt_regex,
            self.btn_prev, self.btn_next, self.lbl_count, self.btn_close,
        ):
            row1.addWidget(widget)

        row2 = QHBoxLayout()
        row2.setSpacing(4)
        row2.addWidget(QLabel("Sostituisci:"))
        row2.addWidget(self.replace_edit, 1)
        row2.addWidget(self.btn_replace)
        row2.addWidget(self.btn_replace_all)
        self._replace_row = QWidget()
        self._replace_row.setLayout(row2)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(4)
        lay.addLayout(row1)
        lay.addWidget(self._replace_row)

        self.setStyleSheet(
            "QToolButton:checked { background-color: #b5d6ff; color: #1f2328; "
            "border: 1px solid #4a90d9; border-radius: 3px; }"
        )

        # --- segnali ---------------------------------------------------------- #
        self.find_edit.submitted.connect(
            lambda shift: self.find_next(backwards=bool(shift))
        )
        self.find_edit.textChanged.connect(self.refresh)
        for opt in (self.opt_case, self.opt_word, self.opt_regex):
            opt.toggled.connect(self.refresh)
        self.btn_prev.clicked.connect(lambda: self.find_next(backwards=True))
        self.btn_next.clicked.connect(lambda: self.find_next())
        self.btn_close.clicked.connect(self.close_bar)
        self.btn_replace.clicked.connect(self.replace_current)
        self.btn_replace_all.clicked.connect(self.replace_all)
        self.replace_edit.returnPressed.connect(self.replace_current)

        esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self.close_bar)

        self.editor.textChanged.connect(self.refresh)
        self.editor.cursorPositionChanged.connect(self._repaint)

        self._replace_row.hide()
        self.hide()

    # -- apertura / chiusura ------------------------------------------------- #
    def show_bar(self, replace: bool = False, initial: str | None = None) -> None:
        """Mostra la barra (con la riga di sostituzione se richiesta)."""
        if initial and "\n" not in initial:
            self.find_edit.setText(initial)
        self._replace_row.setVisible(replace)
        self.show()
        self.refresh()
        if replace:
            self.replace_edit.setFocus()
        else:
            self.find_edit.setFocus()
            self.find_edit.selectAll()

    def close_bar(self) -> None:
        """Nasconde la barra e toglie l'evidenziazione dei risultati."""
        self.hide()
        self.editor.setExtraSelections([])
        self.closed.emit()

    # -- ricerca -------------------------------------------------------------- #
    def _regex(self) -> QRegularExpression | None:
        pattern = self.find_edit.text()
        if not pattern:
            self._last_error = ""
            return None
        if self.opt_word.isChecked():
            pattern = rf"(?<!\w)(?:{pattern})(?!\w)"
        rx = QRegularExpression(pattern)
        if not self.opt_case.isChecked():
            rx.setPatternOptions(
                QRegularExpression.PatternOption.CaseInsensitiveOption
            )
        if not rx.isValid():
            error = f"Espressione non valida: {rx.errorString()}"
            if error != self._last_error:  # evita spam a ogni tasto
                self._last_error = error
                self.notice.emit(error)
            return None
        self._last_error = ""
        return rx

    def _compute_matches(self) -> list[tuple[int, int]]:
        rx = self._regex()
        if rx is None:
            return []
        matches: list[tuple[int, int]] = []
        it = rx.globalMatch(self.editor.toPlainText())
        while it.hasNext():
            m = it.next()
            start, end = m.capturedStart(), m.capturedEnd()
            if end > start:  # i match a lunghezza 0 non sono navigabili
                matches.append((start, end))
        return matches

    def refresh(self) -> None:
        """Ricalcola i risultati e ridisegna l'evidenziazione."""
        if not self.isVisible():
            return
        self._matches = self._compute_matches()
        self._update_count()
        self._repaint()

    def _current_index(self) -> int:
        """Indice del risultato corrente (= selezione uguale a un match)."""
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            return -1
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        for i, (m_start, m_end) in enumerate(self._matches):
            if m_start == start and m_end == end:
                return i
        return -1

    def _repaint(self) -> None:
        """Evidenzia i risultati (il corrente resta la selezione dell'editor)."""
        if not self.isVisible():
            return
        current = self._current_index()
        selections: list[QTextEdit.ExtraSelection] = []
        for i, (start, end) in enumerate(self._matches):
            if i == current:
                continue  # disegnato dalla selezione reale (colore CURRENT)
            sel = QTextEdit.ExtraSelection()
            fmt = QTextCharFormat()
            fmt.setBackground(QBrush(QColor(MATCH_COLOR)))
            sel.format = fmt
            cursor = QTextCursor(self.editor.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = cursor
            selections.append(sel)
        self.editor.setExtraSelections(selections)

    def _update_count(self) -> None:
        if not self.find_edit.text():
            self.lbl_count.setText("")
            return
        index = self._current_index()
        self.lbl_count.setText(
            f"{index + 1 if index >= 0 else 0}/{len(self._matches)}"
        )

    def find_next(self, backwards: bool = False) -> bool:
        """Seleziona il risultato successivo (o precedente), con avvolgimento."""
        if not self._matches:
            self.notice.emit(
                "Nessun risultato" if self.find_edit.text()
                else "Inserisci il testo da cercare"
            )
            return False

        cursor = self.editor.textCursor()
        index = None
        if backwards:
            anchor = (
                cursor.selectionStart() if cursor.hasSelection()
                else cursor.position()
            )
            for i in range(len(self._matches) - 1, -1, -1):
                if self._matches[i][1] <= anchor:
                    index = i
                    break
            if index is None:
                index = len(self._matches) - 1
                self.notice.emit("Ricerca avvolta: tornata alla fine")
        else:
            anchor = (
                cursor.selectionEnd() if cursor.hasSelection()
                else cursor.position()
            )
            for i, (start, _end) in enumerate(self._matches):
                if start >= anchor:
                    index = i
                    break
            if index is None:
                index = 0
                self.notice.emit("Ricerca avvolta: tornata all'inizio")

        start, end = self._matches[index]
        found = QTextCursor(self.editor.document())
        found.setPosition(start)
        found.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(found)
        self.editor.centerCursor()
        self._update_count()
        return True

    # -- sostituzione --------------------------------------------------------- #
    def replace_current(self) -> None:
        """Sostituisce il risultato corrente (selezionato) e va al successivo."""
        if self._current_index() < 0:
            # Nessun risultato selezionato: prima lo si seleziona.
            self.find_next()
            return
        start, end = self._matches[self._current_index()]
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(self.replace_edit.text())
        self.refresh()  # le posizioni cambiano con la sostituzione
        self.find_next()

    def replace_all(self) -> int:
        """Sostituisce tutti i risultati in un unico passo di annullamento."""
        if not self._matches:
            self.notice.emit("Nessun risultato da sostituire")
            return 0
        matches = list(self._matches)
        replacement = self.replace_edit.text()
        cursor = QTextCursor(self.editor.document())
        cursor.beginEditBlock()
        for start, end in reversed(matches):  # dalla fine: posizioni stabili
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(replacement)
        cursor.endEditBlock()
        self.refresh()
        self._update_count()
        self.notice.emit(f"Sostituiti {len(matches)} risultati")
        return len(matches)


# --------------------------------------------------------------------------- #
#  Tab = un file Markdown aperto
# --------------------------------------------------------------------------- #

class MarkdownTab(QWidget):
    """Sorgente + anteprima per un singolo file, con auto-refresh da disco."""

    notice = Signal(str)                 # messaggio per la barra di stato
    modified_changed = Signal(bool)      # stato "modificato" cambiato

    def __init__(
        self,
        path: str | None = None,
        dark: bool = False,
        prefs: ViewPrefs | None = None,
        name: str | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.path = os.path.abspath(path) if path else None
        self.name = name or "Senza titolo"
        self._dark = dark
        self._prefs = prefs if prefs is not None else ViewPrefs()
        self._loading = False

        # Sorgente
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Sorgente Markdown…")
        # Con uno stylesheet attivo il polish (es. all'ingresso in un tab)
        # può azzerare il font dell'editor: il filtro lo riapplica.
        self._src_font: QFont | None = None
        self.editor.installEventFilter(self)

        # Barra trova/sostituisci (stile Kate), sopra l'editor
        self.find_bar = FindReplaceBar(self.editor)
        self.find_bar.closed.connect(self.hide_find)
        self.find_bar.notice.connect(self.notice.emit)

        self.editor_host = QWidget()
        host_lay = QVBoxLayout(self.editor_host)
        host_lay.setContentsMargins(0, 0, 0, 0)
        host_lay.setSpacing(0)
        host_lay.addWidget(self.find_bar)
        host_lay.addWidget(self.editor)

        # Anteprima
        self.preview = PreviewWidget()

        # Vista split
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.editor_host)
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
        if self.path and os.path.isfile(self.path):
            self.watcher.addPath(self.path)

    def eventFilter(self, obj, event) -> bool:
        """Riapplica il font della sorgente se Qt lo azzera durante il polish."""
        if (
            obj is self.editor
            and event.type() == QEvent.Type.FontChange
            and self._src_font is not None
            and self.editor.font() != self._src_font
        ):
            self.editor.setFont(self._src_font)
        return super().eventFilter(obj, event)

    # -- stato -------------------------------------------------------------- #
    @property
    def display_name(self) -> str:
        """Nome del file mostrato nelle tab e nel titolo."""
        if self.path:
            return os.path.basename(self.path)
        return self.name

    def is_modified(self) -> bool:
        return self.editor.document().isModified()

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        self._render_timer.start()

    # -- I/O ---------------------------------------------------------------- #
    def load(self) -> None:
        """Legge il file da disco e lo carica nell'editor."""
        if self.path is None:
            return  # file nuovo: l'editor resta com'è (vuo o testo iniziale)
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
        if self.path is None:
            self.notice.emit("Il file non ha ancora un percorso")
            return False
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

    def save_as(self, path: str) -> bool:
        """Assegna un percorso al file (nuovo o "Salva con nome") e salva."""
        path = os.path.abspath(path)
        old = self.path
        if old == path:
            return self.save()
        if old and old in self.watcher.files():
            self.watcher.removePath(old)
        self.path = path
        if self.save():
            if path not in self.watcher.files():
                self.watcher.addPath(path)
            return True
        self.path = old  # ripristina il percorso precedente in caso di errore
        if old and old not in self.watcher.files() and os.path.isfile(old):
            self.watcher.addPath(old)
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
            self.display_name,
            self._dark,
            self._prefs,
        )
        if self.path:
            base = QUrl.fromLocalFile(os.path.dirname(self.path) + os.sep)
        else:
            base = QUrl()
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
        # Con la barra di ricerca aperta il risultato corrente è la selezione:
        # deve usare il colore dedicato (e testo scuro per restare leggibile).
        highlight_text = "#ffffff"
        if self.find_bar.isVisible():
            highlight, highlight_text = CURRENT_COLOR, "#000000"
        pal = self.editor.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor(base_bg))
        pal.setColor(
            QPalette.ColorRole.Text, QColor(self._prefs.src_color or theme_text)
        )
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(placeholder))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(highlight))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor(highlight_text))
        self.editor.setPalette(pal)

    def set_prefs(self, prefs: ViewPrefs) -> None:
        """Applica font, colori, sintassi e riquadri di sorgente/anteprima."""
        self._prefs = prefs

        # Font della sorgente (dimensioni in pixel)
        default = default_src_font()
        font = QFont(prefs.src_family or default.family())
        font.setPixelSize(max(6, int(prefs.src_size or font_px(default))))
        self._src_font = font
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
        self.editor_host.setVisible(show_source)
        self.preview.setVisible(show_preview)
        if show_source and show_preview:
            self.splitter.setSizes([500, 500])
        elif show_source:
            self.splitter.setSizes([1, 0])
        else:
            self.splitter.setSizes([0, 1])

    # -- trova/sostituisci --------------------------------------------------- #
    def show_find(self, replace: bool = False) -> None:
        """Apre la barra di ricerca (con la sostituzione se richiesta)."""
        cursor = self.editor.textCursor()
        initial = ""
        if cursor.hasSelection():
            initial = cursor.selectedText()
        self.find_bar.show_bar(replace=replace, initial=initial)
        self._update_editor_palette()  # colore del risultato corrente

    def hide_find(self) -> None:
        """Chiude la barra di ricerca e ripristina i colori dell'editor."""
        if not self.find_bar.isVisible():
            return
        self.find_bar.close_bar()
        self._update_editor_palette()
        self.editor.setFocus()

    def toggle_find(self) -> None:
        if self.find_bar.isVisible():
            self.hide_find()
        else:
            self.show_find()

    # -- riformattazione tabelle --------------------------------------------- #
    def reformat_table(self) -> None:
        """Riformatta la tabella sotto il cursore/allineando le colonne."""
        lines = self.editor.toPlainText().split("\n")
        doc = self.editor.document()
        cursor = self.editor.textCursor()

        first_blk = doc.findBlock(cursor.selectionStart()).blockNumber()
        last_blk = doc.findBlock(cursor.selectionEnd()).blockNumber()
        # Una selezione che finisce a inizio riga non include quella riga.
        if (
            last_blk > first_blk
            and doc.findBlock(cursor.selectionEnd()).position()
            == cursor.selectionEnd()
        ):
            last_blk -= 1

        first, last, error = find_table_block(lines, first_blk, last_blk)
        if error:
            self.notice.emit(error)
            return

        block = lines[first : last + 1]
        new_block, error = format_table(block, self._prefs.table_max_width)
        if error:
            self.notice.emit(error)
            return
        if new_block == block:
            self.notice.emit("La tabella è già allineata")
            return

        # Sostituisce il blocco in un unico passo di annullamento.
        first_block = doc.findBlockByNumber(first)
        last_block = doc.findBlockByNumber(last)
        edit = QTextCursor(first_block)
        edit.setPosition(
            last_block.position() + last_block.length() - 1,
            QTextCursor.MoveMode.KeepAnchor,
        )
        edit.beginEditBlock()
        edit.insertText("\n".join(new_block))
        edit.endEditBlock()

        # Seleziona la tabella appena formattata.
        new_last = doc.findBlockByNumber(first + len(new_block) - 1)
        selection = QTextCursor(doc)
        selection.setPosition(first_block.position())
        selection.setPosition(
            new_last.position() + new_last.length() - 1,
            QTextCursor.MoveMode.KeepAnchor,
        )
        self.editor.setTextCursor(selection)
        self.notice.emit(
            f"Tabella formattata ({len(new_block)} righe, "
            f"max {self._prefs.table_max_width} caratteri)"
        )


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
MAX_RECENT_FILES = 10  # voci del menu "Apri recenti"


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

        # --- Modifica e tabelle --------------------------------------------- #
        edit_box = QGroupBox("Modifica e tabelle")
        edit_lay = QGridLayout(edit_box)

        self.table_max_width = QSpinBox()
        self.table_max_width.setRange(40, 400)
        self.table_max_width.setSuffix(" caratteri")
        self.table_max_width.setValue(prefs.table_max_width)
        self.table_max_width.setToolTip(
            "Durante la riformattazione ASCII delle tabelle, le righe più "
            "lunghe della larghezza massima restano inalterate"
        )
        edit_lay.addWidget(QLabel("Larghezza massima riga tabella:"), 0, 0)
        edit_lay.addWidget(self.table_max_width, 0, 1)
        edit_lay.setColumnStretch(1, 1)
        root.addWidget(edit_box)

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
        self.table_max_width.setValue(DEFAULT_TABLE_MAX_WIDTH)

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
            table_max_width=int(self.table_max_width.value()),
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

        # Il contenuto degli appunti cambia anche fuori dall'applicazione
        QApplication.clipboard().dataChanged.connect(self._sync_edit_actions)

    # -- menu e azioni ------------------------------------------------------ #
    def _build_menus(self) -> None:
        menubar = self.menuBar()

        # -- File ---------------------------------------------------------- #
        file_menu = menubar.addMenu("&File")

        self.act_new = QAction("&Nuovo", self)
        self.act_new.setShortcut(QKeySequence.StandardKey.New)
        self.act_new.triggered.connect(self.new_file)
        file_menu.addAction(self.act_new)

        self.act_open = QAction("&Apri…", self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)
        self.act_open.triggered.connect(self.open_dialog)
        file_menu.addAction(self.act_open)

        self.act_recent = file_menu.addMenu("Apri &recenti")
        self.act_recent.aboutToShow.connect(self._populate_recent)

        file_menu.addSeparator()

        self.act_save = QAction("&Salva", self)
        self.act_save.setShortcut(QKeySequence.StandardKey.Save)
        self.act_save.triggered.connect(self.save_current)
        file_menu.addAction(self.act_save)

        self.act_save_as = QAction("Salva &con nome…", self)
        self.act_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.act_save_as.triggered.connect(self.save_as_dialog)
        file_menu.addAction(self.act_save_as)

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

        # -- Modifica -------------------------------------------------------- #
        edit_menu = menubar.addMenu("&Modifica")

        self.act_undo = QAction("&Annulla", self)
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_undo.triggered.connect(lambda: self._edit_call("undo"))
        edit_menu.addAction(self.act_undo)

        self.act_redo = QAction("&Ripeti", self)
        self.act_redo.setShortcuts(
            [QKeySequence("Ctrl+Shift+Z"), QKeySequence("Ctrl+Y")]
        )
        self.act_redo.triggered.connect(lambda: self._edit_call("redo"))
        edit_menu.addAction(self.act_redo)

        edit_menu.addSeparator()

        self.act_cut = QAction("Ta&glia", self)
        self.act_cut.setShortcut(QKeySequence.StandardKey.Cut)
        self.act_cut.triggered.connect(lambda: self._edit_call("cut"))
        edit_menu.addAction(self.act_cut)

        self.act_copy = QAction("&Copia", self)
        self.act_copy.setShortcut(QKeySequence.StandardKey.Copy)
        self.act_copy.triggered.connect(lambda: self._edit_call("copy"))
        edit_menu.addAction(self.act_copy)

        self.act_paste = QAction("In&colla", self)
        self.act_paste.setShortcut(QKeySequence.StandardKey.Paste)
        self.act_paste.triggered.connect(lambda: self._edit_call("paste"))
        edit_menu.addAction(self.act_paste)

        edit_menu.addSeparator()

        self.act_find = QAction("&Cerca…", self)
        self.act_find.setShortcut(QKeySequence("Ctrl+F"))
        self.act_find.triggered.connect(lambda: self._show_find(False))
        edit_menu.addAction(self.act_find)

        self.act_replace = QAction("&Sostituisci…", self)
        self.act_replace.setShortcut(QKeySequence("Ctrl+H"))  # Ctrl+R = Ricarica
        self.act_replace.triggered.connect(lambda: self._show_find(True))
        edit_menu.addAction(self.act_replace)

        self.act_find_next = QAction("Risultato &successivo", self)
        self.act_find_next.setShortcut(QKeySequence("F3"))
        self.act_find_next.triggered.connect(lambda: self._find_next(False))
        edit_menu.addAction(self.act_find_next)

        self.act_find_prev = QAction("Risultato &precedente", self)
        self.act_find_prev.setShortcut(QKeySequence("Shift+F3"))
        self.act_find_prev.triggered.connect(lambda: self._find_next(True))
        edit_menu.addAction(self.act_find_prev)

        # -- Visualizza ------------------------------------------------------- #
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

        # -- Selezione -------------------------------------------------------- #
        selection_menu = menubar.addMenu("&Selezione")

        self.act_reformat_table = QAction("Ri&formatta tabella ASCII", self)
        self.act_reformat_table.setShortcut(QKeySequence("Ctrl+Alt+T"))
        self.act_reformat_table.setToolTip(
            "Allinea le colonne della tabella sotto il cursore "
            "(righe più lunghe della larghezza massima restano inalterate)"
        )
        self.act_reformat_table.triggered.connect(self.reformat_current_table)
        selection_menu.addAction(self.act_reformat_table)

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
        self._adopt_tab(tab)
        self._add_recent(path)
        self.statusBar().showMessage(f"Aperto {path}", 4000)
        return tab

    def new_file(self) -> MarkdownTab | None:
        """Crea un file nuovo: chiede il percorso solo al primo salvataggio."""
        tab = MarkdownTab(
            path=None,
            dark=self._dark,
            prefs=self.prefs,
            name=self._untitled_name(),
        )
        self._adopt_tab(tab)
        self.statusBar().showMessage(f"Nuovo file: {tab.name}", 4000)
        return tab

    def _untitled_name(self) -> str:
        """Nome unico per un file nuovo ("Senza titolo", "Senza titolo 2",…)."""
        existing = set()
        for i in range(self._tabs.count()):
            t = self._tabs.widget(i)
            if isinstance(t, MarkdownTab):
                existing.add(t.display_name)
        base = "Senza titolo"
        if base not in existing:
            return base
        n = 2
        while f"{base} {n}" in existing:
            n += 1
        return f"{base} {n}"

    def _adopt_tab(self, tab: MarkdownTab) -> MarkdownTab:
        """Collega i segnali di un tab e lo aggiunge alla finestra."""
        tab.notice.connect(lambda msg: self.statusBar().showMessage(msg, 6000))
        tab.modified_changed.connect(lambda _mod, t=tab: self._on_modified(t))
        # Stato delle azioni del menu Modifica
        tab.editor.cursorPositionChanged.connect(self._sync_edit_actions)
        document = tab.editor.document()
        document.undoAvailable.connect(
            lambda _b: self._sync_edit_actions()
        )
        document.redoAvailable.connect(
            lambda _b: self._sync_edit_actions()
        )

        idx = self._tabs.addTab(tab, self._tab_label(tab))
        self._tabs.setTabToolTip(idx, tab.path or tab.name)
        self._tabs.setCurrentIndex(idx)
        self._stack.setCurrentWidget(self._tabs)
        self._update_state()
        return tab

    # -- file recenti -------------------------------------------------------- #
    def _recent_files(self) -> list[str]:
        raw = QSettings("mdv", "mdv").value("file/recent", [])
        if raw is None:
            return []
        if isinstance(raw, str):  # con una sola voce QSettings restituisce str
            raw = [raw] if raw else []
        return [p for p in raw if isinstance(p, str) and p]

    def _add_recent(self, path: str) -> None:
        path = os.path.abspath(path)
        recent = [p for p in self._recent_files() if p != path]
        recent.insert(0, path)
        settings = QSettings("mdv", "mdv")
        settings.setValue("file/recent", recent[:MAX_RECENT_FILES])
        settings.sync()

    def _populate_recent(self) -> None:
        """Ricostruisce il sottomenu "Apri recenti" all'apertura."""
        self.act_recent.clear()
        recent = self._recent_files()[:MAX_RECENT_FILES]
        if not recent:
            empty = self.act_recent.addAction("Nessun file recente")
            empty.setEnabled(False)
            return
        for path in recent:
            action = self.act_recent.addAction(os.path.basename(path))
            action.setStatusTip(path)
            action.setToolTip(path)
            action.triggered.connect(
                lambda _checked=False, p=path: self.open_file(p)
            )

    # -- salvataggio ---------------------------------------------------------- #
    def current_tab(self) -> MarkdownTab | None:
        w = self._tabs.currentWidget()
        return w if isinstance(w, MarkdownTab) else None

    def save_current(self) -> None:
        tab = self.current_tab()
        if tab is None:
            return
        if tab.path is None:
            self._save_as(tab, None)
        else:
            tab.save()

    def save_as_dialog(self) -> None:
        tab = self.current_tab()
        if tab:
            self._save_as(tab, None)

    def _save_as(self, tab: MarkdownTab, path: str | None) -> bool:
        """Salva con nome; `path` consente di testare il flusso senza dialogo."""
        if path is None:
            initial = tab.path or os.path.join(os.getcwd(), tab.name)
            path, _ = QFileDialog.getSaveFileName(
                self, "Salva con nome", initial, FILE_FILTER
            )
            if not path:
                return False  # annullato dall'utente
        if not os.path.splitext(path)[1]:
            path += ".md"  # estensione di comodità
        if not tab.save_as(path):
            return False
        idx = self._tabs.indexOf(tab)
        if idx >= 0:
            self._tabs.setTabText(idx, self._tab_label(tab))
            self._tabs.setTabToolTip(idx, tab.path or tab.name)
        self._update_window_title()
        self._add_recent(tab.path)
        self._sync_edit_actions()
        return True

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
            name = tab.display_name
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
            if ret == QMessageBox.StandardButton.Save:
                if tab.path is None:
                    if not self._save_as(tab, None):
                        return  # salvataggio annullato o fallito
                elif not tab.save():
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
        label = tab.display_name
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
        self.act_save_as.setEnabled(has_tabs)
        self.act_reload.setEnabled(has_tabs)
        self.act_close.setEnabled(has_tabs)
        self.act_next.setEnabled(has_tabs)
        self.act_prev.setEnabled(has_tabs)
        self._update_window_title()
        self._sync_edit_actions()

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

    # -- menu Modifica / Selezione -------------------------------------------- #
    def _edit_call(self, name: str) -> None:
        """Inoltra un comando di modifica (undo/redo/cut/copy/paste) all'editor."""
        tab = self.current_tab()
        if tab is not None:
            getattr(tab.editor, name)()

    def _sync_edit_actions(self) -> None:
        """Allinea le azioni del menu Modifica allo stato dell'editor corrente."""
        tab = self.current_tab()
        has = tab is not None
        document = tab.editor.document() if has else None
        cursor = tab.editor.textCursor() if has else None

        self.act_undo.setEnabled(has and document.isUndoAvailable())
        self.act_redo.setEnabled(has and document.isRedoAvailable())
        selected = bool(cursor is not None and cursor.hasSelection())
        self.act_cut.setEnabled(selected)
        self.act_copy.setEnabled(selected)
        self.act_paste.setEnabled(
            has and bool(QApplication.clipboard().text())
        )
        for action in (
            self.act_find,
            self.act_replace,
            self.act_find_next,
            self.act_find_prev,
            self.act_reformat_table,
        ):
            action.setEnabled(has)

    def _show_find(self, replace: bool) -> None:
        tab = self.current_tab()
        if tab is None:
            return
        if not self._show_source:  # la ricerca vive nel riquadro sorgente
            self.act_show_source.setChecked(True)
        tab.show_find(replace)

    def _find_next(self, backwards: bool) -> None:
        tab = self.current_tab()
        if tab is None:
            return
        if not self._show_source:
            self.act_show_source.setChecked(True)
        if not tab.find_bar.isVisible():
            tab.show_find()  # F3 anche con la barra chiusa
        tab.find_bar.find_next(backwards)

    def reformat_current_table(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.reformat_table()

    def _about(self) -> None:
        engine = "QWebEngineView" if HAVE_WEBENGINE else "QTextBrowser (fallback)"
        QMessageBox.information(
            self, "Informazioni su mdv",
            "<b>mdv</b> — reader GUI per file Markdown<br><br>"
            "• più file in tab dedicate, file nuovi e recenti<br>"
            "• menu Modifica: annulla/riprendi, taglia/copia/incolla,<br>"
            "&nbsp;&nbsp;cerca e sostituisci con barra stile Kate (Ctrl+F / Ctrl+H)<br>"
            "• riquadri sorgente/anteprima indipendenti "
            "(Ctrl+Shift+E / Ctrl+Shift+P)<br>"
            "• menu Selezione: riformattazione ASCII delle tabelle (Ctrl+Alt+T)<br>"
            "• Impostazioni: vista all'apertura, font, colori, sintassi, "
            "larghezza tabelle (Ctrl+,)<br>"
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
