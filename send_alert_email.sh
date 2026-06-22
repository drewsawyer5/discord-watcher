#!/usr/bin/env bash
# send_alert_email.sh — code-based out-of-band alert email via gws (Gmail API).
# Used by nuc_watchdog.py. No LLM in the loop.
# Usage:  echo "<body>" | bash send_alert_email.sh "<subject>"
set -euo pipefail

SUBJECT="${1:-[NUC Watchdog] alert}"
TO="${ALERT_TO:-drewsawyer5@gmail.com}"
BODY="$(cat)"

RAW=$(SUBJECT="$SUBJECT" BODY="$BODY" TO="$TO" python - <<'PY'
import base64, os, email.message
m = email.message.EmailMessage()
m['To'] = os.environ['TO']
m['From'] = os.environ['TO']
m['Subject'] = os.environ['SUBJECT']
m.set_content(os.environ['BODY'])
print(base64.urlsafe_b64encode(m.as_bytes()).decode())
PY
)

gws gmail users messages send --params '{"userId":"me"}' --json "{\"raw\":\"$RAW\"}"
