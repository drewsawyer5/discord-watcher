@echo off
:: Discord PA — start both the watcher and Claude channel session

:: Start watcher.py minimized in background
start "discord-watcher" /min python "C:\Users\drews\Life Org\Drew_code\discord-watcher\watcher.py"

:: Start Claude Discord channel session
start "claude-discord" claude --channels plugin:discord:claude-plugins-official