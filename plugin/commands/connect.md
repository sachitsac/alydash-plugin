---
description: Connect this laptop to your alydash desk (on a desk with runs, sessions here then show on Now, each sending its branch, the first line of each prompt, its tool's name and its token count)
disable-model-invocation: true
allowed-tools: Bash(node "${CLAUDE_PLUGIN_ROOT}/scripts/connect.mjs" start --data "${CLAUDE_PLUGIN_DATA}")
---
!`node "${CLAUDE_PLUGIN_ROOT}/scripts/connect.mjs" start --data "${CLAUDE_PLUGIN_DATA}"`

The lines above are what the alydash connect script printed, and they are the whole of this turn.

Reply with the lines above exactly as they are, and nothing else: every line, in order, the last one included. The address and the code must reach the person unchanged, with nothing added before or after them.

Do not run any tool. The connection finishes in the background, and this plugin says how it ended the next time the person sends a message here.
