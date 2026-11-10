---
description: Let this laptop take the agent work you start on items on your alydash desk, each in a worktree beside this repository with Claude Code in auto mode; /alydash:runner stop ends it
disable-model-invocation: true
allowed-tools: Bash(node "${CLAUDE_PLUGIN_ROOT}/scripts/runner.mjs":*)
---
!`node "${CLAUDE_PLUGIN_ROOT}/scripts/runner.mjs" $ARGUMENTS --data "${CLAUDE_PLUGIN_DATA}"`

The lines above are what the alydash runner script printed, and they are the whole of this turn.

Reply with the lines above exactly as they are, every line, in order, and nothing else.

Do not run any tool. The runner works in the background, and it keeps working after this session ends.
