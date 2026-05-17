# discord-watcher

Always-on background ingest processor for Drew's PA system.

## What it does

Polls Discord #inbox and processes two content types without needing a Claude session:

- **URLs** → fetches page content → Gemini Flash → wiki page in `6 - Wiki Hub/Sources/`
- **Voice messages** → faster-whisper (local, CPU) → Gemini Flash → wiki note in `6 - Wiki Hub/Topics/`

A PowerShell supervisor (`supervisor.ps1`) keeps the watcher processes running and restarts them on crash.

## Key files

| File | Purpose |
|---|---|
| `process_ingest.py` | Main processor — polls Discord inbox, routes content, calls LLM, writes vault files |
| `voice_watcher.py` | File system watcher — monitors Discord inbox dir for new voice attachments |
| `supervisor.ps1` | PowerShell watchdog — keeps process_ingest.py and voice_watcher.py alive, auto-restarts on crash |
| `discord_client.py` | Discord REST API wrapper — `send_message()` used by other scripts |
| `restart.py` | Kills claude.exe and triggers supervisor relaunch — called by /restart-session skill |
| `session_start.py` | Posts session-started summary to Discord — runs via SessionStart hook in `~/.claude/settings.json` |
| `launch.bat` | Cold start — launches voice_watcher, process_ingest, and Claude in sequence |
| `launch_claude.bat` | Launches Claude Code with `--channels plugin:discord@claude-plugins-official` |
| `launch_ingest.bat` | Launches process_ingest.py |
| `launch_watcher.bat` | Launches voice_watcher.py |
| `debug.bat` | Runs process_ingest.py in foreground for debugging |
| `fix_tasks.ps1` | One-time admin script — fixes Task Scheduler task definitions (run as Administrator) |

## Codex bridge

Lane 1 Codex bridge files:

- `codex_discord_bridge.py` - Discord gateway for `#codex`
- `codex_exec.py` - stable default runner; sends each prompt through `codex exec`
- `codex_session.py` - experimental persistent Codex PTY session wrapper for later live TUI/status work
- `launch_codex_bridge.bat` - starts the bridge script

Environment:

```
CODEX_CHANNEL_ID=1475166363201962077
CODEX_WORKSPACE=C:\Users\drews\Life Org
CODEX_TURN_TIMEOUT_SECONDS=180
CODEX_SESSION_MODE=exec
CODEX_OUTPUT_DIR=C:\Users\drews\Life Org\Drew_code\discord-watcher-codex-bridge\codex_exec_outputs
```

`CODEX_SESSION_MODE=exec` is the stable path: Discord messages spawn `codex exec`, capture the final answer with `--output-last-message`, and send that text back to `#codex`. Set `CODEX_SESSION_MODE=pty` only when debugging the older live TUI bridge.

## Configuration

Copy `.env.example` to `.env` and set:

```
DISCORD_BOT_TOKEN=...
GENERAL_CHANNEL_ID=...           # #general channel ID (used by discord_client.py, session_start.py)
LLM_PROVIDER=gemini              # or openai_compat
GEMINI_API_KEY=...               # if using gemini
LLM_BASE_URL=...                 # if using openai_compat (Ollama, OpenRouter, etc.)
LLM_MODEL=gemini-2.0-flash
VAULT_ROOT=C:\Users\drews\Life Org\Obsidian
```

## Dependencies

- `faster-whisper` — local voice transcription (no API key needed)
- `requests` — HTTP fetching
- `watchdog` — file system events
- See `requirements.txt` for full list

## Running

```bat
launch.bat             # cold start — all three services at once
launch_ingest.bat      # start ingest processor only
launch_watcher.bat     # start voice watcher only
launch_claude.bat      # start Claude session with Discord channels
launch_codex_bridge.bat # start Codex Discord bridge for #codex
debug.bat              # run ingest in foreground (for debugging)
```

The Task Scheduler runs `supervisor.ps1` every 5 minutes to keep everything alive.
