@echo off
cd /d "C:\Users\drews\Life Org"
rem The positional prompt is REQUIRED: without it the session sits idle after
rem the SessionStart hook (static ping fires, but no model turn runs until a
rem first message arrives) and the start-session summary never posts (#96).
rem The prompt MUST come BEFORE the flags: --channels is variadic (it takes a
rem space-separated LIST of channel specs), so anything placed after it gets
rem eaten as a channel spec and claude exits instantly at arg parse
rem ("--channels entries must be tagged: ..."). That was the insta-quit both
rem times this was attempted (#96).
start "claude-discord" /d "C:\Users\drews\Life Org" claude "Auto-start (no human prompt yet): follow the SessionStart start-session instructions now and post the summary to Discord." --permission-mode bypassPermissions --channels plugin:discord@claude-plugins-official
