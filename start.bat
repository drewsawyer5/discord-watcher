@echo off
:: Discord PA — start both the watcher and Claude channel session

:: Start voice_watcher.py minimized in background
start "voice-watcher" /min python "C:\Users\drews\Life Org\Drew_code\discord-watcher\voice_watcher.py"

:: Start process_ingest.py minimized in background
start "process-ingest" /min python "C:\Users\drews\Life Org\Drew_code\discord-watcher\process_ingest.py"

:: Start Claude Discord channel session
start "claude-discord" /min claude --permission-mode bypassPermissions --channels plugin:discord@claude-plugins-official