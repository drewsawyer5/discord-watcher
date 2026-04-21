@echo off
cd /d "C:\Users\drews\Life Org"
start "claude-discord" claude --permission-mode bypassPermissions --channels plugin:discord@claude-plugins-official /start-session
