# discord-watcher

Always-on background ingest processor for Drew's PA system.

## What it does

Polls Discord #inbox and processes two content types without needing a Claude session:

- **URLs** → fetches page content → Gemini Flash → wiki page in `6 - Wiki Hub/Sources/`
- **Voice messages** -> WhisperX tower via `WHISPER_ENDPOINT` (local faster-whisper fallback) -> Gemini Flash -> wiki note in `6 - Wiki Hub/Topics/`

A PowerShell supervisor (`supervisor.ps1`) keeps the watcher processes running and restarts them on crash.

## Key files

| File | Purpose |
|---|---|
| `process_ingest.py` | Main processor — polls Discord inbox, routes content, calls LLM, writes vault files |
| `discord_voice.py` | Shared Discord voice helper for `discord-watcher`, `pa-bot`, and the Codex bridge. Owns audio detection, WhisperX transcription, fallback local transcription, first-audio-only selection, and empty/garbled transcript guards. |
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
WHISPER_ENDPOINT=http://100.80.186.41:9001
```

Voice defaults:

- `discord_voice.py` loads the shared `Drew_code/.env` file.
- If `WHISPER_ENDPOINT` is set, voice transcription uses `POST /transcribe` on the tower.
- If `WHISPER_ENDPOINT` is empty, it falls back to local `faster-whisper`.
- Empty, too-short, and punctuation-only transcripts are rejected before routing to Codex, NovaNexis, or ingest.
- For v1, only the first audio attachment is processed. Extra audio and mixed non-audio attachments are ignored with an in-channel notice where applicable.

## Dependencies

- `faster-whisper` — local fallback voice transcription when `WHISPER_ENDPOINT` is unset
- `requests` — HTTP fetching
- `watchdog` — file system events
- See `requirements.txt` for full list

## Running

```bat
launch.bat             # cold start — all three services at once
launch_ingest.bat      # start ingest processor only
launch_watcher.bat     # start voice watcher only
launch_claude.bat      # start Claude session with Discord channels
debug.bat              # run ingest in foreground (for debugging)
```

The Task Scheduler runs `supervisor.ps1` every 5 minutes to keep everything alive.
