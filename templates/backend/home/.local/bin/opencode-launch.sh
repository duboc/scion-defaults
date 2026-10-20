#!/bin/sh
# OpenCode launcher for the backend template (the harness-config's `command.base` runs
# `sh /home/scion/.local/bin/opencode-launch.sh`; this file ships in the template's home/ overlay).
#
# Model pin (lessons-learned.md L3, L26): OpenCode's models.dev catalog may resolve models to unavailable variants
# or wrong regions, causing 404s. Vertex AI Gemini 4 Argon requires global publisher locations and pinned catalog entries.
# The Scion provisioner pre-fetches the full catalog into ~/.cache/opencode/models.json, and OpenCode refreshes that
# file in the background, so a pin written once doesn't survive restarts. This launcher rewrites the pinned
# catalog on every start, after the provisioner and before OpenCode reads it. (The stock launcher deleted the file,
# which makes OpenCode fall back to its built-in snapshot.)
export GOOGLE_CLOUD_PROJECT="${HUB_PROJECT}"
export GOOGLE_CLOUD_LOCATION=global
export GOOGLE_CLOUD_REGION=global
export GOOGLE_VERTEX_PROJECT="${HUB_PROJECT}"
export GOOGLE_VERTEX_LOCATION=global
export VERTEXAI_PROJECT="${HUB_PROJECT}"
export VERTEX_LOCATION=global

mkdir -p "$HOME/.config/opencode" "$HOME/.local/state/opencode" "$HOME/.cache/opencode"
rm -f "$HOME/.config/opencode/.opencode.json"

cat << 'JSON' > "$HOME/.cache/opencode/models.json"
{
  "google-vertex": {
    "id": "google-vertex",
    "name": "Vertex",
    "env": ["GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"],
    "npm": "@ai-sdk/google-vertex",
    "models": {
      "gemini-4-argon": {
        "id": "gemini-4-argon",
        "name": "Gemini 4 Argon",
        "tool_call": true,
        "reasoning": true,
        "attachment": true,
        "temperature": true,
        "cost": {"input": 1.25, "output": 10},
        "limit": {"context": 1048576, "output": 65536}
      },
      "gemini-3.8-flash": {
        "id": "gemini-3.8-flash",
        "name": "Gemini 3.8 Flash",
        "tool_call": true,
        "reasoning": true,
        "attachment": true,
        "temperature": true,
        "cost": {"input": 0.15, "output": 0.6},
        "limit": {"context": 1048576, "output": 65536}
      }
    }
  }
}
JSON

cat << 'JSON' > "$HOME/.config/opencode/opencode.json"
{
  "$schema": "https://opencode.ai/config.json",
  "model": "google-vertex/gemini-4-argon",
  "small_model": "google-vertex/gemini-3.8-flash",
  "disabled_providers": ["google-vertex-anthropic", "opencode"]
}
JSON

cat << 'JSON' > "$HOME/.local/state/opencode/model.json"
{"recent": [{"providerID": "google-vertex", "modelID": "gemini-4-argon"}], "favorite": [], "variant": {}}
JSON

# OpenCode would treat GITHUB_TOKEN as a GitHub Copilot credential; Scion's git credential helper reads
# SCION_GIT_TOKEN instead, so git clone/push keep working.
if [ -n "${GITHUB_TOKEN:-}" ]; then
  export SCION_GIT_TOKEN="$GITHUB_TOKEN"
  unset GITHUB_TOKEN
fi
exec opencode --auto -m google-vertex/gemini-4-argon "$@"
