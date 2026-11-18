# mdv — Markdown viewer

Reader GUI per file Markdown con visualizzazione a **tab**, scritto in Python con **PyQt6**.

## Funzionalità

- **Più file in tab dedicate**: apri quanti file vuoi, ognuno nella sua tab (tab trascinabili, chiudibili con la `X`).
- **Due riquadri indipendenti**: sorgente Markdown e anteprima formattata si
  mostrano/nascondono singolarmente dal menu *Visualizza* (`Ctrl+Shift+E` /
  `Ctrl+Shift+P`); almeno un riquadro resta sempre visibile.
- **Menu *File***:
  - **Nuovo** (`Ctrl+N`): apre un file "Senza titolo", il percorso viene chiesto
    al primo salvataggio;
  - **Apri recenti**: ultimi 10 file aperti, in ordine di utilizzo;
  - **Salva con nome…** (`Ctrl+Shift+S`), con estensione `.md` aggiunta se mancante.
- **Menu *Modifica***: annulla/riprendi, taglia/copia/incolla e **cerca/sostituisci**
  con barra stile Kate/KWrite **in fondo alla finestra**: campo *Trova*, opzioni
  (maiuscole/minuscole, parola intera, regex), frecce, contatore e campo
  *Sostituisci*; evidenziazione live dei risultati, `Enter`/`Shift+Enter` per
  scorrere, `Esc` per chiudere.
- **Menu *Selezione***: **riformattazione ASCII delle tabelle** (`Ctrl+Alt+T`):
  allinea le colonne della tabella sotto il cursore (o della selezione) secondo
  gli allineamenti dichiarati; le righe che superano la **larghezza massima**
  configurata restano inalterate, così come le righe che non ci starebbero mai.
  Anche una **sostituzione dentro la tabella** che ne cambia la lunghezza
  (cerca/sostituisci) riallinea automaticamente le colonne, nello stesso passo
  di annullamento.
- **Menu *Impostazioni*** (`Ctrl+,`), con valori predefiniti pari al comportamento
  attuale e persistiti su disco (`~/.config/mdv/mdv.conf`):
  - **vista all'apertura**: split view, solo anteprima formattata o solo sorgente;
  - **font e dimensioni** per ciascun dei due riquadri;
  - **colore del testo** per ciascun riquadro (default: colore del tema);
  - **colorazione della sintassi** della sorgente, attivabile/disattivabile;
  - **larghezza massima riga di tabella** (default 80 caratteri, 40–400)
    usata dalla riformattazione ASCII.
  Le modifiche si applicano subito a tutti i file aperti e vengono ricordate
  alla prossima apertura; un pulsante *Ripristina predefiniti* riporta tutto
  al valore iniziale.
- **Dark mode**: tema scuro per interfaccia, editor e anteprima (`Ctrl+Shift+D`);
  di default segue il tema di sistema.
- **Auto-refresh**: se il file cambia su disco viene ricaricato automaticamente
  (se hai modifiche non salvate, ti avvisa invece di sovrascrivere).
- **Correzione rapida in anteprima** (solo con QtWebEngine): un **doppio click**
  su un paragrafo — o su una singola parola — lo rende editabile direttamente
  in anteprima, con la parola sotto il cursore già selezionata. `Enter` salva
  la correzione nel sorgente Markdown (un solo passo di annullamento), `Esc`
  annulla, un click fuori salva; sul bordo evidenziato compare un breve
  reminder delle istruzioni. Anche le **tabelle si modificano cella per cella**
  (e le citazioni, le liste e i blocchi di codice per intero). La scrittura
  tocca **solo il blocco corretto**: se non viene individuato in modo univoco
  la correzione viene annullata con un avviso. All'anteprima compare una sola
  volta l'avviso "Doppio click su un paragrafo per correggerlo"; con il
  fallback `QTextBrowser` l'anteprima resta in sola lettura.
- Evidenziazione sintattica della sorgente, anteprima con evidenziazione dei blocchi
  di codice (Pygments), tabelle, link cliccabili (gli esterni si aprono nel
  browser di sistema), drag & drop di file nella finestra.

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
| `Ctrl+N`         | Nuovo file                      |
| `Ctrl+O`         | Apri file                       |
| `Ctrl+Shift+S`   | Salva con nome                  |
| `Ctrl+S`         | Salva                           |
| `F5` / `Ctrl+R`  | Ricarica dal disco              |
| `Ctrl+W`         | Chiudi tab                      |
| `Ctrl+Tab`       | Tab successiva / precedente     |
| `Ctrl+Z`         | Annulla                         |
| `Ctrl+Shift+Z` / `Ctrl+Y` | Ripeti                  |
| `Ctrl+X` / `Ctrl+C` / `Ctrl+V` | Taglia / Copia / Incolla |
| `Ctrl+F`         | Cerca (barra stile Kate)        |
| `Ctrl+H`         | Cerca e sostituisci             |
| `F3` / `Shift+F3`| Risultato successivo/precedente |
| `Ctrl+Alt+T`     | Riformatta la tabella ASCII     |
| `Ctrl+Shift+E`   | Mostra/nascondi la sorgente     |
| `Ctrl+Shift+P`   | Mostra/nascondi l'anteprima     |
| Doppio click (anteprima) | Corregge il blocco in place |
| `Enter` / `Esc` (in correzione) | Salva / annulla la correzione |
| `Ctrl+Shift+D`   | Dark mode on/off                |
| `Ctrl+,`         | Impostazioni                    |
| `Ctrl+Q`         | Esci                            |

## Architettura

Un unico file, `mdv.py`, organizzato in:

- `ViewPrefs` + `load_prefs()`/`save_prefs()` — preferenze persistite con `QSettings`;
- `SettingsDialog` — dialog *Impostazioni* (vista, font, colori, sintassi, tabelle);
- `build_html()` / `preview_css()` — conversione Markdown → HTML con CSS chiaro/scuro;
  con `editable=True` inietta il JS della correzione rapida (bridge a console:
  `console.log("__MDV__" + JSON)` sniffato dalla pagina);
- `html_to_markdown()` / `locate_preview_edit()` / `split_source_blocks()` —
  conversione HTML → Markdown e individuazione del blocco sorgente da
  correggere (funzioni pure, testabili senza GUI); la scrittura avviene solo
  se la corrispondenza è univoca;
- `format_table()` + `find_table_block()` — riformattazione ASCII delle tabelle
  (funzioni pure, testabili senza GUI);
- `MarkdownHighlighter` — evidenziazione sintattica della sorgente (attivabile);
- `FindReplaceBar` — barra trova/sostituisci (in fondo alla finestra) con
  evidenziazione live; dopo una sostituzione che ne cambia la larghezza chiede
  al tab di riallineare la tabella interessata (callback `reformat_at`);
- `PreviewWidget` — anteprima con `QWebEngineView` (pagina con bridge dei
  messaggi JS, link esterni aperti dal browser di sistema, scroll preservato
  fra i render; fallback `QTextBrowser` in sola lettura);
- `MarkdownTab` — un file aperto: editor + anteprima + `QFileSystemWatcher` per l'auto-refresh;
- `MainWindow` — tab, menu (File/Modifica/Visualizza/Selezione/Impostazioni/Aiuto),
  file recenti, riquadri indipendenti, temi, drag & drop.
