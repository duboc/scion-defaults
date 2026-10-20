#!/bin/sh
# Hermes launcher for the reviewer template (the harness-config's `command.base` runs
# `sh /home/scion/.local/bin/hermes-launch.sh`; this file ships in the template's home/ overlay).
# Hermes reads its provider and model from ~/.hermes/config.yaml, written here on every start. Hermes has no
# --prompt flag, so the initial task Scion passes is typed into the tmux pane once Hermes is up.
mkdir -p "$HOME/.hermes"

# Hermes env_loader.py loads ~/.hermes/.env with override=True, which overrides config.yaml.
# Ensure VERTEX_REGION is global and purge any stale regional values.
touch "$HOME/.hermes/.env"
grep -v '^VERTEX_REGION=' "$HOME/.hermes/.env" > "$HOME/.hermes/.env.tmp" 2>/dev/null || true
echo "VERTEX_REGION=global" >> "$HOME/.hermes/.env.tmp"
mv "$HOME/.hermes/.env.tmp" "$HOME/.hermes/.env"

export VERTEX_REGION=global
export GOOGLE_CLOUD_LOCATION=global

cat << YAML > "$HOME/.hermes/config.yaml"
provider: vertex
model: google/gemini-4-argon
hooks_auto_accept: true
vertex:
  project_id: ${HUB_PROJECT}
  region: global
YAML

TASK=""
while [ $# -gt 0 ]; do
  case "$1" in
    --prompt)
      shift
      TASK="${1:-}"
      ;;
    --continue)
      ;;
  esac
  shift 2>/dev/null || break
done

if [ -n "$TASK" ]; then
  (sleep 4 && tmux send-keys -t scion -l "$TASK" && tmux send-keys -t scion Enter) &
fi

exec hermes chat --yolo --accept-hooks --provider vertex -m google/gemini-4-argon
