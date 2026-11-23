#!/usr/bin/env python3
"""Fast (<15s) clean-room operator preflight (`scion-factory doctor`).

Checks local CLI prerequisites, minimum versions, and optional GitHub / GCP
authentication without ever printing secret values or mutating cloud state.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any

SEMVER_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")

MIN_VERSIONS: dict[str, tuple[int, int, int]] = {
    "python3": (3, 10, 0),
    "git": (2, 42, 0),
    "terraform": (1, 9, 0),
}

TOOL_REMEDIATIONS: dict[str, str] = {
    "python3": "Install Python 3.10+ (https://www.python.org/downloads/ or `uv python install 3.12`).",
    "git": "Upgrade Git to 2.42+ (https://git-scm.com/downloads).",
    "uv": "Install uv (https://docs.astral.sh/uv/getting-started/installation/).",
    "jq": "Install jq (`brew install jq` or `sudo apt-get install -y jq`).",
    "openssl": "Install OpenSSL (`brew install openssl` or `sudo apt-get install -y openssl`).",
    "gcloud": "Install the Google Cloud CLI (https://cloud.google.com/sdk/docs/install).",
    "gh": "Install GitHub CLI (`brew install gh` or https://cli.github.com/).",
    "terraform": "Install Terraform >= 1.9 (https://developer.hashicorp.com/terraform/install).",
    "kubectl": "Install kubectl (`gcloud components install kubectl --quiet`).",
    "gke-gcloud-auth-plugin": "Install the GKE auth plugin (`gcloud components install gke-gcloud-auth-plugin --quiet`).",
}

TOOL_VERSION_ARGS: dict[str, list[str]] = {
    "python3": ["--version"],
    "git": ["--version"],
    "uv": ["--version"],
    "jq": ["--version"],
    "openssl": ["version"],
    "gcloud": ["version"],
    "gh": ["--version"],
    "terraform": ["version"],
    "kubectl": ["version", "--client"],
    "gke-gcloud-auth-plugin": ["--version"],
}

MODE_TOOLS: dict[str, list[tuple[str, bool]]] = {
    # (tool_name, required)
    "local": [
        ("python3", True),
        ("git", True),
        ("uv", False),
        ("jq", False),
        ("openssl", False),
    ],
    "vm": [
        ("python3", True),
        ("git", True),
        ("uv", False),
        ("jq", True),
        ("openssl", True),
        ("gcloud", True),
        ("gh", True),
    ],
    "gke": [
        ("python3", True),
        ("git", True),
        ("uv", False),
        ("jq", True),
        ("openssl", True),
        ("gcloud", True),
        ("gh", True),
        ("terraform", True),
        ("kubectl", True),
        ("gke-gcloud-auth-plugin", True),
    ],
}


def parse_semver(text: str) -> tuple[int, int, int] | None:
    """Extract the first major.minor[.patch] version tuple from CLI output."""
    m = SEMVER_RE.search(text or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def _fmt_ver(ver: tuple[int, int, int]) -> str:
    return f"{ver[0]}.{ver[1]}.{ver[2]}"


def _run_quick(cmd: list[str], env: dict[str, str] | None = None, timeout: float = 8.0) -> tuple[int, str]:
    try:
        res = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        out = (res.stdout or "") + ("\n" + res.stderr if res.stderr else "")
        return res.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout:.0f}s"
    except OSError as exc:
        return 127, str(exc)


def _check_tool(tool: str, required: bool, env: dict[str, str]) -> dict[str, Any]:
    path_env = env.get("PATH", "")
    binary = shutil.which(tool, path=path_env)
    if not binary:
        return {
            "name": tool,
            "status": "fail" if required else "warn",
            "version": "",
            "detail": f"{tool} not found on PATH",
            "fix": TOOL_REMEDIATIONS.get(tool, f"Install {tool} and add it to PATH."),
        }

    check_env = env
    if tool == "kubectl":
        # Prevent kubectl-dispatcher from hanging on stale/unreachable kubeconfig clusters.
        check_env = {**env, "KUBECONFIG": os.devnull}

    rc, out = _run_quick([binary, *TOOL_VERSION_ARGS[tool]], env=check_env)
    first_line = next((ln.strip() for ln in out.splitlines() if ln.strip()), "")
    parsed = parse_semver(first_line or out)
    ver_str = _fmt_ver(parsed) if parsed else first_line[:60]

    min_ver = MIN_VERSIONS.get(tool)
    if rc != 0 and parsed is None:
        return {
            "name": tool,
            "status": "fail" if required else "warn",
            "version": "",
            "detail": f"{tool} failed version check (rc={rc}): {first_line[:80]}",
            "fix": TOOL_REMEDIATIONS.get(tool, f"Reinstall {tool}."),
        }

    if min_ver is not None:
        if parsed is None:
            return {
                "name": tool,
                "status": "warn",
                "version": ver_str,
                "detail": f"could not parse version for {tool} (expected >= {_fmt_ver(min_ver)})",
                "fix": TOOL_REMEDIATIONS.get(tool, ""),
            }
        if parsed < min_ver:
            return {
                "name": tool,
                "status": "fail" if required else "warn",
                "version": ver_str,
                "detail": f"{tool} {ver_str} is older than required >= {_fmt_ver(min_ver)}",
                "fix": TOOL_REMEDIATIONS.get(tool, f"Upgrade {tool} to >= {_fmt_ver(min_ver)}."),
            }

    return {
        "name": tool,
        "status": "ok",
        "version": ver_str,
        "detail": binary,
        "fix": "",
    }


def _check_gcloud_auth(env: dict[str, str]) -> dict[str, Any]:
    binary = shutil.which("gcloud", path=env.get("PATH", ""))
    if not binary:
        return {
            "name": "gcloud-auth",
            "status": "fail",
            "version": "",
            "detail": "gcloud CLI missing",
            "fix": TOOL_REMEDIATIONS["gcloud"],
        }
    rc, out = _run_quick(
        [binary, "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
        env=env,
    )
    accounts = [ln.strip() for ln in out.splitlines() if ln.strip() and "@" in ln]
    if rc == 0 and accounts:
        return {
            "name": "gcloud-auth",
            "status": "ok",
            "version": "",
            "detail": "active gcloud account configured",
            "fix": "",
        }
    return {
        "name": "gcloud-auth",
        "status": "fail",
        "version": "",
        "detail": "no active gcloud account found",
        "fix": "Run `gcloud auth login && gcloud auth application-default login`.",
    }


def _check_gh_auth(env: dict[str, str]) -> dict[str, Any]:
    # Never print or inspect secret token bytes; only confirm presence or gh auth status.
    if env.get("GITHUB_TOKEN") or env.get("GH_TOKEN"):
        return {
            "name": "gh-auth",
            "status": "ok",
            "version": "",
            "detail": "GitHub token environment variable is set",
            "fix": "",
        }
    binary = shutil.which("gh", path=env.get("PATH", ""))
    if not binary:
        return {
            "name": "gh-auth",
            "status": "fail",
            "version": "",
            "detail": "gh CLI missing and GITHUB_TOKEN unset",
            "fix": TOOL_REMEDIATIONS["gh"],
        }
    rc, out = _run_quick([binary, "auth", "status"], env=env)
    if rc != 0:
        return {
            "name": "gh-auth",
            "status": "fail",
            "version": "",
            "detail": "gh CLI is not authenticated and GITHUB_TOKEN is unset",
            "fix": "Run `gh auth login` (with `repo` scope; add `workflow` scope if backlog issues edit `.github/workflows/*`).",
        }
    scope_line = next((ln.strip() for ln in out.splitlines() if "Token scopes:" in ln), "")
    if scope_line and "repo" not in scope_line:
        return {
            "name": "gh-auth",
            "status": "warn",
            "version": "",
            "detail": "gh CLI authenticated, but `repo` scope was not detected",
            "fix": "Run `gh auth refresh -s repo` (or `-s repo,workflow` for CI workflow edits).",
        }
    return {
        "name": "gh-auth",
        "status": "ok",
        "version": "",
        "detail": "gh CLI authenticated",
        "fix": "",
    }


def run_doctor(
    mode: str = "gke",
    *,
    skip_auth: bool = False,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run deterministic clean-room prerequisite checks and return a structured report."""
    if mode not in MODE_TOOLS:
        raise ValueError(f"unsupported doctor mode: {mode!r} (expected one of {', '.join(MODE_TOOLS)})")
    start = time.monotonic()
    eff_env = dict(os.environ if env is None else env)
    eff_env.setdefault("CLOUDSDK_CORE_DISABLE_PROMPTS", "1")

    checks: list[dict[str, Any]] = []
    for tool, required in MODE_TOOLS[mode]:
        checks.append(_check_tool(tool, required, eff_env))

    if not skip_auth and mode in ("vm", "gke"):
        checks.append(_check_gcloud_auth(eff_env))
        checks.append(_check_gh_auth(eff_env))

    failures = [c for c in checks if c["status"] == "fail"]
    warnings = [c for c in checks if c["status"] == "warn"]
    return {
        "ok": len(failures) == 0,
        "mode": mode,
        "skip_auth": skip_auth,
        "checks": checks,
        "fail_count": len(failures),
        "warn_count": len(warnings),
        "elapsed_sec": round(time.monotonic() - start, 3),
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"scion-factory doctor (mode={report['mode']}, ok={report['ok']}, elapsed={report['elapsed_sec']}s)"
    ]
    for c in report["checks"]:
        tag = c["status"].upper().ljust(4)
        ver = f" [{c['version']}]" if c.get("version") else ""
        lines.append(f"  {tag}  {c['name']}{ver}: {c['detail']}")
        if c.get("fix") and c["status"] != "ok":
            lines.append(f"        fix: {c['fix']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check local clean-room prerequisites for scion-defaults.")
    parser.add_argument("--mode", choices=tuple(MODE_TOOLS), default="gke", help="Deployment mode to verify")
    parser.add_argument("--skip-auth", action="store_true", help="Skip live gcloud and gh authentication checks")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args(argv)

    report = run_doctor(mode=args.mode, skip_auth=args.skip_auth)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
