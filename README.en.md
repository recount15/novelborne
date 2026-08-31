# Novelborne · 书中织梦

大模型驱动的互动小说世界模拟器：AI 负责叙事表达，代码负责锚点、角色、任务、涟漪、收束力和状态一致性。

## Highlights

- Enhanced mode for long-running, high-fidelity original-text simulation.
- Structured blueprint, segment filling, per-slot grading, targeted refill, code assembly, and global prose polish.
- Six narrative-richness levels; ordinary mode exposes levels 1–2, and level 6 requires Agent generation.
- Opening distillation runs parallel plot, anchor, work-archive, and character-card extraction.
- Character state patches require exact evidence from the final narrative before relationship state is updated.
- Dynamic API concurrency with a hard cap of 10 and graceful queueing under provider limits.
- BSD 3-Clause licensed source distribution.

## Modes

| Mode | Source | Scope | Character/anchor runtime |
| --- | --- | --- | --- |
| Ordinary | Public work archive | Short, focused simulation | Lightweight legacy path |
| Enhanced | User-supplied complete TXT | Chapter-by-chapter long-running simulation | Full character, anchor, quest, ripple, nemesis, and persistence pipeline |

## Six narrative-richness levels

1. Light — about 400 characters
2. Brief — about 650 characters
3. Standard — about 950 characters
4. Rich — about 1,350 characters
5. Long-form — about 1,850 characters; Agent recommended
6. Epic — about 2,400 characters; Agent required

The levels describe scene richness rather than a promise of exact model output length. The engine uses slot contracts and segment windows, grades each generated slot independently, refills only failed slots, and safely falls back when a provider cannot satisfy a request.

## Quick start

Requirements: Python 3.10+, Node.js 18+.

```bash
pip install -r requirements.txt
cd frontend
npm install
npm run build
cd ..
python run_app.py
```

Open <http://127.0.0.1:8000>, configure an OpenAI-compatible provider and API key, then choose a mode and start a session.

API keys are held in process memory only. They are not written to state, saves, logs, databases, or release packages.

## LAN access and QR codes

The source launcher listens on `0.0.0.0` by default. On the same Wi-Fi network, click the phone icon in the top bar to view available adapter addresses and generate a session-preserving QR code.

```bash
python run_app.py --host 0.0.0.0 --port 8000
python run_app.py --host 127.0.0.1 --port 8000  # local-only
```

If a phone cannot connect:

- verify both devices are on the same Wi-Fi;
- do not use `--host 127.0.0.1` for LAN access;
- in the windowed build, do not use `--no-lan`;
- allow the executable and selected port through Windows Firewall;
- select the actual Wi-Fi adapter when multiple interfaces are shown.

The QR URL carries the server's original session identifier, so scanning it restores the same session instead of opening a new one.

## Windows builds

```bash
pip install pyinstaller pywebview pythonnet clr-loader
build\build_windows.bat
build\build_windows_windowed.bat
```

Distribute the complete output directories:

- `dist\FateEngine\` — browser/Web build;
- `dist\FateEngineWindowed\` — desktop window build.

Do not distribute only the executable. Runtime data is created in an adjacent `var/` directory.

## Testing

```bash
python -m unittest discover -s . -p "test_*.py"
cd frontend
npm run build
```

The repository includes deterministic unit tests, strengthened FakeClient playtests, HTTP playtest tools, and release build recipes.

## Architecture

```text
launcher
  -> core.server              FastAPI routes and static hosting
      -> core.app             session and round orchestration
          -> core.services    orchestration facades
              -> core.engine  deterministic mechanisms
                  -> assets    public prompts, rules, data, and richness templates
```

Important public service facades include opening distillation, turn pipeline, option generation, character state patches, and directive/cheat-code registration.

## Privacy and copyright boundary

- Runtime data, uploaded TXT files, saves, logs, databases, and private recovery packages are excluded from source and release artifacts.
- User-provided copyrighted manuscripts used for local validation remain on the user's machine and are never included in this repository or its releases.
- Do not publish API keys, session QR codes, or session-bearing URLs.
- Public examples and static assets must remain redistributable.

## License

Novelborne is released under the [BSD 3-Clause License](LICENSE).
