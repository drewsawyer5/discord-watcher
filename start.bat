@echo off
:: Discord PA — start the voice watcher and the Claude channel session.
:: NOTE: process_ingest.py was REMOVED here on 2026-06-23 — ingest is owned
:: SOLELY by the NUC (discord-watcher.service). Running it on A6 too caused
:: every #inbox message to be double-processed. Do NOT re-add it.

:: Start voice_watcher.py minimized in background
start "voice-watcher" /min python "C:\Users\drews\Life Org\Drew_code\discord-watcher\voice_watcher.py"

:: Start Claude Discord channel session in a VISIBLE window (no /min, no hidden).
:: This .bat is the action for the Interactive "Discord Watcher" scheduled task,
:: so the window appears on the logged-in desktop.
start "claude-discord" /d "C:\Users\drews\Life Org" claude --permission-mode bypassPermissions --channels plugin:discord@claude-plugins-official
