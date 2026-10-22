#!/usr/bin/env python3
"""Build and publish the <harness>-factory harness-configs that start agents in their monorepo product folder.

Runs on the Hub VM as the Hub OS user (it needs the `scion` CLI logged in as a Hub admin for --global sync). From
your machine, --via-hub ships it there through run-on-hub.sh with factory-cd.sh embedded:

    scripts/make-factory-harness-configs.py --via-hub              # dry run on the Hub: print the plan
    scripts/make-factory-harness-configs.py --via-hub --apply      # build and publish

For each harness (claude, antigravity, opencode, hermes) it copies ~/.scion/harness-configs/<harness> to
~/.scion/harness-configs/<harness>-factory (a fresh copy every run, so provisioner fixes from a Scion upgrade reach
it), installs config/harness/home/.local/bin/factory-cd.sh into the copy's home/.local/bin/, puts
["sh", "/home/scion/.local/bin/factory-cd.sh"] in front of its command.base, and publishes it with
`scion harness-config sync <harness>-factory --global`.

The bundled configs are never edited: Scion refreshes them from its binary on every Hub restart. factory-cd.sh does
nothing unless FACTORY_SUBDIR is set, so the -factory configs are safe for any template. Templates rendered with a
FACTORY_SUBDIR select them through ${CLAUDE_HARNESS} & co. (scripts/render-templates.py).
See docs/plans/2026-10-22-monorepo-factories.md.
"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCHER_SRC = os.path.join(os.path.dirname(HERE), "config", "harness", "home", ".local", "bin", "factory-cd.sh")
EMBEDDED_LAUNCHER = None  # base64 of factory-cd.sh; --via-hub fills it in, because run-on-hub.sh ships one file only
LAUNCHER = ["sh", "/home/scion/.local/bin/factory-cd.sh"]
HARNESSES = ("claude", "antigravity", "opencode", "hermes")
SUFFIX = "-factory"
BASE_LINE = re.compile(r"^(?P<indent>[ \t]+)base:[ \t]*(?P<list>\[.*\])[ \t]*$", re.M)


def harness_vars(subdir):
    """${<HARNESS>_HARNESS} for the templates: the -factory config when the factory has a product folder."""
    suffix = SUFFIX if subdir not in ("", ".") else ""
    return {f"{h.upper()}_HARNESS": h + suffix for h in HARNESSES}


def prefix_command_base(config_text):
    """Put the factory-cd launcher in front of command.base (a JSON-style flow list), once."""
    match = None
    in_command = False
    for line_match in re.finditer(r"^.*$", config_text, re.M):
        line = line_match.group(0)
        if re.match(r"^command:\s*$", line):
            in_command = True
            continue
        if in_command and line and not line[0].isspace() and not line.startswith("#"):
            in_command = False
        if in_command:
            m = BASE_LINE.match(line)
            if m:
                match = (line_match.start(), line_match.end(), m)
                break
    if match is None:
        raise ValueError("config.yaml has no command.base flow list (base: [\"...\"]) to wrap")
    start, end, m = match
    base = json.loads(m.group("list"))
    if base[:len(LAUNCHER)] == LAUNCHER:
        return config_text, False
    new_line = f"{m.group('indent')}base: {json.dumps(LAUNCHER + base)}"
    return config_text[:start] + new_line + config_text[end:], True


def build(scion_home, harness, apply):
    src = os.path.join(scion_home, "harness-configs", harness)
    dst = os.path.join(scion_home, "harness-configs", harness + SUFFIX)
    config_path = os.path.join(src, "config.yaml")
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"no bundled harness-config {harness} at {src}")
    with open(config_path) as f:
        text, _ = prefix_command_base(f.read())
    print(f"{harness} -> {harness + SUFFIX}: copy {src} to {dst}, command.base = {json.dumps(LAUNCHER)} + bundled base")
    if not apply:
        return dst
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=False,
                    ignore=shutil.ignore_patterns("*.bak.*", ".*.tmp-*", "__pycache__"))
    with open(os.path.join(dst, "config.yaml"), "w") as f:
        f.write(text)
    bin_dir = os.path.join(dst, "home", ".local", "bin")
    os.makedirs(bin_dir, exist_ok=True)
    launcher = os.path.join(bin_dir, "factory-cd.sh")
    with open(launcher, "wb") as f:
        f.write(launcher_bytes())
    os.chmod(launcher, 0o755)
    return dst


def launcher_bytes():
    if EMBEDDED_LAUNCHER:
        return base64.b64decode(EMBEDDED_LAUNCHER)
    with open(LAUNCHER_SRC, "rb") as f:
        return f.read()


def via_hub(argv):
    """Run this script on the Hub VM with factory-cd.sh embedded (run-on-hub.sh copies only the script)."""
    with open(os.path.abspath(__file__)) as f:
        source = f.read()
    embedded = base64.b64encode(launcher_bytes()).decode()
    source = source.replace("EMBEDDED_LAUNCHER = None", f"EMBEDDED_LAUNCHER = {embedded!r}", 1)
    runner = os.environ.get("RUN_ON_HUB", os.path.join(HERE, "run-on-hub.sh"))
    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(tmp, "make_factory_harness_configs_remote.py")
        with open(script, "w") as f:
            f.write(source)
        return subprocess.run([runner, script, *[a for a in argv if a != "--via-hub"]]).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scion-home", default=os.path.expanduser(os.environ.get("SCION_HOME", "~/.scion")))
    parser.add_argument("--harness", default=",".join(HARNESSES), help="comma-separated harnesses")
    parser.add_argument("--apply", action="store_true", help="write the copies (default: print the plan)")
    parser.add_argument("--no-sync", action="store_true", help="don't publish with `scion harness-config sync`")
    parser.add_argument("--via-hub", action="store_true", help="run on the Hub VM through run-on-hub.sh")
    args = parser.parse_args(argv)
    if args.via_hub:
        return via_hub(sys.argv[1:] if argv is None else argv)

    failed = []
    for harness in [h for h in args.harness.split(",") if h]:
        try:
            build(args.scion_home, harness, args.apply)
        except (OSError, ValueError) as e:
            print(f"ERROR {harness}: {e}", file=sys.stderr)
            failed.append(harness)
            continue
        if args.apply and not args.no_sync:
            cmd = ["scion", "harness-config", "sync", harness + SUFFIX, "--global"]
            print("+ " + " ".join(cmd))
            if subprocess.run(cmd).returncode != 0:
                failed.append(harness)
    if not args.apply:
        print("dry run: nothing written (add --apply)")
    if failed:
        print("failed: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
