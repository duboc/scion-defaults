#!/bin/sh
# factory-cd.sh — start the harness in the factory's product folder of a monorepo.
#
# Installed into the home/ of the Hub's harness-configs and put in front of their command.base by
# scripts/patch-harness-start-dir.py, so the harness command becomes:
#     sh /home/scion/.local/bin/factory-cd.sh <original command.base...> <scion args...>
#
# FACTORY_SUBDIR (set by the rendered template's env, or a project-scoped Hub env var) is the product folder
# relative to the repository root, e.g. src/project1. Unset, empty or "." runs the harness unchanged, so the patched
# harness-configs stay safe for every other project on the Hub.
#
# The agent still has the whole repository; only the folder the harness starts in changes. Scion has no
# working-directory setting of its own (the Docker runtime always uses --workdir /workspace or the worktree), see
# docs/plans/2026-10-22-monorepo-factories.md.
#
# Exit codes: 64 FACTORY_SUBDIR is absolute or leaves the repository; 66 the folder does not exist.

sub="${FACTORY_SUBDIR:-}"
sub="${sub#./}"
sub="${sub%/}"
if [ -z "$sub" ] || [ "$sub" = "." ]; then
  exec "$@"
fi

case "/$sub/" in
  //*|*/../*)
    echo "factory-cd: FACTORY_SUBDIR '$FACTORY_SUBDIR' must be a folder inside the repository, relative to its root" >&2
    exit 64
    ;;
esac

root="$(git rev-parse --show-toplevel 2>/dev/null)" || root=""
[ -n "$root" ] || root="$(pwd -P)"
if [ ! -d "$root/$sub" ]; then
  echo "factory-cd: product folder $root/$sub (FACTORY_SUBDIR=$sub) does not exist in this checkout" >&2
  exit 66
fi
cd "$root/$sub" || exit 66
target="$(pwd -P)"

# Antigravity fires only project-local hooks (<workspace>/.agents/hooks.json, written by its provisioner at the
# repository root). Carry them into the product folder so Scion's status hooks keep firing, and keep the copy out
# of Git. The provisioner rewrites the root copy on every start, so refresh ours whenever it differs.
if [ -f "$root/.agents/hooks.json" ]; then
  if ! cmp -s "$root/.agents/hooks.json" "$target/.agents/hooks.json"; then
    mkdir -p "$target/.agents" && cp "$root/.agents/hooks.json" "$target/.agents/hooks.json"
  fi
  common="$(git rev-parse --git-common-dir 2>/dev/null)" || common=""
  if [ -n "$common" ]; then
    case "$common" in /*) ;; *) common="$target/$common" ;; esac
    mkdir -p "$common/info"
    grep -qxF '.agents/' "$common/info/exclude" 2>/dev/null || echo '.agents/' >> "$common/info/exclude"
  fi
fi

# The provisioners pre-trust only /workspace; trust the product folder too so no trust prompt blocks the harness.
if command -v python3 >/dev/null 2>&1; then
  FACTORY_CD_TARGET="$target" python3 - <<'PY' || echo "factory-cd: could not pre-trust $target (continuing)" >&2
import json, os

target = os.environ["FACTORY_CD_TARGET"]
home = os.path.expanduser("~")


def update(path, change):
    if not os.path.isfile(path):
        return
    with open(path) as f:
        data = json.load(f)
    if change(data):
        tmp = path + ".factory-cd.tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)


def claude(data):
    projects = data.setdefault("projects", {})
    entry = projects.setdefault(target, {})
    if entry.get("hasTrustDialogAccepted") is True:
        return False
    entry["hasTrustDialogAccepted"] = True
    entry.setdefault("projectOnboardingSeenCount", 1)
    return True


def antigravity(data):
    trusted = data.get("trustedWorkspaces")
    if not isinstance(trusted, list):
        trusted = data["trustedWorkspaces"] = []
    if target in trusted:
        return False
    trusted.append(target)
    return True


update(os.path.join(home, ".claude.json"), claude)
update(os.path.join(home, ".gemini", "antigravity-cli", "settings.json"), antigravity)
PY
fi

exec "$@"
