# Guida per sperimentare con la UI del launcher

Branch di prova: **`try/launcher-ui`**, creato da `origin/F_ContextualBandit` (commit `cdcc692`),
cioè la versione più completa del launcher (tab Decisions, Bandit, Rewards inclusi).

Questa copia del repository è **separata** dal progetto principale (`../EEPY`): ha la sua
cartella `.git`, quindi niente di quello che fai qui tocca i branch del progetto. Il **push è
disattivato** (`git remote -v` mostra `PUSH-DISABLED-trial-branch`), così nulla finisce su GitHub
per sbaglio.

---

## 1. Avviare e vedere le modifiche

```bash
cd ~/Desktop/Tu-E/Embodying-intelligent-behavior-in-social-context/EEPY-launcher-ui
python3 launch.py                 # apre http://127.0.0.1:8090
```

- **Modifiche a `static/index.html`**: basta **ricaricare la pagina** nel browser. Il server
  rilegge il file a ogni richiesta (`PAGE.read_bytes()` in `__main__.py`) e manda
  `Cache-Control: no-store`. Non serve riavviare niente.
- **Modifiche ai file Python** (`__main__.py`, `parts.py`, ...): Ctrl+C nel terminale del
  launcher e `python3 launch.py` di nuovo.
- Per provare la UI **senza robot né camera**: tab Developer → Robot **dry run**, poi
  Start all. Il panel parte comunque e le card mostrano stati veri.

Gli ambienti sono già pronti in questa cartella: `.venv-robot` è un collegamento a quello del
progetto principale (viene solo usato, non modificato) e `.venv-vision` è locale a questa copia.
Git li ignora (`.git/info/exclude`).

---

## 2. Dove sta la UI

| File | Cosa contiene |
|---|---|
| `eepy/launcher/static/index.html` | **Tutta la UI**: HTML, CSS (`<style>`) e JS (`<script>`) in un unico file, ~700 righe, nessuna libreria esterna, nessun build step. |
| `eepy/launcher/__main__.py` | Server HTTP (solo libreria standard) e API JSON. |
| `eepy/launcher/parts.py` | I tre programmi (sim, panel, vision): comandi, stati, porte, impostazioni. |
| `eepy/launcher/bandit.py`, `decisions.py`, `rewards.py` | Backend dei tab Bandit, Decisions, Rewards. |
| `eepy/launcher/README.md` | Cosa fa il launcher, dal punto di vista dell'utente. |
| `tests/test_launcher.py` | Test (17). Controllano la logica e l'API, non l'aspetto. |

---

## 3. Struttura della pagina

```
<header>   titolo "EEPY launcher" · chips (robot, mode) · step · [Start all] [Stop all]
<nav>      tab: overview · panel · live · decisions · bandit · rewards · dev
<main>     una <section id="..."> per tab, solo una ha class="active"
  #overview    #banner (avvisi) · #cards (una card per programma) · #message
  #panel       toolbar + <iframe id="frame-panel">  (il Wizard panel, porta 8081)
  #live        toolbar + <iframe id="frame-vision"> (pagina Live HMM, porta 8770)
  #decisions   form + #dc-out
  #bandit      #bd-view (cosa ha imparato) · #bd-smoke-out (simulazione)
  #rewards     #rw-live · #rw-recorded · #rw-simulated
  #dev         impostazioni (.seg = pulsanti a segmenti) · #commands · log completo #log-full
```

Il tab aperto viene ricordato in `localStorage` con la chiave `eepy-launcher-tab`.

### Design token (CSS, in cima a `<style>`)

```css
--bg --surface --surface-2 --border --text --muted --accent --accent-text
--ok (verde) --warn (ambra) --bad (rosso) --off (grigio) --ext (viola)
--mono --log-bg --log-text
```

C'è già una versione scura sotto `@media (prefers-color-scheme: dark)`. **Cambia i colori
qui**, non nei singoli elementi, così light e dark restano coerenti.

### Componenti ricorrenti

`.btn` / `.btn.primary` / `.btn.big` · `.dot` (+ `.running .starting .stopping .crashed .external`)
· `.card` / `.card-head` / `.mini-log` · `.warnbox` · `.hint` · `.box` · `.seg` · `.chip`
· `.rw-table` (tabelle dei tab Rewards e Bandit).

---

## 4. Come funziona il JS (da sapere prima di cambiare l'HTML)

- `refresh()` chiama `GET /api/status` **ogni secondo** e poi `render()`.
- `render()` **ricostruisce da zero** `#cards` e `#commands` con `innerHTML`, ogni secondo.
  Conseguenza: qualsiasi stato dentro una card (un menu aperto, un campo di testo, un'animazione
  CSS appena partita) viene azzerato a ogni giro. Se aggiungi elementi interattivi nelle card,
  mettili fuori da `#cards` oppure aggiorna solo ciò che cambia invece di riscrivere tutto.
- `pollLogs()` chiama `GET /api/logs?since=N` ogni secondo; tiene al massimo 4000 righe.
- `rewardsLive()` ogni 3 secondi.
- Il form Developer usa un flag `dirty`: finché non premi Save, `render()` non sovrascrive i campi.
- Gli elementi vengono trovati per **id** (`$("id")` = `document.getElementById`). Se rinomini
  o togli un id, cerca nel `<script>` chi lo usa, o il JS si rompe in silenzio.

### Stati di un programma → testo e colore

| `state` | Testo (`STATE_TEXT`) | Pallino |
|---|---|---|
| `stopped` | Stopped | grigio |
| `starting` | Starting… | ambra, lampeggia |
| `running` | Running | verde |
| `stopping` | Stopping… | ambra, lampeggia |
| `crashed` | Crashed | rosso |
| `external` | Running in a terminal | viola |

---

## 5. L'API (il contratto da non rompere)

La UI può cambiare liberamente; queste chiamate devono restare uguali, a meno di cambiare
anche il Python.

| Metodo e percorso | Corpo | Risposta |
|---|---|---|
| `GET /` | | la pagina `index.html` |
| `GET /api/status` | | vedi sotto |
| `GET /api/logs?since=N&part=panel` | | `{"lines": [[n, part, testo], ...], "last": n}` |
| `POST /api/start` · `/api/stop` · `/api/restart` | `{"part": "sim" \| "panel" \| "vision"}` | `{"ok": bool, "message": str}` |
| `POST /api/start-all` · `/api/stop-all` | `{}` | `{"ok", "message"}` |
| `POST /api/settings` | campi delle impostazioni | `{"ok", "message"}` |
| `POST /api/bandit/run` · `/api/decisions/run` · `/api/rewards/run` | vedi i `.py` omonimi | JSON del report |
| `GET /api/rewards/sessions` · `/api/rewards/live` | | JSON |

Forma di `/api/status`:

```json
{
  "settings": {"backend": "sim", "mode": "hmm", "robot_host": "", "camera": "",
               "panel_port": 8081, "live_port": 8770,
               "sim_extra": "", "panel_extra": "", "vision_extra": ""},
  "other_panel": false,
  "robot_host": "192.168.50.95",
  "robot": null,
  "needed": ["sim", "panel", "vision"],
  "parts": {
    "panel": {"label": "Wizard panel", "state": "running", "pid": 2120, "exit_code": null,
              "port": 8081, "url": "http://127.0.0.1:8081/", "uptime": 610,
              "command": ".venv-robot/bin/python -m eepy.wizard ...", "missing": []}
  },
  "busy": false, "step": "", "message": "Vision + live HMM started."
}
```

`backend`: `dry` | `sim` | `real`. `mode`: `wizard` | `rules` | `hmm` | `hmm_bandit`.
`robot` vale `null` tranne con il robot vero (`{"host", "reachable"}`).

---

## 6. Vincoli da rispettare

- **Niente librerie esterne né CDN.** Il launcher deve funzionare in laboratorio, anche senza
  internet, e il server usa solo la libreria standard di Python.
- **La pagina deve contenere la parola `EEPY`**: lo controlla `test_the_page_and_the_api`.
- Deve restare leggibile **in chiaro e in scuro** e su una finestra stretta (c'è già un
  `@media (max-width: 600px)`).
- Gli `<iframe>` caricano pagine di altri programmi (panel, Live HMM): il loro aspetto non si
  cambia da qui.

Dopo ogni modifica:

```bash
.venv-robot/bin/python -m pytest -q tests/test_launcher.py
```

---

## 7. Spunti di miglioramento

Osservazioni fatte provando il launcher; sono idee da valutare, non difetti accertati.

- **Primo avvio.** Quando manca un ambiente (es. `.venv-vision`), la card dice solo
  "set up the virtualenvs (README, step 1)". Si potrebbe mostrare il comando esatto da
  copiare, come già fa il tab Developer per i programmi.
- **Modalità.** La modalità (`hmm`, `hmm_bandit`, ...) si cambia solo nel tab Developer e
  richiede Save + riavvio. Un selettore vicino a Start all, con l'avviso "riavvia per applicare",
  renderebbe più chiaro cosa sta girando.
- **Ordine di avvio.** Start all avvia sim → panel → vision e si ferma al primo errore. Una
  piccola barra di avanzamento a tre passi renderebbe visibile dove si trova (oggi c'è solo il
  testo in `#step`).
- **Permesso camera.** Al primo avvio di vision macOS chiede il permesso e il primo tentativo
  fallisce (`not authorized to capture video`) prima di riuscire. Un suggerimento nella card,
  quando compare quella riga nel log, eviterebbe di pensare a un errore.
- **Gerarchia dei tab.** Sette tab con lo stesso peso: Overview / Wizard panel / Live HMM
  servono durante una sessione, Decisions / Bandit / Rewards / Developer servono per analisi
  e configurazione. Si potrebbero separare visivamente i due gruppi.
- **Log nelle card.** Le ultime 9 righe in monospace sono utili per chi sviluppa, meno per chi
  conduce l'esperimento. Si potrebbero comprimere, con un "mostra output".

---

## 8. Git in questa copia

```bash
git status                                    # cosa hai cambiato
git diff origin/F_ContextualBandit            # tutte le differenze rispetto al punto di partenza
git add -A && git commit -m "UI: ..."         # salva un passo (resta solo qui)
git checkout -- eepy/launcher/static/index.html   # butta via le modifiche non salvate a un file
```

Per aggiornare la base con le novità del team: `git fetch origin`, poi
`git rebase origin/F_ContextualBandit` (o il branch del launcher che nel frattempo è entrato in
`main`).

Se un giorno vuoi proporre le modifiche al team, riattiva il push consapevolmente
(`git remote set-url --push origin git@github.com:JenneVandromme/EEPY.git`) e apri una PR.
Fino ad allora tutto resta su questo computer.
