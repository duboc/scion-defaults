#!/usr/bin/env python3
"""Read-only preflight for an existing Scion Hub, before you add a factory to it. Runs on the Hub VM.

    scripts/run-on-hub.sh scripts/hub-preflight.py            # human-readable report
    scripts/run-on-hub.sh scripts/hub-preflight.py --json     # the same report as JSON

It reads factory.env values that run-on-hub.sh forwards (PO_EMAIL, BUILD_PROJECT, EVAL_PROJECT, FACTORY_PROJECTS) and
reports what the end-to-end setup needs from the Hub:
  * the Hub is healthy (GET /healthz) and its Scion version;
  * PO_EMAIL is a Hub user with the admin role (the scripts act as that user; the Hub creates users at first sign-in);
  * which harness images exist (scion-claude, scion-antigravity, scion-opencode, scion-hermes);
  * the harness-configs on the Hub, including the <harness>-factory ones from make-factory-harness-configs.py;
  * the existing Hub projects, and whether the new factory's project slugs are already taken;
  * the factories the watchdog and the status sync already cover, and the merged FACTORY_PROJECTS to install them
    with: installing either one rewrites its list, so leaving another factory out stops covering it.
It changes nothing. It exits 1 when the Hub is unhealthy or PO_EMAIL can't act as the admin, else 0.
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import urllib.request

HARNESSES = ("claude", "antigravity", "opencode", "hermes")


def parse_env(text):
    """KEY=value lines (an optional `export ` prefix, quotes stripped); comments and blank lines ignored."""
    values = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'\"")
    return values


def merge_projects(current, new):
    """The projects already covered, then the new ones, without duplicates."""
    merged = []
    for slug in list(current) + list(new):
        if slug and slug not in merged:
            merged.append(slug)
    return merged


def missing_images(present, harnesses=HARNESSES):
    """Harnesses whose scion-<harness> image is neither tagged locally nor under localhost/scion/."""
    return [h for h in harnesses
            if f"scion-{h}:latest" not in present and f"localhost/scion/scion-{h}:latest" not in present]


def _split(value):
    return [p.strip() for p in (value or "").split(",") if p.strip()]


def _has_github_token(path):
    """True when the Hub token file exists and is non-empty, without reading secret bytes."""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def build_report(scion_home, health, images, sync_env, po_email, new_projects, github_token_present=True):
    errors, warnings = [], []
    if not health or health.get("status") != "healthy":
        errors.append(f"the Hub is not healthy (GET /healthz: {health or 'no answer'})")

    conn = sqlite3.connect(f"file:{os.path.join(scion_home, 'hub.db')}?mode=ro", uri=True)
    user = conn.execute("SELECT email, role FROM users WHERE email = ?", (po_email,)).fetchone() if po_email else None
    existing = [r[0] for r in conn.execute("SELECT slug FROM projects WHERE slug <> 'global' ORDER BY slug")]
    conn.close()
    if not po_email:
        errors.append("PO_EMAIL is not set in factory.env")
    elif not user:
        errors.append(f"{po_email} is not a Hub user yet: sign in to the Hub web UI once with that account")
    elif user[1] != "admin":
        errors.append(f"{po_email} has role {user[1]!r}, not admin: add it to the Hub's admin_emails")

    for slug in new_projects:
        if slug in existing:
            warnings.append(f"project {slug} exists already: project-create skips it (its workspace mode is fixed)")
    if images is not None:
        for h in missing_images(images):
            warnings.append(f"no scion-{h} image: agents on the {h} harness can't start until you build it")
    if not github_token_present:
        warnings.append(
            "~/.factory-github-token is missing or empty: Git-backed project-create needs a PAT "
            "(include 'workflow' scope if agents edit .github/workflows/*)"
        )

    hc_dir = os.path.join(scion_home, "harness-configs")
    harness_configs = sorted(os.listdir(hc_dir)) if os.path.isdir(hc_dir) else []

    watchdog_env = os.path.join(scion_home, "watchdog.env")
    watchdog = _split(parse_env(open(watchdog_env).read()).get("FACTORY_PROJECTS")) if os.path.exists(watchdog_env) else []
    sync = _split(parse_env(sync_env).get("FACTORY_PROJECTS"))
    merged = merge_projects(merge_projects(watchdog, sync), new_projects)
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "scion_version": (health or {}).get("scionVersion"), "po_user": po_email if user else None,
            "github_token_present": bool(github_token_present),
            "existing_projects": existing, "harness_configs": harness_configs,
            "watchdog_projects": watchdog, "sync_projects": sync, "factory_projects": ",".join(merged)}


def _health(url):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/healthz", timeout=10) as resp:
            return json.loads(resp.read().decode())
    except (OSError, ValueError):
        return None


def _images():
    try:
        out = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return set(out.stdout.split()) if out.returncode == 0 else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scion-home", default=os.path.expanduser(os.environ.get("SCION_HOME", "~/.scion")))
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--skip-health", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--skip-docker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    new = _split(os.environ.get("FACTORY_PROJECTS")) or [p for p in (os.environ.get("BUILD_PROJECT"),
                                                                     os.environ.get("EVAL_PROJECT")) if p]
    sync_path = os.path.expanduser("~/factory-sync/sync.env")
    token_path = os.path.expanduser(os.environ.get("GITHUB_TOKEN_FILE", "~/.factory-github-token"))
    report = build_report(
        scion_home=args.scion_home,
        health={"status": "healthy", "scionVersion": "skipped"} if args.skip_health
        else _health(os.environ.get("HUB_LOCAL_URL", "http://localhost:8080")),
        images=None if args.skip_docker else _images(),
        sync_env=open(sync_path).read() if os.path.exists(sync_path) else "",
        po_email=os.environ.get("PO_EMAIL", ""), new_projects=new,
        github_token_present=_has_github_token(token_path))

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Scion {report['scion_version']}  PO admin: {report['po_user'] or 'MISSING'}")
        print(f"existing projects: {', '.join(report['existing_projects']) or '(none)'}")
        print(f"harness-configs:   {', '.join(report['harness_configs']) or '(none)'}")
        print(f"watchdog covers:   {', '.join(report['watchdog_projects']) or '(not installed)'}")
        print(f"sync covers:       {', '.join(report['sync_projects']) or '(not installed)'}")
        for e in report["errors"]:
            print(f"ERROR {e}")
        for w in report["warnings"]:
            print(f"WARN  {w}")
        print(f"\nInstall the watchdog and the sync with:\n  FACTORY_PROJECTS={report['factory_projects']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
