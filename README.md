# News Digest

A local news-headline collection and multi-layer LLM digest application, with a Flask browser interface and Windows system-tray controls. Licensed under [MIT](<LICENSE>), copyright Wyakh. Dependencies retain their own licenses; see [third-party notices](<THIRD_PARTY.md>).

## Objectives and behavioral requirements

- Collect enabled publishers' headlines, retain persistent headline IDs and database history, process batches, synthesize related coverage and write a digest using the existing pipeline.
- Make digest history, headlines, notes, comments, starred chains, costs, source trends, settings and run status accessible locally.
- Start predictably from the same project folder through the batch file, locally built launcher, Start Menu or login Startup; do not silently select another port, attach to another server or stop another application's process.
- Preserve existing scraper behavior, IDs, database/file formats, algorithms, prompts, model assignments and request parameters. Launcher and maintenance work is not a new pipeline design or a promise of new features.
- Keep private configuration and history separate from public source. Use one authoritative working source/Git folder rather than maintaining competing runnable exports. Temporary harnesses and obsolete exports are not part of the intended source deliverable.

These requirements describe the intended contract; the evidence and known limits below distinguish it from what has actually been observed.

## Install on Windows

The supported preparation environment is 64-bit Windows with Python 3.13. The [dependency file](<requirements.txt>) contains 66 exact pins, including Windows tray support and Playwright. Those pins were installed and checked on Python 3.13 during local preparation. Chromium is a separate Playwright browser installation.

From a writable project folder in PowerShell:

```powershell
py -3.13 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m playwright install chromium
# Only for a new installation with no private config folder:
Copy-Item -Recurse config.example config
# Build the unsigned local launcher; this does not run the application:
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\launcher\build-launcher.ps1
```

Do **not** overwrite an existing private configuration with the examples. Before any paid run, review the copied resources:

- [Example main configuration](<config.example/config.json>): `openai_api_key` and `anthropic_api_key` are deliberately blank. Supply your own provider credentials and review the `models` and `llm_settings` entries for batch, synthesis and writer layers. Example model names are preserved settings, **not verified available models**.
- [Example sources](<config.example/sources.json>): all sources are disabled. Enable only sources you may lawfully access and respect publisher terms, robots policies and rate limits.
- [Example pricing](<config.example/pricing.json>): per-million-token estimates are **not verified current provider prices**. Review rates before relying on cost reports; historical run costs are stored separately.
- Preserve the five required prompts: [Western batch](<config.example/prompt_batch_western.txt>), [Huanqiu batch](<config.example/prompt_batch_huanqiu.txt>), [independent Chinese batch](<config.example/prompt_batch_chinese_indep.txt>), [synthesis](<config.example/prompt_synthesis.txt>) and [writer](<config.example/prompt_writer.txt>), plus the [Markdown stylesheet](<config.example/markdown-style.css>). These pipeline/rendering resources are supplied unchanged rather than redesigned for this maintenance work. Preserve prompt placeholders when editing.

The [launcher build script](<launcher/build-launcher.ps1>) compiles the included [C# launcher](<launcher/NewsDigestLauncher.cs>) into `NewsDigest.exe` using an **existing Windows .NET Framework v4 `csc.exe`**. The build downloads or installs nothing and requests no administrator elevation; the execution-policy override is limited to that build process. If the compiler is absent, the build fails rather than fetching one.

The resulting executable is locally built and **unsigned**, not a signed standalone Python application. It launches this folder's explicit `venv\Scripts\pythonw.exe`, passes the full tray-script path as a quoted argument and sets the working directory to the project root. It does not modify a Python executable, global PATH or machine policy. Its News Digest name, icon and metadata identify the launcher honestly, not a renamed Python binary. Inspect the source; Windows may warn about an unsigned executable. Rebuild after launcher-source changes, and recreate the venv/reinstall shortcuts if relocating the project. An activated venv is unnecessary for the commands shown here.

## User journeys

### Start and find the controls

```powershell
# Equivalent daily entry points after building the launcher:
.\news.bat
.\NewsDigest.exe
```

The [batch entry point](<news.bat>), executable, Start Menu shortcut and login Startup shortcut all lead to the same [tray entry point](<tray.py>) and launch settings. Starting the tray does not itself open a browser, scrape headlines or run AI.

1. Beside the Windows clock, open the up-arrow/hidden-icon overflow if needed.
2. Right-click **News Digest**.
3. Choose **Main page** to open the local browser interface, **Newest digest** for the latest digest, or **Notes page** for the existing notes view. Newest digest is disabled when none exists.
4. Use Main-page navigation for digest history, settings, headlines, costs, source trends and run status.
5. Use **Run scraper & AI digest** only when you intend to contact publishers and potentially incur provider charges. A recency warning may direct you to the browser Run page for confirmation.
6. Choose **Quit** to stop this instance's owned server and tray. Quit refuses while a run is active; let the run finish first.

An empty installation has no digest history until you deliberately generate or restore it. Source content and model outputs are external, untrusted material; check important claims against original reporting.

### Set up Start Menu and login Startup

The [shortcut helper](<setup_autostart.py>) operates on per-user shortcuts:

```powershell
.\venv\Scripts\python.exe .\setup_autostart.py status
.\venv\Scripts\python.exe .\setup_autostart.py install
.\venv\Scripts\python.exe .\setup_autostart.py remove
```

`install` creates **both** the Start Menu and login Startup entries and does not immediately launch the application. Launch from Start yourself; login Startup is intended for the next sign-in. `remove` removes the owned shortcuts, not your source, configuration or history. Reinstall shortcuts after moving the project. Windows Startup Apps display and Search indexing are Windows-managed behavior, not guaranteed by shortcut creation.

### Choose a port and diagnose startup

The public default is `http://127.0.0.1:5000`. Every supported entry point uses the same optional private launch configuration, whose [example](<config.example/launch.json>) is:

```json
{"port": 5000}
```

Copy/edit it as `config/launch.json`. If the file or `port` member is absent, the default is 5000. The value must be a JSON **integer from 1 through 65535**, not a boolean or string. The host is fixed at `127.0.0.1`; there is no network-host setting, automatic fallback or port scanning. Choose a different explicit port yourself if needed. No particular account's private override is a public default.

A Windows-session-local mutex identifies this project folder and port. A duplicate launch reports **already starting/running**, leaves the existing owner alone and does **not** open a browser or attach to a possibly unready listener. Another session/project or unrelated listener occupying the port causes a visible startup failure instead of a false success. No conflicting process is killed.

The tray path binds its server and waits for serving-loop readiness before presenting tray controls; browser actions therefore follow startup readiness rather than an arbitrary delay. Startup failures handled by the tray are appended to private `data/startup.log` where possible and shown in a native MessageBox even under windowless Python. Launcher failures before Python starts are also shown by MessageBox. Keep diagnostics private; they may contain local paths and operational details.

For a visible console, or for a separate web-only session when the port is free:

```powershell
# Console diagnostics for the normal tray/server entry point:
.\venv\Scripts\python.exe .\tray.py
# Alternative web-only entry point, not an additional tray instance:
.\venv\Scripts\python.exe .\web\app.py
```

The [web-only entry point](<web/app.py>) uses the same configured port and opens its browser after server readiness, but has no tray controls. Do not run it alongside the tray on the same port expecting attachment or fallback.

### Edit settings and preserve private history

Settings edits are local file mutations. The [settings helper](<web/settings.py>) validates registered JSON settings, snapshots an existing file before saving and retains up to ten timestamped backups per registered file under `web/backups/`, pruning older backups on save. Text prompts do not receive equivalent schema validation. A backup can be loaded into the editor before saving; this is not a transactional or unlimited undo guarantee. Keep independent backups before consequential changes.

Preserve the private `config/`, `data/` and `web/backups/` folders during source maintenance and upgrades. They include credentials, database/headline history, digest artifacts, costs, comments and starred chains. Do not merge example configuration or public sample material over a working account's history. Shortcut removal and public-source cleanup are not authorization to delete private runtime history.

## Known limits and safety

### Retention is not guaranteed by the recency dialog

The UI contains a 12-hour recency confirmation flow, but [the storage helper](<ai/digest_storage.py#L43-L64>) `find_recent_ai_run()` computes the maximum candidate run ID and then falls through to `None`. Automatic pre-run deletion through that helper is therefore inactive. This pre-existing behavior is **intentionally unchanged**: silently activating deletion would change retention semantics.

Do not interpret the UI confirmation as a data-retention guarantee. Run filenames have minute resolution, so runs in the same minute can collide and overwrite artifacts. Explicit delete actions remain destructive. Back up important history independently.

### Local and paid-operation boundary

- Run actions can contact real publishers and charge your configured model-provider account. Do not start a paid run merely to check installation.
- The interface has no authentication and exposes local settings/data mutations. Keep it loopback-only; do **not** expose it to a network, proxy it publicly or assume localhost makes untrusted content safe.
- Real provider calls and scraping have **not** been verified end to end for this preparation. Provider availability, prices, publisher layouts and access restrictions can change.
- Maintenance is deliberately bounded: log-tail memory is bounded, but each read still scans the file in O(file bytes), without caching; latest-digest selection is a single pass preserving the existing ordering. SDK-client close is best-effort and must preserve the primary exception or successful result. Completion-toast failure is isolated from run outcome and must not retry a successful paid run. These are maintenance constraints, not broad performance or reliability guarantees.

## Verification evidence and unverified behavior

During local acceptance in the current Windows session, the following were observed on the installed application:

- Start Menu launch started the tray and owned server; read-only pages and the latest digest returned HTTP 200.
- Duplicate Startup-shortcut and batch launches left the existing server owner unchanged.
- Graceful quit of the owned backend and subsequent relaunch were observed.
- Windows UI Automation exposed a News Digest button in the hidden-icon overflow.

The user subsequently visually confirmed that the Windows Startup menu looks correct; this is user-reported confirmation, separate from the backend/accessibility checks above. Acceptance remains narrower than full desktop coverage: tray context-menu item interaction, browser pixel rendering, logout/login startup execution and Windows Search indexing were **not verified**. HTTP responses do not establish visual correctness or prove every navigation action works.

Earlier synthetic offline checks passed within their exercised scope; their harnesses and exports are now deliberately removed rather than retained as a maintained automated suite. No exhaustive coverage, live end-to-end success or global guarantee is claimed.

## Privacy and source distribution

Keep private configuration, runtime data, backups, venvs, generated launchers, shortcuts and account-specific notes out of commits and archives. The [ignore rules](<.gitignore>) reduce accidents but do not prove staged contents or existing Git history are safe. Review the actual deliverable and reachable history before publication. Local consolidation or a local commit is not remote publication.
