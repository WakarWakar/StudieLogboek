# Studielogboek

Keeps your study log for you. While **Study Mode** is on, it records the pages you read, the videos you watch, what you search for, which PDFs you open and which apps you work in. Two hotkeys let you add notes and screenshots. One click turns it all into a Dutch Word logbook with a weekly progress report and an APA 7 reference list.

Everything stays on your own PC. Nothing is sent anywhere.

```
Firefox extension ──(127.0.0.1:5176)──▶ Studielogboek app (tray) ──▶ logboek.db ──▶ Word (.docx)
                                         ├─ window tracker (Word, Verkenner, cloud desktop, ...)
                                         └─ Ctrl+Alt+N note · Ctrl+Alt+S screenshot
```

## Install (once)

1. **App:** double-click `install.bat`. It creates a private Python environment in `.venv` and installs the dependencies.
2. **Start:** double-click `Studielogboek starten.vbs`. A book icon appears in the system tray, next to the clock. It's grey when Study Mode is off and green when it's on.
   To start it automatically when you log in, double-click `autostart aanzetten.bat`.
3. **Firefox extension.** Pick one:
   - **Quick (temporary, removed when Firefox closes):** open `about:debugging#/runtime/this-firefox` → *Load Temporary Add-on…* → choose `extension\manifest.json`.
   - **Permanent, signed by Mozilla (recommended):** create a free account on [addons.mozilla.org](https://addons.mozilla.org/developers/) and generate API keys at [Manage API Keys](https://addons.mozilla.org/developers/addon/api/key/). Then double-click `extension ondertekenen.bat` and paste the two keys when it asks. The keys aren't saved anywhere.
     "Unlisted" means Mozilla signs it but doesn't publish it. When it's done, drag the `.xpi` from `web-ext-artifacts\` onto a Firefox window and click *Toevoegen*. It then stays installed after restarts.
     After changing the extension, raise `version` in `extension\manifest.json` and sign again.
   - **Permanent without signing:** use *Firefox Developer Edition* or *ESR*, set `xpinstall.signatures.required` to `false` in `about:config`, zip the contents of `extension\` into `studielogboek.xpi` and open it in Firefox.

   If the popup shows **"Toestemming geven"**, click it. Firefox needs your permission before the extension can see which pages you open.

## Daily use

| What | How |
|---|---|
| Start/stop a study session | Click the tray icon, or use the switch in the Firefox toolbar popup. Both stay in sync. |
| Quick note | **Ctrl+Alt+N**. Pick a type (*Geleerd, Bevinding, Vraag-ToDo, Gesprek begeleider, Feedback*), optionally tick competencies, type, then **Enter** to save (**Shift+Enter** for a new line). The note is attached to the page or app you were in. |
| Screenshot | **Ctrl+Alt+S**. Drag a region (or click once for the whole screen), add a caption, and save. In Word it becomes *Figuur n*. |
| Word logbook | Tray → *Exporteren naar Word…* → one internship week, a date range, or everything. The file opens straight away and is saved in `Documents\Studielogboek\exports`. |
| Never record a site | Firefox popup → *Deze site nooit opslaan*, or tray → *Instellingen…* (sites and programs, one per line). |

Notes and screenshots also work when Study Mode is off. You pressed the hotkey yourself, so they're always saved.

## What ends up in the Word file

- **Overzicht:** study time, number of sources and notes per internship week (week 1 = 31-08-2026).
- **Per week, a Voortgangsrapport:** *Wat heb ik gedaan* (filled in automatically: study time, most-used sources, videos, searches, apps), *Wat heb ik geleerd*, *Feedback* and *Volgende stappen* (your ToDo notes as clickable checkboxes), plus grey *Vul aan:* prompts for the parts only you can write.
- **Per day:** *Gesprekken met begeleider*, *Wat heb ik geleerd*, *Bevindingen*, *Open vragen & ToDo's*, *Screenshots*, and an *Activiteiten* table (tijd | activiteit | bron | duur) with clickable links. Office rows say what you did in the file, e.g. *"Stage midterm presentatie.pptx: maakte dia's en werkte aan dia 3 'Introductie stagebedrijf' (8 min) · 14 → 15 dia's"*. The weekly *Wat heb ik gedaan* lists the same per document.
- **AI-gebruik:** when you used which AI tool and for how long, for chapter 6 of your portfolio.
- **Bevindingen per competentie:** your tagged notes and screenshots grouped per competency, for chapter 5.
- **Literatuurlijst (APA 7):** with *Geraadpleegd op*. It includes pages you read for at least 1 minute, videos watched for at least 1 minute, PDFs open for at least 30 seconds, and any source you wrote a note on. Authors and dates come from the page itself, so give them a quick check.

Headings use Word's built-in heading styles (*Kop 1/2/3*), so the navigation pane and an automatic table of contents work. `docs\voorbeeld-logboek-week10.docx` is an example export from a test session.

## What is recorded, and what isn't

| Recorded | Never recorded |
|---|---|
| Page title, link (tracking parameters such as `utm_*`, `fbclid` removed), time the page was actually in front of you | Page text, form input, passwords |
| YouTube: title, channel, length, seconds actually watched (ads excluded) | Private windows |
| Search terms on Google, Google Scholar, Bing, DuckDuckGo, Wikipedia, YouTube | Anything while Study Mode is off |
| AI tools (ChatGPT, Claude, Gemini, Copilot, Perplexity, Google AI Mode): **only which tool and how long** | Your prompts, the answers, chat titles |
| Foreground app + window title every 5 s: Word document name, Verkenner folder path, cloud/remote desktop session name | What happens *inside* a remote/cloud desktop. Use a quick note for that. |
| **Word / PowerPoint / Excel:** the heading your cursor is under, the slide number and title, the sheet name, how long you spent in each, and the word or slide count at the start and end. In Word this becomes, for example, *"schreef in '5.1.2 Leerproces' (12 min) · +240 woorden"* | The text you type in your documents and slides |
| | Excluded sites and apps (default: mail, WhatsApp, social media, banks, password managers) |

- Time stops counting when you switch away from Firefox, or after 2 minutes without input unless audio or video is playing. App tracking pauses after 5 minutes without input.
- Visits shorter than 10 seconds are dropped.
- If the app isn't running, the extension keeps up to 20,000 entries and sends them once the app is back.
- The local endpoint only accepts the extension and local tools. Ordinary web pages can't write to it.
- All data lives in `Documents\Studielogboek\`: `logboek.db` (SQLite), `screenshots\`, `exports\` and `companion.log`. Delete the folder to erase everything.

## Troubleshooting

- **Firefox popup says "app niet actief":** start `Studielogboek starten.vbs`.
- **A hotkey does nothing:** another program already uses it. The tray shows a notification at startup when that happens.
- **Debug log:** set the environment variable `STUDIELOGBOEK_DEBUG=1` before starting. Every received entry is then written to `companion.log`.

## Development

```bash
.venv/Scripts/python -m unittest discover -s companion/tests
```

```bash
node --test extension/test/background.test.mjs
```

```bash
cd extension && npx web-ext lint
```

```bash
cd companion && ../.venv/Scripts/python -m studielogboek export --week 10
```

- `companion/studielogboek/`: `server.py` (local endpoint), `tracker.py` (foreground windows), `ui.py` (tray, note and screenshot dialogs, export dialog), `export.py` (Word + APA), `db.py`, `config.py` (week 1 date, thresholds, note types, competencies).
- `extension/`: `background.js` (visit tracking, queue), `content/youtube.js` (watched seconds), `content/meta.js` (author/date for APA), `popup/`.
