"""
restart_claude.py — kills and relaunches Claude Code desktop app.
Called by the /restart-session skill. Must run as a detached background process.

Usage: python restart_claude.py
"""
import os
import subprocess
import time

CLAUDE_EXE = r"C:\Users\drews\.local\bin\claude.exe"
LIFE_ORG_DIR = r"C:\Users\drews\Life Org"
WAIT_BEFORE_KILL = 12   # seconds — give Claude time to finish its Discord post
WAIT_AFTER_KILL = 3     # seconds — let OS release resources before relaunch


def main():
    time.sleep(WAIT_BEFORE_KILL)

    # Kill claude.exe (suppressing errors if already gone)
    os.system("taskkill /F /IM claude.exe 2>nul")

    time.sleep(WAIT_AFTER_KILL)

    # Relaunch Claude Code in a new independent console
    subprocess.Popen(
        [CLAUDE_EXE],
        cwd=LIFE_ORG_DIR,
        creationflags=subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS,
        close_fds=True,
    )


if __name__ == "__main__":
    main()
