#!/usr/bin/env python3
"""Ensure Antigravity hooks run cleanly with telemetry enabled and Gemini 4 Argon. Runs ON the Hub VM, as the Hub's OS user.

Telemetry was previously turned off in hooks because sciontool init (PID 1) had lacked SCION_GCP_PROJECT_ID,
causing the local OTLP receiver on 127.0.0.1:4317 to fail starting. Now that the pipeline starts properly and
listens on :4317/:4318, hooks flush immediately to loopback and exit without hanging.

Places checked and patched:
  1. All provision.py locations under ~/.scion:
     - ~/.scion/harness-configs/antigravity/provision.py
     - ~/.scion/workspace-storage/hubs/*/harness-configs/global/antigravity/provision.py
     - ~/.scion/project-configs/*/.scion/agents/*/home/.scion/harness/provision.py
  2. All hooks.json files:
     - ~/.scion/projects/*/.agents/hooks.json
  3. Pre-start hooks staged across all agent homes:
     - ~/.scion/project-configs/*/.scion/agents/*/home/.scion/hooks/pre-start.d/30-project-custom
  4. Project pre-start hooks registered and active in Hub DB / API:
     - agy-telemetry-off for each active project in hub.db

  python3 patch-antigravity-hooks.py           # check only (default): report what is patched
  python3 patch-antigravity-hooks.py --apply   # patch all idempotently; exit 0

Run through run-on-hub.sh: scripts/run-on-hub.sh scripts/patch-antigravity-hooks.py --apply
"""
import argparse
import base64
import datetime
import glob
import hashlib
import hmac
import json
import os
import py_compile
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

SCION_HOME = os.path.expanduser(os.environ.get("SCION_HOME", "~/.scion"))
HUB_DB = os.path.join(SCION_HOME, "hub.db")
HUB_ENV = os.path.join(SCION_HOME, "hub.env")
HUB_URL = os.environ.get("HUB_LOCAL_URL", "http://localhost:8080").rstrip("/")

PROVISIONER_PATTERNS = [
    os.path.join(SCION_HOME, "harness-configs", "antigravity", "provision.py"),
    os.path.join(SCION_HOME, "workspace-storage", "hubs", "*", "harness-configs", "global", "antigravity", "provision.py"),
    os.path.join(SCION_HOME, "project-configs", "*", ".scion", "agents", "*", "home", ".scion", "harness", "provision.py"),
]
HOOK_FILES_PATTERN = os.path.join(SCION_HOME, "projects", "*", ".agents", "hooks.json")
AGENT_HOMES_PATTERN = os.path.join(SCION_HOME, "project-configs", "*", ".scion", "agents", "*", "home")

OLD = '"| sciontool hook --dialect=antigravity"'
DISABLED_HOOK = '"| SCION_TELEMETRY_ENABLED=false sciontool hook --dialect=antigravity"'

ARGON_MODEL = "Gemini 4 Argon (High)"
OLD_FLASH = 'FLASH_MODEL = "Gemini 3.8 Flash (Medium)"'
NEW_FLASH = f'FLASH_MODEL = "{ARGON_MODEL}"'
OLD_PRO = 'PRO_MODEL = "Gemini 3.1 Pro (Low)"'
NEW_PRO = f'PRO_MODEL = "{ARGON_MODEL}"'

CUSTOM_HOOK_SCRIPT = """#!/bin/sh
f=/workspace/.agents/hooks.json
if [ -f "$f" ]; then
  sed -i 's/| SCION_TELEMETRY_ENABLED=false sciontool hook --dialect=antigravity/| sciontool hook --dialect=antigravity/g' "$f"
  sed -i 's/SCION_TELEMETRY_ENABLED=false //g' "$f"
fi

# Ensure settings.json has Gemini 4 Argon (High) and location: global
for s in "$HOME/.gemini/antigravity-cli/settings.json" "/home/scion/.gemini/antigravity-cli/settings.json"; do
  if [ -f "$s" ]; then
    python3 -c "
import json
try:
    with open('$s') as fp: d = json.load(fp)
    d['model'] = 'Gemini 4 Argon (High)'
    if 'gcp' in d and isinstance(d['gcp'], dict):
        d['gcp']['location'] = 'global'
    with open('$s', 'w') as fp: json.dump(d, fp, indent=2); fp.write('\\n')
except Exception: pass
"
  fi
done

export GOOGLE_CLOUD_LOCATION=global
exit 0
"""


def backup(path):
    bak = f"{path}.bak-{datetime.date.today():%Y%m%d}"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    return bak


def atomic_write(path, text, check=None):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="." + os.path.basename(path) + ".")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    if check:
        check(tmp)
    os.chmod(tmp, os.stat(path).st_mode & 0o777)
    os.replace(tmp, path)


def check_or_patch_provisioner(path, apply):
    if not os.path.exists(path):
        return f"{path}: not found"
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    new_src = src
    needs_patch = False

    # Remove SCION_TELEMETRY_ENABLED=false so sciontool hook flushes telemetry to loopback
    if "SCION_TELEMETRY_ENABLED=false sciontool hook" in new_src:
        new_src = new_src.replace("SCION_TELEMETRY_ENABLED=false sciontool hook", "sciontool hook")
        needs_patch = True
    if "SCION_TELEMETRY_ENABLED=false " in new_src:
        new_src = new_src.replace("SCION_TELEMETRY_ENABLED=false ", "")
        needs_patch = True

    is_antigravity = "antigravity" in path or "FLASH_MODEL" in new_src or "agy" in new_src
    if is_antigravity:
        if OLD_FLASH in new_src:
            new_src = new_src.replace(OLD_FLASH, NEW_FLASH)
            needs_patch = True
        if OLD_PRO in new_src:
            new_src = new_src.replace(OLD_PRO, NEW_PRO)
            needs_patch = True

        old_loc_secret = 'ctx.read_secret("GOOGLE_CLOUD_LOCATION", env_fallback=True)\n            or ctx.read_secret("GOOGLE_CLOUD_REGION", env_fallback=True)'
        new_loc_secret = 'ctx.read_secret("GOOGLE_CLOUD_LOCATION", env_fallback=True)\n            or "global"'
        if old_loc_secret in new_src:
            new_src = new_src.replace(old_loc_secret, new_loc_secret)
            needs_patch = True

        old_gcp_loc = '_gcp_location="${GOOGLE_CLOUD_LOCATION:-${GOOGLE_CLOUD_REGION:-global}}"'
        new_gcp_loc = '_gcp_location="${GOOGLE_CLOUD_LOCATION:-global}"'
        if old_gcp_loc in new_src:
            new_src = new_src.replace(old_gcp_loc, new_gcp_loc)
            needs_patch = True

        if '["agy", "--version"], text=True, timeout=5' in new_src:
            new_src = new_src.replace(
                '["agy", "--version"], text=True, timeout=5',
                '["agy", "--version"], text=True, timeout=30',
            )
            needs_patch = True

        if 'export GOOGLE_CLOUD_LOCATION="${_gcp_location:-global}"' in new_src:
            new_src = new_src.replace(
                'export GOOGLE_CLOUD_LOCATION="${_gcp_location:-global}"',
                'export GOOGLE_CLOUD_LOCATION="global"',
            )
            needs_patch = True

        if 'export GOOGLE_CLOUD_LOCATION=' not in new_src and 'exec agy' in new_src:
            new_src = new_src.replace(
                'exec agy',
                'export GOOGLE_CLOUD_LOCATION="global"\nexec agy'
            )
            needs_patch = True

        target_telemetry = "d['enableTelemetry'] = False"
        addition_model = f"d['enableTelemetry'] = False\nd['model'] = '{ARGON_MODEL}'"
        if target_telemetry in new_src and f"d['model'] = '{ARGON_MODEL}'" not in new_src:
            new_src = new_src.replace(target_telemetry, addition_model)
            needs_patch = True

    if not needs_patch:
        return f"{path}: patched"

    if not apply:
        return f"{path}: NOT patched"

    bak = backup(path)
    atomic_write(path, new_src, check=lambda p: py_compile.compile(p, doraise=True))
    return f"{path}: patched now (backup {bak})"


def hook_commands(data):
    for entries in data.get("scion-hooks", {}).values():
        for entry in entries:
            for hook in entry.get("hooks", [entry]):
                if "| sciontool hook" in hook.get("command", "") or "SCION_TELEMETRY_ENABLED=false" in hook.get("command", ""):
                    yield hook


def check_or_patch_hooks_file(path, apply):
    if not os.path.exists(path):
        return f"{path}: not found"
    data = json.load(open(path))
    pending = [h for h in hook_commands(data) if "SCION_TELEMETRY_ENABLED=false" in h.get("command", "")]
    if not pending:
        return f"{path}: patched (telemetry enabled)"
    if not apply:
        return f"{path}: NOT patched ({len(pending)} commands have telemetry disabled)"
    bak = backup(path)
    for hook in pending:
        hook["command"] = hook["command"].replace("SCION_TELEMETRY_ENABLED=false sciontool hook", "sciontool hook")
        hook["command"] = hook["command"].replace("SCION_TELEMETRY_ENABLED=false ", "")
    atomic_write(path, json.dumps(data, indent=2) + "\n")
    return f"{path}: re-enabled telemetry in {len(pending)} commands now (backup {bak})"


def check_or_stage_pre_start_hook(home_path, apply):
    res = []
    # 1. settings.json
    s_path = os.path.join(home_path, ".gemini", "antigravity-cli", "settings.json")
    if os.path.exists(s_path):
        try:
            with open(s_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            loc = d.get("gcp", {}).get("location") if isinstance(d.get("gcp"), dict) else None
            if d.get("model") != ARGON_MODEL or loc != "global":
                if apply:
                    d["model"] = ARGON_MODEL
                    if "gcp" in d and isinstance(d["gcp"], dict):
                        d["gcp"]["location"] = "global"
                    atomic_write(s_path, json.dumps(d, indent=2) + "\n")
                    res.append(f"{s_path}: patched settings.json")
                else:
                    res.append(f"{s_path}: NOT patched")
        except Exception as e:
            res.append(f"{s_path}: error {e}")

    # 2. agy-wrapper.sh
    w_path = os.path.join(home_path, ".scion", "harness", "agy-wrapper.sh")
    if os.path.exists(w_path):
        try:
            with open(w_path, "r", encoding="utf-8") as f:
                w_src = f.read()
            target_telemetry = "d['enableTelemetry'] = False"
            addition_model = f"d['enableTelemetry'] = False\nd['model'] = '{ARGON_MODEL}'"
            loc_target = '_gcp_location="${GOOGLE_CLOUD_LOCATION:-${GOOGLE_CLOUD_REGION:-global}}"'
            loc_replace = '_gcp_location="${GOOGLE_CLOUD_LOCATION:-global}"'
            w_needs = False
            new_w_src = w_src
            if target_telemetry in new_w_src and f"d['model'] = '{ARGON_MODEL}'" not in new_w_src:
                new_w_src = new_w_src.replace(target_telemetry, addition_model)
                w_needs = True
            if loc_target in new_w_src:
                new_w_src = new_w_src.replace(loc_target, loc_replace)
                w_needs = True
            if w_needs:
                if apply:
                    atomic_write(w_path, new_w_src)
                    res.append(f"{w_path}: patched agy-wrapper.sh")
                else:
                    res.append(f"{w_path}: NOT patched")
        except Exception as e:
            res.append(f"{w_path}: error {e}")

    # 3. 30-project-custom
    hook_dir = os.path.join(home_path, ".scion", "hooks", "pre-start.d")
    hook_path = os.path.join(hook_dir, "30-project-custom")
    if os.path.exists(hook_path):
        try:
            content = open(hook_path, "r", encoding="utf-8").read()
            is_exec = (os.stat(hook_path).st_mode & 0o700) == 0o700
            if "SCION_TELEMETRY_ENABLED=false sciontool hook" not in content and is_exec and "Gemini 4 Argon" in content:
                res.append(f"{hook_path}: patched (telemetry enabled, Argon verified)")
                return "\n".join(res)
        except Exception:
            pass
    if not apply:
        res.append(f"{hook_path}: NOT patched (missing or invalid)")
        return "\n".join(res)
    os.makedirs(hook_dir, exist_ok=True)
    with open(hook_path, "w", encoding="utf-8") as f:
        f.write(CUSTOM_HOOK_SCRIPT)
    os.chmod(hook_path, 0o700)
    res.append(f"{hook_path}: patched now")
    return "\n".join(res)


_TOKEN = None


def _token():
    email = os.environ.get("PO_EMAIL")
    conn = sqlite3.connect(f"file:{HUB_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    if email:
        user = conn.execute("SELECT id, email, role FROM users WHERE email = ?", (email,)).fetchone()
    else:
        user = conn.execute("SELECT id, email, role FROM users WHERE role = 'admin' LIMIT 1").fetchone()
    if not user:
        user = conn.execute("SELECT id, email, role FROM users LIMIT 1").fetchone()
    if not user:
        sys.exit("no user found in hub.db")
    secret = None
    if os.path.exists(HUB_ENV):
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


def ensure_project_pre_start_hooks(apply):
    lines = []
    if not os.path.exists(HUB_DB):
        return ["hub.db: not found"]

    conn = sqlite3.connect(f"file:{HUB_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='project_pre_start_hooks'")
    if not cursor.fetchone():
        return ["hub.db table project_pre_start_hooks: NOT FOUND"]

    projects = conn.execute("SELECT id, slug FROM projects WHERE slug <> 'global' ORDER BY slug").fetchall()
    for p in projects:
        pid = p["id"]
        pslug = p["slug"]
        row = conn.execute(
            "SELECT id, slug, status, script FROM project_pre_start_hooks WHERE project_id = ? AND slug = 'agy-telemetry-off'",
            (pid,),
        ).fetchone()

        is_ok = row and row["status"] == "active" and "SCION_TELEMETRY_ENABLED=false sciontool hook" not in (row["script"] or "") and "Gemini 4 Argon" in (row["script"] or "")
        if is_ok:
            lines.append(f"project {pslug}: hook agy-telemetry-off active (clean telemetry & Argon)")
            continue

        if not apply:
            lines.append(f"project {pslug}: hook agy-telemetry-off NOT clean")
            continue

        # Apply via Hub API
        status, res = api("GET", f"/api/v1/projects/{pid}/pre-start-hooks")
        existing_hooks = res.get("hooks", []) if isinstance(res, dict) else []
        matching = next((h for h in existing_hooks if h.get("slug") == "agy-telemetry-off"), None)

        hook_id = None
        if matching:
            hook_id = matching["id"]
            api("PUT", f"/api/v1/projects/{pid}/pre-start-hooks/{hook_id}", {
                "script": CUSTOM_HOOK_SCRIPT,
                "description": "Ensure Gemini 4 Argon and clean telemetry hooks",
            })
        else:
            status, created = api("POST", f"/api/v1/projects/{pid}/pre-start-hooks", {
                "name": "agy-telemetry-off",
                "slug": "agy-telemetry-off",
                "description": "Ensure Gemini 4 Argon and clean telemetry hooks",
                "script": CUSTOM_HOOK_SCRIPT,
            })
            hook_id = created.get("id")

        if hook_id:
            api("POST", f"/api/v1/projects/{pid}/pre-start-hooks/{hook_id}/activate")
            lines.append(f"project {pslug}: hook agy-telemetry-off updated/activated now")
        else:
            lines.append(f"project {pslug}: hook agy-telemetry-off UNEXPECTED failed to create")

    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="patch (default is check only)")
    args = parser.parse_args()

    lines = []

    # 1. Provisioners
    prov_files = []
    for pat in PROVISIONER_PATTERNS:
        prov_files.extend(glob.glob(pat))
    prov_files = sorted(set(prov_files))
    if not prov_files:
        lines.append("provisioners: none found")
    else:
        for p in prov_files:
            lines.append(check_or_patch_provisioner(p, args.apply))

    # 2. Hooks files
    hooks_files = sorted(glob.glob(HOOK_FILES_PATTERN))
    if not hooks_files:
        lines.append(f"no hooks.json under {HOOK_FILES_PATTERN}")
    else:
        for h in hooks_files:
            lines.append(check_or_patch_hooks_file(h, args.apply))

    # 3. Agent home pre-start hooks
    agent_homes = sorted(glob.glob(AGENT_HOMES_PATTERN))
    if not agent_homes:
        lines.append(f"no agent homes under {AGENT_HOMES_PATTERN}")
    else:
        for ah in agent_homes:
            lines.append(check_or_stage_pre_start_hook(ah, args.apply))

    # 4. Project pre-start hooks via Hub API / DB
    lines.extend(ensure_project_pre_start_hooks(args.apply))

    # 5. Patch running docker containers
    containers = [
        "psearch--frontend",
        "psearch-eval--eval-lead",
        "psearch-eval--eval-deployer",
        "psearch-eval--factory-analyst",
        "psearch-eval--search-evaluator",
        "psearch-eval--ui-craftsman",
    ]
    for c in containers:
        check_cmd = ["docker", "inspect", c]
        if subprocess.run(check_cmd, capture_output=True).returncode != 0:
            continue
        if args.apply:
            script = f'import json; p = "/home/scion/.gemini/antigravity-cli/settings.json"; d = json.load(open(p)); d["model"] = "{ARGON_MODEL}"; (d.get("gcp") or {{}})["location"] = "global"; json.dump(d, open(p, "w"), indent=2)'
            res = subprocess.run(["docker", "exec", "-u", "scion", c, "python3", "-c", script], capture_output=True, text=True)
            if res.returncode == 0:
                lines.append(f"container {c}: patched settings.json")
            else:
                lines.append(f"container {c}: failed to patch: {res.stderr.strip()}")

    print("\n".join(lines))

    failed = any("UNEXPECTED" in line or (not args.apply and ("NOT patched" in line or "NOT active" in line)) for line in lines)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
