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
    scripts/run-on-hub.sh scripts/scion-admin.py project-create --slug project1 --git-url https://github.com/o/r.git \
        --branch main --mode shared --token-file '~/.factory-github-token'
    scripts/run-on-hub.sh scripts/scion-admin.py shared-dirs                  # scratchpad + knowledge-plane, dry run
    scripts/run-on-hub.sh scripts/scion-admin.py shared-dirs --apply
    scripts/run-on-hub.sh scripts/scion-admin.py scion start lead -t lead --project=psearch --harness-auth vertex-ai "Read your instructions."

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
import http.client
import json
import os
import re
import sqlite3
import subprocess
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


def api(method, path, body=None, timeout=60):
    global _TOKEN
    if _TOKEN is None:
        _TOKEN = _token()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(HUB_URL + path, data=data, method=method,
                                 headers={"Authorization": "Bearer " + _TOKEN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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


WORKSPACE_MODES = ("shared", "per-agent", "worktree-per-agent")


def read_token_file(path):
    """The GitHub token from a file on the VM (put there over SSH stdin; never on a command line or in factory.env)."""
    if not path:
        return ""
    with open(os.path.expanduser(path)) as f:
        return f.read().strip()


def project_create_body(slug, git_url, branch, mode, token):
    """POST /api/v1/projects body for a git-backed Hub project.

    `scion hub projects create` has no token flag, and a `shared` project is cloned at creation, so a private
    repository needs `githubToken` here (the Hub stores it as the project's GITHUB_TOKEN secret, injection
    "always"). The branch travels as the scion.dev/default-branch label, like the CLI sends it: the request has no
    branch field, and without the label the Hub clones `main` (lessons-learned.md L28).
    """
    if mode not in WORKSPACE_MODES:
        raise ValueError(f"unknown workspace mode {mode!r}: use one of {', '.join(WORKSPACE_MODES)}")
    if not git_url.startswith("https://"):
        raise ValueError(f"git URL {git_url!r}: use the https:// clone URL (the Hub clones over HTTPS with the token)")
    if mode == "shared" and not token:
        raise ValueError("a shared project is cloned at creation: pass --token-file with a GitHub token")
    body = {"name": slug, "slug": slug, "gitRemote": git_url, "workspaceMode": mode,
            "labels": {"scion.dev/default-branch": branch, "scion.dev/clone-url": git_url,
                       "scion.dev/source-url": git_url}}
    if token:
        body["githubToken"] = token
    return body


def cmd_project_create(args):
    if _db().execute("SELECT id FROM projects WHERE slug = ?", (args.slug,)).fetchone():
        print(f"{args.slug}: exists (workspace mode is fixed at creation; delete and recreate to change it)")
        return
    try:
        body = project_create_body(args.slug, args.git_url, args.branch, args.mode, read_token_file(args.token_file))
    except (OSError, ValueError) as e:
        sys.exit(str(e))
    # A shared project is cloned inside this request: a large monorepo takes minutes.
    try:
        status, out = api("POST", "/api/v1/projects", body, timeout=1800)
    except (http.client.RemoteDisconnected, ConnectionError, TimeoutError, urllib.error.URLError) as e:
        # The Hub closes the connection at its write timeout (60 s by default) but keeps cloning. Don't POST
        # again: wait for the Hub to finish (it seeds .scion/project-id) or to roll the project back.
        print(f"{args.slug}: the Hub closed the connection ({type(e).__name__}) at its write timeout; "
              "waiting for the clone to finish")
        pid = wait_for_shared_clone(args.slug, getattr(args, "wait", 1800))
        print(f"{args.slug}: created id={pid} mode={args.mode} branch={args.branch}")
        return
    project = out.get("project", out)
    print(f"{args.slug}: created HTTP {status} id={project.get('id')} mode={args.mode} branch={args.branch}")


def wait_for_shared_clone(slug, wait, poll=10):
    """Return the project ID once the Hub has seeded the shared checkout; exit if it rolled back or ran out of time.

    The Hub inserts the project row, clones, then writes <SCION_HOME>/projects/<slug>/.scion/project-id. A failed
    clone deletes the row again (handlers_projects_core.go, rollbackCreatedProject).
    """
    seen_row = False
    for _ in range(max(1, int(wait) // poll)):
        row = _db().execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()
        if row:
            seen_row = True
            marker = os.path.join(SCION_HOME, "projects", slug, ".scion", "project-id")
            try:
                with open(marker) as f:
                    if f.read().strip() == row["id"]:
                        return row["id"]
            except OSError:
                pass
        elif seen_row:
            sys.exit(f"{slug}: the Hub rolled the project back (the clone failed); see journalctl -u scion-hub")
        time.sleep(poll)
    sys.exit(f"{slug}: no seeded checkout after {wait}s; check journalctl -u scion-hub and "
             f"`scion-admin.py projects` before you retry")


# Shared directories: project-level folders mounted into every agent at /scion-volumes/<name>, shown as tabs next to
# "workspace" in the project's Files view. The factory uses `scratchpad` (read-write notes and briefs outside Git)
# and `knowledge-plane` (read-only for agents; factory-watchdog.py keeps it in sync with the factory's knowledge plane).
DEFAULT_SHARED_DIRS = "scratchpad,knowledge-plane:ro"
_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")  # Scion's shared-dir name rule (pkg/api/types.go isValidSlug)


def parse_dir_specs(text):
    """"scratchpad,knowledge-plane:ro" -> [{"name": "scratchpad", "read_only": False}, {..., "read_only": True}]."""
    dirs = []
    for spec in [s.strip() for s in text.split(",") if s.strip()]:
        name, _, mode = spec.partition(":")
        if not _SLUG.match(name) or mode not in ("", "ro", "rw"):
            raise ValueError(f"bad shared-dir spec {spec!r}: use <name>[:ro|:rw], name in lowercase letters, digits, dashes")
        dirs.append({"name": name, "read_only": mode == "ro"})
    if not dirs:
        raise ValueError("no shared directories given")
    return dirs


def plan_shared_dirs(existing, wanted):
    """Which wanted dirs to create, and the mode mismatches to report (never fixed automatically)."""
    have = {d["name"]: bool(d.get("read_only")) for d in existing}
    create, conflicts = [], []
    for d in wanted:
        if d["name"] not in have:
            create.append(d)
        elif have[d["name"]] != d["read_only"]:
            mode = lambda ro: "read-only" if ro else "read-write"
            conflicts.append(f"{d['name']} is {mode(have[d['name']])}, wanted {mode(d['read_only'])} "
                             "(fix it by hand: removing it deletes its files)")
    return create, conflicts


def default_projects(env):
    """FACTORY_PROJECTS, else BUILD_PROJECT and EVAL_PROJECT (both forwarded by run-on-hub.sh)."""
    projects = [p.strip() for p in env.get("FACTORY_PROJECTS", "").split(",") if p.strip()]
    return projects or [p for p in (env.get("BUILD_PROJECT"), env.get("EVAL_PROJECT")) if p]


def cmd_shared_dirs(args):
    try:
        wanted = parse_dir_specs(args.dirs)
    except ValueError as e:
        sys.exit(str(e))
    projects = [p for p in (args.projects or "").split(",") if p] or default_projects(os.environ)
    if not projects:
        sys.exit("no projects: pass --projects or set FACTORY_PROJECTS / BUILD_PROJECT+EVAL_PROJECT in factory.env")
    problems = 0
    for slug in projects:
        pid = project_id(slug)
        _, out = api("GET", f"/api/v1/projects/{pid}/shared-dirs")
        create, conflicts = plan_shared_dirs(out.get("sharedDirs") or [], wanted)
        for d in create:
            mode = "read-only" if d["read_only"] else "read-write"
            if args.apply:
                status, _ = api("POST", f"/api/v1/projects/{pid}/shared-dirs", d)
                print(f"{slug}: created {d['name']} ({mode}) HTTP {status}")
            else:
                print(f"{slug}: would create {d['name']} ({mode}) at /scion-volumes/{d['name']}")
        for c in conflicts:
            print(f"{slug}: CONFLICT {c}")
            problems += 1
        if not create and not conflicts:
            print(f"{slug}: ok ({', '.join(d['name'] for d in wanted)})")
    if not args.apply:
        print("dry run: nothing changed (add --apply). Agents get new mounts when their container is next created.")
    if problems:
        sys.exit(1)


def cmd_scion(args):
    """Run the scion CLI on the VM as the PO (admin): `scion-admin.py scion start lead -t lead --project=x "task"`.

    The CLI accepts the minted admin JWT as SCION_HUB_TOKEN (verified on nightly-20261022, lessons-learned.md L28);
    the token only lives in the child's environment and is never printed.
    """
    env = dict(os.environ, SCION_HUB_ENDPOINT=HUB_URL, SCION_HUB_TOKEN=_token())
    sys.exit(subprocess.run(["scion", *args.args], env=env).returncode)


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

    pc = sub.add_parser("project-create", help="create a git-backed Hub project (idempotent: skips an existing slug)")
    pc.add_argument("--slug", required=True)
    pc.add_argument("--git-url", required=True, help="https://github.com/<owner>/<repo>.git")
    pc.add_argument("--branch", required=True, help="the project's branch (BUILD_BRANCH or EVAL_BRANCH)")
    pc.add_argument("--mode", default="shared", help=f"workspace mode: {', '.join(WORKSPACE_MODES)} (default: shared)")
    pc.add_argument("--token-file", default="", help="file on the VM holding a GitHub token (required for shared)")
    pc.add_argument("--wait", type=int, default=1800,
                    help="seconds to wait for a shared clone after the Hub drops the connection (default: 1800)")
    pc.set_defaults(func=cmd_project_create)

    sd = sub.add_parser("shared-dirs", help="ensure the factory's shared directories exist in each project")
    sd.add_argument("--projects", help="comma-separated project slugs (default: FACTORY_PROJECTS, else "
                                       "BUILD_PROJECT,EVAL_PROJECT)")
    sd.add_argument("--dirs", default=DEFAULT_SHARED_DIRS, help=f"<name>[:ro|:rw],... (default: {DEFAULT_SHARED_DIRS})")
    sd.add_argument("--apply", action="store_true", help="create the missing directories (default: dry run)")
    sd.set_defaults(func=cmd_shared_dirs)

    sch = sub.add_parser("schedule", help="manage recurring message schedules (heartbeats)")
    sch.add_argument("action", choices=["list", "create", "update", "pause", "resume"])
    sch.add_argument("--project", required=True)
    sch.add_argument("--name")
    sch.add_argument("--cron", help='5-field cron in UTC, e.g. "*/15 * * * *"')
    sch.add_argument("--agent")
    sch.add_argument("--text")
    sch.set_defaults(func=cmd_schedule)

    cli = sub.add_parser("scion", help="run the scion CLI as the PO (admin), e.g. scion start <agent> -t <template> "
                                       "--project=<slug> \"<task>\"")
    cli.add_argument("args", nargs=argparse.REMAINDER, help="arguments for the scion CLI")
    cli.set_defaults(func=cmd_scion)

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
