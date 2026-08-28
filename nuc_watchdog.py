#!/usr/bin/env python3
"""nuc_watchdog.py — independent off-NUC watchdog (runs on A6, NOT on the NUC).

Monitors the NUC + its key services and is the safety net that survives a NUC
death. Behaviour (per Drew, 2026-06-22):
  - service DOWN (NUC reachable): auto-restart with backoff (won't fight a
    crashloop) + email alert WITH the last ~20 journal lines.
  - NUC unreachable (powered-off / hung): email alert for MANUAL power-cycle
    (no Wake-on-LAN yet — deferred option). DEBOUNCED so a transient
    Tailscale/SSH blip can't false-alarm (root cause of the 2026-06-22 false
    "NUC UNREACHABLE"): reachability is retried in-tick AND must fail across
    NUC_UNREACHABLE_DEBOUNCE consecutive ticks before any alert fires.
  - State-tracked so it alerts on change, not every tick.

Single-shot: schedule via Task Scheduler every ~5 min. No LLM in the loop.
Email goes through send_alert_email.sh -> gws (verified from Windows python).

Modes:  (none) = monitor+act ·  --check = read-only status, no email/restart ·
        --selftest = send one test email
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "nuc_watchdog_state.json"
EMAIL_SH = HERE / "send_alert_email.sh"
LOG_FILE = HERE / "nuc_watchdog.log"

NUC = os.environ.get("NUC_SSH", "nuc-ts")
SERVICES = os.environ.get("NUC_SERVICES", "pa-bot discord-watcher").split()
SSH_TIMEOUT = 10
MAX_RESTARTS_PER_WINDOW = 3          # backoff: stop fighting a crashloop
WINDOW_SECONDS = 3600
LOG_TAIL = 20
# Debounce for the unreachable check (added 2026-06-24 after a 1-sec Tailscale
# blip false-alarmed "NUC UNREACHABLE" while the NUC was fine, 57d uptime).
REACH_RETRIES = int(os.environ.get("NUC_REACH_RETRIES", "3"))        # in-tick attempts
REACH_RETRY_DELAY = int(os.environ.get("NUC_REACH_RETRY_DELAY", "3"))  # seconds between attempts
UNREACHABLE_DEBOUNCE = int(os.environ.get("NUC_UNREACHABLE_DEBOUNCE", "2"))  # consecutive failing ticks before alert
# Same idea for the per-service check: a transient `systemctl is-active` blip
# (SSH hiccup → empty/garbled output) must NOT trigger a disruptive restart +
# email. Retry in-tick; only a persistently non-active service is treated as down.
# (Added 2026-06-25 after false "was DOWN — auto-restart OK" alerts on healthy services.)
SVC_CHECK_RETRIES = int(os.environ.get("NUC_SVC_CHECK_RETRIES", "3"))
# The restart path is destructive (sudo systemctl restart) and, unlike the
# unreachable path, had no cross-tick debounce: one bad tick = one restart.
# Require this many consecutive ticks of non-active before restarting.
SVC_DOWN_DEBOUNCE = int(os.environ.get("NUC_SVC_DOWN_DEBOUNCE", "2"))
def _find_bash():
    # PATH-independent: scheduled tasks (S4U) have a different PATH where
    # which("bash") can resolve to a broken npm shim. Prefer known Git Bash.
    for p in (r"C:\Program Files\Git\usr\bin\bash.exe",
              r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
              os.environ.get("NUC_WATCHDOG_BASH", "")):
        if p and os.path.exists(p):
            return p
    return shutil.which("bash") or "bash"


BASH = _find_bash()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def log(msg):
    line = f"[{now_iso()}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"nuc_reachable": True, "services": {}}


def save_state(s):
    STATE_FILE.write_text(json.dumps(s, indent=2))


def ssh(args, timeout=SSH_TIMEOUT):
    """ssh <NUC> <args...> -> (returncode, stdout, stderr)."""
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", NUC] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 255, "", "ssh timeout"


def send_email(subject, body):
    try:
        r = subprocess.run([BASH, str(EMAIL_SH), subject], input=body, text=True,
                           capture_output=True, cwd=str(HERE), timeout=90)
        ok = r.returncode == 0 and '"id"' in r.stdout
        log(f"email {'sent' if ok else 'FAILED'}: {subject}"
            + ("" if ok else f" | out={r.stdout[-200:]} err={r.stderr[-200:]}"))
        return ok
    except Exception as e:
        log(f"email EXCEPTION: {e}")
        return False


def nuc_reachable():
    """True if the NUC answers SSH. Retries in-tick to ride out a transient
    Tailscale/SSH blip before the caller treats it as a real outage."""
    for attempt in range(1, REACH_RETRIES + 1):
        rc, _, _ = ssh(["true"])
        if rc == 0:
            return True
        if attempt < REACH_RETRIES:
            log(f"reachability attempt {attempt}/{REACH_RETRIES} failed — retrying in {REACH_RETRY_DELAY}s")
            time.sleep(REACH_RETRY_DELAY)
    return False


def svc_active(svc):
    """Return `systemctl is-active <svc>`, retrying to ride out transient blips.

    A single SSH/systemctl hiccup can return "" or a non-active string for a
    service that is actually fine; reporting that as down triggers a needless
    restart + alert. Retry in-tick and only report a non-active status if it
    persists across attempts.

    Returns None when the answer is unknown: an SSH transport failure (rc 255)
    says nothing about the service. `systemctl is-active` itself exits non-zero
    for a genuinely inactive service but still prints the status word, so
    "transport failed" is rc 255 with empty stdout — never conflate the two.
    """
    last = ""
    transport_failed = False
    for attempt in range(1, SVC_CHECK_RETRIES + 1):
        rc, out, _ = ssh(["systemctl", "is-active", svc])
        last = out.strip()
        transport_failed = (rc == 255 and not last)
        if last == "active":
            return last
        if attempt < SVC_CHECK_RETRIES:
            log(f"{svc} is-active={last!r} rc={rc} (attempt {attempt}/{SVC_CHECK_RETRIES}) — retrying in {REACH_RETRY_DELAY}s")
            time.sleep(REACH_RETRY_DELAY)
    if transport_failed:
        return None
    return last


def svc_log_tail(svc, n=LOG_TAIL):
    _, out, err = ssh(["journalctl", "-u", svc, "-n", str(n), "--no-pager", "-o", "short-iso"])
    return (out or err).strip()


def restart_svc(svc):
    rc, out, err = ssh(["sudo", "systemctl", "restart", svc], timeout=20)
    return rc == 0, (out + err).strip()


def within_window(ts):
    return bool(ts) and (time.time() - ts) < WINDOW_SECONDS


def run_check():
    """Read-only: print status, no email, no restart."""
    reach = nuc_reachable()
    print(f"NUC ({NUC}) reachable: {reach}")
    if not reach:
        return
    for svc in SERVICES:
        print(f"  {svc}: {svc_active(svc) or 'UNKNOWN (ssh transport failure)'}")


def main():
    if "--selftest" in sys.argv:
        send_email("[NUC Watchdog] SELFTEST", f"Watchdog selftest — email path OK.\n{now_iso()}")
        return
    if "--check" in sys.argv:
        run_check()
        return

    state = load_state()

    # 1) reachability — powered-off / hung NUC -> manual alert (no WoL yet).
    #    Debounced: a single failing tick (transient blip) must NOT alert. We
    #    require UNREACHABLE_DEBOUNCE consecutive failing ticks first.
    if not nuc_reachable():
        misses = state.get("unreachable_misses", 0) + 1
        state["unreachable_misses"] = misses
        if misses < UNREACHABLE_DEBOUNCE:
            save_state(state)
            log(f"NUC unreachable — miss {misses}/{UNREACHABLE_DEBOUNCE} (debounce, no alert yet)")
            return
        if state.get("nuc_reachable", True):          # alert once, on change
            send_email("[NUC Watchdog] NUC UNREACHABLE — manual power-cycle likely needed",
                       f"The NUC ({NUC}) failed SSH on {misses} consecutive checks "
                       f"(debounce threshold {UNREACHABLE_DEBOUNCE}), as of {now_iso()}.\n"
                       "Auto-recovery is not attempted for power/hang events (by design).\n"
                       "Action: check power/console; power-cycle if hung.")
        state["nuc_reachable"] = False
        save_state(state)
        log(f"NUC UNREACHABLE (confirmed after {misses} consecutive misses)")
        return
    # reachable — clear the debounce counter and announce recovery if needed
    if state.get("unreachable_misses", 0):
        log(f"NUC reachable again — clearing {state['unreachable_misses']} miss(es)")
    state["unreachable_misses"] = 0
    if not state.get("nuc_reachable", True):
        # Only consume the state transition if the all-clear actually left —
        # otherwise the mailbox's last word stays "power-cycle needed" forever
        # (happened 2026-07-11: the recovery email 401'd and was never resent).
        if send_email("[NUC Watchdog] NUC back online",
                      f"NUC ({NUC}) is reachable again as of {now_iso()}."):
            state["nuc_reachable"] = True
        else:
            log("NUC back online but all-clear email FAILED — leaving state down so it retries next tick")
    else:
        state["nuc_reachable"] = True

    # 2) per-service health
    for svc in SERVICES:
        st = state["services"].setdefault(svc, {"down": False, "restarts": [], "last_alert": 0})
        status = svc_active(svc)
        if status is None:
            log(f"{svc} status UNKNOWN (SSH transport failure) — no restart/alert this tick")
            continue
        if status == "active":
            if st.get("down"):
                send_email(f"[NUC Watchdog] {svc} recovered",
                           f"{svc} is active again as of {now_iso()}.")
            st["down"] = False
            st["down_misses"] = 0
            continue

        # non-active over a working transport — debounce before the destructive path
        misses = st.get("down_misses", 0) + 1
        st["down_misses"] = misses
        if misses < SVC_DOWN_DEBOUNCE:
            log(f"{svc} is-active={status!r} — miss {misses}/{SVC_DOWN_DEBOUNCE} (debounce, no restart yet)")
            continue
        st["down_misses"] = 0

        # DOWN
        tail = svc_log_tail(svc)
        st["restarts"] = [t for t in st.get("restarts", []) if within_window(t)]
        if len(st["restarts"]) < MAX_RESTARTS_PER_WINDOW:
            restart_svc(svc)
            st["restarts"].append(time.time())
            time.sleep(5)
            recovered = svc_active(svc) == "active"
            send_email(
                f"[NUC Watchdog] {svc} was DOWN — auto-restart {'OK' if recovered else 'FAILED'}",
                f"{svc} was not active at {now_iso()}.\n"
                f"Auto-restart #{len(st['restarts'])} of {MAX_RESTARTS_PER_WINDOW}/hr: "
                f"{'recovered' if recovered else 'STILL DOWN'}.\n\n"
                f"--- last {LOG_TAIL} log lines ---\n{tail}")
            st["down"] = not recovered
        else:
            if time.time() - st.get("last_alert", 0) > WINDOW_SECONDS:
                send_email(f"[NUC Watchdog] {svc} CRASHLOOPING — backoff hit",
                           f"{svc} restarted {MAX_RESTARTS_PER_WINDOW}x within the hour and is still down.\n"
                           f"Auto-restart paused to avoid fighting a crashloop. Manual attention needed.\n\n"
                           f"--- last {LOG_TAIL} log lines ---\n{tail}")
                st["last_alert"] = time.time()
            st["down"] = True
        log(f"{svc} DOWN handled (restarts in window: {len(st['restarts'])})")

    save_state(state)
    log("watchdog run complete")
    # Dead-man heartbeat: Kuma push monitor "Job: NUC watchdog tick" — silence
    # past 15 min means this watchdog stopped running (a crash exits before this).
    try:
        import urllib.request
        urllib.request.urlopen("http://100.99.196.69:3001/api/push/VYqtHb97nL", timeout=10)
    except Exception:
        pass


if __name__ == "__main__":
    main()
