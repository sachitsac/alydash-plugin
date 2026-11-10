// `/alydash:setup`. Puts into the repository it is run in the four things the
// desk needs from a repository before a merge and a deploy show up there by
// themselves:
//
//   .github/pull_request_template.md       the "What you'll notice" section,
//                                          which is the Timeline's entry
//   .github/workflows/merge-notes.yml      fails a pull request without one
//   .github/alydash/merge-notes*.mjs       that check, needing nothing installed
//   .github/workflows/sync.yml             tells the desk a deploy finished
//
// `/alydash:connect` puts a laptop's sessions on Now and nothing more. Without
// these files a connected repository's merges never reach the Timeline and its
// items never move to shipped, because nothing tells the desk to read it and
// its pull requests carry nothing for the desk to publish.
//
// It writes files and stops: nothing is committed, pushed or sent anywhere.
// A file that is already there and is not one this command wrote is kept as it
// is and named, so a repository's own template or workflow is never replaced.
//
// The desk's address is written into sync.yml, read from the credential
// `/alydash:connect` stored for this repository. It is no secret: the job
// proves who it is with a token GitHub signs for that run, and the desk
// accepts it only from a workflow file named sync.yml on main, in a
// repository one of its apps names.

import { execFile } from 'node:child_process';
import { mkdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readMergeNotes, shipsTrailer } from '../templates/merge-notes.mjs';
import { hasWork, isLive, isRevoked, markRevoked, pickCredential, pluginDataDir, sendsHeartbeat } from './lib/credentials.mjs';
import { connectionOf, deskRefusal, deskUrl, probeConnection } from './lib/desk.mjs';
import { repoOf } from './lib/repo.mjs';
import { HEADING, SYNC_FILE, TEMPLATE_FILE, existing, onThisLaptop } from './lib/setup-state.mjs';

const TEMPLATES = join(dirname(fileURLToPath(import.meta.url)), '..', 'templates');

export const NOT_A_REPO = 'Run /alydash:setup inside the GitHub repository you connected.';
export const NOT_CONNECTED =
  'This repository is not connected to a desk yet, so there is no desk to point it at. Run /alydash:connect here first, then /alydash:setup again.';

// ALYDAS-385. What setup says, and status says in place of the files, on a
// desk built without Work: it has no board, so there is nothing for these
// files to make a repository reach (`hasWork`, read off the scopes connect
// filed).
export const NOTHING_TO_SET_UP = 'This desk has no board, so there is nothing to set up here.';

// The first line of every workflow this command writes. A sync.yml or a
// merge-notes.yml that starts with it is ours and is brought up to date; one
// that does not is the repository's own and is left alone.
export const OURS = '# Installed by /alydash:setup.';

// The desk reads a deploy only from a workflow file of this name
// (`DEPLOY_WORKFLOW` in the desk's github-sync.ts), and takes a sync only from
// one of this name (`SYNC_WORKFLOW` in its actions-identity.ts).
const DEPLOY_FILE = '.github/workflows/deploy.yml';
const NOTES_FILE = '.github/workflows/merge-notes.yml';

const oneLine = (text) => String(text).replace(/\s+/g, ' ').trim();

// ALYDAS-391. Setup writes no sync workflow for a desk on this laptop
// (onThisLaptop, in setup-state.mjs) and says why, and status counts the sync
// as missing.
const originOf = (desk) => new URL(desk).origin;
const cannotReach = (desk) => `GitHub's runners cannot reach a desk at ${originOf(desk)}`;

// The `name:` of a workflow, as GitHub matches it in `workflow_run`, or null
// when the file gives none. A top-level key only, with its quotes and any
// trailing comment taken off.
export function workflowName(text) {
  const raw = /^name:[ \t]*(.*)$/m.exec(String(text))?.[1];
  if (raw === undefined) return null;
  const quoted = /^(["'])(.*?)\1/.exec(raw.trim());
  const name = quoted ? quoted[2] : raw.replace(/\s+#.*$/, '').trim();
  return name === '' ? null : name;
}

// sync.yml for a repository. With `deployName`, it runs after each successful
// run of that workflow on main, which is a deploy; with null, on each push to
// main. Both values are written as JSON strings, which YAML reads as they are,
// so neither can end its own line and start another key.
export function syncWorkflow({ desk, deployName }) {
  const afterDeploy = deployName !== null;
  const on = afterDeploy
    ? [
        '  # After the deploy workflow completes on main, so the deploy this sync is',
        '  # about is already in the list of successful runs the desk reads. The name',
        "  # is that workflow's own `name:`; rename it there and this never fires.",
        '  workflow_run:',
        `    workflows: [${JSON.stringify(deployName)}]`,
        '    types: [completed]',
        '    branches: [main]',
      ]
    : [
        '  # This repository had no .github/workflows/deploy.yml when this was',
        '  # installed, so the board is synced on each push to main.',
        '  push:',
        '    branches: [main]',
      ];
  const when = afterDeploy
    ? [
        '    # Only a deploy that succeeded, started by a push or by hand, from this',
        '    # repository and not a fork.',
        '    if: >-',
        "      github.event_name == 'workflow_dispatch' ||",
        "      (github.event.workflow_run.conclusion == 'success' &&",
        "       (github.event.workflow_run.event == 'push' ||",
        "        github.event.workflow_run.event == 'workflow_dispatch') &&",
        '       github.event.workflow_run.head_repository.full_name == github.repository)',
      ]
    : [];
  return [
    `${OURS} The alydash board follows this repository with nobody`,
    '# pressing anything: one call to the desk, once per deploy.',
    '#',
    '# The desk reads the pull requests merged since its last look, publishes each',
    '# one\'s "What you\'ll notice" section on the Timeline, and moves the items named',
    '# on a `Ships:` line.',
    '#',
    '# There is no secret here. The job asks GitHub for an OpenID Connect token for',
    "# this run, with the desk's address as the audience, and sends that. The desk",
    "# checks GitHub's signature and takes the call only from this file, named",
    '# sync.yml, on main, in a repository one of its apps names. Do not rename this',
    '# file.',
    '',
    'name: sync the board',
    '',
    'on:',
    ...on,
    '  # By hand, from the Actions tab, for a deploy that went out while the desk',
    '  # was down. Start it from main: the desk refuses a token for another branch.',
    '  workflow_dispatch:',
    '',
    '# Queued, never cancelled: two syncs of one board at once would race.',
    'concurrency:',
    '  group: board-sync',
    '  cancel-in-progress: false',
    '',
    'permissions:',
    '  id-token: write',
    '',
    'jobs:',
    '  sync:',
    ...when,
    '    runs-on: ubuntu-latest',
    '    timeout-minutes: 10',
    '    steps:',
    '      - name: Sync the board from the repository',
    '        env:',
    `          DESK_URL: ${JSON.stringify(desk)}`,
    '          REPOSITORY: https://github.com/${{ github.repository }}',
    '        run: |',
    '          ID_TOKEN=$(curl -sS -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=$DESK_URL" | jq -r \'.value // empty\') || true',
    '',
    '          if [ -z "$ID_TOKEN" ]; then',
    '            echo "::error::GitHub gave this run no OIDC token, so the board was not synced. The job needs permissions: id-token: write."',
    '            exit 1',
    '          fi',
    '          echo "::add-mask::$ID_TOKEN"',
    '',
    '          code=$(curl --silent --show-error --max-time 300 \\',
    "            --output sync.json --write-out '%{http_code}' \\",
    '            --request POST "$DESK_URL/api/work/sync" \\',
    '            --header "authorization: Bearer $ID_TOKEN" \\',
    "            --header 'content-type: application/json' \\",
    '            --data "{\\"repository\\":\\"$REPOSITORY\\"}") || code="000"',
    '',
    '          echo "The desk answered $code:"',
    '          cat sync.json || true',
    '          echo',
    '',
    '          if [ "$code" != "200" ]; then',
    '            echo "::error::The board sync answered $code, so the board is behind the repository. A 401 means the desk does not know this repository: give one of its apps this repository on Connections."',
    '            exit 1',
    '          fi',
    '',
    '          if grep -q \'"refusal":"\' sync.json; then',
    '            echo "::error::The sync ran and could not move at least one item. The desk has raised an Ask with the detail."',
    '            exit 1',
    '          fi',
    '',
  ].join('\n');
}

// ALYDAS-127, S6 (the plan's P3). run.yml for a repository: what the desk
// dispatches when a person presses Start with agent on an item and picks the
// GitHub Actions runner. Its two inputs are the start and the item. The job
// trades its OIDC token (run.yml on main, the desk as the audience) at
// /api/github/run-token for a desk token bound to that one start, installs the
// alydash plugin, and runs the start's prompt in Claude Code on the person's
// own subscription (the CLAUDE_CODE_OAUTH_TOKEN secret, which
// `claude setup-token` prints). No desk secret is in it. The inputs reach the
// scripts only through `env`, never written into a script, so neither can end
// a line of shell. The desk URL is a JSON string, as in sync.yml.
//
// claude-code-action's inputs are its v1.0.270 action.yml's: `prompt`,
// `claude_code_oauth_token`, `plugin_marketplaces` (git URLs, one a line),
// `plugins` (`name@marketplace`, one a line) and `claude_args`.
//
// ALYDAS-127, S7. Claude runs in auto permission mode, as on the laptop (Ask
// 52e9d54d), as a session the trade step names, so the step after it can
// resume that session. The token holds Read items too (Ask 2d3d3fd0). And a
// run that ends at a hold does not end there (Ask 79e3e335, runner-waits):
// the step after the action waits at the hold with the same token and run,
// through the plugin's `runner.mjs carry`, and resumes the session with the
// answer, until fifteen minutes before the token's six hours are up, when it
// gives up waiting with a line on the run.
export const RUN_FILE = '.github/workflows/run.yml';
// Gate 8 (Ask 2cef338c, public-plugin-repo). alydash is private, so a run
// takes the plugin from its own public repository, which `pnpm plugin:publish`
// in alydash writes (scripts/src/publish-plugin.ts PUBLIC_REMOTE, the same
// URL): `plugin/` and a marketplace naming it, one tag a release. Laptops
// still install from alydash's own marketplace.
export const PLUGIN_MARKETPLACE = 'https://github.com/sachitsac/alydash-plugin.git';
export const PLUGIN_NAME = 'alydash@alydash';

// Gate 8 fix (G8S-5, ruled in Ask 5b44eb42). What the job runs is pinned. Each
// action by its full commit, its release tag beside it (actions/checkout's
// v4.4.0, a lightweight tag; claude-code-action's v1.0.270, an annotated tag,
// to its commit). The carry step's plugin is cloned at the tag of this
// plugin's own version, `plugin-v<version>`, which each release pushes, not
// main's head. Claude Code is installed at the version that commit of
// claude-code-action installs (CLAUDE_CODE_VERSION in its base-action, and
// src/entrypoints/run.ts). The action's `plugin_marketplaces` takes a git URL
// that ends in `.git` and no ref (MARKETPLACE_URL_REGEX in its
// base-action/src/install-plugins.ts), or a local path (`isLocalPath`: one
// that starts with `/`, `./` or `../`), which it hands to `claude plugin
// marketplace add` as it is. So the release is cloned at its tag in a step of
// its own, and the action and the carry step both use that one clone.
const CHECKOUT_ACTION = 'actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0';
const CLAUDE_CODE_ACTION = 'anthropics/claude-code-action@9eb012b6d0900c2b9eede65ee1758cec4d2973e9 # v1.0.270';
const CLAUDE_CODE_VERSION = '2.1.321';
const PLUGIN_VERSION = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', '.claude-plugin', 'plugin.json'), 'utf8')).version;

export function runWorkflow({ desk }) {
  return [
    `${OURS} The desk starts agent work on an item here, on your`,
    '# Claude subscription, when you press Start with agent and pick GitHub Actions.',
    '#',
    '# The desk dispatches this file with the start and the item. The job asks',
    "# GitHub for an OpenID Connect token for this run, with the desk's address as",
    '# the audience, and trades it at the desk for a token that can report this one',
    '# run, read its item and ask you a question, for six hours. The desk takes the',
    '# trade only from this file, named run.yml, on main. Do not rename this file.',
    '#',
    '# When the run stops at a hold to ask you something, the job waits for your',
    '# answer on the desk and then carries on in the same session, for up to the',
    "# token's six hours.",
    '#',
    '# The one secret it needs is CLAUDE_CODE_OAUTH_TOKEN: run `claude setup-token`',
    "# and add what it prints under the repository's Settings, Secrets and variables,",
    '# Actions. There is no desk secret here.',
    '',
    'name: agent work from the desk',
    '',
    'on:',
    '  workflow_dispatch:',
    '    inputs:',
    '      start:',
    '        description: The start the desk dispatched this run for',
    '        required: true',
    '        type: string',
    '      item:',
    '        description: The item the run works on',
    '        required: true',
    '        type: string',
    '',
    '# One run per start; a second dispatch of the same start waits, and its trade',
    '# is refused because the start was claimed by the first.',
    'concurrency:',
    '  group: run-${{ inputs.start }}',
    '  cancel-in-progress: false',
    '',
    'permissions:',
    '  id-token: write',
    '  contents: write',
    '  pull-requests: write',
    '',
    'jobs:',
    '  run:',
    '    runs-on: ubuntu-latest',
    '    # The desk token lasts six hours; a GitHub-hosted job, at most as long.',
    '    timeout-minutes: 360',
    '    steps:',
    `      - uses: ${CHECKOUT_ACTION}`,
    '        with:',
    '          fetch-depth: 0',
    '',
    '      - name: Trade the run token at the desk',
    '        id: desk',
    '        env:',
    `          DESK_URL: ${JSON.stringify(desk)}`,
    '          START: ${{ inputs.start }}',
    '          REPOSITORY: ${{ github.repository }}',
    '        run: |',
    '          ID_TOKEN=$(curl -sS -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=$DESK_URL" | jq -r \'.value // empty\') || true',
    '',
    '          if [ -z "$ID_TOKEN" ]; then',
    '            echo "::error::GitHub gave this run no OIDC token, so the desk was not asked. The job needs permissions: id-token: write."',
    '            exit 1',
    '          fi',
    '          echo "::add-mask::$ID_TOKEN"',
    '',
    '          code=$(curl --silent --show-error --max-time 60 \\',
    "            --output run-token.json --write-out '%{http_code}' \\",
    '            --request POST "$DESK_URL/api/github/run-token" \\',
    '            --header "authorization: Bearer $ID_TOKEN" \\',
    "            --header 'content-type: application/json' \\",
    "            --data \"$(jq -cn --arg start \"$START\" '{start: $start}')\") || code=\"000\"",
    '',
    '          if [ "$code" != "200" ]; then',
    '            echo "The desk answered $code:"',
    '            cat run-token.json || true',
    '            echo',
    '            echo "::error::The desk gave this run no token (it answered $code), so the work was not started. A 404 means the start was claimed already or ran out; press Start with agent again."',
    '            rm -f run-token.json',
    '            exit 1',
    '          fi',
    '',
    "          TOKEN=$(jq -r '.token' run-token.json)",
    '          echo "::add-mask::$TOKEN"',
    '',
    '          # Where the plugin keeps its connection, so its hook reports this run',
    '          # on Now with the token the desk minted for it.',
    '          DATA="$HOME/.claude/plugins/data/alydash-alydash"',
    '          mkdir -p "$DATA" && chmod 700 "$DATA"',
    "          jq --arg desk \"$DESK_URL\" --arg repo \"$REPOSITORY\" '{entries: [{desk: $desk, appId: .app.id, appName: .app.name, repo: $repo, token: .token, expiresAt: .expiresAt, scopes: [\"heartbeat:AgentSession\", \"raise:Ask\", \"read:WorkItem\"]}]}' run-token.json > \"$DATA/credentials.json\"",
    '          chmod 600 "$DATA/credentials.json"',
    '',
    "          # The session is named here, so the step after the action can resume it;",
    "          # that step stops waiting fifteen minutes before the token runs out.",
    "          EXPIRES=$(date -u -d \"$(jq -r '.expiresAt' run-token.json)\" +%s)",
    '',
    '          DELIMITER="prompt-$(openssl rand -hex 16)"',
    '          {',
    '            echo "token=$TOKEN"',
    '            echo "session=$(cat /proc/sys/kernel/random/uuid)"',
    '            echo "until=$(date -u -d "@$((EXPIRES - 900))" +%Y-%m-%dT%H:%M:%SZ)"',
    "            echo \"budget=$(jq -r '.start.budgetUsd // empty' run-token.json)\"",
    '            echo "credentials=$DATA/credentials.json"',
    '            echo "prompt<<$DELIMITER"',
    "            jq -r '.start.prompt' run-token.json",
    '            echo "$DELIMITER"',
    '          } >> "$GITHUB_OUTPUT"',
    '          rm -f run-token.json',
    '',
    '      - name: Fetch the alydash plugin at its release',
    '        run: |',
    `          git clone --quiet --depth 1 --branch plugin-v${PLUGIN_VERSION} ${PLUGIN_MARKETPLACE} "$RUNNER_TEMP/alydash-plugin"`,
    '',
    '      - name: Run the start in Claude Code',
    `        uses: ${CLAUDE_CODE_ACTION}`,
    '        env:',
    `          ALYDASH_URL: ${JSON.stringify(desk)}`,
    '          ALYDASH_RUN_START: ${{ inputs.start }}',
    '          ALYDASH_WORK_ITEM: ${{ inputs.item }}',
    '          ALYDASH_RUN_BUDGET: ${{ steps.desk.outputs.budget }}',
    '          ALYDASH_SESSION_TOKEN: ${{ steps.desk.outputs.token }}',
    '          ALYDASH_CREDENTIALS: ${{ steps.desk.outputs.credentials }}',
    '        with:',
    '          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}',
    '          plugin_marketplaces: ${{ runner.temp }}/alydash-plugin',
    `          plugins: ${PLUGIN_NAME}`,
    '          prompt: ${{ steps.desk.outputs.prompt }}',
    '          claude_args: --permission-mode auto --session-id ${{ steps.desk.outputs.session }}',
    '',
    '      - name: Wait at a hold on the desk, then carry on',
    '        env:',
    `          ALYDASH_URL: ${JSON.stringify(desk)}`,
    '          ALYDASH_RUN_START: ${{ inputs.start }}',
    '          ALYDASH_WORK_ITEM: ${{ inputs.item }}',
    '          ALYDASH_RUN_BUDGET: ${{ steps.desk.outputs.budget }}',
    '          ALYDASH_CREDENTIALS: ${{ steps.desk.outputs.credentials }}',
    '          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}',
    '          SESSION: ${{ steps.desk.outputs.session }}',
    '          UNTIL: ${{ steps.desk.outputs.until }}',
    '        run: |',
    '          # The plugin fetched before the action, for its runner: it waits at the',
    '          # hold the session ended at, with this run\'s token, and resumes the',
    '          # session with your answer.',
    '          PLUGIN="$RUNNER_TEMP/alydash-plugin"',
    `          command -v claude >/dev/null || npm install --global --silent @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}`,
    '          node "$PLUGIN/plugin/scripts/runner.mjs" carry "$GITHUB_WORKSPACE" "$SESSION" --until "$UNTIL"',
    '',
  ].join('\n');
}

// The desk this repository is connected to: the one named for this session,
// or else the desk of the credential connect stored for it, as the hook picks
// it (ALYDAS-445, CR-1: lib/credentials.mjs pickCredential, live first, one
// that beats first, then the latest expiry). A credential that has run out
// still names its desk when none is live.
export function deskFor(dataDir, repo, env = process.env, now = new Date()) {
  const pinned = deskUrl(env);
  if (pinned !== null) return pinned;
  return pickCredential(dataDir, repo, { now })?.desk ?? null;
}

// The entry for `repo` on `desk`, picked the same way, live or run out, or
// null when this laptop has none there (a desk named by ALYDASH_URL it never
// connected to).
function entryOn(dataDir, repo, desk, now = new Date()) {
  return pickCredential(dataDir, repo, { desk, now });
}

// ALYDAS-451, O2. How the first line names the desk: a desk without Work
// follows no merges, so the repository is connected to it; one without runs
// shows none of its sessions, so the repository is followed by it; with both,
// the repository reports to it.
function deskLine(repo, desk, entry) {
  if (!hasWork(entry)) return `${repo} is connected to ${oneLine(desk)}.`;
  if (!sendsHeartbeat(entry)) return `${repo} is followed by ${oneLine(desk)}.`;
  return `${repo} reports to ${oneLine(desk)}.`;
}

// ALYDAS-450. The connection line for a live entry, from asking the desk
// whether it still takes the token (lib/desk.mjs probeConnection): `asked`
// is whether the desk answered either way.
async function liveLine(entry, until, probe) {
  let answer;
  try {
    answer = await probe(entry.desk, entry.token);
  } catch (error) {
    const why = error instanceof Error ? oneLine(error.message).replace(/\.$/, '') : 'unknown error';
    return { asked: false, revoked: false, line: `Connection: live until ${day(until)} on this laptop. The desk could not be asked whether it still is: ${why}.` };
  }
  const said = connectionOf(answer);
  if (said === 'revoked') return { asked: true, revoked: true, line: 'Connection: revoked on the desk. Run /alydash:connect.' };
  // Gate 8 (W4). Agent access switched off for the app: not live, and a thing
  // to do, in the words the desk's refusals use for where the switch is.
  if (said === 'switched-off') {
    const app = oneLine(entry.appName);
    return {
      asked: true,
      revoked: false,
      off: true,
      line: `Connection: agent access is switched off for ${app} on the desk. Switch it back on for ${app} in Modules, and this connection works again.`,
    };
  }
  if (said === 'live') return { asked: true, revoked: false, line: `Connection: live until ${day(until)}.` };
  return {
    asked: false,
    revoked: false,
    line: `Connection: live until ${day(until)} on this laptop. The desk did not say whether it still is (it answered ${answer.status}).`,
  };
}

// Writes one file under `root`, and says in a line what happened. `replace`
// says whether a file already there, with other content, is one this command
// may bring up to date.
function install(root, path, text, replace, keptBecause) {
  const target = join(root, path);
  const found = existing(target);
  if (found.kind === 'other') return { path, state: 'kept', line: `Kept ${path}: it is not a plain file, so it was left alone.` };
  if (found.kind === 'file' && found.text === text) return { path, state: 'level', line: `Already there: ${path}` };
  if (found.kind === 'file' && !replace(found.text)) return { path, state: 'kept', line: `Kept ${path}: ${keptBecause}` };
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, text);
  return { path, state: found.kind === 'file' ? 'updated' : 'added', line: `${found.kind === 'file' ? 'Updated' : 'Added'}: ${path}` };
}

function topLevel(cwd) {
  return new Promise((resolve) => {
    execFile('git', ['-C', cwd, 'rev-parse', '--show-toplevel'], { timeout: 5000 }, (error, stdout) => {
      const text = String(stdout ?? '').trim();
      resolve(error || text === '' ? null : text);
    });
  });
}

const template = (name) => readFileSync(join(TEMPLATES, name), 'utf8');
const startsOurs = (text) => text.startsWith(OURS);


// What /alydash:setup would put in `root` for `desk`: each file's path and
// text, whether a different file already there is ours to replace, and what
// to say when it is not. `setup` writes these; `status` only looks. No
// sync.yml for a desk on this laptop (ALYDAS-391).
function planFor(root, desk, entry) {
  const deploy = existing(join(root, DEPLOY_FILE));
  const deployName = deploy.kind === 'file' ? workflowName(deploy.text) : null;
  const files = [
    {
      path: TEMPLATE_FILE,
      text: template('pull_request_template.md'),
      replace: () => false,
      keptBecause:
        'this repository has its own. The Timeline needs it to open with the "What you\'ll notice" section; copy that section in from the plugin\'s templates/pull_request_template.md.',
    },
    // The two check files are the plugin's alone, in a folder of its name.
    { path: '.github/alydash/merge-notes.mjs', text: template('merge-notes.mjs'), replace: () => true, keptBecause: '' },
    { path: '.github/alydash/merge-notes-check.mjs', text: template('merge-notes-check.mjs'), replace: () => true, keptBecause: '' },
    {
      path: NOTES_FILE,
      text: template('merge-notes.yml'),
      replace: startsOurs,
      keptBecause: 'this repository has its own workflow of that name.',
    },
    {
      path: SYNC_FILE,
      text: syncWorkflow({ desk, deployName }),
      replace: startsOurs,
      keptBecause: 'this repository has its own workflow of that name. The desk takes a sync only from a file named sync.yml.',
    },
    // ALYDAS-127, S6. Only for a desk with runs (a connection that beats),
    // which GitHub's runners can reach.
    {
      path: RUN_FILE,
      text: runWorkflow({ desk }),
      replace: startsOurs,
      keptBecause:
        'this repository has its own workflow of that name. The desk starts agent work on GitHub Actions only through a file named run.yml.',
    },
  ]
    .filter((file) => file.path !== SYNC_FILE || !onThisLaptop(desk))
    .filter((file) => file.path !== RUN_FILE || (sendsHeartbeat(entry) && !onThisLaptop(desk)));
  return { deployName, files };
}

// The newest merged pull request, through the GitHub CLI when it is there and
// signed in: `{ number, body }`, null when the repository has merged none, or
// undefined when it could not be asked.
function newestMergedWithGh(root) {
  return new Promise((resolve) => {
    execFile(
      'gh',
      ['pr', 'list', '--state', 'merged', '--limit', '10', '--json', 'number,body,mergedAt'],
      { cwd: root, timeout: 15000, maxBuffer: 8 * 1024 * 1024, env: { ...process.env, GH_PROMPT_DISABLED: '1', NO_COLOR: '1' } },
      (error, stdout) => {
        if (error) return resolve(undefined);
        try {
          const merged = JSON.parse(String(stdout)).filter((one) => typeof one?.mergedAt === 'string');
          merged.sort((a, b) => Date.parse(b.mergedAt) - Date.parse(a.mergedAt));
          resolve(merged.length === 0 ? null : { number: merged[0].number, body: String(merged[0].body ?? '') });
        } catch {
          resolve(undefined);
        }
      },
    );
  });
}

const day = (iso) => new Date(iso).toISOString().slice(0, 10);

// `/alydash:status`. What this repository still lacks for the desk to follow
// it, each line followed by the one thing to do. It writes nothing but what
// the desk said of the connection, on its entry in the credential store
// (Gate 8, W3), so a revoked entry is no longer picked over a live one.
export async function status({
  env = process.env,
  dataArg,
  cwd = process.cwd(),
  now = new Date(),
  newestMerged = newestMergedWithGh,
  probe = probeConnection,
} = {}) {
  let dataDir;
  try {
    dataDir = pluginDataDir(env, dataArg);
  } catch (error) {
    return { code: 1, lines: [error.message] };
  }
  const repo = await repoOf(cwd);
  const root = repo === null ? null : await topLevel(cwd);
  if (repo === null || root === null) return { code: 1, lines: [NOT_A_REPO.replace('setup', 'status')] };
  const desk = deskFor(dataDir, repo, env, now);
  if (desk === null) return { code: 1, lines: [NOT_CONNECTED.replace('/alydash:setup again', '/alydash:status again')] };

  // The connection: the entry for this repository on that desk.
  const latest = entryOn(dataDir, repo, desk, now);
  // ALYDAS-385. A desk without Work follows no repository's merges, so this
  // one is connected there and reports nothing.
  const board = hasWork(latest);
  const lines = [deskLine(repo, desk, latest)];
  let missing = 0;
  const lacks = (line) => {
    missing += 1;
    lines.push(line);
  };

  const until = latest === null ? NaN : Date.parse(latest.expiresAt);
  // ALYDAS-450. Live on this laptop is asked of the desk, once.
  let asked = false;
  if (isLive(latest, now)) {
    const live = await liveLine(latest, until, probe);
    asked = live.asked;
    if (live.revoked || (live.asked && isRevoked(latest))) {
      try {
        markRevoked(dataDir, latest, live.revoked ? now : null);
      } catch {
        // A store that cannot be written costs only the ranking.
      }
    }
    if (live.revoked || live.off) lacks(live.line);
    else lines.push(live.line);
  } else if (latest === null) lacks('Connection: none stored for this repository on this laptop. Run /alydash:connect.');
  else if (Number.isNaN(until)) lacks('Connection: its end date cannot be read. Run /alydash:connect.');
  // ALYDAS-386: a desk without runs never showed this repository's sessions.
  else if (sendsHeartbeat(latest)) lacks(`Connection: ran out on ${day(until)}, so sessions here no longer show on Now. Run /alydash:connect.`);
  else lacks(`Connection: ran out on ${day(until)}. Run /alydash:connect.`);

  if (!board) {
    lines.push(NOTHING_TO_SET_UP, missing === 0 ? 'Nothing is missing.' : `${missing} ${missing === 1 ? 'thing needs' : 'things need'} doing, each named above.`);
    return { code: 0, lines };
  }

  const { deployName, files } = planFor(root, desk, latest);
  // The two check files serve the merge notes workflow this command writes. A
  // repository with a workflow of its own by that name checks its own way, so
  // their absence there is nothing missing.
  const notes = existing(join(root, NOTES_FILE));
  const ownCheck = notes.kind === 'file' && !startsOurs(notes.text);
  for (const file of files) {
    if (ownCheck && file.path.startsWith('.github/alydash/')) continue;
    const found = existing(join(root, file.path));
    if (found.kind === 'none') lacks(`Missing: ${file.path}. Run /alydash:setup.`);
    else if (found.kind === 'other') lacks(`Not a plain file: ${file.path}. Move it aside, then run /alydash:setup.`);
    else if (found.text === file.text) lines.push(`There: ${file.path}`);
    else if (file.replace(found.text)) lacks(`Out of date: ${file.path}. Run /alydash:setup to bring it level.`);
    else if (file.path === TEMPLATE_FILE && !HEADING.test(found.text)) {
      lacks(`Your own: ${file.path}, and it has no "What you'll notice" section, so pull requests written from it give the Timeline nothing to publish. Copy the section in from the plugin's templates/pull_request_template.md.`);
    } else if (file.path === TEMPLATE_FILE) lines.push(`Your own: ${file.path}, and it has the "What you'll notice" section.`);
    else lines.push(`Your own: ${file.path} (this command did not write it, so it was not checked)`);
  }
  // ALYDAS-391. planFor leaves sync.yml out for a desk on this laptop, so the
  // loop above never names it, and without this a repository that can never
  // sync would read as having nothing missing.
  if (onThisLaptop(desk)) {
    lacks(`No sync workflow: ${cannotReach(desk)}. To sync, connect this repository to a desk on https, then run /alydash:setup.`);
  }
  lines.push(
    deployName !== null
      ? `Deploys: read from the "${oneLine(deployName)}" workflow on main.`
      : `Deploys: no ${DEPLOY_FILE} with a name, so the desk sees no deploy here. The app has to be one that ships on merge.`,
  );

  const merged = await newestMerged(root);
  if (merged === undefined) lines.push('Newest merged pull request: could not look (the GitHub CLI, gh, is not installed or not signed in).');
  else if (merged === null) lines.push('Newest merged pull request: none yet.');
  else {
    const hasNotes = readMergeNotes(merged.body).ok;
    const ships = shipsTrailer(merged.body) !== null;
    if (hasNotes && ships) lines.push(`Newest merged pull request (#${merged.number}): has its "What you'll notice" section and a Ships line.`);
    else {
      const gaps = [
        ...(hasNotes ? [] : ['no complete "What you\'ll notice" section, so it is not on the Timeline']),
        ...(ships ? [] : ['no Ships line, so it closed no item']),
      ];
      lacks(`Newest merged pull request (#${merged.number}): ${gaps.join('; ')}. Write the next one from the template.`);
    }
  }

  lines.push(missing === 0 ? 'Nothing is missing.' : `${missing} ${missing === 1 ? 'thing needs' : 'things need'} doing, each named above.`);
  lines.push(
    asked
      ? "This checked the laptop and the repository, and asked the desk whether the connection is live. Whether the desk's GitHub app can read this repository shows on Connections."
      : "This checked the laptop and the repository only. Whether the desk's GitHub app can read this repository shows on Connections.",
  );
  return { code: 0, lines };
}

export async function setup({ env = process.env, dataArg, cwd = process.cwd() } = {}) {
  let dataDir;
  try {
    dataDir = pluginDataDir(env, dataArg);
  } catch (error) {
    return { code: 1, lines: [error.message] };
  }
  const repo = await repoOf(cwd);
  const root = repo === null ? null : await topLevel(cwd);
  if (repo === null || root === null) return { code: 1, lines: [NOT_A_REPO] };

  const desk = deskFor(dataDir, repo, env);
  if (desk === null) return { code: 1, lines: [NOT_CONNECTED] };
  // ALYDAS-385. Nothing is written for a desk without Work.
  const entry = entryOn(dataDir, repo, desk);
  if (!hasWork(entry)) return { code: 0, lines: [NOTHING_TO_SET_UP] };
  const refusal = await deskRefusal(desk);
  if (refusal !== null) return { code: 1, lines: [`Nothing was written: ${refusal}`] };

  const { deployName, files } = planFor(root, desk, entry);
  const done = files.map((file) => install(root, file.path, file.text, file.replace, file.keptBecause));

  const changed = done.filter((one) => one.state === 'added' || one.state === 'updated');
  const lines = [deskLine(repo, desk, entry), ...done.map((one) => one.line)];
  // ALYDAS-391. For a desk on this laptop, the one sentence that says why no
  // sync workflow was written, in place of when the board syncs: it never
  // does from here.
  const local = onThisLaptop(desk);
  lines.push(
    local
      ? `${cannotReach(desk)}, so no sync workflow was written.`
      : deployName !== null
        ? `The board syncs after each successful run of the "${oneLine(deployName)}" workflow on main.`
        : `No ${DEPLOY_FILE} with a name here, so the board syncs on each push to main. The desk sees a deploy only as a run of a workflow file named deploy.yml; without one, set the app to ship on merge.`,
  );
  if (changed.length === 0) lines.push('Nothing changed.');
  else {
    lines.push(
      local
        ? 'Nothing was committed. Commit these on a branch and merge them.'
        : 'Nothing was committed. Commit these on a branch and merge them: the sync starts with the first deploy after that.',
      'The desk reads pull requests through its GitHub app: on Connections, check this repository is ticked.',
      'To make a missing "What you\'ll notice" section stop a merge, add the "merge notes" check to main\'s required checks.',
    );
  }
  return { code: 0, lines };
}

// `--data <dir>` from an argument list, or undefined.
function dataArgOf(args) {
  const at = args.indexOf('--data');
  return at === -1 ? undefined : args[at + 1];
}

export async function main(argv) {
  const checking = argv[0] === 'status';
  let outcome;
  try {
    outcome = checking ? await status({ dataArg: dataArgOf(argv) }) : await setup({ dataArg: dataArgOf(argv) });
  } catch {
    // Never the error's own message: it can quote a path or what it read.
    outcome = { code: 1, lines: [`/alydash:${checking ? 'status' : 'setup'} stopped on an unexpected error. Run it again.`] };
  }
  await new Promise((resolve) => process.stdout.write(outcome.lines.map((line) => `${line}\n`).join(''), resolve));
  return outcome.code;
}

const invoked = process.argv[1] ? realpathSync(process.argv[1]) : null;
if (invoked === realpathSync(fileURLToPath(import.meta.url))) {
  process.exit(await main(process.argv.slice(2)));
}
