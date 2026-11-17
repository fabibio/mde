# mdv — Markdown viewer

Reader GUI per file Markdown con visualizzazione a **tab**, scritto in Python con **PyQt6**.

## Funzionalità

- **Più file in tab dedicate**: apri quanti file vuoi, ognuno nella sua tab (tab trascinabili, chiudibili con la `X`).
- **Vista split**: sorgente Markdown a sinistra, anteprima renderizzata a destra.
  Con `Ctrl+Shift+L` si nasconde la sorgente e resta solo la modalità lettura.
- **Dark mode**: tema scuro per interfaccia, editor e anteprima (`Ctrl+Shift+D`);
  di default segue il tema di sistema.
- **Auto-refresh**: se il file cambia su disco viene ricaricato automaticamente
  (se hai modifiche non salvate, ti avvisa invece di sovrascrivere).
- Evidenziazione sintattica della sorgente, anteprima con evidenziazione dei blocchi
  di codice (Pygments), tabelle, link cliccabili, drag & drop di file nella finestra.

## Requisiti

- Python 3.10+
- Pacchetti: `PyQt6`, `PyQt6-WebEngine`, `markdown`, `pygments`, `pymdown-extensions`

## Installazione

```bash
python -m venv venv
./venv/bin/pip install PyQt6 PyQt6-WebEngine markdown pygments pymdown-extensions
```

## Utilizzo

```bash
./venv/bin/python mdv.py                 # avvio vuoto
./venv/bin/python mdv.py esempio.md      # apre il file di esempio
./venv/bin/python mdv.py *.md --dark     # più file, tema scuro
```

Opzioni:

| Opzione          | Effetto                                       |
|------------------|-----------------------------------------------|
| `--dark`, `-d`   | tema scuro forzato                            |
| `--light`, `-l`  | tema chiaro forzato                           |
| `--no-webengine` | usa il fallback `QTextBrowser` (senza GL)     |
| `--help`, `-h`   | mostra l'aiuto                                |

In alternativa, su sistemi senza OpenGL o in ambienti headless:

```bash
MDV_NO_WEBENGINE=1 ./venv/bin/python mdv.py esempio.md
```

## Scorciatoie

| Tasto            | Azione                          |
|------------------|---------------------------------|
| `Ctrl+O`         | Apri file                       |
| `Ctrl+S`         | Salva                           |
| `F5` / `Ctrl+R`  | Ricarica dal disco              |
| `Ctrl+W`         | Chiudi tab                      |
| `Ctrl+Tab`       | Tab successiva / precedente     |
| `Ctrl+Shift+L`   | Vista split on/off              |
| `Ctrl+Shift+D`   | Dark mode on/off                |
| `Ctrl+Q`         | Esci                            |

## Architettura

Un unico file, `mdv.py`, organizzato in:

- `build_html()` / `preview_css()` — conversione Markdown → HTML con CSS chiaro/scuro;
- `MarkdownHighlighter` — evidenziazione sintattica della sorgente;
- `PreviewWidget` — anteprima con `QWebEngineView` (fallback `QTextBrowser`);
- `MarkdownTab` — un file aperto: editor + anteprima + `QFileSystemWatcher` per l'auto-refresh;
- `MainWindow` — tab, menu, temi, drag & drop.
