#!/usr/bin/env python3
"""PO/admin actions against the Scion Hub API. Runs ON the Hub VM, as the Hub's OS user.

Run it from your machine through run-on-hub.sh:

    scripts/run-on-hub.sh scripts/scion-admin.py projects
    scripts/run-on-hub.sh scripts/scion-admin.py message --project psearch --agent lead --text "PO -> lead: ..."
    scripts/run-on-hub.sh scripts/scion-admin.py schedule list --project psearch
    scripts/run-on-hub.sh scripts/scion-admin.py schedule create --project psearch --name psearch-lead-heartbeat \
        --cron "*/15 * * * *" --agent lead --text "$(cat config/heartbeat-message.txt)"
    scripts/run-on-hub.sh scripts/scion-admin.py schedule update --project psearch --name psearch-lead-heartbeat --text "..."
    scripts/run-on-hub.sh scripts/scion-admin.py schedule pause --project psearch --name psearch-lead-heartbeat
    scripts/run-on-hub.sh scripts/scion-admin.py experiments
    scripts/run-on-hub.sh scripts/scion-admin.py experiments --set web.terminal_workspace=false

Authentication: mints a 5-minute interactive user JWT for PO_EMAIL, signed with the key the Hub derives from
SCION_SERVER_SESSION_SECRET in ~/.scion/hub.env (sha256("scion-hub-signing-key:user_signing_key:" + secret)).
That is equivalent to an admin web session, so it only works on the VM and the token is never printed.
Personal access tokens (scion_pat_*) don't work here: hub-admin routes (/api/v1/admin/*) reject them and they
cannot author schedules. See lessons-learned.md (L10).
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

SCION_HOME = os.path.expanduser(os.environ.get("SCION_HOME", "~/.scion"))
HUB_DB = os.path.join(SCION_HOME, "hub.db")
HUB_ENV = os.path.join(SCION_HOME, "hub.env")
HUB_URL = os.environ.get("HUB_LOCAL_URL", "http://localhost:8080").rstrip("/")


def _db():
    conn = sqlite3.connect(f"file:{HUB_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def project_id(slug):
    row = _db().execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()
    if not row:
        sys.exit(f"unknown project slug: {slug}")
    return row["id"]


def _token():
    email = os.environ.get("PO_EMAIL")
    if not email:
        sys.exit("PO_EMAIL is not set (it comes from factory.env via run-on-hub.sh)")
    user = _db().execute("SELECT id, email, role FROM users WHERE email = ?", (email,)).fetchone()
    if not user:
        sys.exit(f"no Hub user with email {email}")
    secret = None
    with open(HUB_ENV) as f:
        for line in f:
            if line.startswith("SCION_SERVER_SESSION_SECRET="):
                secret = line.split("=", 1)[1].strip()
    if not secret:
        sys.exit(f"SCION_SERVER_SESSION_SECRET not found in {HUB_ENV}")
    key = hashlib.sha256(("scion-hub-signing-key:user_signing_key:" + secret).encode()).digest()

    def b64(data):
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    now = int(time.time())
    claims = {
        "iss": "scion-hub", "sub": user["id"], "aud": ["scion-hub-api"], "exp": now + 300, "nbf": now - 10,
        "iat": now, "uid": user["id"], "email": user["email"], "name": user["email"], "role": user["role"],
        "type": "access", "client": "web",
    }
    signing_input = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()) + "." + b64(json.dumps(claims).encode())
    signature = hmac.new(key, signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + b64(signature)


_TOKEN = None


def api(method, path, body=None):
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = _token()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(HUB_URL + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + _TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as err:
        sys.exit(f"{method} {path} -> HTTP {err.code}: {err.read().decode()[:500]}")


def cmd_projects(_args):
    for row in _db().execute("SELECT slug, id FROM projects ORDER BY slug"):
        print(f"{row['slug']}\t{row['id']}")


def cmd_message(args):
    pid = project_id(args.project)
    body = {"message": args.text}
    if args.interrupt:
        body["interrupt"] = True
    if args.wake:
        body["wake"] = True
    status, out = api("POST", f"/api/v1/projects/{pid}/agents/{args.agent}/message", body)
    print(f"HTTP {status} status={out.get('status')} message_id={out.get('message_id')} "
          f"agent_phase={out.get('agent_phase')}")


def _schedules(pid):
    _, out = api("GET", f"/api/v1/projects/{pid}/schedules")
    return out.get("schedules", []) if isinstance(out, dict) else out


def _find_schedule(pid, name):
    for sched in _schedules(pid):
        if sched.get("name") == name:
            return sched
    sys.exit(f"no schedule named {name}")


def cmd_schedule(args):
    pid = project_id(args.project)
    if args.action == "list":
        for s in _schedules(pid):
            payload = json.loads(s.get("payload") or "{}")
            print(f"{s.get('name')}\tcron={s.get('cronExpr')}\tstatus={s.get('status')}\tagent={payload.get('agentName')}"
                  f"\tnext={s.get('nextRunAt')}\tlast={s.get('lastRunAt')} ({s.get('lastRunStatus')})"
                  f"\truns={s.get('runCount')}\terrors={s.get('errorCount')}")
        return
    if args.action == "create":
        for flag in ("name", "cron", "agent", "text"):
            if not getattr(args, flag):
                sys.exit(f"schedule create needs --{flag}")
        status, out = api("POST", f"/api/v1/projects/{pid}/schedules", {
            "name": args.name, "cronExpr": args.cron, "eventType": "message",
            "agentName": args.agent, "message": args.text,
        })
        print(f"HTTP {status} id={out.get('id')} next={out.get('nextRunAt')}")
        return
    if not args.name:
        sys.exit(f"schedule {args.action} needs --name")
    sched = _find_schedule(pid, args.name)
    if args.action in ("pause", "resume"):
        status, _ = api("POST", f"/api/v1/projects/{pid}/schedules/{sched['id']}/{args.action}")
        print(f"HTTP {status} {args.name} {args.action}d")
        return
    if args.action == "update":
        body = {}
        if args.cron:
            body["cronExpr"] = args.cron
        if args.text or args.agent:
            payload = json.loads(sched.get("payload") or "{}")
            payload["agentName"] = args.agent or payload.get("agentName")
            payload["message"] = args.text or payload.get("message")
            body["payload"] = json.dumps(payload)
        if not body:
            sys.exit("schedule update needs --cron and/or --text/--agent")
        status, out = api("PATCH", f"/api/v1/projects/{pid}/schedules/{sched['id']}", body)
        print(f"HTTP {status} status={out.get('status')} next={out.get('nextRunAt')}")


def cmd_agent_start(args):
    pid = project_id(args.project)
    status, out = api("POST", f"/api/v1/projects/{pid}/agents/{args.agent}/start", {"forceResume": args.resume})
    print(f"HTTP {status}: {out}")


def cmd_experiments(args):
    _, current = api("GET", "/api/v1/admin/experiments")
    if not args.set:
        for exp in current.get("experiments", []):
            print(f"{exp['name']}\tenabled={exp['enabled']}\tdefault={exp['default']}\toverride={exp['override']}")
        return
    name, _, value = args.set.partition("=")
    if value not in ("true", "false", "default"):
        sys.exit("use --set NAME=true|false|default")
    override = None if value == "default" else value == "true"
    status, out = api("PUT", "/api/v1/admin/experiments",
                      {"overrides": {name: override}, "expected_revision": current.get("revision", 0)})
    enabled = {e["name"]: e["enabled"] for e in out.get("experiments", [])}.get(name)
    print(f"HTTP {status} {name} enabled={enabled} (users see it after reloading the page)")


def cmd_metrics(args):
    base_path = "/api/v1/metrics/dashboard"
    if args.project:
        pid = project_id(args.project)
        base_path = f"/api/v1/projects/{pid}/metrics/dashboard"
    views = ["summary", "sessions", "model-calls", "tokens"] if args.view == "all" else [args.view]
    results = {}
    for v in views:
        q = [f"view={v}"]
        if args.period:
            q.append(f"period={args.period}")
        path = f"{base_path}?{'&'.join(q)}"
        status, body = api("GET", path)
        results[v] = body
    out = results[views[0]] if len(views) == 1 else results
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        print(f"Metrics Dashboard ({base_path}, view={args.view}, period={args.period or 7}d):")
        print(json.dumps(out, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("projects", help="list project slugs and IDs").set_defaults(func=cmd_projects)

    msg = sub.add_parser("message", help="send a message to an agent as the PO")
    msg.add_argument("--project", required=True)
    msg.add_argument("--agent", required=True)
    msg.add_argument("--text", required=True)
    msg.add_argument("--interrupt", action="store_true", help="interrupt the harness first")
    msg.add_argument("--wake", action="store_true", help="resume a stopped/suspended agent first")
    msg.set_defaults(func=cmd_message)

    start = sub.add_parser("agent-start", help="start an agent via Hub API")
    start.add_argument("--project", required=True)
    start.add_argument("--agent", required=True)
    start.add_argument("--resume", action="store_true", help="force resume if in error phase")
    start.set_defaults(func=cmd_agent_start)

    sch = sub.add_parser("schedule", help="manage recurring message schedules (heartbeats)")
    sch.add_argument("action", choices=["list", "create", "update", "pause", "resume"])
    sch.add_argument("--project", required=True)
    sch.add_argument("--name")
    sch.add_argument("--cron", help='5-field cron in UTC, e.g. "*/15 * * * *"')
    sch.add_argument("--agent")
    sch.add_argument("--text")
    sch.set_defaults(func=cmd_schedule)

    exp = sub.add_parser("experiments", help="show or override hub experiments (feature flags)")
    exp.add_argument("--set", metavar="NAME=true|false|default")
    exp.set_defaults(func=cmd_experiments)

    met = sub.add_parser("metrics", help="query Hub metrics dashboard")
    met.add_argument("--project", help="project slug (optional)")
    met.add_argument("--view", default="summary", choices=["summary", "sessions", "model-calls", "tokens", "all"],
                     help="metrics dashboard view (default: summary)")
    met.add_argument("--period", type=int, help="period in days (1-90, default: 7)")
    met.add_argument("--json", action="store_true", help="output full raw JSON")
    met.set_defaults(func=cmd_metrics)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
