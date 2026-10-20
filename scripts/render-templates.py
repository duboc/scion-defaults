#!/usr/bin/env python3
"""Render the agent templates in ../templates with your factory.env values. Runs on YOUR machine.

    scripts/render-templates.py                      # -> build/templates/<role>/ (git-ignored by build/)
    scripts/render-templates.py --out /tmp/tpl --roles lead,backend
    scripts/render-templates.py --file config/heartbeat-message.txt   # render one file to stdout

For every role folder in templates/ (folders starting with "_" are shared parts, not roles):
  * copies all files, replacing ${VAR} for the variables in factory.env plus the derived DEPLOYER_SA and
    RUNTIME_SA. Nothing else is touched: "$schema", "$HOME", "$$" and "${GITHUB_TOKEN:-}" stay as they are, so
    shell scripts and JSON files survive. (Python's string.Template would turn "$$" into "$".)
  * writes agents.md (the template's agent_instructions file) from templates/_shared/agents.md, the operating
    protocol every agent follows, followed by the role's own agents.extra.md when it has one.
It fails if a referenced variable has no value, so a half-filled factory.env can't produce broken prompts.

Upload the result as described in templates/README.md.
"""
import argparse
import os
import re
import shutil
import stat
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VARS = ("HUB_PROJECT HUB_ZONE HUB_VM HUB_OS_USER HUB_LOCAL_URL SANDBOX_PROJECT SANDBOX_REGION SPANNER_INSTANCE "
        "SPANNER_DATABASE BQ_DATASET DATA_BUCKET DEPLOYER_SA_NAME RUNTIME_SA_NAME SERVING_SERVICE DASHBOARD_SERVICE "
        "GITHUB_REPO BUILD_PROJECT EVAL_PROJECT BUILD_BRANCH EVAL_BRANCH PO_EMAIL DEPLOYER_SA RUNTIME_SA").split()
PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


def load_env(path):
    values = {}
    with open(path) as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    for name in ("DEPLOYER", "RUNTIME"):
        if values.get(f"{name}_SA_NAME") and values.get("SANDBOX_PROJECT"):
            values.setdefault(f"{name}_SA", f"{values[name + '_SA_NAME']}@{values['SANDBOX_PROJECT']}"
                                            ".iam.gserviceaccount.com")
    return {k: v for k, v in values.items() if k in VARS}


def render(text, values, missing, where):
    def repl(match):
        name = match.group(1)
        if name not in VARS:
            return match.group(0)
        if not values.get(name):
            missing.add(f"{name} (in {where})")
            return match.group(0)
        return values[name]
    return PLACEHOLDER.sub(repl, text)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--env", default=os.environ.get("FACTORY_ENV", os.path.join(ROOT, "config", "factory.env")))
    parser.add_argument("--src", default=os.path.join(ROOT, "templates"))
    parser.add_argument("--out", default=os.path.join(ROOT, "build", "templates"))
    parser.add_argument("--roles", help="comma-separated subset of roles (default: all)")
    parser.add_argument("--file", help="render this one file to stdout instead of the templates")
    args = parser.parse_args()

    if not os.path.exists(args.env):
        sys.exit(f"missing {args.env} (copy config/factory.env.example and fill it in)")
    values = load_env(args.env)
    if args.file:
        missing = set()
        text = render(open(args.file).read(), values, missing, args.file)
        if missing:
            sys.exit("missing values in factory.env:\n  " + "\n  ".join(sorted(missing)))
        sys.stdout.write(text.rstrip("\n"))
        return 0
    roles = sorted(d for d in os.listdir(args.src)
                   if os.path.isdir(os.path.join(args.src, d)) and not d.startswith("_"))
    if args.roles:
        wanted = args.roles.split(",")
        unknown = set(wanted) - set(roles)
        if unknown:
            sys.exit(f"unknown roles: {', '.join(sorted(unknown))}")
        roles = wanted
    shared_path = os.path.join(args.src, "_shared", "agents.md")
    shared = open(shared_path).read()

    missing = set()
    for role in roles:
        src_dir, out_dir = os.path.join(args.src, role), os.path.join(args.out, role)
        if os.path.exists(out_dir):
            shutil.rmtree(out_dir)
        for dirpath, _, files in os.walk(src_dir):
            for name in files:
                src = os.path.join(dirpath, name)
                rel = os.path.relpath(src, src_dir)
                if rel == "agents.extra.md":
                    continue
                dst = os.path.join(out_dir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(src) as f:
                    text = render(f.read(), values, missing, f"{role}/{rel}")
                with open(dst, "w") as f:
                    f.write(text)
                shutil.copymode(src, dst)
        extra_path = os.path.join(src_dir, "agents.extra.md")
        agents = render(shared, values, missing, "_shared/agents.md")
        if os.path.exists(extra_path):
            agents += "\n" + render(open(extra_path).read(), values, missing, f"{role}/agents.extra.md")
        with open(os.path.join(out_dir, "agents.md"), "w") as f:
            f.write(agents)
        for script in ("home/.local/bin/opencode-launch.sh", "home/.local/bin/hermes-launch.sh"):
            path = os.path.join(out_dir, script)
            if os.path.exists(path):
                os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"rendered {role} -> {out_dir}")

    if missing:
        sys.exit("missing values in factory.env:\n  " + "\n  ".join(sorted(missing)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
