---
description: Set this repository up so its merges and deploys reach your alydash desk (adds a pull request template, a merge notes check and a board sync workflow; commits nothing)
disable-model-invocation: true
allowed-tools: Bash(node "${CLAUDE_PLUGIN_ROOT}/scripts/setup.mjs" --data "${CLAUDE_PLUGIN_DATA}")
---
!`node "${CLAUDE_PLUGIN_ROOT}/scripts/setup.mjs" --data "${CLAUDE_PLUGIN_DATA}"`

The lines above are what the alydash setup script printed. It has already written the files it names into this repository's working tree, and committed nothing.

Reply with the lines above exactly as they are, every line, in order. Then stop: do not commit, push or open a pull request for these files unless the person asks for that.
