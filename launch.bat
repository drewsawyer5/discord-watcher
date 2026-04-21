@echo off
:: Discord PA — cold start: launches voice watcher, ingest processor, and Claude session
call "%~dp0launch_watcher.bat"
call "%~dp0launch_ingest.bat"
call "%~dp0launch_claude.bat"
