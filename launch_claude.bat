@echo off
cd /d "C:\Users\drews\Life Org"
rem The trailing positional prompt is REQUIRED: without it the session sits idle
rem after the SessionStart hook (static ping fires, but no model turn runs until
rem a first message arrives) and the start-session summary never posts (#96).
start "claude-discord" /d "C:\Users\drews\Life Org" claude --permission-mode bypassPermissions --channels plugin:discord@claude-plugins-official "Auto-start (no human prompt yet): follow the SessionStart start-session instructions now and post the summary to Discord."
