#!/usr/bin/env python3
"""Create or update agent templates on the Hub through its API, safely. Runs on YOUR machine.

    scripts/push-templates.py --project psearch-eval --roles eval-lead,ui-craftsman            # dry run (default)
    scripts/push-templates.py --project psearch-eval --roles eval-lead,ui-craftsman --apply

Renders the roles from ../templates with factory.env (like render-templates.py), ships them to the Hub VM through
run-on-hub.sh and, for each role, in the given Scion project:
  * no template yet                            -> create it, upload the files, finalize
  * template with its own storage path         -> upload the changed files, finalize (what `scion templates push` does)
  * template whose storage path is another one's (L16) -> rename that row to <role>-legacy, so running agents keep
    resolving it by ID, and create a fresh <role>. It never uploads into a path that isn't the template's own: that
    would overwrite the other template's files, which is exactly what `scion templates push` does in that situation.
The files sent are Scion's default template from the VM (home/ dotfiles, skills/: the same base `scion templates
create` uses) overlaid with the rendered role files. Afterwards it prints a verification table and fails if any
template outside the requested roles changed.

Templates only affect agents when the agents are created (or reincarnated). Running agents are not touched, but an
agent restarted after its template changed is checked against a different content hash, so update templates when you
are about to recreate the agents that use them.
"""
import argparse
import base64
import glob
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

REMOTE = r'''
import base64, glob, hashlib, hmac, io, json, os, sqlite3, sys, tarfile, time, urllib.error, urllib.request

PAYLOAD, PROJECT, ROLES, APPLY = __ARGS__
SCION_HOME = os.path.expanduser(os.environ.get("SCION_HOME", "~/.scion"))
HUB_URL = os.environ.get("HUB_LOCAL_URL", "http://localhost:8080").rstrip("/")
CONFIG_FILES = ("agents.md", "system-prompt.md", "scion-agent.yaml")


def db():
    conn = sqlite3.connect(f"file:{SCION_HOME}/hub.db?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def token():
    # Short-lived admin JWT, as in scion-admin.py (personal access tokens can't do admin work; L10).
    user = db().execute("SELECT id, email, role FROM users WHERE email = ?", (os.environ.get("PO_EMAIL"),)).fetchone()
    if not user:
        user = db().execute("SELECT id, email, role FROM users WHERE role = 'admin' LIMIT 1").fetchone()
    if not user:
        sys.exit("PO_EMAIL is not a Hub user")
    secret = next(line.split("=", 1)[1].strip() for line in open(f"{SCION_HOME}/hub.env")
                  if line.startswith("SCION_SERVER_SESSION_SECRET="))
    key = hashlib.sha256(("scion-hub-signing-key:user_signing_key:" + secret).encode()).digest()
    b64 = lambda data: base64.urlsafe_b64encode(data).rstrip(b"=").decode()
    now = int(time.time())
    claims = {"iss": "scion-hub", "sub": user["id"], "aud": ["scion-hub-api"], "exp": now + 600, "nbf": now - 10,
              "iat": now, "uid": user["id"], "email": user["email"], "name": user["email"], "role": user["role"],
              "type": "access", "client": "web"}
    signing = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()) + "." + b64(json.dumps(claims).encode())
    return signing + "." + b64(hmac.new(key, signing.encode(), hashlib.sha256).digest())


TOKEN = None


def call(method, url, body=None, raw=None, headers=None):
    global TOKEN
    TOKEN = TOKEN or token()
    full_url = url if url.startswith("http") else HUB_URL + url
    hdrs = {}
    if full_url.startswith(HUB_URL):
        hdrs["Authorization"] = "Bearer " + TOKEN
    data = raw
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(full_url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode()
            return json.loads(text) if text.strip().startswith(("{", "[")) else text
    except urllib.error.HTTPError as err:
        sys.exit(f"{method} {url} -> HTTP {err.code}: {err.read().decode()[:400]}")


def sha(content):
    return "sha256:" + hashlib.sha256(content).hexdigest()


def upload(tid, files, paths):
    resp = call("POST", f"/api/v1/templates/{tid}/upload",
                {"files": [{"path": p, "size": len(files[p][0])} for p in sorted(paths)]})
    for info in resp["uploadUrls"]:
        call(info.get("method") or "PUT", info["url"], raw=files[info["path"]][0], headers=info.get("headers") or {})


def finalize(tid, files, harness):
    manifest = {"version": "1.0", "harness": harness, "files": [
        {"path": p, "size": len(c), "hash": sha(c), "mode": f"{m & 0o777:04o}"} for p, (c, m) in sorted(files.items())]}
    return call("POST", f"/api/v1/templates/{tid}/finalize", {"manifest": manifest})


def create(role, files, harness, pid, description):
    tid = call("POST", "/api/v1/templates", {"name": role, "harness": harness, "scope": "project", "projectId": pid,
                                             "description": description})["template"]["id"]
    upload(tid, files, files)
    return tid, finalize(tid, files, harness)


def main():
    files_by_role = {}
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(PAYLOAD)), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile():
                role, rel = member.name.split("/", 1)
                files_by_role.setdefault(role, {})[rel] = (tar.extractfile(member).read(), member.mode)

    home_matches = sorted(glob.glob(os.path.join(SCION_HOME, "workspace-storage", "hubs", "*", "templates", "global", "default", "home")))
    default_home_dir = home_matches[0] if home_matches and os.path.isdir(home_matches[0]) else os.path.join(SCION_HOME, "templates", "default", "home")
    if not os.path.isdir(default_home_dir):
        sys.exit(f"default home directory not found (checked workspace-storage and {SCION_HOME}/templates/default/home)")

    default_tpl_matches = sorted(glob.glob(os.path.join(SCION_HOME, "workspace-storage", "hubs", "*", "templates", "global", "default")))
    default_dir = default_tpl_matches[0] if default_tpl_matches and os.path.isdir(default_tpl_matches[0]) else f"{SCION_HOME}/templates/default"

    if os.path.isdir(default_dir):
        for dirpath, _, names in os.walk(default_dir):
            for name in names:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, default_dir)
                if rel not in CONFIG_FILES:
                    for role in ROLES:
                        files_by_role[role].setdefault(rel, (open(full, "rb").read(), os.stat(full).st_mode))

    for dirpath, _, names in os.walk(default_home_dir):
        for name in names:
            full = os.path.join(dirpath, name)
            rel = os.path.join("home", os.path.relpath(full, default_home_dir))
            for role in ROLES:
                files_by_role[role].setdefault(rel, (open(full, "rb").read(), os.stat(full).st_mode))

    project = db().execute("SELECT id FROM projects WHERE slug = ?", (PROJECT,)).fetchone()
    if not project:
        sys.exit(f"unknown project {PROJECT}")
    pid = project["id"]
    before = {r["id"]: r["content_hash"] for r in db().execute("SELECT id, content_hash FROM templates")}
    print(f"project {PROJECT} ({pid}), apply={APPLY}")

    touched = set()
    for role in ROLES:
        files = files_by_role[role]
        config = files["scion-agent.yaml"][0].decode()
        field = lambda key: next((l.split(":", 1)[1].split("#")[0].strip().strip('"') for l in config.splitlines()
                                  if l.startswith(key + ":")), "")
        harness, description = field("default_harness_config"), field("description")

        row = db().execute("SELECT id, storage_path, storage_uri, files FROM templates WHERE scope='project' AND scope_id=? AND slug=?",
                           (pid, role)).fetchone()
        is_isolated = False
        if row and row["storage_path"] and row["storage_uri"]:
            shared_cnt = db().execute(
                "SELECT count(*) as cnt FROM templates WHERE storage_path = ? AND id <> ? AND slug NOT LIKE '%-legacy'",
                (row["storage_path"], row["id"])
            ).fetchone()["cnt"]
            if shared_cnt == 0:
                is_isolated = True

        if row and is_isolated:
            touched.add(row["id"])
            remote = {f["path"]: f["hash"] for f in json.loads(row["files"] or "[]")}
            changed = sorted(p for p, (c, _) in files.items() if remote.get(p) != sha(c))
            dropped = sorted(set(remote) - set(files))
            if not changed and not dropped:
                print(f"{role}: up to date ({row['id']})")
                continue
            print(f"{role}: update in place {row['id']}: changed {changed or '-'}, dropped {dropped or '-'}")
            if APPLY:
                if changed:
                    upload(row["id"], files, changed)
                result = finalize(row["id"], files, harness)
                print(f"{role}: updated, status={result.get('status')} hash={result.get('contentHash')}")
            continue
        if row:
            touched.add(row["id"])
            legacy = role + "-legacy"
            if db().execute("SELECT 1 FROM templates WHERE scope='project' AND scope_id=? AND slug=?",
                            (pid, legacy)).fetchone():
                sys.exit(f"{role}: shares another template's storage and {legacy} already exists; resolve by hand")
            print(f"{role}: row {row['id']} uses storage it doesn't own ({row['storage_path']}): rename it to "
                  f"{legacy} and create a fresh {role} with {len(files)} files")
            if APPLY:
                call("PATCH", f"/api/v1/templates/{row['id']}", {
                    "name": legacy, "slug": legacy,
                    "description": "Legacy row sharing another template's files; kept so running agents' templateId "
                                   "still resolves. Do not start agents from it."})
        else:
            print(f"{role}: create with {len(files)} files")
        if APPLY:
            tid, result = create(role, files, harness, pid, description)
            touched.add(tid)
            print(f"{role}: created {tid}, status={result.get('status')} hash={result.get('contentHash')}")

    print("\nverification:")
    for role in ROLES:
        for r in db().execute("SELECT id, slug, status, storage_path, storage_uri, content_hash FROM templates WHERE scope='project' "
                              "AND scope_id=? AND slug IN (?, ?) ORDER BY slug", (pid, role, role + "-legacy")):
            shared_cnt = db().execute(
                "SELECT count(*) as cnt FROM templates WHERE storage_path = ? AND id <> ? AND slug NOT LIKE '%-legacy'",
                (r["storage_path"], r["id"])
            ).fetchone()["cnt"]
            own = bool(r["storage_path"] and r["storage_uri"] and shared_cnt == 0)
            print(f"  {r['slug']:<26} {r['status']:<8} own_storage={own} "
                  f"{r['id']} {r['content_hash']}")
    after = {r["id"]: r["content_hash"] for r in db().execute("SELECT id, content_hash FROM templates")}
    others = [tid for tid, h in before.items() if tid not in touched and after.get(tid) != h]
    if others:
        sys.exit(f"WARNING: templates outside the requested roles changed: {others}")
    print("other templates unchanged: yes")


main()
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--project", required=True, help="Scion project slug")
    parser.add_argument("--roles", required=True, help="comma-separated role folders from ../templates")
    parser.add_argument("--apply", action="store_true", help="make the changes (default: dry run)")
    args = parser.parse_args()
    roles = [r for r in args.roles.split(",") if r]

    user = os.environ.get("HUB_OS_USER", "scion")
    if not re.match(r"^[a-z_][a-z0-9_-]*$", user):
        sys.exit(f"invalid HUB_OS_USER: {user}")

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "templates")
        subprocess.run([sys.executable, os.path.join(HERE, "render-templates.py"), "--out", out, "--roles", ",".join(roles)],
                       check=True, stdout=subprocess.DEVNULL)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for role in roles:
                tar.add(os.path.join(out, role), arcname=role)
        payload = base64.b64encode(buf.getvalue()).decode()
        remote = REMOTE.replace("__ARGS__", repr((payload, args.project, roles, args.apply)))
        script = os.path.join(tmp, "push_templates_remote.py")
        with open(script, "w") as f:
            f.write(remote)
        return subprocess.run([os.path.join(HERE, "run-on-hub.sh"), script]).returncode


if __name__ == "__main__":
    sys.exit(main())
