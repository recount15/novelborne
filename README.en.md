# Novelborne · 书中织梦

> **v3.0.0 development candidate — not an accepted production release.** Integration is ongoing. A matching version number is not evidence of functional, model-quality, security, or package acceptance.

Novelborne is an interactive novel simulator: models generate narrative, while code controls character context, source boundaries, tasks, and state commits.

## Documentation

- [Chinese user guide](docs/USER_MANUAL.md)
- [Code structure & features](docs/CODE_OVERVIEW.md) (Chinese)
- [中文](README.md)

## v3 operation and boundaries

1. **Database-only active character library.** A fresh database starts empty. Legacy JSON, Markdown, and recovery assets are not automatic runtime fallbacks. Existing private files do not prove that characters are present in the active library.
2. **Unified designer save.** Use the character-library save action and verify the server-confirmed revision. A separate persona save followed by a second character save is no longer the intended workflow. UI integration is still being validated.
3. **Prepare only means full-book preparation.** Upload a TXT you are entitled to use, verify chapter splitting, configure a model, and submit a `fullbook` preparation job. Follow its state; use cancellation/resume controls when available. Only `READY` means ready. Preparation alone must not create a game session or a played-library entry: prepared and played are different states.
4. **Reader search needs no model.** Exact and text-fuzzy searches inspect source text independently of model setup. Fuzzy means approximate text matching, not semantic search or AI answers. Open a result to inspect its chapter and highlight.
5. **Reader interview uses the end of the current chapter.** It is a separate conversation, not a game action or a way to change game resources. It may discuss events within that chapter, so finish reading it first. Missing applicable character data requires preparation; it is not permission to silently run paid extraction.
6. **Start from this chapter uses its beginning.** Return to setup, review the selected chapter and settings, then create a new session. It must not overwrite the existing game or preload events from later in the chapter.
7. **Agent-cluster generation is bounded.** Calls, retries, concurrency, deadlines, and budgets are limited. Code-level hard gates still apply; model approval cannot authorize invalid commits. Failed/cancelled work may already have incurred provider charges. Budget accounting is not a guaranteed invoice or currency quote.

These descriptions explain the implementation's user-facing boundaries, not a claim that every path has passed end-to-end acceptance. If a control is unavailable or a request fails, preserve the error and task identifier rather than assuming success.

## Quick start

Requirements: Python 3.10+, Node.js 18+. Install Python dependencies yourself (the script below only checks and prints a reminder — it never installs anything):

```bash
pip install -r requirements.txt
```

Then build and start in one step (on Windows you can also double-click `start.bat`):

```bash
python setup_and_run.py
```

The script checks the environment, builds the frontend, and starts the service at <http://127.0.0.1:21560/> (it falls back to an adjacent port if 21560 is busy). To run the steps manually instead:

```bash
cd frontend
npm ci
npm run build
cd ..
python run_app.py --host 127.0.0.1 --port 21560
```

Open <http://127.0.0.1:21560>. Configure an OpenAI-compatible model for generation/preparation/interviews. Send API keys in request bodies, never URL query strings; do not include them in screenshots, logs, exports, or bug reports. Reader text search requires no API key.

## Data, LAN, and upgrades

Runtime data uses the selected data directory, normally `var/`. Stop the service and back up the database, sessions, and uploads before migration. Keep legacy assets quarantined in a private backup location; do not automatically restore them into the active library or run force-overwrite recovery scripts.

For trusted LAN use, launch with `--host 0.0.0.0`, allow the selected firewall port, and use the correct adapter address. Session-bearing URLs and QR codes are private. Do not expose the service directly to the public Internet.

## Candidate acceptance

No verified screenshots are included here; no mockup is presented as a screenshot. Windows build recipes require fresh builds, clean-machine smoke tests, functional acceptance, and artifact privacy audits before distribution. No Android APK is claimed as verified.

Never distribute runtime databases, uploads, private recovery packages, credentials, or copyrighted manuscripts. A private working tree is not a sanitized public source package.

Licensed under the [GNU AGPL-3.0](LICENSE) (AGPL-3.0-or-later). AGPL covers this project's source code and built-in assets only, including its network-interaction source-sharing obligation (Section 13) when the software is offered over a network; user-imported manuscripts, characters, and generated content remain the property of their owners and are not licensed for redistribution.
