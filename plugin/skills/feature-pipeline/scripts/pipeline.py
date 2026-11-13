#!/usr/bin/env python3
"""feature-pipeline run ledger.

One JSON file per run is the source of truth; an append-only events file
carries phase boundaries with session ids; the board HTML is rendered from
the JSON and never hand-edited.

  pipeline.py init   <slug> --title T --goal G [--repo R] [--branch B] [--hold 2|2,8|none] [--root docs/superpowers]
  pipeline.py phase  <slug> <n> start|done|failed|skipped|blocked [--note ...] [--force "reason"]
  pipeline.py note   <slug> <n> [--plain P] [--scene S] [--summary S] [--next N] [--decided X]... [--measured X]...
                                [--backstory B] [--fixed-means X]... [--wont-change X]...  (these three: phase 0)
                                [--wrong X]... [--overruled X]... [--produced PATH[=note]]...
                                [--doc "path|title|about|in short"]...   (names a produced file; beats like --produced)
                                [--lede L] [--number "figure|means"]... [--option "name|chosen|why"]...
                                [--assumed "text|later"]... [--left-out X]...   (--wrong "what => did" fills problems too)
  pipeline.py step   <slug> <n> <number|text> [todo|doing|done|dropped] [--sub LINE] [--add]   (beats; dropped needs --sub)
  pipeline.py story  <slug> [--happened X]... [--stands S] [--needs-you "what|why"]...
                            [--term "CODE=plain name"]... [--clear happened|stands|needs-you|terms]   (beats)
  pipeline.py table  <slug> <n> --columns "a,b,c" --row "x|y|z" [--row ...] [--replace]
  pipeline.py defer  <slug> --item X --needs Y
  pipeline.py ui-gate skip <slug> --reason "why"   (this run draws nothing; Phase 8 refuses it if a rendered file changed)
  pipeline.py ui-gate check <slug>                 (each of the run's UI gate reports, checked; exit 2 with each problem)
  pipeline.py ui-gate record <slug> [--replace]    (the reports' findings into Phase 8's table; refuses what check refuses)
  pipeline.py set    <slug> key value            (branch, goal, title, ...; holds 2,8 or none)
  pipeline.py usage  <slug>                      (tokens, time, cost per phase from transcripts)
  pipeline.py render <slug>                      (writes the board HTML)
  pipeline.py hold   <slug> <n> [--question Q]    (phase n done through a person on the desk: blocked, asked, waits)
  pipeline.py status <slug> [n]                  (the boundary block to paste)
  pipeline.py check  <slug> <n>                  (artifact checks for closing phase n)
  pipeline.py ready  <slug> --item KEY [--item KEY ...] [--pr N]  (every Ships key; with --pr also its description via gh; no before-merging step open on the desk or written and unanswered: exit 0; 3 lists them; 4 unread; 2 no key)

Every mutating command re-runs usage + render, so the board is always current.

Set ALYDASH_URL and ALYDASH_SESSION_TOKEN and every phase boundary is also
reported to the alydash desk, which draws it on the Now strip and on the
run's own page (`<desk>/runs/<id>`), and with ALYDASH_WORK_ITEM on that item's
screen too. A session a runner started from the desk (Start with agent on an
item) is also given ALYDASH_RUN_START, which every beat carries so the desk
joins the run to that start, and ALYDASH_RUN_BUDGET, sent as the run's budget
when the ledger states none: `init --budget` outranks it, and a value that is
not a budget is ignored. With no token exported, a credential the alydash plugin stored for
this repository (its store is named by ALYDASH_CREDENTIALS) reports the same
way. With neither, nothing is sent and nothing else changes. A stored
credential for a desk without runs (not given heartbeat:AgentSession) reports
nothing either, and hold and wait exit 4 there.

Python 3.9+, stdlib only.
"""
import argparse, datetime as dt, glob, hashlib, json, os, posixpath, re, shlex, stat, subprocess, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "..", "assets", "board.html")

PHASES = [
    (0, "frame", "Frame", "phase"),
    (1, "brainstorm", "Brainstorm", "phase"),
    (2, "prototype", "Prototype", "phase"),
    (3, "plan", "Plan", "phase"),
    (4, "verify", "Verify the brief", "gate"),
    (5, "dispatch", "Dispatch", "phase"),
    (6, "guards", "Break the guards", "gate"),
    (7, "goal", "Goal achieved", "gate"),
    (8, "review", "Review the diff", "gate"),
    (9, "finish", "Finish", "phase"),
]
# ALYDAS-173, S2. What each phase is for, in words for someone who has never
# read code: the first line of the phase's scene on the desk and the board.
# Fixed text that ships with the skill, stamped on a phase by `phase <n>
# start`, so a skill that rewords one changes it at the next start; a phase
# not yet started is sent this text by `beat_ledger` and drawn with it by
# `render`. The long form of each phase stays in references/phases.md.
PHASE_PURPOSE = {
    0: "Before anything is built, the agent restates the problem in plain words and writes down exactly "
       "how we will know it is fixed. Nothing changes in this phase.",
    1: "With the problem pinned down, the agent works out the ways it could be solved, weighs them "
       "against each other and picks one. Still no code.",
    2: "Before committing to a plan, the agent tries the idea cheaply, so problems show up while they are "
       "cheap to fix. For a screen that is a clickable mock-up; for a change nobody sees, it is a "
       "measurement of how things behave today.",
    3: "The agent turns the chosen approach into a build plan: small slices, each with a test that has to "
       "fail before the change and pass after it.",
    4: "A checkpoint. Every claim the plan makes about the existing code is checked against the code "
       "itself, so nobody builds on a wrong assumption.",
    5: "The building. Each slice starts with a test that fails on the old code, then the change that "
       "makes it pass.",
    6: "A checkpoint. A test that passes whatever the code does protects nothing, so the agent breaks "
       "the new code on purpose and checks that the tests notice.",
    7: "A checkpoint. The agent runs the real app and checks each proof written down in Frame by using "
       "it, not by reading the tests.",
    8: "A checkpoint. Separate reviewers read the finished change, each looking for a different kind of "
       "problem, before it comes to you.",
    9: "The agent writes up where things stand and hands over. It stops short of merging, because "
       "merging puts the change live.",
}
# ALYDAS-173, S4 (criterion 6). Each phase's usual steps, in plain words: the
# to-do list a phase starts with, seeded all todo by `phase <n> start` on a
# phase that has none, and ticked with `step`, which beats on every call so
# the desk's run page moves as the work does. The agent adds its own with
# `step --add`. Written from references/phases.md, gates.md and prototype.md.
PHASE_STEPS = {
    0: ["Read the request and what is already known",
        "Say the problem in plain words",
        "Write down how we will know it is fixed",
        "Say how each proof will be seen"],
    1: ["Read the code the change touches",
        "Work out the ways it could be solved",
        "Weigh them against each other",
        "Pick one and write the design"],
    2: ["Decide what to try out",
        "Build a quick mock-up or a measurement",
        "Look at what it shows",
        "Settle the design detail it points to"],
    3: ["Cut the work into small slices",
        "Name the failing test for each slice",
        "Order the slices, thinnest first",
        "Write the plan"],
    4: ["List every claim the plan makes about the code",
        "Check each claim against the code itself",
        "Carry the corrections into the slices"],
    # ALYDAS-225, Gate 8. The wave's steps, not one slice's loop: the agent
    # running Dispatch hands slices out, and a builder does the loop.
    5: ["Hand each slice to a builder",
        "See each slice's failing test before its fix",
        "See each builder break its own change",
        "Run the whole suite",
        "Collect the reports"],
    6: ["List the new and changed tests",
        "Break the code each one guards",
        "Check each test goes red, then put the code back",
        "Guard any gap that was found"],
    7: ["Start the real app",
        "Check each proof by using it",
        "Take a picture of each screen",
        "Write down what was seen"],
    8: ["Hand the whole change to the reviewers",
        "Reproduce each finding",
        "Rule on each: fix now, later, or ask you",
        "Make the fixes, each test first"],
    9: ["Write the handover",
        "Write the pull request text",
        "Commit the work, and stop short of merging"],
}
# The three states a step is in, in the order it moves through them, and
# (ALYDAS-225, K2) the fourth: dropped, a step that was not done, with its
# reason as the line under it, so a list never ticks what did not happen.
STEP_STATES = ("todo", "doing", "done", "dropped")

def step_reason(text):
    """ALYDAS-225, Gate 8. Whether `text` is a reason once the beat has cleaned
    it: `ledger_text` takes out control characters and direction marks and
    trims, as `story_entry` does to the line under a step, and `said` is the
    desk's test of a blank. A reason of an escape alone says nothing there."""
    return isinstance(text, str) and said(ledger_text(text, "stepSub"))

def step_state(step):
    """ALYDAS-225, Gate 6. A step's state as the script reads it everywhere: the
    close's check, the rule that keeps one doing, and the beat. A state outside
    STEP_STATES, or none (a ledger a hand wrote), is to-do, so the three agree.
    Gate 8: so is a dropped step with no reason that survives the beat's
    cleaning, which only a hand can write. The desk refuses a whole beat for a
    dropped step with no line, so the beat never carries one."""
    state = step.get("state")
    if state not in STEP_STATES or (state == "dropped" and not step_reason(step.get("sub"))):
        return "todo"
    return state
# ALYDAS-173, S2. The Frame-only notes, by flag, and the ledger key each writes.
FRAME_NOTES = {"backstory": "backstory", "fixed_means": "fixed-means", "wont_change": "wont-change"}
# ALYDAS-173. The notes that are lists, which `--clear` empties to [] even on a
# ledger written before they existed.
LIST_NOTES = ("fixed_means", "wont_change", "numbers", "options", "assumed", "left_out", "problems", "steps")
STATUSES = ("pending", "active", "done", "failed", "skipped", "blocked")
EVENT_TO_STATUS = {"start": "active", "done": "done", "failed": "failed",
                   "skipped": "skipped", "blocked": "blocked"}
# ALYDAS-175, S5. The phases that wait for a person's review on the desk
# (`hold`), set per run. A ledger written before `holds` existed has no key
# and reads as LEGACY_HOLDS, Phase 2 (the prototype), so a run begun before the
# change still waits there. ALYDAS-174, S6 (Ask 1126cfb8, "Hold at 2 and 8 by
# default"): `init` holds at Phase 2 and at Gate 8, where the desk's Review the
# delivery sheet is read, unless `--hold` says otherwise.
LEGACY_HOLDS = (2,)
INIT_HOLDS = (2, 8)
NO_HOLDS = "none"
# A held phase lets the next one start once it is one of these.
HOLD_PASSED = ("done", "skipped")

def parse_holds(text):
    """`2`, `2,8` or `none` as a sorted list of phase numbers, 0 to 9, each once.
    Anything else raises ValueError with the reason."""
    text = (text or "").strip()
    if text.lower() == NO_HOLDS:
        return []
    found = []
    for part in text.split(","):
        part = part.strip()
        if not re.fullmatch(r"[0-9]", part):
            raise ValueError(f"{part!r} is not a phase number 0 to 9: give phase numbers like 2,8, or none")
        if int(part) in found:
            raise ValueError(f"phase {part} is named twice")
        found.append(int(part))
    return sorted(found)

def hold_argument(text):
    """`init --hold`, refused by argparse (exit 2) when it is not phase numbers."""
    try:
        return parse_holds(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))

# ---------- ALYDAS-17, S5: the run's budget ----------
#
# The desk's own bound for a budget is its bound for a cost
# (`LEDGER_BOUNDS.costUsd` in packages/modules/runs/src/ledger-bounds.ts), which this
# file's LEDGER_BOUNDS copies and `test_the_ledger_bounds_table_is_the_desks_ledger_bounds_table` pins.
#
# Gate 8, N1 / SEC-1: the floor is a cent, the desk's floor. The desk stores a
# budget as numeric(12,4), so anything under $0.00005 lands as $0 and strands
# the run (Approve and Deny on its Ask both do nothing).
BUDGET_FLOOR_USD = 0.01

def parse_budget(text):
    """A budget in dollars: a finite number from the floor, a cent, to the
    desk's bound. Raises ValueError naming what a budget is."""
    bound = LEDGER_BOUNDS["costUsd"]
    words = (f"a budget is an amount in dollars, at least ${BUDGET_FLOOR_USD:.2f} and at most ${bound:,}, "
             f"like 5 or 12.50; got {text!r}")
    try:
        value = float(str(text).strip().lstrip("$"))
    except ValueError:
        raise ValueError(words)
    if not (BUDGET_FLOOR_USD <= value <= bound):  # NaN fails both comparisons
        raise ValueError(words)
    return int(value) if value == int(value) else value

def budget_argument(text):
    try:
        return parse_budget(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))

def run_budget(run):
    """The ledger's budget when it is one a beat may send, else None."""
    value = run.get("budget_usd")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if 0 < value <= LEDGER_BOUNDS["costUsd"] else None

def usd(value):
    """Dollars to the cent, for the budget's lines: $5.00, $12.50, $1,200.00."""
    return f"${value:,.2f}"

def run_phase_range(run):
    """ALYDAS-303, S9 (c6). `[first, last]`: the lowest and the highest phase
    number on the ledger, which are the phases this run's arc has. Only whole
    numbers within the desk's bound for one (`LEDGER_BOUNDS["n"]`) count, and
    a ledger with none is the skill's own arc, PHASES. Sent on every beat as
    `phaseRange`, which the desk checks `holds` against; a desk from before
    ALYDAS-303 ignores the key."""
    phases = run.get("phases")
    numbers = [p.get("n") for p in phases if isinstance(p, dict)] if isinstance(phases, list) else []
    numbers = [n for n in numbers if type(n) is int and 0 <= n <= LEDGER_BOUNDS["n"]]
    numbers = numbers or [n for n, *_ in PHASES]
    return [min(numbers), max(numbers)]

def run_holds(run):
    """The phases this run holds, within its phase range (`run_phase_range`).
    No key (an older ledger) is LEGACY_HOLDS, not init's default."""
    if "holds" not in run:
        return list(LEGACY_HOLDS)
    holds = run.get("holds")
    first, last = run_phase_range(run)
    return sorted({h for h in holds if type(h) is int and first <= h <= last}) if isinstance(holds, list) else list(LEGACY_HOLDS)

def holds_words(holds):
    return "none" if not holds else ", ".join(f"Phase {h}" for h in holds)

# ---------- helpers ----------

def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def parse_ts(s):
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return None

def die(msg, code=1):
    print(f"pipeline: {msg}", file=sys.stderr)
    sys.exit(code)

def repo_root():
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        if out.returncode == 0:
            return out.stdout.strip()
    except FileNotFoundError:
        pass
    return os.getcwd()

def git_branch():
    try:
        out = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True)
        return out.stdout.strip() if out.returncode == 0 else ""
    except FileNotFoundError:
        return ""

# ---------- ALYDAS-135, S6: the commit a beat describes, and the branch it is on ----------
#
# The desk reads a phase's produced files from GitHub at the commit the beat
# names, so a phase boundary puts the branch on GitHub before the beat goes,
# and (S8) a beat names its commit only when origin has it.
#
# The push is **by name and nowhere else**: `git push origin
# HEAD:refs/heads/<branch>`. Never a bare `git push`, because this repository's
# branches track `origin/main`, and under `push.default=upstream` a bare push
# from a feature branch moves main, which is a production deploy. And never
# from main, master or origin's default branch, which is the same deploy by a
# shorter road. A detached HEAD has no branch to name and pushes nothing. A
# repository with no remote called `origin` pushes nothing and says nothing.
#
# A failed or refused push is one line on stderr and never a failed phase, the
# rule the heartbeat itself keeps: nothing the network does may stop a phase
# closing.

PUSH_TIMEOUT_S = 60
NEVER_PUSHED = ("main", "master")
COMMIT_SHAPE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

def git_here(*args, timeout=10, errors=None, text=True):
    """git in the repository `repo_root` finds, never prompting for a password.
    `errors` is how output that is not UTF-8 is decoded (`replace` for file
    names, which git gives as the bytes they are); strict when not given.
    ALYDAS-218, S3: `text=False` answers bytes, for names matched exactly."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(["git", "-C", repo_root()] + list(args),
                          capture_output=True, text=text, timeout=timeout, env=env, errors=errors)

def head_commit():
    """What `git rev-parse HEAD` says, or None outside a repository or before its first commit."""
    try:
        out = git_here("rev-parse", "HEAD")
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip() if out.returncode == 0 else ""
    return sha if COMMIT_SHAPE.fullmatch(sha) else None

# S8 (ruled 2026-09-26). The commits this process pushed to origin, so a beat
# can name one without asking git again.
PUSHED = set()

def on_origin(sha):
    """Whether an origin branch this repository knows of contains `sha` (`git branch -r --contains`)."""
    try:
        out = git_here("branch", "-r", "--contains", sha, "--format=%(refname)")
    except (OSError, subprocess.SubprocessError):
        return False
    if out.returncode != 0:
        return False
    return any(ref.startswith("refs/remotes/origin/") and ref != "refs/remotes/origin/HEAD"
               for ref in out.stdout.split())

def commit_on_github():
    """HEAD's commit when origin has it (just pushed, or on an origin branch already), else None.

    S8, ruled 2026-09-26: the desk reads a produced file from GitHub at the
    commit a beat names, so a commit only this machine has is a file the desk
    says it cannot read. With no commit the desk lists the paths instead."""
    sha = head_commit()
    if sha is None:
        return None
    return sha if sha in PUSHED or on_origin(sha) else None

class NotPushed(Exception):
    """Why a branch was not pushed, for push_branch's one stderr line."""

def origin_default():
    """The name of origin's default branch, or None when it cannot be established.

    `refs/remotes/origin/HEAD` first, which a clone has; else origin is asked
    (`git ls-remote --symref origin HEAD`), which a checkout made by `git init`
    and `git remote add` needs. Gate 8, security F5: with neither, the default
    used to be taken as no branch at all, so a `trunk` default was pushed."""
    local = git_here("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD").stdout.strip()
    if local.startswith("refs/remotes/origin/") and len(local) > len("refs/remotes/origin/"):
        return local[len("refs/remotes/origin/"):]
    try:
        asked = git_here("ls-remote", "--symref", "origin", "HEAD", timeout=PUSH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None
    if asked.returncode == 0:
        for line in asked.stdout.splitlines():
            ref, _, name = line.partition("\t")
            if name.strip() == "HEAD" and ref.startswith("ref: refs/heads/") and len(ref) > len("ref: refs/heads/"):
                return ref[len("ref: refs/heads/"):]
    return None

def push_branch():
    """Push HEAD to origin under the branch's own name, when that is safe; never raises."""
    branch = ""
    try:
        remotes = git_here("remote")
        if remotes.returncode != 0 or "origin" not in remotes.stdout.split():
            return
        branch = git_here("branch", "--show-current").stdout.strip()
        if not branch:
            return
        if branch in NEVER_PUSHED:
            return
        default = origin_default()
        if default is None:
            # Gate 8, security F5: a default branch nobody can name might be
            # this one, and pushing it could be a deploy.
            reason = ("origin's default branch is not known (no origin/HEAD, and git ls-remote --symref "
                      "origin HEAD did not say); `git remote set-head origin --auto` sets it")
            raise NotPushed(reason)
        if branch == default:
            return
        sha = head_commit()
        out = git_here("push", "origin", f"HEAD:refs/heads/{branch}", timeout=PUSH_TIMEOUT_S)
        if out.returncode == 0:
            if sha:
                PUSHED.add(sha)
            return
        said = [line.strip() for line in out.stderr.splitlines() if line.strip()]
        reason = said[-1] if said else f"git push exited {out.returncode}"
    except NotPushed as e:
        reason = str(e)
    except subprocess.TimeoutExpired:
        reason = f"git push took longer than {PUSH_TIMEOUT_S}s"
    except (OSError, subprocess.SubprocessError) as e:
        reason = f"{type(e).__name__}: {e}"
    print(f"pipeline: {printable(branch or 'the branch')} was not pushed, so the desk may not find this commit: "
          f"{printable(reason)}", file=sys.stderr)

# ---------- ALYDAS-206: hold checks the desk can open what it asks about ----------
#
# The desk opens a phase's recorded files from GitHub at the commit the beat
# names, and a beat names `commit_on_github()` or nothing (then the desk keeps
# an older commit). So before hold raises a question about those files, it asks
# whether that commit exists and holds them. The core below is pure: the facts
# are gathered once by `repo_facts()`, after hold's own push.
#
# Every reason the design lists, per path: a path outside the repository
# first, then (in the design's order) no repository, no origin, detached HEAD,
# a branch the pipeline never pushes, a branch not pushed, and a file not in
# the commit the desk reads. A detached HEAD or `main` whose commit origin has
# already opens: the desk reads the commit, not the branch.

class RepoFacts:
    """What the check needs to know about the repository, gathered by `repo_facts()`."""
    __slots__ = ("head", "has_origin", "branch", "default", "on_github", "entry", "files", "here")

    def __init__(self, head=None, has_origin=False, branch="", default=None, on_github=None,
                 entry=lambda commit, path: None, files=lambda commit: [], here=lambda path: False):
        self.head = head              # HEAD's commit, or None outside a repository
        self.has_origin = has_origin  # a remote called origin exists
        self.branch = branch          # the branch HEAD is on, or "" when detached
        self.default = default        # origin's default branch, or None when not known
        self.on_github = on_github    # what commit_on_github() answered: the commit the desk reads, or None
        # ALYDAS-218, S3. entry(commit, path): what that commit's tree has at
        # the path, read entry by entry and byte for byte: ("file", None),
        # ("dir", None), ("symlink", its text) or ("submodule", None) for the
        # last segment; ("through-symlink" or "in-submodule", that entry's
        # path) when an earlier segment is a link or a gitlink; None for a
        # miss. Replaces 212's `kind`.
        self.entry = entry
        # ALYDAS-212, S4. files(commit): every file path in that commit, which
        # `explain_miss` reads to name why a path missed. P2: asked only after
        # a miss, and `repo_facts()` reads it from git at most once.
        self.files = files
        # Gate 8, C1. here(path): a file is at that path from the repository
        # root in the working tree, so a miss there is the file not committed.
        self.here = here

def desk_path_problem(path):
    """ALYDAS-212, S2. The desk's `runFilePathProblem` (`merge-notes.ts`), check
    for check and in its order, so a path that breaks two gets the first one's
    name: "empty", "absolute" (a leading `/` or `\\`, or a drive letter),
    "climbs-out" (a `..` segment under either separator), "backslash", "nul",
    or None when the desk would ask GitHub for it."""
    if path == "":
        return "empty"
    if path.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", path):
        return "absolute"
    if ".." in re.split(r"[\\/]", path):
        return "climbs-out"
    if "\\" in path:
        return "backslash"
    if "\0" in path:
        return "nul"
    return None

def as_the_url_sends_it(path):
    """ALYDAS-212, S1. The path as the desk's request for it reaches GitHub: the
    gateway encodes each segment on its own, which leaves a `.` segment as it is,
    and the WHATWG URL parser then drops it. A `.` before a `/` goes; a `.` at
    the end goes too but leaves the slash before it, as the parser does
    (`docs/prototypes/.` is sent as `docs/prototypes/`). A `..` never gets here:
    `desk_path_problem` refuses it first."""
    segments = path.split("/")
    kept = [s for s in segments[:-1] if s != "."]
    kept.append("" if segments[-1] == "." else segments[-1])
    return "/".join(kept)

class Reason(str):
    """ALYDAS-220, S1. A reason a path would not open: the text, exactly as hold
    prints and logs it, carrying `case` (which kind of reason, a key of
    `REASON_FIXES`) and `spelling` (the path the fix should record, or None).
    `fixes_for` reads only those two, never the text, so a path whose name
    holds another reason's words gets its own fix. Only the text is logged."""

    def __new__(cls, text, case, spelling=None):
        reason = super().__new__(cls, text)
        reason.case = case
        reason.spelling = spelling
        return reason

OUTSIDE_REASON = "not a path inside the repository"
# ALYDAS-212, S2. The desk's rule refuses these two by name. A backslash path
# whose `/` spelling the desk's commit has names it ("; <spelling> is in
# <commit>"), and that spelling is the one `fixes_for` records.
BACKSLASH_REASON = "a backslash, which the desk refuses"
NUL_REASON = "a NUL, which the desk refuses"
# ALYDAS-212, S3. A folder: the desk would fetch GitHub's listing of it and
# show that as the file. The reason reads "<path> is a directory in <commit>,
# not a file", and ends with this.
DIRECTORY_REASON = ", not a file"
# ALYDAS-212, S4. A path the commit has no file at, explained from the
# commit's files. Each reason ends with the spelling the commit has, as
# "; <spelling> is in <commit>", which `fixes_for` records; when several files
# fit, "; <up to three> are in <commit>", no spelling and the generic fix.
DOUBLED_SLASH_REASON = "a doubled /, which GitHub does not find"
TRAILING_SLASH_REASON = "a trailing /, which GitHub does not find"
# ALYDAS-220, S2. Said as capital letters, the words a person reads (Sachit,
# Phase 2); the case name "another-case" stays.
ANOTHER_CASE_REASON = "in other capital letters, which GitHub does not find"
FROM_A_SUBFOLDER_REASON = "written from a subfolder, not the repository root"
# ALYDAS-218, S4. The NFD bytes of an NFC name (or the other way round): the
# same letters on screen, which GitHub matches byte for byte.
OTHER_UNICODE_WORDS = "in other Unicode spelling (its accents are stored as different bytes)"
OTHER_UNICODE_REASON = f"{OTHER_UNICODE_WORDS}, which GitHub does not find"
# ALYDAS-219, S4. Each shape a miss can need, by case: what is said when it is
# the only one, and its words when several are joined with " and ", in this
# order. A folder found that way is said with its shapes' words alone.
MISS_SHAPES = {
    "doubled-slash": (DOUBLED_SLASH_REASON, "a doubled /"),
    "trailing-slash": (TRAILING_SLASH_REASON, "a trailing /"),
    "another-case": (ANOTHER_CASE_REASON, "in other capital letters"),
    "other-unicode": (OTHER_UNICODE_REASON, OTHER_UNICODE_WORDS),
    "from-a-subfolder": (FROM_A_SUBFOLDER_REASON, "written from a subfolder"),
}
# ALYDAS-218, S3. A symlink or a submodule in the commit's tree, as GitHub
# answers each (the design's table, and Gate 4 for links). Only a link that
# reaches a committed file opens; the reasons never say how the desk shows the
# rest, since it shows a path with no extension as a path only.
LINK_TO_A_FOLDER_REASON = "a link to the folder {target}, not a file"
LINK_TO_NOTHING_REASON = "a link to {target}, which is not in the commit"
THROUGH_A_LINK_REASON = "goes through the link {link}, which GitHub does not follow"
SUBMODULE_REASON = "a submodule, not a file"
IN_A_SUBMODULE_REASON = "inside the submodule {module}, which the desk cannot read"
# Gate 4. A chain of links is followed this far, and no further.
LINK_HOPS = 5
NO_REPO_REASON = "not in a git repository"
NO_ORIGIN_REASON = "the repository has no origin"
UNCOMMITTED_REASON = "not committed: "
DETACHED_REASON = "HEAD is detached, so no branch was pushed"
NEVER_PUSHED_REASON = ", which the pipeline never pushes"
PUSH_FIX = "push the branch so origin has this commit"
# ALYDAS-206, S3. The fix for each reason that pushing does not mend; only
# "not pushed" is mended by a push. Gate 8, ST1: on main or origin's default a
# push can be a deploy, and a detached HEAD has no branch, so those two are
# mended by committing on a feature branch. ALYDAS-220, S1: keyed by the
# reason's case, one per kind of reason.
ON_A_FEATURE_BRANCH_FIX = ("commit it on a feature branch, which hold pushes (the pipeline never pushes main, "
                           "origin's default or a detached HEAD)")
REASON_FIXES = {
    "detached": ON_A_FEATURE_BRANCH_FIX,
    "never-pushed": ON_A_FEATURE_BRANCH_FIX,
    "not-pushed": PUSH_FIX,
    "outside": "record the file by its path from the repository root",
    "backslash": "record the path with / between folders, never \\",
    "nul": "record the path without the NUL",
    "directory": "record a file inside it",
    "doubled-slash": "record it with one / between folders",
    "trailing-slash": "record it without the trailing /",
    "another-case": "record it in the capital letters the commit has",
    "from-a-subfolder": "record the file by its path from the repository root",
    "other-unicode": "record it in the Unicode spelling the commit has",
    "several-shapes": "record it as the commit spells it, from the repository root",
    "link-to-a-folder": "record the file itself, not a link to its folder",
    "link-to-nothing": "record the file itself, not the link",
    "through-a-link": "record the file by its path without the link",
    "submodule": "record a file from this repository",
    "in-a-submodule": "record a file from this repository",
    "no-repo": "change to a directory inside the repository",
    "no-origin": "add the repository's GitHub remote as origin",
    "not-committed": "commit the file (hold pushes it)",
}

def fixes_for(bad, how=None):
    """The refusal's fixes: one per distinct fix in `bad`, in the order they came.
    Gate 8, ST1: `how(path, spelling)` is the note that records a path afresh,
    added to each fix that says to record it, with the spelling to record when
    the reason names one; `--produced` alone keeps the wrong path. ALYDAS-220,
    S1: each `why` is a `Reason`, and only its case and spelling are read."""
    fixes = []
    for path, why in bad:
        named = why.spelling
        fix = REASON_FIXES[why.case] if named is None else f"record it as {named}"
        if how is not None and fix.startswith("record"):
            fix = f"{fix} ({how(path, named)})"
        if fix not in fixes:
            fixes.append(fix)
    return fixes

def explain_miss(path, sent, commit, files, here=False, facts=None):
    """ALYDAS-212, S4. Why `path` (looked up as `sent`, the URL's spelling)
    missed in `commit`, from that commit's `files`. First, the path with its
    empty segments dropped is committed (a doubled /, else a trailing /).
    ALYDAS-219, S4: else every shape at once, and each one needed named
    (`fitting_paths`): empty segments dropped, capital letters, Unicode
    spelling, from a subfolder. A file found wins over a folder the files
    imply, which is named as a directory. Else not committed, worded as
    ALYDAS-206 words it. P3: up to three named, and several get the generic
    fix, never a guess. Gate 8, C1: `here`, a file at the path in the working
    tree, is that file not committed, never a committed one in a subfolder
    that ends the same. ALYDAS-218, Gate 6 (DF2, DF3): with `facts`, a path
    through a link or into a submodule is looked for when no entry fits, and
    the entry found is judged as hold would judge it recorded (`named`)."""
    sha = commit[:7]
    segments = sent.split("/")
    joined = "/".join(s for s in segments if s)
    if joined != sent and joined in files:
        shape, case = ((DOUBLED_SLASH_REASON, "doubled-slash") if "" in segments[:-1]
                       else (TRAILING_SLASH_REASON, "trailing-slash"))
        return named(shape, joined, case, sha, facts)
    # ALYDAS-219, S4. A name that is not UTF-8 can be neither sent nor
    # recorded (S5), so it is never named; the rest are read as ever (C3).
    files = [f for f in files if utf8(f)]
    # Every folder the files imply, in the order they are first seen.
    folders = list(dict.fromkeys("/".join(f.split("/")[:k]) for f in files for k in range(1, f.count("/") + 1)))
    slash = ["doubled-slash"] if "" in segments[:-1] else []
    shapes, found = fitting_paths(joined, [f for f in files if f != joined], here)
    if not found and facts is not None:
        shapes, found = fitting_paths(joined, beyond_links(joined, commit, files, facts), here)
    if found:
        shapes = (slash or (["trailing-slash"] if segments[-1] == "" else [])) + shapes
        said = MISS_SHAPES[shapes[0]][0] if len(shapes) == 1 else " and ".join(MISS_SHAPES[s][1] for s in shapes)
        case = shapes[0] if len(shapes) == 1 else "several-shapes"
        if len(found) == 1:
            return named(said, found[0], case, sha, facts)
        return Reason(f"{said}; {named_few(found)} are in {sha}", case)
    shapes, found = fitting_paths(joined, folders, here)
    if found:
        said = " and ".join(MISS_SHAPES[s][1] for s in slash + shapes)
        said = f"{said}; " if said else ""
        if len(found) == 1:
            return Reason(f"{said}{found[0]} is a directory in {sha}{DIRECTORY_REASON}", "directory")
        return Reason(f"{said}{named_few(found)} are directories in {sha}, not files", "directory")
    return Reason(f"{UNCOMMITTED_REASON}{path} is not in {sha}", "not-committed")

def named(said, spelling, case, sha, facts):
    """ALYDAS-218, Gate 6 (DF1 to DF3). A miss's reason, `said`, naming
    `spelling`, the entry its shapes reach. The fix records that spelling only
    when hold would pass it recorded; else the reason says what the entry is
    (a link to a folder, a submodule, a path through a link) and carries that
    reason's case and spelling, so its fix is the one that mends it."""
    judged = None if facts is None else recorded_reason(spelling, facts)
    if judged is None:
        return Reason(f"{said}; {spelling} is in {sha}", case, spelling)
    verb = "" if judged.case == "through-a-link" else "is "
    return Reason(f"{said}; {spelling} {verb}{judged}", judged.case, judged.spelling)

def recorded_reason(spelling, facts):
    """ALYDAS-218, Gate 6. What hold would say of `spelling` were it recorded:
    None when the desk opens it. Every spelling a fix offers is checked here."""
    bad = desk_cannot_open([spelling], facts)
    return bad[0][1] if bad else None

def beyond_links(joined, commit, files, facts):
    """ALYDAS-218, Gate 6 (DF2). Paths `files` cannot list: below each link to
    a folder and each submodule whose name, folded, is one of `joined`'s
    folders. Below a link, the files of the folder it reaches, said through
    the link; below a submodule, the rest of `joined`, since its files are not
    in this commit. `fitting_paths` then reads them as it reads files."""
    want = joined.split("/")
    folders = [fold(s) for s in want[:-1]]
    paths = []
    for name in files:
        last = fold(name.rpartition("/")[2])
        if last not in folders:
            continue
        found = facts.entry(commit, name)
        if found == ("submodule", None):
            paths += [f"{name}/{'/'.join(want[i + 1:])}" for i, s in enumerate(folders) if s == last]
        elif found is not None and found[0] == "symlink":
            reached, real = follow_links(commit, name, facts.entry)
            if reached is not None and reached[0] == "dir":
                below = f"{real}/" if real else ""
                paths += [f"{name}/{f[len(below):]}" for f in files if f.startswith(below)]
    return list(dict.fromkeys(paths))

def utf8(name):
    """A name read with `surrogateescape` whose bytes were UTF-8."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True

def named_few(found):
    """P3. Up to three named, then how many more: "a, b and 2 more"."""
    named = found[:3] if len(found) <= 3 else found[:3] + [f"{len(found) - 3} more"]
    return f"{', '.join(named[:-1])} and {named[-1]}"

def fold(segment):
    """ALYDAS-219, S4. A name with capital letters and Unicode spelling folded away."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", segment).casefold())

def fitting_paths(joined, pool, here):
    """ALYDAS-219, S4. The paths in `pool` that `joined` reaches once capital
    letters, Unicode spelling and a subfolder are allowed for, and the shapes
    they need, in `MISS_SHAPES`' order: ([], []) when none does. Segment by
    segment, so `x.html` never fits `ax.html`. Only the paths that need the
    fewest shapes are kept, the whole path before a subfolder, so a path one
    shape away is never lost among guesses. `here` rules a subfolder out."""
    want = joined.split("/")
    folded = [fold(s) for s in want]
    best, shapes, found = None, [], []
    for candidate in pool:
        names = candidate.split("/")
        if len(names) < len(want) or [fold(s) for s in names[-len(want):]] != folded:
            continue
        sub = len(names) > len(want)
        if sub and here:
            continue
        tail = "/".join(names[-len(want):])
        needs = [case for case, needed in (
            ("another-case", unicodedata.normalize("NFC", tail) != unicodedata.normalize("NFC", joined)),
            ("other-unicode", tail.casefold() != joined.casefold()),
            ("from-a-subfolder", sub)) if needed]
        rank = (sub, len(needs))
        if best is None or rank < best:
            best, shapes, found = rank, needs, [candidate]
        elif rank == best:
            found.append(candidate)
            shapes = [case for case in MISS_SHAPES if case in shapes or case in needs]
    return shapes, found

def why_not_on_github(facts):
    """The one reason every path shares when the desk has no commit to read, or None when it has one."""
    if facts.head is None:
        return Reason(NO_REPO_REASON, "no-repo")
    if not facts.has_origin:
        return Reason(NO_ORIGIN_REASON, "no-origin")
    if facts.on_github is not None:
        return None
    if not facts.branch:
        return Reason(DETACHED_REASON, "detached")
    if facts.branch in NEVER_PUSHED or facts.branch == facts.default:
        # commit_on_github() reads this checkout's origin/* refs, and hold does
        # not fetch, so origin may have the commit since.
        return Reason(f"on {facts.branch}{NEVER_PUSHED_REASON}, and origin does not have "
                      f"{facts.head[:7]} as far as this checkout last fetched", "never-pushed")
    return Reason(f"not pushed: {facts.branch} is not on origin at {facts.head[:7]}", "not-pushed")

def resolve_link(folder, target):
    """ALYDAS-218, S3. Where a symlink in `folder` whose text is `target`
    points, from the repository root, as GitHub resolved each target at Gate 4:
    joined to the link's folder and normalised, so `..`, `./` and `//` are
    followed. "" is the root. None for an absolute target, which GitHub does
    not follow, and for one that leaves the repository."""
    if target.startswith("/"):
        return None
    resolved = posixpath.normpath(posixpath.join(folder, target))
    if resolved == ".." or resolved.startswith("../"):
        return None
    return "" if resolved == "." else resolved

def climbs_only_folders(commit, folder, target, entry):
    """ALYDAS-218, Gate 8 (C2). Whether every `..` in a link's `target`, read
    from the link's `folder`, climbs out of a real folder of the commit. Past a
    link, `..` climbs out of where the link points, which `resolve_link` does
    not read: git calls such a link dangling, and GitHub answered its text
    (measured on scratch/alydas-218-c2 at c6e8d8b), so it is not followed."""
    parts = folder.split("/") if folder else []
    for segment in target.split("/"):
        if segment in ("", "."):
            continue
        if segment != "..":
            parts.append(segment)
        elif parts:
            found = entry(commit, "/".join(parts))
            if found is None or found[0] != "dir":
                return False
            parts.pop()
    return True

def shown(text):
    """ALYDAS-218, Gate 8 (C1). A name read with `surrogateescape`, its bytes
    that are not UTF-8 written as `\\xff`, so it can be printed and logged."""
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")

def follow_links(commit, sent, entry):
    """ALYDAS-218, S3. `entry`'s answer for `sent` once every link on the way,
    the last segment or an earlier one, is replaced by its target, and the path
    that answer is for. (None, None) when a target is outside the repository or
    the chain runs past LINK_HOPS."""
    path, found = sent, entry(commit, sent)
    for hop in range(LINK_HOPS + 1):
        if found is None or found[0] not in ("symlink", "through-symlink"):
            return found, path
        if hop == LINK_HOPS:
            break
        link = path if found[0] == "symlink" else found[1]
        rest = path[len(link):]
        target = found[1] if found[0] == "symlink" else entry(commit, link)[1]
        to = resolve_link(posixpath.dirname(link), target)
        if to is None or not climbs_only_folders(commit, posixpath.dirname(link), target, entry):
            break
        path = to + rest if to else rest[1:]
        found = entry(commit, path)
    return None, None

def desk_cannot_open(paths, facts):
    """One (path, reason) for each recorded path the desk could not open, in order; [] when all open."""
    bad = []
    shared = why_not_on_github(facts) if paths else None
    for path in paths:
        # ALYDAS-212, S2. The desk's own rule, before anything about the
        # repository: it refuses these whatever the commit holds.
        problem = desk_path_problem(path)
        if problem in ("empty", "absolute", "climbs-out"):
            bad.append((path, Reason(OUTSIDE_REASON, "outside")))
        elif problem == "nul":
            bad.append((path, Reason(NUL_REASON, "nul")))
        elif problem == "backslash":
            bad.append((path, slash_spelling_committed(path, facts, shared)))
        elif shared is not None:
            bad.append((path, shared))
        else:
            # ALYDAS-212, S1. Looked up as the URL sends it, so hold and the desk
            # agree; the reason still names the path as recorded. S3: only a
            # file opens, and a folder is named as a directory.
            sent = as_the_url_sends_it(path)
            why = entry_reason(path, sent, facts)
            if why is not None:
                bad.append((path, why))
    return bad

def entry_reason(path, sent, facts):
    """ALYDAS-218, S3. Why the commit's tree entry at `sent` would not open, or
    None when it is a file, or a link that reaches one (GitHub serves the
    target's page)."""
    commit = facts.on_github
    found = facts.entry(commit, sent)
    if found is None:
        # ALYDAS-212, S4. A miss names its case, from the commit's files,
        # which are read only here (P2).
        return explain_miss(path, sent, commit, facts.files(commit), facts.here(sent), facts)
    kind, detail = found
    if kind == "file":
        return None
    if kind == "dir":
        return Reason(f"{path} is a directory in {commit[:7]}{DIRECTORY_REASON}", "directory")
    if kind == "submodule":
        return Reason(SUBMODULE_REASON, "submodule")
    if kind == "in-submodule":
        return Reason(IN_A_SUBMODULE_REASON.format(module=detail), "in-a-submodule")
    reached, real = follow_links(commit, sent, facts.entry)
    opens = reached is not None and reached[0] == "file"
    if kind == "through-symlink":
        # The real path is the spelling when it is a committed file, so the
        # fix records it.
        said = THROUGH_A_LINK_REASON.format(link=detail)
        # ALYDAS-218, Gate 6. Named only when hold would pass it recorded.
        # Gate 8 (C1): never a name `note` would refuse.
        if opens and utf8(real) and recorded_reason(real, facts) is None:
            return Reason(f"{said}; {real} is in {commit[:7]}", "through-a-link", real)
        return Reason(said, "through-a-link")
    if opens:
        return None
    if reached is not None and reached[0] == "dir":
        return Reason(LINK_TO_A_FOLDER_REASON.format(target=shown(detail)), "link-to-a-folder")
    return Reason(LINK_TO_NOTHING_REASON.format(target=shown(detail)), "link-to-nothing")

def slash_spelling_committed(path, facts, shared):
    """ALYDAS-212, S2, P1. A backslash path's reason, naming its `/` spelling
    when the commit the desk reads has that file, so the fix can record it;
    without it when it does not, or when the desk has no commit to read. S3: a
    file, a blob; a folder under that spelling would not open either.
    ALYDAS-220, S1: the whole reason, the spelling carried as data.
    ALYDAS-218, S3: named when that spelling opens, read by its tree entries.
    Gate 6 (DF1): judged as hold judges it recorded, so a `/` spelling
    through a link is not named; the file that link reaches is, through
    `named`. A spelling hold would refuse for any other reason is not."""
    forward = path.replace("\\", "/")
    if shared is not None or desk_path_problem(forward) is not None:
        return Reason(BACKSLASH_REASON, "backslash")
    judged = recorded_reason(forward, facts)
    if judged is not None and (judged.case != "through-a-link" or judged.spelling is None):
        return Reason(BACKSLASH_REASON, "backslash")
    return named(BACKSLASH_REASON, forward, "backslash", facts.on_github[:7], facts)

def repo_facts():
    """The facts `desk_cannot_open` reads, from the repository `repo_root` finds. Never raises."""
    def git_bytes(*args):
        # ALYDAS-218, S3. Bytes, and names as committed: macOS git would
        # otherwise precompose an NFD spelling into the NFC name. None when
        # git fails.
        try:
            out = git_here("-c", "core.precomposeunicode=false", *args, text=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout if out.returncode == 0 else None
    trees, texts = {}, {}
    def listing(tree):
        # ALYDAS-218, S3, P3. One folder's entries, {name: (mode, object)},
        # names as bytes. Listed by the tree's object, never by a path, so no
        # name is read as a pathspec or a revision; each tree is listed once
        # per hold, however many paths walk it.
        if tree not in trees:
            got = {}
            for line in (git_bytes("ls-tree", "-z", tree) or b"").split(b"\0"):
                meta, tab, name = line.partition(b"\t")
                fields = meta.decode("ascii", "replace").split(" ")
                if tab and len(fields) == 3:
                    got[name] = (fields[0], fields[2])
            trees[tree] = got
        return trees[tree]
    def text_of(blob):
        # A symlink's text, its blob read as bytes.
        if blob not in texts:
            texts[blob] = (git_bytes("cat-file", "blob", blob) or b"").decode("utf-8", "surrogateescape")
        return texts[blob]
    def entry(commit, path):
        # ALYDAS-218, S3. The path walked segment by segment through the
        # commit's trees, each name matched byte for byte. An empty segment
        # before the last is a miss (a doubled /); an empty last one is the
        # folder it ends (a trailing /, as 212's `cat-file` had it), and a
        # file before it is a miss.
        if not isinstance(commit, str) or not COMMIT_SHAPE.fullmatch(commit):
            return None
        segments = path.split("/")
        tree = commit
        for i, segment in enumerate(segments):
            last = i == len(segments) - 1
            if segment == "":
                return ("dir", None) if last else None
            found = listing(tree).get(segment.encode("utf-8", "surrogateescape"))
            if found is None:
                return None
            mode, obj = found
            here = "/".join(segments[:i + 1])
            if mode == "160000":
                return ("submodule", None) if last else ("in-submodule", here)
            if mode == "120000":
                return ("symlink", text_of(obj)) if last else ("through-symlink", here)
            if mode != "040000":
                return ("file", None) if last and mode in ("100644", "100755") else None
            if last:
                return ("dir", None)
            tree = obj
        return None
    listed = {}
    def files(commit):
        # ALYDAS-212, S4, P2. Read from git once, the first time a path
        # missed, and never when every path opens. `-z` so a name git would
        # quote (a `"`, a non-ASCII letter) comes back as committed. A failed
        # read is no files, which leaves the miss "not committed".
        # ALYDAS-219, S4 (C3). Read as bytes, by the commit's object and no
        # path, each name decoded with `surrogateescape`, so one name that is
        # not UTF-8 no longer empties the list.
        if commit not in listed:
            out = (git_bytes("ls-tree", "-r", "-z", "--name-only", commit)
                   if isinstance(commit, str) and COMMIT_SHAPE.fullmatch(commit) else None)
            listed[commit] = [f.decode("utf-8", "surrogateescape") for f in (out or b"").split(b"\0") if f]
        return listed[commit]
    def here(path):
        # Gate 8, C1. The working tree, from the repository root.
        try:
            return os.path.isfile(os.path.join(repo_root(), path))
        except (OSError, ValueError):
            return False
    head = head_commit()
    if head is None:
        return RepoFacts(entry=entry, files=files)
    try:
        has_origin = "origin" in git_here("remote").stdout.split()
        branch = git_here("branch", "--show-current").stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return RepoFacts(head=head, entry=entry, files=files)
    default = None
    if has_origin and branch and branch not in NEVER_PUSHED:
        try:
            default = origin_default()
        except (OSError, subprocess.SubprocessError):
            default = None
    return RepoFacts(head=head, has_origin=has_origin, branch=branch, default=default,
                     on_github=commit_on_github(), entry=entry, files=files, here=here)

def recorded_paths(p):
    """The paths a phase recorded, as the desk lists them (`beat_ledger`)."""
    return [plain(x.get("path"), None) for x in (p.get("produced") or [])
            if isinstance(x, dict) and plain(x.get("path"), None)]

def forced_by_hand(p):
    """Whether a person forced this phase: `p["forced"]`, unless it is only a
    hold's own `--force`, which goes past the file check and nothing else
    (Gate 8, C1). A force the hold's went over is still the person's."""
    hold = p.get("hold") if isinstance(p.get("hold"), dict) else {}
    if p.get("forced") and p.get("forced") == hold.get("forced"):
        return bool(hold.get("forced_over"))
    return bool(p.get("forced"))

def find_run(slug, root=None):
    root = root or "docs/superpowers"
    cands = sorted(glob.glob(os.path.join(root, "boards", f"*-{slug}.json")))
    if not cands:
        die(f"no run named {slug!r} under {root}/boards (run init first)")
    return cands[-1]

def load(path):
    with open(path) as f:
        return json.load(f)

def save(path, run):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(run, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)

def events_path(path):
    return path[:-5] + ".events.jsonl"

def html_path(path):
    return path[:-5] + ".html"

def read_events(path):
    p = events_path(path)
    if not os.path.exists(p):
        return []
    out = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out

def append_event(path, ev):
    with open(events_path(path), "a") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")

def log_event(path, phase, event, note, extra=None):
    ev = {"ts": now_iso(), "session": os.environ.get("CLAUDE_CODE_SESSION_ID", ""),
          "cwd": os.getcwd(), "phase": phase, "event": event, "note": note or ""}
    if extra:
        ev.update(extra)
    append_event(path, ev)
    return ev

def phase_of(run, n):
    for p in run["phases"]:
        if p["n"] == n:
            return p
    die(f"phase {n} does not exist (0-9)")

def fmt_dur(s):
    if s is None:
        return "–"
    s = int(s)
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"

def fmt_k(n):
    if n is None:
        return "–"
    n = int(n)
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n/1000:.0f}k" if n >= 10_000 else f"{n/1000:.1f}k"
    return f"{n/1_000_000:.1f}M"

def fmt_usd(x):
    if x is None:
        return "–"
    return f"${x:,.0f}" if x >= 10 else f"${x:.2f}"

# ---------- commands ----------

def cmd_init(a):
    root = a.root
    os.makedirs(os.path.join(root, "boards"), exist_ok=True)
    date = a.date or dt.date.today().isoformat()
    path = os.path.join(root, "boards", f"{date}-{a.slug}.json")
    if os.path.exists(path) and not a.force:
        die(f"{path} exists; pass --force to overwrite")
    run = {
        "schema": 2,
        "slug": a.slug, "title": a.title, "date": date, "root": root,
        "repo": a.repo or os.path.basename(repo_root()), "branch": a.branch or git_branch(),
        "goal": a.goal, "goal_path": a.goal_path or "",
        "phases": [], "deferred": [], "events": [], "telemetry": {},
        # ALYDAS-175, S5. The phases that wait for a person's review.
        "holds": list(a.hold),
    }
    # ALYDAS-223, Gate 8. The commit the run started from, when git can say:
    # the UI gate reads the run's own change against it (`changed_files`).
    base = run_base()
    if base:
        run["base"] = base
    # ALYDAS-17, S5. Only when one was stated: no key is no budget.
    if a.budget is not None:
        run["budget_usd"] = a.budget
    for n, key, name, kind in PHASES:
        run["phases"].append({
            "n": n, "key": key, "name": name, "kind": kind, "status": "pending",
            "summary": "", "next": "", "decided": [], "measured": [], "wrong": [],
            "overruled": [], "produced": [], "table": None, "skip_reason": "",
            "forced": "", "started": "", "ended": "", "telemetry": {},
            # ALYDAS-173, S1. One sentence a newcomer understands, under the
            # phase's name on the board and the desk; `phase done` needs it.
            "plain": "",
            # ALYDAS-173, S2. Where the story is, and Frame's backstory, what
            # "fixed" means and what won't change. No purpose: `phase start`
            # stamps it.
            "scene": "", "backstory": "", "fixed_means": [], "wont_change": [],
            # ALYDAS-173, S3. The Story view's packets: the lede, what the
            # numbers mean, the options weighed, what was assumed, what was
            # left out, and what went wrong with what was done about it.
            "lede": "", "numbers": [], "options": [], "assumed": [], "left_out": [], "problems": [],
            # ALYDAS-173, S4. The phase's to-do list, seeded at its start.
            "steps": [],
        })
    # ALYDAS-173, S3. The codes this run uses, each with its plain name.
    run["terms"] = {}
    # ALYDAS-173, S6. The run's short version, which `story` writes.
    run["story"] = {"happened": [], "stands": "", "needs_you": []}
    save(path, run)
    open(events_path(path), "a").close()
    refresh(path)
    print(path)

def refuse_unclosable(path, run, n, held=False):
    """Exit 2 when phase n's artifact checks fail, saying what is missing: in
    words on the run log, and with the command to type on stderr. Shared by
    `phase n done` and `hold` (ALYDAS-175, S2). `held` is the hold's call."""
    problems = run_checks(run, n)
    say_ui_gate_notices(run, n, sys.stderr)
    if problems:
        # Gate 8, ST-1. The log says what is missing in words; the command
        # line to type goes to stderr below, for the agent.
        log_event(path, n, "check", "; ".join(problem.plain for problem in problems))
        # ALYDAS-223, Gate 8, security 7. A problem may quote a report or name a file.
        print(printable(f"phase {n} cannot close: " + "; ".join(problems)), file=sys.stderr)
        # ALYDAS-225, K1. A hold's `--force` goes past the desk-can-open check
        # only, never these, so a hold does not offer it.
        if held:
            print(f"fix them, then run hold {run['slug']} {n} again.", file=sys.stderr)
        else:
            print("fix them, or pass --force \"reason\" to close anyway (recorded on the board).", file=sys.stderr)
        sys.exit(2)

def say_ui_gate_notices(run, n, to):
    """ALYDAS-223, Gate 6. Beside Phase 8's check, passed or refused: what the
    UI gate says without refusing (`ui_gate_notices`), a line each."""
    if n == 8:
        for line in ui_gate_notices(run):
            print(printable(f"note: {line}"), file=to)

def refuse_unheld(run, n, event):
    """ALYDAS-175, S5. Exit 2 when `phase n done` would close a holding phase
    without the person's review, or `phase n start` would start past a holding
    phase that is not answered yet (running, blocked, or never held).
    ALYDAS-174, Gate 8 W-3: `phase n skipped` on a holding phase is refused
    as `done` is, since a skip passes the hold (HOLD_PASSED) just as well."""
    holds = run_holds(run)
    slug = run["slug"]
    if event in ("done", "skipped") and n in holds:
        verb, how = ("closed", "close") if event == "done" else ("skipped", "skip")
        print(f"phase {n} waits for your review on the desk, so it is not {verb} by hand: "
              f"run hold {slug} {n}, which asks and closes it on the answer "
              f"(or pass --force \"reason\" to {how} it anyway, recorded on the board).", file=sys.stderr)
        sys.exit(2)
    if event == "start":
        for h in holds:
            if h < n and phase_of(run, h).get("status") not in HOLD_PASSED:
                waiting = phase_of(run, h)
                label = "Gate" if waiting.get("kind") == "gate" else "Phase"
                print(f"phase {n} cannot start: {label} {h} ({waiting.get('name', '')}) waits for your review "
                      f"and is {waiting.get('status')}: run hold {slug} {h} and wait for the answer "
                      f"(or pass --force \"reason\" to start anyway, recorded on the board).", file=sys.stderr)
                sys.exit(2)

def refuse_skipped_review(run):
    """ALYDAS-223, S2. Exit 2 when Phase 8 would be skipped on a run whose
    changed files include a rendered surface, or cannot be worked out."""
    surfaces, problem = ui_gate_surfaces(run)
    if problem:
        print(printable(f"phase 8 cannot be skipped: {problem}, and a run that touched a screen cannot skip the "
                        f"review (or pass --force \"reason\" to skip anyway, recorded on the board)."), file=sys.stderr)
        sys.exit(2)
    if surfaces:
        print(printable(f"phase 8 cannot be skipped: a run that touched a screen cannot skip the review, and this one "
                        f"changed {surface_names(surfaces)} (or pass --force \"reason\" to skip anyway, recorded on "
                        f"the board)."), file=sys.stderr)
        sys.exit(2)

def cmd_phase(a):
    path = find_run(a.slug, a.root)
    run = load(path)
    phase_of(run, a.n)
    # The checks and the plain line first (a holding phase needs them too, and
    # `hold` runs the same ones), then the hold: no hand close, no start past it.
    if a.event == "done" and not a.force:
        refuse_unclosable(path, run, a.n)
    if not a.force:
        refuse_unheld(run, a.n, a.event)
    if a.event == "skipped" and not a.note:
        die("skipping needs --note with the reason; it is shown on the board")
    # ALYDAS-223, S2. `skipped` runs no check, so Phase 8's review, the UI gate
    # with it, could be stepped round on a run that drew something.
    if a.event == "skipped" and a.n == 8 and not a.force:
        refuse_skipped_review(run)
    over_budget(stamp_phase(path, a.n, a.event, a.note, a.force))

def next_step_up(p, after=-1):
    """ALYDAS-225, K3. A running phase always has a step doing: when none is,
    the first to-do step after step `after` (counted from 0), else the first
    to-do step anywhere, becomes the one doing. A phase with a step doing, or
    with none left to-do, is left as it is. Pure: it changes `p` and nothing
    else. Returns the step it moved, counted from 0, or None. Gate 6: called
    wherever the script changes a running phase's steps, so the rule is one."""
    steps = p.get("steps") or []
    if any(step_state(step) == "doing" for step in steps):
        return None
    for i in list(range(after + 1, len(steps))) + list(range(0, after + 1)):
        if step_state(steps[i]) == "todo":
            steps[i]["state"] = "doing"
            return i
    return None

def stamp_phase(path, n, event, note=None, force=None, finish_step=None, waiting=False, push=True):
    """Stamp one phase boundary: the ledger, the board, then the desk, then the
    boundary block, then whatever the desk said. `phase` and `hold` both close
    through here, so telemetry and the block behave the same. `finish_step`
    closes the step being done (by default only on `done`). `push=False` when
    the caller pushed already (ALYDAS-206: hold pushes once, before its check)."""
    run = load(path)
    p = phase_of(run, n)
    words = note or (p["summary"] if event != "start" else "")
    ev = log_event(path, n, event, words, {"forced": force} if force else None)
    if force:
        p["forced"] = force
    p["status"] = EVENT_TO_STATUS[event]
    if event == "start":
        p["started"] = ev["ts"]
        p["ended"] = ""
        # ALYDAS-173, S2. Always, so a reworded purpose lands at the next start.
        if n in PHASE_PURPOSE:
            p["purpose"] = PHASE_PURPOSE[n]
        # ALYDAS-173, S4. Only on a phase with no steps, so a restarted phase
        # keeps its ticks and a list the agent wrote is not seeded over.
        if not p.get("steps") and n in PHASE_STEPS:
            p["steps"] = [{"text": text, "state": "todo", "sub": ""} for text in PHASE_STEPS[n]]
        # ALYDAS-225, K3. The start beat itself carries a step doing, so a
        # running phase is never shown with every step to-do. Also on a restart
        # (a send-back's among them) and on steps the agent added before the start.
        next_step_up(p)
    else:
        p["ended"] = ev["ts"]
        if not p["started"]:
            p["started"] = ev["ts"]
    if event == "skipped":
        p["skip_reason"] = note
    # ALYDAS-173, S4. A phase that closes finished the step it was on; a step
    # still todo stays todo, so a skipped step shows.
    if event == "done" if finish_step is None else finish_step:
        for step in p.get("steps") or []:
            if step.get("state") == "doing":
                step["state"] = "done"
    save(path, run)
    refresh(path)
    # After the stamp and after the render, so what the desk is told is what the
    # board already says, and a desk that is down cannot lose the boundary.
    fresh = load(path)
    ending = n == 9 and event == "done"
    # S6. Before the beat, so the commit it names is on GitHub when the desk
    # asks for a file at it; only when there is a desk to tell.
    if push and desk_configured():
        push_branch()
    directive = heartbeat(fresh, n, event=event, ended=ending, path=path)
    print(status_block(fresh, n))
    return obey(path, fresh, n, directive, ended_already=ending, waiting=waiting)

# ALYDAS-173, S3. The Story view's packets as `note` takes them, each read in
# full before anything is written, so a refused one leaves the ledger as it was.
def story_number(value):
    figure, _, means = value.partition("|")
    if not figure.strip():
        die(f'--number "{value}": write it as "figure|what it means", with a figure before the bar')
    return {"figure": figure.strip(), "means": means.strip()}

def story_option(value):
    parts = value.split("|", 2)
    if len(parts) < 2 or parts[1].strip() not in ("chosen", "rejected") or not parts[0].strip():
        die(f'--option "{value}": write it as "name|chosen|why" or "name|rejected|why"')
    return {"name": parts[0].strip(), "chosen": parts[1].strip() == "chosen",
            "why": parts[2].strip() if len(parts) > 2 else ""}

def story_assumed(value):
    text, _, later = value.partition("|")
    if not text.strip():
        die(f'--assumed "{value}": write it as "what was assumed|what came of it later", the second half optional')
    # Gate 7 (Sachit). The desk prints "Later:" itself, so one written in
    # front of the text is taken off rather than shown twice.
    return {"text": text.strip(), "later": re.sub(r"^later\s*:\s*", "", later.strip(), flags=re.I)}

def packet_words(event, entry):
    """Gate 8, ST-1. A Story packet's entry as the run log reads it: the halves
    in words, never the `a|b|c` the flag takes or Python's True and False."""
    if isinstance(entry, str):
        return entry
    if event == "number":
        return entry["figure"] + (", " + entry["means"] if entry["means"] else "")
    if event == "option":
        head = f"{entry['name']} ({'chosen' if entry['chosen'] else 'not chosen'})"
        return head + (": " + entry["why"] if entry["why"] else "")
    return entry["text"] + (f" (later: {entry['later']})" if entry["later"] else "")

def story_problem(value):
    """`what => did` as a problem, or None for a line with no ` => ` or an empty half before it."""
    what, arrow, did = value.partition(" => ")
    return {"what": what.strip(), "did": did.strip()} if arrow and what.strip() else None

# Gate 8, ST-2. What each Story flag of `note` is held to, in one place: a
# text flag names its bound in LEDGER_BOUNDS; a flag with halves names the
# Story packet whose halves (STORY_ENTRY_FIELDS) carry the bounds, and how its
# value is read into them. A `--wrong` line with no ` => ` has no halves.
NOTE_BOUNDS = {
    "plain": "plain", "scene": "scene", "backstory": "backstory", "lede": "lede",
    "fixed-means": "storyEntry", "wont-change": "storyEntry", "left-out": "storyEntry",
    "number": ("numbers", story_number), "option": ("options", story_option),
    "assumed": ("assumed", story_assumed), "wrong": ("problems", story_problem),
}

def note_bounds(a):
    """Refuse, before anything is written, any Story text `note` was given past
    its bound, naming the flag and the limit, as `--doc` does. Counted as the
    desk counts: trimmed, in code points."""
    for flag, rule in NOTE_BOUNDS.items():
        given = getattr(a, flag.replace("-", "_"))
        for value in given if isinstance(given, list) else [] if given is None else [given]:
            if isinstance(rule, str):
                size = len(plain(value, None))
                if size > LEDGER_BOUNDS[rule]:
                    die(f"--{flag} is {size} characters; keep it to {LEDGER_BOUNDS[rule]}")
                continue
            packet, read = rule
            entry = read(value)
            for field, bound in STORY_ENTRY_FIELDS[packet] if entry else ():
                size = len(plain(entry[field], None))
                if size > LEDGER_BOUNDS[bound]:
                    die(f"--{flag}: the {field} is {size} characters; keep it to {LEDGER_BOUNDS[bound]}")

def note_utf8(a):
    """ALYDAS-219, S5. Refuse, before anything is written, any path or text
    `note` was given with a byte that is not UTF-8 (argv brings it in as a
    surrogate): the event log could not hold it and the desk could not send
    it. A path is named, as its bytes, with the fix; a text names its flag."""
    for key, given in vars(a).items():
        for value in given if isinstance(given, list) else [given]:
            if key in ("root", "slug") or not isinstance(value, str) or utf8(value):
                continue
            pth = {"produced": value.partition("=")[0], "doc": value.split("|", 1)[0],
                   "drop_produced": value}.get(key, "").strip()
            if pth and not utf8(pth):
                shown = printable(os.fsencode(pth).decode("utf-8", "backslashreplace"))
                die(f"{shown}: a byte that is not UTF-8, which the desk cannot send; rename the file and record the new name")
            die(f"--{key.replace('_', '-')}: a byte that is not UTF-8, which the desk cannot send; write it again in UTF-8")

def note_doc(value):
    """ALYDAS-173, S5. `path|title|about|in short` as a produced file's document
    fields, each trimmed, or a refusal: the path and the title are required,
    and each text is held to the desk's bound here, before anything is written.
    The in-short text is the rest of the line, so a pipe inside it stays."""
    parts = [part.strip() for part in value.split("|", 3)]
    parts += [""] * (4 - len(parts))
    pth, title, about, in_short = parts
    if not pth or not title:
        die(f'--doc "{value[:60]}": a document is "path|title|about|in short", and needs its path and title')
    for text, field, words in ((title, "title", "title"), (about, "about", "about line"), (in_short, "inShort", "in-short text")):
        if len(text) > LEDGER_BOUNDS[field]:
            die(f"--doc: the {words} is {len(text)} characters; keep it to {LEDGER_BOUNDS[field]}")
    doc = {"title": title}
    if about:
        doc["about"] = about
    if in_short:
        doc["in_short"] = in_short
    return pth, doc

def cmd_note(a):
    path = find_run(a.slug, a.root)
    run = load(path)
    p = phase_of(run, a.n)
    note_utf8(a)
    docs = [note_doc(x) for x in a.doc or []]
    note_bounds(a)
    # Gate 8, ST1. A recorded path dropped, exactly as recorded, before any
    # file is produced, so one note mends a path hold refused. A path the
    # phase never recorded is refused before anything is written.
    for gone in a.drop_produced or []:
        if not any(e.get("path") == gone for e in p["produced"]):
            die(f"phase {a.n} recorded no file {gone}: nothing dropped")
    packets = {
        "numbers": ("number", [story_number(x) for x in a.number or []]),
        "options": ("option", [story_option(x) for x in a.option or []]),
        "assumed": ("assumed", [story_assumed(x) for x in a.assumed or []]),
        "left_out": ("left-out", [x for x in a.left_out or []]),
    }
    # ALYDAS-173, S2. Frame's three belong to Frame; refused before anything is written.
    if a.n != 0:
        for key, flag in FRAME_NOTES.items():
            if getattr(a, key) is not None:
                die(f"--{flag} is Frame's: write it on phase 0 (note {a.slug} 0 --{flag} \"...\")")
    # ALYDAS-225, Gate 8. The steps of a phase that has started are its
    # account of the work, and the close reads them: cleared while one was
    # to-do or being done, the list was empty, the close found nothing to-do
    # and nothing said the steps had been there. Refused before anything is
    # written. A phase not yet started has done none of them, and a list all
    # done or dropped has nothing the close would have named.
    if "steps" in (a.clear or []) and p.get("status") in ("active", "blocked"):
        open_steps = [f'step {i} ("{step.get("text", "")}")' for i, step in enumerate(p.get("steps") or [], start=1)
                      if step_state(step) in ("todo", "doing")]
        if open_steps:
            die(f"phase {a.n} has started, and {', '.join(open_steps)} {'is' if len(open_steps) == 1 else 'are'} still "
                f"to do or being done, so its steps are not cleared and nothing was written: drop each with its reason "
                f'(step {a.slug} {a.n} <i> dropped --sub "<why>"), or add your own with step {a.slug} {a.n} "<text>" --add')
    if a.summary is not None:
        p["summary"] = a.summary
        log_event(path, a.n, "result", a.summary)
    # ALYDAS-173, S1. The phase's plain line, logged the way a summary is.
    if a.plain is not None:
        p["plain"] = a.plain
        log_event(path, a.n, "plain", a.plain)
    if a.next is not None:
        p["next"] = a.next
    # ALYDAS-173, S2. Where the story is, and Frame's backstory, each logged.
    if a.scene is not None:
        p["scene"] = a.scene
        log_event(path, a.n, "scene", a.scene)
    if a.backstory is not None:
        p["backstory"] = a.backstory
        log_event(path, a.n, "backstory", a.backstory)
    for key in ("fixed_means", "wont_change"):
        for item in getattr(a, key) or []:
            entries = p.setdefault(key, [])
            if item not in entries:
                entries.append(item)
                log_event(path, a.n, FRAME_NOTES[key], item)
    # ALYDAS-173, S3. The lede, logged, and the packets appended, a repeat ignored.
    if a.lede is not None:
        p["lede"] = a.lede
        log_event(path, a.n, "lede", a.lede)
    for key, (event, entries) in packets.items():
        for entry in entries:
            kept = p.setdefault(key, [])
            if entry not in kept:
                kept.append(entry)
                log_event(path, a.n, event, packet_words(event, entry))
    for key in ("decided", "measured", "wrong", "overruled"):
        for item in getattr(a, key) or []:
            # ALYDAS-173, S3. A line that says what went wrong and what was
            # done about it (`what => did`) is also a problem for Story; the
            # whole line stays in `wrong`, which Technical draws.
            problem = story_problem(item) if key == "wrong" else None
            if item not in p[key]:
                p[key].append(item)
                # Gate 8, ST-1. The log reads the arrow as words.
                log_event(path, a.n, key, f"{problem['what']}; done about it: {problem['did']}"
                          if problem and problem["did"] else item)
            if problem is not None and problem not in p.setdefault("problems", []):
                p["problems"].append(problem)
    # Gate 8, C-3. A path already produced (named by `--doc`, or produced
    # before) is one file: its note is updated, not a second entry added. A
    # path given with no note keeps the note it has.
    for gone in a.drop_produced or []:
        p["produced"] = [e for e in p["produced"] if e.get("path") != gone]
        log_event(path, a.n, "dropped", gone)
    for item in a.produced or []:
        pth, _, note = item.partition("=")
        pth, note = pth.strip(), note.strip()
        entry = next((e for e in p["produced"] if e.get("path") == pth), None)
        if entry is None:
            p["produced"].append({"path": pth, "note": note})
        elif note and entry.get("note") != note:
            entry["note"] = note
        else:
            continue
        log_event(path, a.n, "wrote", pth + (": " + note if note else ""))
    # ALYDAS-173, S5. A document upserts by path: a file already produced keeps
    # its note and gains the title, about line and in-short text given (one not
    # given is kept); a new path is added with no note. Logged as a file written.
    for pth, doc in docs:
        entry = next((e for e in p["produced"] if e.get("path") == pth), None)
        if entry is None:
            entry = {"path": pth, "note": ""}
            p["produced"].append(entry)
        entry.update(doc)
        log_event(path, a.n, "wrote", pth + ": " + doc["title"])
    if a.clear:
        for key in a.clear:
            # ALYDAS-173, S2. `fixed-means` and `wont-change` name their keys
            # with underscores, and are lists on a ledger that never had them.
            key = key.replace("-", "_")
            # Gate 8. A clear of the steps leaves a line in the run log.
            if key == "steps":
                count = len(p.get("steps") or [])
                log_event(path, a.n, "step", f"cleared {count} step{'' if count == 1 else 's'}")
            p[key] = [] if key in LIST_NOTES or isinstance(p.get(key), list) else ""
    save(path, run)
    refresh(path)
    # S9 (Sachit, 2026-09-26: "keep everything within"). A produced file, the
    # Phase 2 variants page above all, is shown in the desk's run view while
    # its phase is still open, so a note that records one pushes and beats the
    # way a boundary does: the push first, so the commit the beat names is one
    # GitHub can serve the file at.
    #
    # ALYDAS-173 Gate 8. Every note beats, not only one that records a file:
    # the plain line, the scene, the lede and the rest are the desk's Story
    # view, and the run log shows each as it is written.
    # Gate 8 (ruling). Only a note that records a file pushes: a push can
    # start a preview build, and nothing else a note sends needs the branch
    # on origin. The beat's `commit` is still sent only once it is there.
    beat = beat_the_write(path, push=bool(a.produced or docs))
    print(f"phase {a.n} noted")
    over_budget(obey_the_write(path, beat))

def beat_the_write(path, push=False):
    """One beat for a write between boundaries (a note, a step, a story, a log
    line), or None with no desk. Pushed first only when `push`: a note that
    records a file, which the desk opens at the commit the beat names. It names the phase the run is
    in, not the phase written to (Gate 8, F2: the ledger is edited backwards,
    and a note on Phase 0 while Phase 2 runs must not rewind the Now card),
    and a write after the run has ended keeps it ended. Answers what `obey`
    needs, or None when the desk said nothing to obey."""
    if not desk_configured():
        return None
    fresh = load(path)
    now = current_phase(fresh)
    ending = phase_of(fresh, 9).get("status") == "done"
    if push:
        push_branch()
    directive = heartbeat(fresh, now, ended=ending, path=path)
    return (directive, now, ending) if directive else None

def obey_the_write(path, beat, waiting=False):
    """Obey what the desk answered a write's beat: a stop exits 3; a budget
    stop is answered BUDGET_REASON, for the command's `over_budget`."""
    if beat:
        directive, now, ending = beat
        return obey(path, load(path), now, directive, ended_already=ending, waiting=waiting)
    return None

def waits_on_a_hold(p):
    """Whether phase `p` is blocked by a hold that was recorded, not by hand:
    `hold` writes `p["hold"]` before it blocks the phase, and `phase <n>
    blocked` writes none. `hold` resumes such a wait, and (ALYDAS-225, Gate 8)
    `step` leaves no step to-do on one."""
    return p.get("status") == "blocked" and isinstance(p.get("hold"), dict)

def step_list(steps):
    """The phase's steps as `step` names them, one per line, for a refusal."""
    return "\n".join(f"  {i}. {s.get('text', '')} ({s.get('state', 'todo')})" for i, s in enumerate(steps, start=1)) or "  (none)"

def cmd_step(a):
    """ALYDAS-173, S4 (criterion 6). Tick one of a phase's steps: set its state,
    the line under it, or add it. One step is doing at a time, so moving one to
    doing marks the one doing before it done: the agent moved on. ALYDAS-225,
    K2: a step not done is dropped, with its reason under it. K3: on a running
    phase, done or dropped moves the next to-do step to doing. Everything is
    checked before anything is written. Every call beats, as a note does,
    naming the phase the run is in."""
    path = find_run(a.slug, a.root)
    run = load(path)
    p = phase_of(run, a.n)
    steps = p.setdefault("steps", [])
    if a.sub is not None and len(a.sub.strip()) > LEDGER_BOUNDS["stepSub"]:
        die(f"--sub is {len(a.sub.strip())} characters; keep the line under a step to {LEDGER_BOUNDS['stepSub']}")
    if a.add:
        text = a.which.strip()
        if not text or len(text) > LEDGER_BOUNDS["stepText"]:
            die(f"--add: a step is a line of 1 to {LEDGER_BOUNDS['stepText']} characters")
        steps.append({"text": text, "state": "todo", "sub": ""})
        index = len(steps) - 1
    elif a.which.isdigit():
        index = int(a.which) - 1
        if not 0 <= index < len(steps):
            die(f"phase {a.n} has no step {a.which}; its steps are:\n{step_list(steps)}" if steps
                else f"phase {a.n} has no steps yet; add one with --add")
    else:
        matches = [i for i, s in enumerate(steps) if s.get("text") == a.which]
        if not matches:
            die(f'phase {a.n} has no step "{a.which}"; its steps are:\n{step_list(steps)}' if steps
                else f"phase {a.n} has no steps yet; add one with --add")
        index = matches[0]
    step = steps[index]
    if a.state is not None:
        if a.state == "doing":
            for i, other in enumerate(steps):
                if i != index and other.get("state") == "doing":
                    other["state"] = "done"
        # ALYDAS-225, Gate 8. The line under a dropped step is why it was
        # dropped. Moved to another state, that line would be read as what the
        # step is doing or what came of it, so it goes with the drop unless the
        # call gives the step a line of its own (`--sub`, below).
        if step.get("state") == "dropped" and a.state != "dropped" and a.sub is None:
            step["sub"] = ""
        step["state"] = a.state
    if a.sub is not None:
        step["sub"] = a.sub.strip()
    # ALYDAS-225, K2. A dropped step says why: the reason is given with the
    # drop, and is not blanked afterwards, because the desk refuses a whole
    # beat that carries a dropped step with no line under it. Gate 8: tested
    # as the beat will send it (`step_reason`), not as it was typed, since the
    # beat cleans a line and a reason of control characters alone is then no
    # line at all. Nothing is written yet.
    if (a.state == "dropped" and not step_reason(a.sub)) or (step.get("state") == "dropped" and not step_reason(step.get("sub"))):
        # Gate 8. A step being added is not on the ledger yet, so it has no
        # number to name: the fix is the same `--add` call, with its reason.
        named = f"{shlex.quote(step['text'])} dropped --add" if a.add else f"{index + 1} dropped"
        die(f'a dropped step says why: step {a.slug} {a.n} {named} --sub "<why>"')
    # ALYDAS-225, Gate 8. A hold checked the steps before it asked, and the
    # answer closes the phase without checking again (`hold_answered`). So a
    # step is not left to-do while it waits: refused here, at the source. A
    # step added done or dropped, and a line alone, leave nothing to do and are
    # taken. A phase blocked by hand closes through `phase done`, which checks.
    if waits_on_a_hold(p) and step_state(step) == "todo" and (a.add or a.state is not None):
        ways = (f'add it done (step {a.slug} {a.n} "<text>" done --add) or dropped '
                f'(step {a.slug} {a.n} "<text>" dropped --sub "<why>" --add)' if a.add else
                f'leave step {index + 1} as it is, or drop it (step {a.slug} {a.n} {index + 1} dropped --sub "<why>")')
        die(f"phase {a.n} is waiting on a person's answer on the desk, so no step can be left to do on it: "
            f"the answer would close the phase over it. Nothing was written: {ways}, or wait for the answer")
    # ALYDAS-225, K3. A step finished or dropped on the phase that is running
    # hands on to the next, so there is one doing between ticks until none is
    # left. A phase that is not running (never started, blocked, closed) is
    # ticked as it is: nothing on it is being done. Gate 6: whatever the call
    # did (`todo` on the one doing, `--add`, a line alone), not only after done
    # or dropped, so a running phase with a step to-do is never left with none
    # doing. `todo` on the only step left to do therefore stays doing.
    moved = next_step_up(p, index) if p.get("status") == "active" else None
    # Gate 6. Said, so a step the command did not name going to doing (or the
    # one it set to todo staying doing) does not surprise the agent. Gate 8:
    # and logged, in the same words, so the run log shows the move the rule
    # made as well as the one the command named. The first event is the named
    # step in the state it ended in, which the rule may have changed.
    rule_moved = None
    if moved == index and a.state == "todo":
        rule_moved = (f"step {moved + 1} stays doing: it is the only step still to do, "
                      f"and a running phase always has one step doing")
    elif moved is not None:
        rule_moved = f"step {moved + 1} is now doing: a running phase always has one step doing"
    log_event(path, a.n, "step", f"step {index + 1} {step.get('state', 'todo')}: {step.get('text', '')}")
    if rule_moved:
        log_event(path, a.n, "step", rule_moved)
    save(path, run)
    refresh(path)
    beat = beat_the_write(path)
    print(f"phase {a.n} step {index + 1} {step.get('state', 'todo')}")
    if rule_moved:
        print(f"phase {a.n} {rule_moved}")
    over_budget(obey_the_write(path, beat))

def story_need(value):
    """ALYDAS-173, S6. `--needs-you "what|why"` as the ledger keeps it: split on
    the first pipe, so a pipe inside the why stays; the why may be left out."""
    what, _, why = value.partition("|")
    what, why = what.strip(), why.strip()
    if not what:
        die(f'--needs-you "{value}": write it as "what|why", and say what')
    if len(what) > LEDGER_BOUNDS["needWhat"]:
        die(f"--needs-you: what is waiting is {len(what)} characters; keep it to {LEDGER_BOUNDS['needWhat']}")
    if len(why) > LEDGER_BOUNDS["needWhy"]:
        die(f"--needs-you: why is {len(why)} characters; keep it to {LEDGER_BOUNDS['needWhy']}")
    return {"what": what, "why": why}

def cmd_story(a):
    """ALYDAS-173, S3. The run's terms: each code the run's texts use, with its
    plain name, which the desk shows when a reader points at the code. Read in
    full before anything is written.

    S6: and the run's short version, the band at the top of its desk page:
    what happened (lines, appended, a repeat ignored), where it stands (one
    line, replaced), and what is waiting on Sachit (appended). Each is held to
    the desk's bounds here, and a call that would pass one exits 1 with
    nothing written. Every call beats, as a step does, so a finished run's band
    appears without waiting for a phase event."""
    terms = []
    for value in a.term or []:
        code, equals, name = value.partition("=")
        if not equals or not code.strip() or not name.strip():
            die(f'--term "{value}": write it as "CODE=plain name"')
        terms.append((code.strip(), name.strip()))
    happened = [line.strip() for line in a.happened or []]
    for line in happened:
        if not line or len(line) > LEDGER_BOUNDS["happenedLine"]:
            die(f"--happened: a line of what happened is 1 to {LEDGER_BOUNDS['happenedLine']} characters")
    stands = a.stands.strip() if a.stands is not None else None
    if stands is not None and len(stands) > LEDGER_BOUNDS["stands"]:
        die(f"--stands is {len(stands)} characters; keep where the run stands to {LEDGER_BOUNDS['stands']}")
    needs = [story_need(value) for value in a.needs_you or []]
    path = find_run(a.slug, a.root)
    run = load(path)
    kept = run.setdefault("terms", {})
    clear = set(a.clear or [])
    story = run.get("story") if isinstance(run.get("story"), dict) else {}
    story = {"happened": list(story.get("happened") or []), "stands": story.get("stands") or "",
             "needs_you": list(story.get("needs_you") or [])}
    if "happened" in clear:
        story["happened"] = []
    if "stands" in clear:
        story["stands"] = ""
    if "needs-you" in clear:
        story["needs_you"] = []
    said_now = []
    for line in happened:
        if line not in story["happened"]:
            story["happened"].append(line)
            said_now.append("What happened: " + line)
    if stands is not None and stands != story["stands"]:
        story["stands"] = stands
        said_now.append("Where it stands: " + stands)
    for need in needs:
        if need not in story["needs_you"]:
            story["needs_you"].append(need)
            said_now.append("Waiting on you: " + need["what"])
    if len(story["happened"]) > LEDGER_BOUNDS["happened"]:
        die(f"--happened: the short version holds {LEDGER_BOUNDS['happened']} lines of what happened; "
            f"--clear happened and write them again")
    if len(story["needs_you"]) > LEDGER_BOUNDS["needsYou"]:
        die(f"--needs-you: the short version holds {LEDGER_BOUNDS['needsYou']} things waiting on you; "
            f"--clear needs-you and write them again")
    run["story"] = story
    if "terms" in clear:
        kept.clear()
    for code, name in terms:
        if kept.get(code) != name:
            kept[code] = name
            log_event(path, None, "term", f"{code} stands for {name}")
    for note in said_now:
        log_event(path, None, "story", note)
    save(path, run)
    refresh(path)
    beat = beat_the_write(path)
    print("story noted")
    over_budget(obey_the_write(path, beat))

def cmd_table(a):
    path = find_run(a.slug, a.root)
    run = load(path)
    p = phase_of(run, a.n)
    cols = [c.strip() for c in a.columns.split(",")] if a.columns else None
    if p["table"] is None or a.replace:
        if not cols:
            die("a new table needs --columns")
        p["table"] = {"columns": cols, "rows": []}
    elif cols:
        p["table"]["columns"] = cols
    for r in a.row or []:
        cells = [c.strip() for c in r.split("|")]
        p["table"]["rows"].append(cells)
        log_event(path, a.n, "row", " / ".join(cells))
    save(path, run)
    refresh(path)
    print(f"phase {a.n} table: {len(p['table']['rows'])} rows")

def cmd_defer(a):
    path = find_run(a.slug, a.root)
    run = load(path)
    run["deferred"].append({"item": a.item, "needs": a.needs or ""})
    log_event(path, current_phase(run), "deferred", a.item + (" (needs: " + a.needs + ")" if a.needs else ""))
    save(path, run)
    refresh(path)
    print(f"deferred: {len(run['deferred'])} items")

def cmd_ui_gate(a):
    """ALYDAS-223, S1. `ui-gate skip`: the run says it has no rendered surface.
    Phase 8's check reads it, and refuses it when a rendered file changed."""
    path = find_run(a.slug, a.root)
    run = load(path)
    if a.action == "check":
        return ui_gate_check(run)
    if a.action == "record":
        return ui_gate_record(path, run, a.replace)
    reason = (a.reason or "").strip()
    if not said(reason):
        die("ui-gate skip needs --reason with why this run has no rendered surface", 2)
    run["ui_gate"] = dict(run.get("ui_gate") if isinstance(run.get("ui_gate"), dict) else {}, skip=reason)
    log_event(path, current_phase(run), "ui-gate", f"skipped, no rendered surface: {reason}")
    save(path, run)
    refresh(path)
    print("ui-gate skip recorded")

def ui_gate_check(run):
    """ALYDAS-223, S2. `ui-gate check`: the run's reports through check_report.
    S5: a result for each report file, a cross line for each problem or one
    tick line with the verdict and the findings counted by severity. Exit 2
    when anything is wrong. Writes nothing."""
    _, wrong = ui_gate_checked(run)
    if wrong:
        sys.exit(2)

def ui_gate_checked(run):
    """What `ui-gate check` prints, printed: the run's reports, and whether
    anything is wrong with any of them. S6: `record` asks the same question."""
    # Gate 8, security 7. Every line holds a report's, a ruling's or a file's
    # own text, so every line goes to the terminal through `printable`.
    say = lambda line: print(printable(line))
    reports, missing = ui_gate_reports(run)
    if missing:
        say(f"✗ {missing}")
        return [], True
    context, shared = ui_gate_context(run, reports)
    wrong = bool(shared)
    for line in shared:
        say(f"✗ {line}")
    # Gate 6. Said, not refused: the run changed the project's own gate files.
    for line in ui_gate_notices(run):
        say(f"note: {line}")
    for name, report, unreadable in reports:
        problems = [str(unreadable)] if unreadable else [f"{name}: {line}" for line in check_report(report, **context)]
        for line in problems:
            say(f"✗ {line}")
        if problems:
            wrong = True
            continue
        counts = ", ".join(f"{sum(1 for f in report['findings'] if f['severity'] == s)} {s}" for s in UI_GATE_SEVERITIES)
        say(f"✓ UI gate report {name}: {report['verdict']}, {counts}")
    return reports, wrong

def _cut(text, bound, tail=""):
    """`text` then `tail`, in at most `bound` characters: the text is what is
    cut, with an ellipsis where it was, and the tail is kept whole."""
    text = text.strip()
    if len(text) + len(tail) <= bound:
        return text + tail
    return text[:max(0, bound - len(tail) - 1)] + "…" + tail

def ui_gate_rows(reports):
    """ALYDAS-223, S6. The findings of every report as table rows: one row a
    finding, by severity, then domain in the seven's order, then the reports
    in name order. Pure. Each cell is cut to the ledger's `cell` bound, and a
    finding keeps its rule id."""
    domains = {domain.casefold(): (at, domain) for at, domain in enumerate(UI_GATE_DOMAINS)}
    found = []
    for _, report, unreadable in reports:
        for f in ((report or {}).get("findings") or []) if not unreadable else []:
            at, domain = domains.get(_text(f.get("domain")).casefold(), (len(domains), _text(f.get("domain"))))
            severity = _text(f.get("severity"))
            rank = UI_GATE_SEVERITIES.index(severity) if severity in UI_GATE_SEVERITIES else len(UI_GATE_SEVERITIES)
            found.append((rank, at, [
                severity, UI_GATE_OWNED + domain, _text(f.get("location")),
                _cut(_text(f.get("before")), LEDGER_BOUNDS["cell"], f" [{_text(f.get('rule'))}]"),
                f"ruled: {_text(f.get('ruling'))}" if f.get("ruling") is not None else "open"]))
    found.sort(key=lambda entry: entry[:2])   # stable: the reports' own order holds inside a domain
    return [[_cut(cell, LEDGER_BOUNDS["cell"]) for cell in row] for _, _, row in found]

def _ui_gate_row(row):
    """Whether a table row is the record's own: its Domain starts with
    `UI gate: `, as written. Gate 6: it was any row whose Domain was one of
    the seven in any case, which took a reviewer's hand-written `UI` row."""
    return isinstance(row, list) and len(row) > 1 and isinstance(row[1], str) and row[1].startswith(UI_GATE_OWNED)

class TableFull(ValueError):
    """Gate 6. Phase 8's table has no room for even one row of the record's."""

def ui_gate_table(reports, there=None, rulings=()):
    """ALYDAS-223, S6. Phase 8's table once the reports' findings are
    recorded in it. Pure. `there` is the table the phase has, when it has the
    record's columns: the code review's rows live in it too. The record owns
    only the rows whose Domain starts `UI gate: ` (`_ui_gate_row`): it takes
    those out and puts the current ones where the first of them was (after
    every other row when there was none), and leaves each other row as it
    found it, in place. The ledger's `rows` bound counts every row: the
    others are all kept, the record's are cut to the room left, and the last
    of them says how many findings were left out.

    Gate 6. `rulings` are the ids of the rulings the run added or changed:
    they are one row, the record's first. And when the other rows leave no
    room for even the row that says what was left out, it raises `TableFull`
    and writes nothing, where it used to drop every finding without a word."""
    kept = [row for row in (there or {}).get("rows") or []]
    at = next((i for i, row in enumerate(kept) if _ui_gate_row(row)), len(kept))
    before = [row for row in kept[:at] if not _ui_gate_row(row)]
    after = [row for row in kept[at:] if not _ui_gate_row(row)]
    findings = ui_gate_rows(reports)
    ruled = [["", UI_GATE_RULED, UI_GATE_RULINGS, _cut(
        f"this run added or changed {len(rulings)} of the project's rulings: {', '.join(rulings)}",
        LEDGER_BOUNDS["cell"]), ""]] if rulings else []
    rows = ruled + findings
    room = max(0, LEDGER_BOUNDS["rows"] - len(before) - len(after))
    if rows and not room:
        raise TableFull(f"Phase 8's table already holds {len(before) + len(after)} rows that are not the UI gate's, and "
                        f"a table holds {LEDGER_BOUNDS['rows']} rows, so there is no room for the "
                        f"{len(findings)} finding{'' if len(findings) == 1 else 's'}"
                        + (" and the row naming the changed rulings" if ruled else "")
                        + ", nor for a row saying they were left out")
    if len(rows) > room:
        keep = rows[:room - 1]
        left = len(findings) - len([row for row in keep if row[1] != UI_GATE_RULED])
        rows = keep + [["", UI_GATE_LEFT_OUT, "", f"{left} more findings were left out"
                        + (", and so was the row naming the changed rulings" if ruled and not keep else "")
                        + f": a table holds {LEDGER_BOUNDS['rows']} rows, and the reports hold them all", ""]]
    return {"columns": list(UI_GATE_RECORD_COLUMNS), "rows": before + rows + after}

def ui_gate_record(path, run, replace):
    """ALYDAS-223, S6. `ui-gate record`: the findings of the run's reports
    into Phase 8's table (`ui_gate_table`). Refuses what `ui-gate check`
    refuses. A table there with the record's columns is shared with the code
    review, whose rows are left alone; one with other columns is refused
    unless `--replace` says to take its place. Gate 7: it beats, as `note`
    and `log` do, since it changes what the run page draws, and pushes
    nothing."""
    reports, wrong = ui_gate_checked(run)
    if wrong:
        die("ui-gate record: nothing was recorded, because ui-gate check refuses the reports (above)", 2)
    p = phase_of(run, 8)
    there = p.get("table") if isinstance(p.get("table"), dict) else None
    took = None
    if there and list(there.get("columns") or []) != list(UI_GATE_RECORD_COLUMNS):
        count = len(there.get("rows") or [])
        was = f"{', '.join(str(c) for c in there.get('columns') or [])}; {count} row{'' if count == 1 else 's'}"
        if not replace:
            die(printable(f"ui-gate record: Phase 8 already has a table whose columns are not "
                          f"{', '.join(UI_GATE_RECORD_COLUMNS)} ({was}), and recording would replace it. Nothing was "
                          f"recorded. To replace it: ui-gate record {run['slug']} --replace"), 2)
        took, there = was, None
    # Gate 6. The rulings this run added or changed are a row of the record's.
    rulings = ui_gate_changed_rulings(changed_files(run))
    try:
        p["table"] = ui_gate_table(reports, there, rulings)
    except TableFull as full:
        die(f"ui-gate record: nothing was recorded. {full}. Move some of the other rows into the review doc, then "
            f"record again", 2)
    findings = sum(len(report["findings"]) for _, report, _ in reports)
    others = sum(1 for row in p["table"]["rows"] if not _ui_gate_row(row))
    # Gate 8, correctness 11. What was written, counted off the table: the
    # record's rows that are findings, not the findings the reports hold.
    wrote = sum(1 for row in p["table"]["rows"] if _ui_gate_row(row) and row[1] not in (UI_GATE_LEFT_OUT, UI_GATE_RULED))
    said_it = ((f"recorded {findings} finding{'' if findings == 1 else 's'}" if wrote == findings else
                f"recorded {wrote} of {findings} findings")
               + f" from {len(reports)} report{'' if len(reports) == 1 else 's'} in Phase 8's table"
               + (f" ({findings - wrote} were left out, since a table holds {LEDGER_BOUNDS['rows']} rows)"
                  if wrote != findings else "")
               + (f", with a row naming the {len(rulings)} ruling{'' if len(rulings) == 1 else 's'} this run added or "
                  f"changed" if rulings else "")
               + (f", beside {others} other row{'' if others == 1 else 's'}" if others else "")
               + (f", and replaced the table that was there ({took})" if took else ""))
    log_event(path, 8, "ui-gate", said_it)
    save(path, run)
    refresh(path)
    beat = beat_the_write(path)
    print(printable(f"ui-gate record: {said_it}"))
    over_budget(obey_the_write(path, beat))

def cmd_set(a):
    path = find_run(a.slug, a.root)
    run = load(path)
    # ALYDAS-175, S5. The one list key `set` takes, parsed and checked here.
    if a.key == "holds":
        try:
            holds = parse_holds(a.value)
        except ValueError as e:
            die(f"holds: {e}", 2)
        run["holds"] = holds
        log_event(path, current_phase(run), "set", f"the phases that wait for your review to {holds_words(holds)}")
        save(path, run)
        refresh(path)
        print("holds set")
        return
    # ALYDAS-17, S5. The budget is a number, checked here, never the string
    # the generic path below would store; `budget_usd` is the same key.
    if a.key in ("budget", "budget_usd"):
        try:
            budget = parse_budget(a.value)
        except ValueError as e:
            die(f"budget: {e}", 2)
        run["budget_usd"] = budget
        log_event(path, current_phase(run), "set", f"the budget to {usd(budget)} (the desk takes it from the run's first beat only)")
        save(path, run)
        refresh(path)
        print("budget set")
        return
    if a.key not in run or isinstance(run[a.key], (list, dict)):
        die(f"cannot set {a.key!r}; scalar keys only: " + ", ".join(k for k, v in run.items() if not isinstance(v, (list, dict))))
    run[a.key] = a.value
    log_event(path, current_phase(run), "set", f"the {a.key.replace('_', ' ')} to {a.value}")
    save(path, run)
    refresh(path)
    # ALYDAS-135, S8. `artifact_url` is still a key an old ledger can set (Gate
    # 8: `init` no longer writes it, so a new ledger cannot), and
    # setting it no longer beats: the desk draws the run itself, so a board
    # link is nothing it needs to hear about.
    print(f"{a.key} set")

def cmd_log(a):
    path = find_run(a.slug, a.root)
    run = load(path)
    n = a.phase if a.phase is not None else current_phase(run)
    log_event(path, n, a.kind, a.message)
    refresh(path)
    # ALYDAS-173 Gate 8. The desk shows the run log live, so a line beats.
    beat = beat_the_write(path)
    print(f"logged to phase {n}")
    over_budget(obey_the_write(path, beat))

def cmd_usage(a):
    path = find_run(a.slug, a.root)
    run = load(path)
    compute_usage(run, read_events(path))
    save(path, run)
    t = run["telemetry"]
    print(f"wall {fmt_dur(t.get('wall_s'))} · tokens out {fmt_k(t.get('tokens',{}).get('output'))} · "
          f"cost {fmt_usd(t.get('cost_usd'))} · agents {t.get('agents',0)} · sessions {len(t.get('sessions',[]))}")
    for p in run["phases"]:
        pt = p.get("telemetry") or {}
        if pt:
            print(f"  {p['n']} {p['name']:<18} {fmt_dur(pt.get('wall_s')):>7} {fmt_dur(pt.get('active_s')):>7} act {fmt_k(pt.get('tokens',{}).get('output')):>6} out "
                  f"{fmt_usd(pt.get('cost_usd')):>7} {pt.get('agents',0)} agents")

def cmd_render(a):
    path = find_run(a.slug, a.root)
    run = load(path)
    run["events"] = read_events(path)
    out = render(run)
    with open(html_path(path), "w") as f:
        f.write(out)
    print(html_path(path))

def cmd_status(a):
    path = find_run(a.slug, a.root)
    run = load(path)
    n = a.n if a.n is not None else current_phase(run)
    print(status_block(run, n))

def cmd_check(a):
    path = find_run(a.slug, a.root)
    run = load(path)
    problems = run_checks(run, a.n)
    say_ui_gate_notices(run, a.n, sys.stdout)
    if problems:
        for pr in problems:
            # ALYDAS-223, Gate 8, security 7. Phase 8's problems quote a report.
            print(printable("✗ " + pr))
        sys.exit(2)
    print(f"✓ phase {a.n} artifacts present")

# ---------- refresh (usage + render) ----------

def refresh(path):
    run = load(path)
    events = read_events(path)
    try:
        compute_usage(run, events)
    except Exception as e:  # telemetry must never block the run
        run.setdefault("telemetry", {})["error"] = str(e)
    run["events"] = events
    save(path, run)
    with open(html_path(path), "w") as f:
        f.write(render(run))

def current_phase(run):
    for p in run["phases"]:
        if p["status"] == "active":
            return p["n"]
    last = 0
    for p in run["phases"]:
        if p["status"] in ("done", "skipped", "failed", "blocked"):
            last = p["n"]
    return last

# ---------- usage from transcripts ----------

PROJECTS = os.path.expanduser("~/.claude/projects")
# Standard price ratios relative to the input rate; the absolute rate per model
# is calibrated from the session's own cost-state line when it has one. The
# transcripts do not carry every call the session paid for, so phase costs run
# below the session's own total (ALYDAS-135 S4 measured 16 to 33 percent).
W_CACHE_WRITE, W_CACHE_READ, W_OUTPUT = 1.25, 0.10, 5.0

# ALYDAS-135, S4. List prices in dollars per million tokens, for a session with
# no calibrated rate: Claude Code writes the cost-state line only near the end
# of a session, so without these a live run has no cost at all. Columns are
# input, cache write with the 5-minute TTL, cache write with the 1-hour TTL,
# cache read, output.
#
# Source: the claude-api skill's model table (cached 2026-06-24) and its
# shared/prompt-caching.md (writes are 1.25x input for 5 minutes, 2x for one
# hour). Copied here 2026-09-26. Prices change: when a model is added or a
# price moves, update this table from the same source and note the new date.
PRICES = {
    "claude-opus-5-5": (4.00, 5.00, 8.00, 0.20, 20.00),
    "claude-sonnet-5": (2.00, 2.50, 4.00, 0.20, 10.00),
    "claude-fable-5-1": (10.00, 12.50, 20.00, 0.25, 50.00),
    "claude-haiku-4-5": (1.00, 1.25, 2.00, 0.10, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 1.25, 2.00, 0.10, 5.00),
}

def base_model(model):
    """The id a price is listed under: `claude-opus-5-5[1m]` is `claude-opus-5-5`,
    and a dated id is its base when the table lists the base but not the date."""
    m = re.sub(r"\[[^\]]*\]$", "", model or "")
    if m not in PRICES:
        undated = re.sub(r"-\d{8}$", "", m)
        if undated in PRICES:
            return undated
    return m

def split_writes(u):
    """(5-minute, 1-hour) cache-write tokens. A flat count with no split, or the
    part of it the split does not account for, is taken as 5-minute."""
    cc = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    w1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
    w5m = cc.get("ephemeral_5m_input_tokens", 0) or 0
    rest = max(0, (u.get("cache_creation_input_tokens", 0) or 0) - w5m - w1h)
    return w5m + rest, w1h

def price_message(model, u):
    """Dollars for one message from the table, or None when the model is not in it."""
    p = PRICES.get(base_model(model))
    if p is None:
        return None
    w5m, w1h = split_writes(u)
    return (p[0] * u.get("input_tokens", 0) + p[1] * w5m + p[2] * w1h
            + p[3] * u.get("cache_read_input_tokens", 0) + p[4] * u.get("output_tokens", 0)) / 1_000_000

def message_cost(model, u, rates):
    """One message, priced through exactly one source: the session's calibrated
    rate for its base model when there is one, else the table, else None."""
    base = base_model(model)
    if base in rates:
        return rates[base] * weighted(u)
    return price_message(model, u)

def find_transcript(session):
    if not session:
        return None
    hits = glob.glob(os.path.join(PROJECTS, "*", f"{session}.jsonl"))
    return hits[0] if hits else None

def iter_jsonl(p):
    with open(p) as f:
        for line in f:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

# Gate 6. Claude Code writes an assistant line with this model and an all-zero
# usage for "API Error: 500", "went to sleep mid-response" and "Login expired"
# (62 lines in 32 transcripts on this machine, 2026-09-26). No call was billed,
# so the line is skipped: priced as unknown, one of them made a whole run's cost
# unknown. A synthetic line that does carry tokens is kept, and is unpriced.
SYNTHETIC_MODEL = "<synthetic>"
TOKEN_COUNTS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")

def spent_nothing(model, u):
    """A `<synthetic>` line whose four token counts are all zero."""
    return model == SYNTHETIC_MODEL and not any(u.get(k) for k in TOKEN_COUNTS)

# Gate 8, F1. `claude --resume` appends a second `cost-state` line under the
# same session id with a new `startTime`, and that line counts only the resumed
# segment: in c77da73c (2026-09-24) its modelUsage is the resumed segment's own
# message tokens, to the token, and its total $4.75 beside the first segment's
# $184.32. Lines that share a startTime repeat one running total (12 of 13 real
# transcripts with two lines write the same line twice). So a session's bill is
# the last line of each startTime, added up across startTimes.
MODEL_USAGE_KEYS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens", "costUSD")

def session_total(segments):
    """One cost-state for a session from the last line of each of its segments."""
    lines = list(segments.values())
    if len(lines) == 1:
        return dict(lines[0])
    total = dict(lines[-1])
    total["totalCostUSD"] = sum(l.get("totalCostUSD") or 0 for l in lines)
    if not all(isinstance(l.get("totalCostUSD"), (int, float)) for l in lines):
        total["totalCostUSD"] = None
    total["totalAPIDuration"] = sum(l.get("totalAPIDuration") or 0 for l in lines)
    total["hasUnknownModelCost"] = any(l.get("hasUnknownModelCost") for l in lines)
    usage = {}
    for l in lines:
        for model, mu in (l.get("modelUsage") or {}).items():
            acc = usage.setdefault(model, {})
            for k in MODEL_USAGE_KEYS:
                acc[k] = acc.get(k, 0) + (mu.get(k) or 0)
    total["modelUsage"] = usage
    total["segments"] = len(lines)
    return total

def read_session(session):
    """Return (messages, agents, cost_state, turns) for one session id."""
    t = find_transcript(session)
    if not t:
        return [], [], None, []
    msgs, cost, seen, turns = [], None, set(), []  # turns: every timestamped entry, for working time
    segments = {}  # Gate 8, F1: the last cost-state line of each startTime
    for d in iter_jsonl(t):
        typ = d.get("type")
        if typ == "cost-state":
            segments[d.get("startTime")] = dict(d)
            cost = session_total(segments)
        if typ in ("assistant", "user", "system") and d.get("timestamp"):
            ts = parse_ts(d.get("timestamp"))
            if ts:
                turns.append(ts)
        if typ == "assistant":
            m = d.get("message") or {}
            u = m.get("usage") or {}
            ts = parse_ts(d.get("timestamp"))
            # one API response is written once per content block, each line
            # repeating the same usage object: count each message id once
            key = m.get("id") or d.get("requestId") or d.get("uuid")
            if ts and u and key not in seen and not spent_nothing(m.get("model"), u):
                seen.add(key)
                msgs.append((ts, m.get("model") or "", u))
                # S3. A response after the session's total is spend the total
                # does not hold, so that total is no longer the bill.
                if cost is not None:
                    cost["_spent_after"] = True
    agents = []
    for ap in glob.glob(os.path.join(os.path.dirname(t), session, "subagents", "agent-*.jsonl")):
        first = None; tot = {}; last = None; model = ""; seen = set(); priced = []
        for d in iter_jsonl(ap):
            ts = parse_ts(d.get("timestamp"))
            if ts:
                first = first or ts; last = ts
            if d.get("type") == "assistant":
                m = d.get("message") or {}
                u = m.get("usage") or {}
                if spent_nothing(m.get("model"), u):
                    continue
                model = model or m.get("model") or ""
                key = m.get("id") or d.get("requestId") or d.get("uuid")
                if key in seen:
                    continue
                seen.add(key)
                if u:
                    # S4: each message kept with its own model and its usage
                    # whole, cache_creation split included, to be priced one by one
                    priced.append((m.get("model") or model, u))
                for k, v in u.items():
                    if isinstance(v, (int, float)):
                        tot[k] = tot.get(k, 0) + v
        meta = {}
        mp = ap[:-6] + ".meta.json"
        if os.path.exists(mp):
            try:
                meta = load(mp)
            except Exception:
                meta = {}
        if first:
            agents.append({"id": os.path.basename(ap)[6:-6], "first": first, "last": last,
                           "model": model, "usage": tot, "messages": priced,
                           "description": meta.get("description", "")})
    return msgs, agents, cost, turns

IDLE_S = 180  # a gap longer than this is the user away, not the model working

def working_seconds(stamps, start, end):
    """Sum of gaps between consecutive transcript entries inside the window, each gap capped at IDLE_S."""
    inside = sorted(t for t in stamps if start <= t <= end)
    total = 0.0
    for a, b in zip(inside, inside[1:]):
        total += min((b - a).total_seconds(), IDLE_S)
    return int(total)

def weighted(u):
    return (u.get("input_tokens", 0) + W_CACHE_WRITE * u.get("cache_creation_input_tokens", 0)
            + W_CACHE_READ * u.get("cache_read_input_tokens", 0) + W_OUTPUT * u.get("output_tokens", 0))

def calibrate_rates(cost_state):
    """Per-model $ per weighted token, from the session's own totals.

    Keyed by base id (S4): cost-state says `claude-opus-5-5[1m]` where the
    messages say `claude-opus-5-5`. Two keys with one base are pooled into one
    rate, so no message can be priced by both."""
    cost, weight = {}, {}
    if not cost_state:
        return {}
    for model, mu in (cost_state.get("modelUsage") or {}).items():
        w = (mu.get("inputTokens", 0) + W_CACHE_WRITE * mu.get("cacheCreationInputTokens", 0)
             + W_CACHE_READ * mu.get("cacheReadInputTokens", 0) + W_OUTPUT * mu.get("outputTokens", 0))
        if w > 0 and mu.get("costUSD"):
            base = base_model(model)
            cost[base] = cost.get(base, 0) + mu["costUSD"]
            weight[base] = weight.get(base, 0) + w
    return {base: cost[base] / weight[base] for base in cost}

def add_usage(acc, u):
    acc["input"] = acc.get("input", 0) + u.get("input_tokens", 0)
    acc["cache_write"] = acc.get("cache_write", 0) + u.get("cache_creation_input_tokens", 0)
    acc["cache_read"] = acc.get("cache_read", 0) + u.get("cache_read_input_tokens", 0)
    acc["output"] = acc.get("output", 0) + u.get("output_tokens", 0)

def compute_usage(run, events):
    # phase windows from events: start -> next terminal event for that phase (or now)
    windows = {}
    for ev in sorted(events, key=lambda e: e.get("ts", "")):
        n = ev.get("phase"); ts = parse_ts(ev.get("ts"))
        if n is None or ts is None or ev.get("event") not in EVENT_TO_STATUS:
            continue
        w = windows.setdefault(n, {"start": None, "end": None, "sessions": set()})
        if ev.get("session"):
            w["sessions"].add(ev["session"])
        if ev["event"] == "start":
            w["start"] = w["start"] or ts
            w["end"] = None
        else:
            w["start"] = w["start"] or ts
            w["end"] = ts
    sessions = set()
    for w in windows.values():
        sessions |= w["sessions"]
    sessions = sorted(s for s in sessions if s)
    data = {s: read_session(s) for s in sessions}
    rates_by_session = {s: calibrate_rates(data[s][2]) for s in sessions}
    now = dt.datetime.now(dt.timezone.utc)
    total = {"tokens": {}, "cost_usd": 0.0, "agents": 0, "wall_s": 0, "active_s": 0, "sessions": sessions,
             "api_s": None, "cost_known": False}
    agent_rows = []
    # S4. Every message is priced or it is not, and one that is not makes the
    # cost unknown, for its phase and for the run: a partial is not shown as a
    # lower bound, because the desk prints whatever number it is sent as the cost.
    run_priced = run_unpriced = 0; unpriced_models = set()
    for p in run["phases"]:
        w = windows.get(p["n"])
        if not w:
            p["telemetry"] = {}
            continue
        start, end = w["start"], w["end"] or now
        pt = {"wall_s": int((end - start).total_seconds()), "active_s": 0, "tokens": {}, "cost_usd": 0.0, "agents": 0,
              "cost_known": False, "agent_list": []}
        priced = unpriced = 0
        for s in sessions:
            msgs, agents, cost, turns = data[s]
            pt["active_s"] += working_seconds(turns, start, end)
            rates = rates_by_session[s]
            for ts, model, u in msgs:
                if start <= ts <= end:
                    add_usage(pt["tokens"], u)
                    c = message_cost(model, u, rates)
                    if c is None:
                        unpriced += 1; unpriced_models.add(model)
                    else:
                        pt["cost_usd"] += c; priced += 1
            for ag in agents:
                if start <= ag["first"] <= end:
                    pt["agents"] += 1
                    add_usage(pt["tokens"], {
                        "input_tokens": ag["usage"].get("input_tokens", 0),
                        "cache_creation_input_tokens": ag["usage"].get("cache_creation_input_tokens", 0),
                        "cache_read_input_tokens": ag["usage"].get("cache_read_input_tokens", 0),
                        "output_tokens": ag["usage"].get("output_tokens", 0)})
                    costs = [message_cost(am, au, rates) for am, au in ag.get("messages", [])]
                    ag_cost = sum(c for c in costs if c is not None)
                    pt["cost_usd"] += ag_cost
                    priced += sum(1 for c in costs if c is not None)
                    unpriced += sum(1 for c in costs if c is None)
                    unpriced_models.update(am for (am, _), c in zip(ag.get("messages", []), costs) if c is None)
                    row = {"phase": p["n"], "description": ag["description"], "model": ag["model"],
                           "first": ag["first"].isoformat(), "last": ag["last"].isoformat() if ag["last"] else "",
                           "wall_s": int((ag["last"] - ag["first"]).total_seconds()) if ag["last"] else 0,
                           "output": ag["usage"].get("output_tokens", 0),
                           "cost_usd": ag_cost if costs and None not in costs else None}
                    pt["agent_list"].append(row); agent_rows.append(row)
        pt["cost_known"] = priced > 0 and unpriced == 0
        pt["cost_usd"] = round(pt["cost_usd"], 2) if pt["cost_known"] else None
        run_priced += priced; run_unpriced += unpriced
        p["telemetry"] = pt
        for k, v in pt["tokens"].items():
            total["tokens"][k] = total["tokens"].get(k, 0) + v
        total["cost_usd"] += pt["cost_usd"] or 0.0; total["agents"] += pt["agents"]; total["wall_s"] += pt["wall_s"]
        total["active_s"] += pt["active_s"]
    total["cost_known"] = run_priced > 0 and run_unpriced == 0
    # session-level facts straight from cost-state, when present
    api = 0; sess_cost = 0.0; any_cost = False
    for s in sessions:
        cost = data[s][2]
        if cost:
            api += cost.get("totalAPIDuration", 0) / 1000; sess_cost += cost.get("totalCostUSD", 0); any_cost = True
    total["api_s"] = int(api) if any_cost else None
    total["session_cost_usd"] = round(sess_cost, 2) if any_cost else None
    # S3 (Sachit, 2026-09-26). Claude Code's own total is the bill, so when it
    # covers the run it is the cost, exact. It covers the run only when every
    # session the run spans has one: `session_cost_usd` above sums whichever
    # sessions have a total, so a run still going in a second session would
    # otherwise send a part as if it were the whole. And a total is not the
    # bill when Claude Code says a model's cost was unknown to it, or when the
    # session carried on past the line. The declared limit: a total is the
    # whole session's, so a session that did other work before the run began
    # counts that too.
    total["session_cost_exact"] = bool(sessions) and all(
        data[s][2] is not None
        and not data[s][2].get("hasUnknownModelCost")
        and not data[s][2].get("_spent_after")
        and isinstance(data[s][2].get("totalCostUSD"), (int, float))
        for s in sessions)
    total["cost_usd"] = round(total["cost_usd"], 2) if total["cost_known"] else None
    if total["cost_known"]:
        total["cost_note"] = ("priced per message: a session's own cost-state rate where it has one, "
                              "else list prices of 2026-06-24")
    elif unpriced_models:
        total["cost_note"] = "no price for " + ", ".join(sorted(m or "an unnamed model" for m in unpriced_models))
    else:
        total["cost_note"] = "no cost data in these transcripts"
    total["agent_rows"] = agent_rows
    total["computed_at"] = now_iso()
    run["telemetry"] = total

# ---------- artifact checks ----------

FAIL_RE = re.compile(r"(FAIL|failed|✕|✗|AssertionError|Error:|expected .* (to|but)|Tests:\s+\d+ failed)", re.I)

def _fenced_blocks(text):
    return re.findall(r"```[^\n]*\n(.*?)```", text, re.S)

def _has_red_output(text):
    return any(FAIL_RE.search(b) for b in _fenced_blocks(text))

def _find(root, pattern):
    return sorted(glob.glob(os.path.join(root, pattern)))

class Problem(str):
    """Gate 8, ST-1. What a phase still needs before it closes: the string is
    the full line for stderr, the command to type included; `plain` is the
    same thing in words for the run log a person reads on the desk."""
    def __new__(cls, full, plain=None):
        problem = super().__new__(cls, full)
        problem.plain = plain or full
        return problem

# ---------- ALYDAS-223, S1: the UI gate ----------
#
# A run that changed a file a browser draws cannot close Phase 8 on a review
# doc alone: it needs a UI gate report that approves. What "changed" means is
# asked of git (the branch against origin's default, plus what is uncommitted
# and untracked), and when git cannot say, the phase is refused rather than
# passed: an unknown is not an empty list.

DEFAULT_SURFACES = ("*.tsx", "*.jsx", "*.css", "*.scss", "*.html", "*.vue", "*.svelte")
# Gate 7, D1: also the review's tests split (`review record`), so Python's
# test shapes are here, and the UI gate and the review agree on what a test is.
DEFAULT_IGNORE = ("*.test.*", "*.spec.*", "**/__tests__/**", "test_*.py", "*_test.py")
UI_GATE_CONFIG = "ui-gate/config.json"
UI_GATE_NAMED = 5
LOCAL_DEFAULTS = ("main", "master")

class ChangedUnknown(str):
    """Why the changed files could not be worked out. Never an empty list."""

class Changed(list):
    """Gate 6. The changed files, and `base`: the merge-base they were worked
    out against."""
    base = None

NOTHING_AHEAD = ("the run has nothing ahead of its base and no uncommitted or untracked file, so it may already be "
                 "merged or be on the default branch, and that is not the same as having changed nothing")

def default_base():
    """Gate 8, correctness 1. The merge-base of HEAD with the default branch,
    as a commit, and None; or None and why there is none. `git merge-base
    HEAD origin/<default>` (S2: with no origin default, a local `main`, then
    `master`). `init` records it on the run and `changed_files` works it out
    again for a ledger that has none. May raise what running git raises."""
    if git_here("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        return None, NO_REPO_REASON
    default = origin_default()
    if default:
        ref, against = f"refs/remotes/origin/{default}", f"origin/{default}"
    else:
        # S2. No origin default: a local main, then master, unless HEAD is
        # that branch (its merge-base with itself would say nothing changed).
        here = git_here("branch", "--show-current").stdout.strip()
        local = [name for name in LOCAL_DEFAULTS if name != here
                 and git_here("rev-parse", "--verify", "--quiet", f"refs/heads/{name}^{{commit}}").returncode == 0]
        if not local:
            return None, ("origin's default branch could not be found, and there is no local "
                          + " or ".join(LOCAL_DEFAULTS) + " that is not this branch")
        ref, against = f"refs/heads/{local[0]}", f"the local {local[0]}"
    base = git_here("merge-base", "HEAD", ref)
    sha = base.stdout.strip()
    if base.returncode != 0 or not COMMIT_SHAPE.fullmatch(sha):
        return None, f"this branch and {against} have no merge-base here" + (" (fetch origin)" if default else "")
    return sha, None

def run_base():
    """Gate 8, correctness 1. What `init` records as the run's base: the
    commit the run started from, or None when git cannot say. Never raises:
    a run is started outside a repository too."""
    try:
        return default_base()[0]
    except (OSError, subprocess.SubprocessError):
        return None

def recorded_base(run):
    """The base the ledger recorded, when it is a commit git still knows;
    else None, and the merge-base is worked out as it was before the field."""
    sha = run.get("base")
    if not isinstance(sha, str) or not COMMIT_SHAPE.fullmatch(sha):
        return None
    return sha if git_here("cat-file", "-e", sha + "^{commit}").returncode == 0 else None

def changed_files(run):
    """The files this run changed, as repository-relative paths, or a
    `ChangedUnknown` saying why they could not be worked out.

    Gate 8, correctness 1, as ruled. The base is the merge-base with the
    default branch as it is now (`default_base`) while that is not HEAD: a
    branch, rebased or not, is read against where it leaves the default
    branch. When it is HEAD, the branch is already merged or the run is on
    the default branch, and the base is the commit the run started from,
    which `init` recorded on the ledger (`run["base"]`), when it is there and
    git still knows it: so those runs are still judged by their own change.
    Then `git diff
    --name-only --no-renames <base> --`, which reads the working tree and so
    counts uncommitted changes to tracked files, plus the untracked files git
    does not ignore. In the repository the command runs in (`repo_root`).

    Whichever base it is: when it is HEAD and nothing is uncommitted or
    untracked, "changed nothing" cannot be told from "already merged", and
    the answer is unknown (`NOTHING_AHEAD`), not an empty list.

    Gate 6. `--no-renames`: a renamed file is one path deleted and one added,
    so both sides are listed, and `page.tsx` renamed to `page.ts` is still a
    page that changed. And the list carries its base (`Changed.base`): what
    the project's `ui-gate/` files held before the run is read at that commit
    (`at_base`).

    Gate 8. `--` after the commit, so a file named as the commit's hash is
    not an ambiguous argument; and the names are decoded with replacement, so
    a path that is not UTF-8 is a path and not a traceback."""
    try:
        if git_here("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            return ChangedUnknown(NO_REPO_REASON)
        sha, why = default_base()
        if sha is None:
            return ChangedUnknown(why)
        head = git_here("rev-parse", "HEAD").stdout.strip()
        if sha == head:
            sha = recorded_base(run) or sha
        diff = git_here("diff", "--name-only", "--no-renames", "-z", sha, "--", errors="replace")
        untracked = git_here("ls-files", "--others", "--exclude-standard", "-z", errors="replace")
        if diff.returncode != 0 or untracked.returncode != 0:
            return ChangedUnknown("git could not list them: " + (diff.stderr or untracked.stderr).strip()[:200])
        names = sorted({name for out in (diff, untracked) for name in out.stdout.split("\0") if name})
        if not names and head == sha:
            return ChangedUnknown(NOTHING_AHEAD)
    except (OSError, subprocess.SubprocessError) as e:
        return ChangedUnknown(f"git could not be run ({type(e).__name__})")
    changed = Changed(names)
    changed.base = sha
    return changed

def at_base(sha, relative):
    """Gate 6. What a repository file held at the merge-base, as text; None
    when it was not there. Raises ValueError when git cannot say."""
    try:
        if git_here("cat-file", "-e", f"{sha}:{relative}").returncode != 0:
            return None
        out = git_here("show", f"{sha}:{relative}")
    except (OSError, subprocess.SubprocessError) as e:
        raise ValueError(f"could not be read at the merge-base ({type(e).__name__})")
    if out.returncode != 0:
        raise ValueError("could not be read at the merge-base: " + out.stderr.strip()[:200])
    return out.stdout

def _glob_regex(pattern):
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out)

def _glob_match(path, pattern):
    """A pattern with no `/` is matched against the file's name, at any depth;
    one with a `/` against the whole path, `*` inside one folder, `**` across."""
    subject = path if "/" in pattern else path.rsplit("/", 1)[-1]
    return _glob_regex(pattern).fullmatch(subject) is not None

def ui_gate_defaults(artifact_root=None):
    """The config with no project file: `artifact_root` is the run's own root
    as a repository-relative path, or None when it is not inside the repository."""
    return {"surfaces": list(DEFAULT_SURFACES), "ignore": list(DEFAULT_IGNORE), "artifact_root": artifact_root}

def is_rendered_surface(path, config):
    """Whether a repository-relative path is a file a browser draws. Pure."""
    root = config.get("artifact_root")
    if root and (path == root or path.startswith(root.rstrip("/") + "/")):
        return False
    if any(_glob_match(path, pattern) for pattern in config["ignore"]):
        return False
    return any(_glob_match(path, pattern) for pattern in config["surfaces"])

def ui_gate_config(run, sha):
    """The defaults, or the project's `ui-gate/config.json` at the repository
    root: each of `surfaces` and `ignore` it gives replaces that default list.
    Raises ValueError, saying why, for a file that is there and cannot be used.

    Gate 6. The file is read as it is at the merge-base `sha`, the default
    branch's version, and never from the working tree: a run cannot loosen its
    own gate. What the run wrote there takes effect for the runs after it."""
    top = os.path.realpath(repo_root())
    root = os.path.relpath(os.path.realpath(run["root"]), top).replace(os.sep, "/")
    config = ui_gate_defaults(None if root == "." or root == ".." or root.startswith("../") else root)
    held = at_base(sha, UI_GATE_CONFIG)
    if held is None:
        return config
    if len(held.encode("utf-8", "replace")) > UI_GATE_MOST_BYTES:
        raise ValueError(f"is over {UI_GATE_MOST_SAID}, and was not used")
    try:
        given = json.loads(held)
    except ValueError as e:
        raise ValueError(f"is not JSON that can be read ({e})")
    if not isinstance(given, dict):
        raise ValueError("is not a JSON object")
    for key in ("surfaces", "ignore"):
        if key in given:
            if not isinstance(given[key], list) or not all(isinstance(g, str) and g for g in given[key]):
                raise ValueError(f"has a {key} that is not a list of globs")
            config[key] = list(given[key])
    return config

def ui_gate_surfaces(run):
    """The rendered surfaces this run changed and None, or None and the
    Problem that says why they could not be worked out."""
    changed = changed_files(run)
    if isinstance(changed, ChangedUnknown):
        return None, Problem(f"the files this run changed could not be worked out ({changed}), so nothing says "
                             f"whether it touches a screen",
                             f"the files this run changed could not be worked out: {changed}")
    try:
        config = ui_gate_config(run, changed.base)
    except ValueError as e:
        return None, Problem(f"{UI_GATE_CONFIG} {e}", f"the project's {UI_GATE_CONFIG} {e}")
    return [path for path in changed if is_rendered_surface(path, config)], None

def ui_gate_changed_rulings(changed):
    """Gate 6. The ids of the rulings this run added or changed, in the order
    the file has them: those in `ui-gate/rulings.json` now that the merge-base
    does not hold, word for word. Empty when the run left the file alone, and
    when git could not say what the run changed (`changed` is unknown)."""
    if isinstance(changed, ChangedUnknown) or UI_GATE_RULINGS not in changed:
        return []
    def entries(held):
        listed = held.get("rulings") if isinstance(held, dict) else None
        return [e for e in listed if isinstance(e, dict) and _text(e.get("id"))] if isinstance(listed, list) else []
    try:
        before = json.loads(at_base(changed.base, UI_GATE_RULINGS) or "{}")
    except ValueError:
        before = {}
    was = {_text(e["id"]): e for e in entries(before)}
    # Gate 8, security 2. Read as the check reads it: not through a link that leads out.
    top = os.path.realpath(repo_root())
    now = gate_json(os.path.join(top, *UI_GATE_RULINGS.split("/")), [top])
    return list(dict.fromkeys(_text(e["id"]) for e in entries(now) if was.get(_text(e["id"])) != e))

def ui_gate_notices(run):
    """Gate 6. What the gate says and does not refuse, each one plain line:
    that the run changed the project's `ui-gate/config.json`, which it is
    therefore not judged by, and which rulings it added or changed. Nothing
    when git cannot say what changed: Phase 8 refuses that on its own."""
    changed = changed_files(run)
    if isinstance(changed, ChangedUnknown):
        return []
    lines = []
    if UI_GATE_CONFIG in changed:
        lines.append(f"this run changed {UI_GATE_CONFIG}: its files are judged by that file as the default branch has "
                     f"it (the merge-base), and the change takes effect for later runs")
    ids = ui_gate_changed_rulings(changed)
    if ids:
        lines.append(f"this run added or changed {len(ids)} of the rulings in {UI_GATE_RULINGS}: {', '.join(ids)}")
    return lines

def surface_names(surfaces):
    names = ", ".join(surfaces[:UI_GATE_NAMED])
    if len(surfaces) > UI_GATE_NAMED:
        names += f" and {len(surfaces) - UI_GATE_NAMED} more"
    return names

UI_GATE_DATE = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"

def ui_gate_pattern(run):
    """S5. A report is `ui-gate-report`; what the measuring module writes is
    `ui-measure`, so neither is read as the other. Gate 6. This is a report's
    name in words, for a message; `ui_gate_files` is what matches the names."""
    return f"measurements/<date>-{run['slug']}-ui-gate-report[-<screen>].json"

def ui_gate_files(run, kind):
    """Gate 6. The run's own reports (`kind` "report") or measurement files
    ("measure"), in name order. The slug is matched whole: the name is a
    date, a hyphen, the slug, then `-ui-gate-report` and either nothing or a
    hyphen and more, or `-ui-measure-` and more. So the run `scratch` reads
    nothing of the run `scratch-two`, nor of `two-scratch`."""
    slug = re.escape(run["slug"])
    after = r"-ui-gate-report(?:-.*)?" if kind == "report" else r"-ui-measure-.+"
    shape = re.compile(UI_GATE_DATE + "-" + slug + after + r"\.json", re.S)
    return [hit for hit in _find(run["root"], "measurements/*.json") if shape.fullmatch(os.path.basename(hit))]

class Unreadable(str):
    """Why a file that is there could not be read as JSON."""

class NotRead(Unreadable):
    """Gate 8, security 2 and 6. Why a file that is there was not opened at
    all, as the words that follow its name: "<name> leads outside ..."."""

class TooBig(NotRead):
    """It is over the ceiling (`UI_GATE_MOST_BYTES`)."""

UI_GATE_MOST_BYTES = 5 * 1024 * 1024
UI_GATE_MOST_SAID = f"{UI_GATE_MOST_BYTES // (1024 * 1024)} MB"

def _read_json(path):
    """What a JSON file holds, or an `Unreadable` saying why it holds nothing."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        return Unreadable(str(e))

def not_read(path, tops):
    """Gate 8, security 2 and 6. Why a file the gate found for itself is not
    opened, as a `NotRead`, or None when it may be: its real path, with every
    symbolic link resolved, is inside one of `tops` (real paths: the
    repository, and for a run's own files its artifact root), it is a regular
    file, and it is not over the ceiling. So a link that leads out, a device
    and a pipe are named and never opened: the file is only looked at
    (`os.stat`), which answers for a pipe and a device without reading them."""
    real = os.path.realpath(path)
    if not any(real.startswith(top.rstrip(os.sep) + os.sep) for top in tops):
        return NotRead("leads outside the repository (a symbolic link), and was not read")
    try:
        held = os.stat(real)
    except OSError as e:
        return NotRead(f"could not be looked at ({e.strerror or type(e).__name__}), and was not read")
    if not stat.S_ISREG(held.st_mode):
        return NotRead("is not a regular file (it is a folder, a device or a pipe), and was not read")
    if held.st_size > UI_GATE_MOST_BYTES:
        return TooBig(f"is over {UI_GATE_MOST_SAID} ({held.st_size} bytes), and was not read")
    return None

def gate_json(path, tops):
    """What a JSON file the gate found holds; a `NotRead` when it may not be
    opened (`not_read`); an `Unreadable` when it does not parse."""
    return not_read(path, tops) or _read_json(os.path.realpath(path))

def gate_tops(run=None):
    """The real paths a file of the gate's may be inside: the repository,
    and, for a run's own reports and measurements, the run's artifact root."""
    return [os.path.realpath(repo_root())] + ([os.path.realpath(run["root"])] if run else [])

def ui_gate_reports(run):
    """S5. Every UI gate report of the run, one a screen, in name order: each
    its file name, what it holds, and a Problem when it is not JSON. Or an
    empty list and the Problem that says there is none."""
    root, pattern = run["root"], ui_gate_pattern(run)
    hits = ui_gate_files(run, "report")
    if not hits:
        return [], Problem(f"UI gate report missing ({root}/{pattern})", "the UI gate report is missing")
    reports = []
    tops = gate_tops(run)
    for hit in hits:
        name, held = os.path.basename(hit), gate_json(hit, tops)
        if isinstance(held, NotRead):
            reports.append((name, None, Problem(f"UI gate report {name} {held}", f"the UI gate report {name} {held}")))
        elif isinstance(held, Unreadable):
            reports.append((name, None, Problem(f"UI gate report {name} is not JSON that can be read ({held})",
                                                f"the UI gate report {name} cannot be read")))
        else:
            reports.append((name, held, None))
    return reports, None

def _inside(top, relative):
    """A repository-relative path as a real one inside `top`, or None when it
    is not text or leads out of the repository."""
    if not isinstance(relative, str) or not said(relative) or os.path.isabs(relative):
        return None
    full = os.path.realpath(os.path.join(top, relative))
    return full if full.startswith(top.rstrip(os.sep) + os.sep) else None

def ui_gate_context(run, reports=()):
    """S5. Everything `check_report` is handed beside the report, read here so
    the check itself reads nothing: the project's rulings, the run's
    measurement files, a lookup of a screen file and one of a file's lines.
    And the problems that are no one report's: a rulings file or a measurement
    file that cannot be used, each named.

    Gate 6. A measurement file that is less than a measurement is named and
    not counted, and so is one whose screen none of `reports` reviews."""
    top = os.path.realpath(repo_root())
    problems, rulings = [], []
    path = os.path.join(top, *UI_GATE_RULINGS.split("/"))
    if os.path.lexists(path):
        held = gate_json(path, [top])
        if isinstance(held, NotRead):
            problems.append(f"{UI_GATE_RULINGS} {held}")
        elif isinstance(held, Unreadable):
            problems.append(f"{UI_GATE_RULINGS} is not JSON that can be read ({held})")
        else:
            rulings, wrong = check_rulings(held)
            problems.extend(f"{UI_GATE_RULINGS}: {line}" for line in wrong)
    measurements = {}
    tops = gate_tops(run)
    for hit in ui_gate_files(run, "measure"):
        name, held = os.path.basename(hit), gate_json(hit, tops)
        if isinstance(held, NotRead):
            problems.append(f"measurement file {name} {held}")
        elif isinstance(held, Unreadable):
            problems.append(f"measurement file {name} is not JSON that can be read ({held})")
        elif check_measurement(held):
            problems.append(f"measurement file {name} is not a measurement: {'; '.join(check_measurement(held))}")
        else:
            measurements[name] = held
    problems.extend(unreviewed_measurements(
        measurements, {_text(report.get("screen")) for _, report, _ in reports if isinstance(report, dict)}))

    def named(relative):
        """A path a report or a ruling wrote, under the repository; None when
        it is not a path from its root or nothing is there."""
        if not isinstance(relative, str) or not said(relative) or os.path.isabs(relative):
            return None
        full = os.path.join(top, relative)
        return full if os.path.lexists(full) else None

    def screen_file(relative):
        # Gate 8. None when nothing is there; a `NotRead` for a link that
        # leads out, a folder, a device, a pipe or a file over the ceiling.
        full = named(relative)
        return gate_json(full, [top]) if full else None

    def read_lines(relative):
        full = named(relative)
        why = not_read(full, [top]) if full else None
        if isinstance(why, TooBig):
            return why
        if not full or why:
            return None
        try:
            with open(os.path.realpath(full), "rb") as f:
                lines = f.read().decode("utf-8", "replace").split("\n")
        except OSError:
            return None
        # A file that ends in a newline has no line after it.
        return lines[:-1] if lines[-1] == "" else lines

    # Gate 8, security 6. One check reads a file once, however many findings
    # cite a ruling whose `where` is in it.
    read = {}
    def file_lines(relative):
        if relative not in read:
            read[relative] = read_lines(relative)
        return read[relative]

    def file_there(relative):
        # Gate 8, correctness 11. A finding's location: a regular file whose
        # real path is inside the repository.
        full = _inside(top, relative)
        return bool(full) and os.path.isfile(full)

    return {"rulings": rulings, "measurements": measurements, "screen_file": screen_file,
            "file_lines": file_lines, "file_there": file_there}, problems

# ---------- ALYDAS-223, S2: what a UI gate report has to be ----------

UI_GATE_DOMAINS = ("Accessibility", "Layout", "Writing", "Typography", "Colors", "UI", "Placement")
UI_GATE_SEVERITIES = ("HIGH", "MEDIUM", "LOW")
UI_GATE_SOURCES = ("measured", "source")
UI_GATE_RESULTS = ("clear", "findings", "not-reviewed")
UI_GATE_FIELDS = ("severity", "domain", "rule", "location", "before", "after", "why", "source")
UI_GATE_MOST_FINDINGS = 15
UI_GATE_CUT = "left out by the cap"   # Gate 7: what a coverage row says when the cap cut all its domain's findings
UI_GATE_RECORD_COLUMNS = ("Severity", "Domain", "Where", "Finding", "Standing")
# Gate 6. A row is the record's own when its Domain starts with this, exactly:
# a finding's row is `UI gate: Layout`. Any other row, whatever its Domain
# says (`UI`, `layout`, `UI gate`), is somebody else's and is never touched.
UI_GATE_OWNED = "UI gate: "
UI_GATE_LEFT_OUT = UI_GATE_OWNED + "left out"   # the record's row saying how many findings were left out
UI_GATE_RULED = UI_GATE_OWNED + "rulings"       # the record's row naming the rulings the run added or changed
UI_GATE_LOCATION = re.compile(r"\S(?:.*\S)?:[1-9][0-9]*")

UI_GATE_RULINGS = "ui-gate/rulings.json"
# ALYDAS-455. `where` is the file alone and `quote` the place in it, found
# once in the whole file, so a line added above the place moves nothing.
UI_GATE_RULING_FIELDS = ("id", "choice", "where", "quote", "rules", "covers")

# Gate 6. The rule book, as far as the check needs it: every rule id
# `references/ui-rules.md` defines, and the ones its tables mark automatic
# HIGH (`yes`). A rule that is HIGH only in the case its row names
# (`WRITE-ERROR`, `UI-MOTION`) is not in the second list. `test_ui_rules.py`
# parses the tables and holds both lists to them.
UI_GATE_RULES = (
    "A11Y-FOCUS", "A11Y-NAME", "A11Y-KEYBOARD", "A11Y-TARGET", "A11Y-MOTION", "A11Y-COLOR-ONLY", "A11Y-STRUCTURE",
    "A11Y-ANNOUNCE",
    "LAYOUT-CLIP", "LAYOUT-HIDDEN", "LAYOUT-GROUP", "LAYOUT-CONTROL",
    "WRITE-DESTRUCTIVE", "WRITE-ERROR", "WRITE-LABEL", "WRITE-CONSISTENT", "WRITE-EMPTY",
    "TYPE-TRUNCATE", "TYPE-HEADING-ORDER", "TYPE-SCALE", "TYPE-INPUT-ZOOM", "TYPE-MEASURE",
    "COLOR-CONTRAST", "COLOR-MEANING", "COLOR-TREATMENT", "COLOR-TOKEN",
    "UI-TRANSITION-ALL", "UI-MOTION", "UI-RADIUS", "UI-STATE",
    "PLACE-MISSING", "PLACE-OFFSCREEN", "PLACE-SHARE", "PLACE-ORDER",
)
UI_GATE_AUTOMATIC_HIGH = (
    "A11Y-FOCUS", "A11Y-NAME", "A11Y-KEYBOARD", "A11Y-MOTION", "A11Y-COLOR-ONLY",
    "LAYOUT-CLIP", "LAYOUT-HIDDEN", "WRITE-DESTRUCTIVE", "TYPE-TRUNCATE",
    "COLOR-CONTRAST", "COLOR-MEANING", "PLACE-MISSING",
)
# Gate 6. The four contexts the measuring module opens (`ui_measure.mjs`).
UI_GATE_CONTEXTS = ("desktop-light", "desktop-dark", "phone", "narrow")

def _one_of(values):
    return ", ".join(values[:-1]) + " or " + values[-1]

def _all_of(values):
    values = list(values)
    return values[0] if len(values) == 1 else ", ".join(values[:-1]) + " and " + values[-1]

def _text(value):
    return value.strip() if isinstance(value, str) and said(value) else ""

def check_rulings(data):
    """ALYDAS-223, S5. What a project's `ui-gate/rulings.json` holds: the
    rulings that can be used, and what is wrong with the rest as plain lines.
    Pure. A ruling is named by its place in the list, counted from 1, and its
    id. Its `where` is looked at when a finding cites it (`check_report`)."""
    entries = data.get("rulings") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return [], ["it is not a JSON object with a list named rulings"]
    rulings, problems, seen = [], [], set()
    for at, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            problems.append(f"ruling {at} is not a JSON object")
            continue
        label = f"ruling {at} ({_text(entry.get('id'))})" if _text(entry.get("id")) else f"ruling {at}"
        wrong = []
        for field in UI_GATE_RULING_FIELDS:
            value = entry.get(field)
            if field in ("rules", "covers"):
                if value is None or value == "" or value == []:
                    wrong.append(f"{label} has no {field}")
                elif not isinstance(value, list) or not all(_text(v) for v in value):
                    wrong.append(f"{label} has a {field} that is not a list of text")
            elif field == "quote" and field in entry and not _text(value):
                wrong.append(f"{label} has a quote that is not text: give words the ruled place holds, once in its file")
            elif not _text(value):
                wrong.append(f"{label} has no {field}")
        if not wrong and entry["id"].strip() in seen:
            wrong.append(f"the id {entry['id'].strip()} is recorded twice")
        if wrong:
            problems.extend(wrong)
            continue
        seen.add(entry["id"].strip())
        rulings.append(entry)
    return rulings, problems

def _ruling_problems(label, f, rulings, file_lines):
    """S5, C. What is wrong with the ruling a finding cites."""
    cited = _text(f.get("ruling"))
    ruling = next((r for r in rulings or [] if isinstance(r, dict) and _text(r.get("id")) == cited), None)
    if ruling is None:
        return [f"{label} cites the ruling {cited}, and no such ruling is recorded ({UI_GATE_RULINGS})"]
    problems = []
    rules = [_text(r) for r in ruling.get("rules") or [] if _text(r)] if isinstance(ruling.get("rules"), list) else []
    covers = [_text(c) for c in ruling.get("covers") or [] if _text(c)] if isinstance(ruling.get("covers"), list) else []
    if _text(f.get("rule")) and _text(f.get("rule")) not in rules:
        problems.append(f"{label} cites the ruling {cited}, which covers the rule{'s' if len(rules) > 1 else ''} "
                        f"{_all_of(rules) if rules else '(none)'}, not this one")
    location = _text(f.get("location"))
    if location and UI_GATE_LOCATION.fullmatch(location) and location.rsplit(":", 1)[0] not in covers:
        problems.append(f"{label} is at {location.rsplit(':', 1)[0]}, and the ruling {cited} covers the "
                        f"file{'s' if len(covers) > 1 else ''} {_all_of(covers) if covers else '(none)'}")
    where = ruling.get("where")
    if not _text(where):
        problems.append(f"{label} cites the ruling {cited}, which points nowhere: its where is not a path")
    elif UI_GATE_LOCATION.fullmatch(where.strip()):
        # ALYDAS-455. The shape before: a line moves when one is added above it.
        problems.append(f"{label} cites the ruling {cited}, which points nowhere: its where is {where.strip()}, a "
                        f"path and a line; a where is the file alone, and its quote says the place")
    elif _leaves(where.strip()):
        problems.append(f"{label} cites the ruling {cited}, which points nowhere: {where.strip()} is outside the "
                        f"repository (a where is a path from the repository root, with no .. and not absolute)")
    else:
        path = where.strip()
        lines = file_lines(path) if file_lines else None
        if isinstance(lines, Unreadable):
            # Gate 8, security 6. There, and not read: over the ceiling.
            problems.append(f"{label} cites the ruling {cited}, which points nowhere: {path} {lines}")
        elif lines is None:
            problems.append(f"{label} cites the ruling {cited}, which points nowhere: {path} names a file that is "
                            f"not in the repository")
        elif not _text(ruling.get("quote")):
            problems.append(f"{label} cites the ruling {cited}, which has no quote, so it names no place in {path}")
        else:
            # Gate 6, and ALYDAS-455. The quote, case and spacing aside, is in
            # the file once: not there is a ruling whose comment is gone, and
            # twice does not say which place is ruled.
            squeezed = lambda words: " ".join(words.split()).casefold()
            times = squeezed(" ".join(lines)).count(squeezed(ruling["quote"]))
            if times == 0:
                problems.append(f"{label} cites the ruling {cited}, whose quote is not at its where: "
                                f"\"{_cut(ruling['quote'], 80)}\" is not in {path}")
            elif times > 1:
                problems.append(f"{label} cites the ruling {cited}, whose quote does not say which place is ruled: "
                                f"\"{_cut(ruling['quote'], 80)}\" is in {path} {times} times")
    return problems

def _leaves(path):
    """Gate 6. Whether a path written in a report or a ruling leads out of the
    repository as written: it is absolute, or one of its segments is `..`."""
    return os.path.isabs(path) or path.startswith(("/", "\\")) or ".." in re.split(r"[/\\]", path)

def check_measurement(held):
    """Gate 6. What makes a measurement file less than a measurement, as plain
    lines; none when it has what the measuring module writes: a `screen`, a
    `state`, and a `contexts` object holding each of the four contexts with a
    `failures` list and an `innerWidth`. Pure."""
    if not isinstance(held, dict):
        return ["it is not a JSON object"]
    wrong = [f"it has no {field}" for field in ("screen", "state") if not _text(held.get(field))]
    contexts = held.get("contexts")
    if not isinstance(contexts, dict):
        return wrong + ["it has no contexts object"]
    for name in UI_GATE_CONTEXTS:
        context = contexts.get(name)
        if not isinstance(context, dict):
            wrong.append(f"it has no {name} context")
            continue
        lacks = [words for words, there in (
            ("a failures list", isinstance(context.get("failures"), list)),
            ("an innerWidth", isinstance(context.get("innerWidth"), (int, float))
             and not isinstance(context.get("innerWidth"), bool))) if not there]
        if lacks:
            wrong.append(f"its {name} context has no {' and no '.join(lacks)}")
    return wrong

def unreviewed_measurements(measurements, screens):
    """Gate 6. A measurement file of the run whose screen no report reviews,
    each as a plain line. Pure. A screen's name is matched exactly, case and
    spacing included, so a failing file under another spelling of a reviewed
    screen is refused here and not passed over."""
    return [f"measurement file {name} measured the screen {_text(held.get('screen'))}, and no report of this run is "
            f"for a screen spelled exactly that: measured but not reviewed"
            for name, held in sorted(measurements.items())
            if isinstance(held, dict) and _text(held.get("screen")) not in screens]

def _screen_blocks(report, screen_file):
    """Gate 7. The blocks of the report's screen file, each a JSON object, or
    None when that file cannot be used or is for another screen: then
    `_screen_problems` says what is wrong with it, and the blocks are not
    compared."""
    named = _text(report.get("screenFile"))
    held = screen_file(named) if named and screen_file and not _leaves(named) else None
    if not isinstance(held, dict) or isinstance(held, Unreadable):
        return None
    if _text(report.get("screen")) and _text(held.get("screen")) != _text(report.get("screen")):
        return None
    return [b for b in held.get("blocks") or [] if isinstance(b, dict)] if isinstance(held.get("blocks"), list) else []

def _held(report):
    """How many findings the report holds."""
    return len(report["findings"]) if isinstance(report.get("findings"), list) else 0

def _holds(held):
    """"the report holds 3 findings, and a report carries at most 15"."""
    return (f"the report holds {held} finding{'' if held == 1 else 's'}, and a report carries at most "
            f"{UI_GATE_MOST_FINDINGS}")

def _placement_problems(report, read, label, blocks):
    """Gate 7 (the board item's criterion 8). The report states every block:
    `placement` has one entry for each block of the screen file, with the
    jobs it serves as the file has them, whether its place fits, a note, and,
    when it does not fit, the Placement finding that says why. And every
    Placement finding is the reason some block does not fit."""
    if "placement" not in report:
        return ["placement is missing: one entry for each block of the screen file, saying which jobs it serves and "
                "whether its place fits"]
    entries = report["placement"]
    if not isinstance(entries, list):
        return ["placement is not a list"]
    problems, findings, pointed, named = [], dict(read), set(), {}
    for at, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            problems.append(f"placement entry {at} is not a JSON object")
            continue
        block = _text(entry.get("block"))
        mine = f"placement entry {at} ({block})" if block else f"placement entry {at}"
        if not block:
            problems.append(f"{mine} names no block")
        named.setdefault(block, []).append(at)
        fits, pointer = entry.get("fits"), entry.get("finding")
        # Gate 7. `leftOut`: the block does not fit, and the cap cut the
        # finding that says why, so there is none to point at. The note then
        # names the placement rule, and notVerified says something was cut.
        left_out = entry.get("leftOut", False)
        if not isinstance(left_out, bool):
            problems.append(f"{mine} has a leftOut that is not true or false")
            left_out = False
        if not isinstance(fits, bool):
            problems.append(f"{mine} has a fits that is not true or false")
        elif left_out and fits:
            problems.append(f"{mine} has leftOut true and fits true: leftOut is for a block that does not fit, whose "
                            f"finding the cap left out")
        elif left_out and pointer is not None:
            problems.append(f"{mine} has leftOut true and a finding: when the finding is in the report, point at it "
                            f"and leave leftOut out")
        elif left_out:
            names = lambda rule: re.search(rf"(?<![A-Z0-9-]){re.escape(rule)}(?![A-Z0-9-])", str(entry.get("note") or ""))
            placing = [rule for rule in UI_GATE_RULES if rule.startswith("PLACE-")]
            if not any(names(rule) for rule in placing):
                # Gate 8. The ones the cap may cut: an automatic HIGH is not offered.
                cut = [rule for rule in placing if rule not in UI_GATE_AUTOMATIC_HIGH]
                problems.append(f"{mine} says its objection was left out by the cap, and its note does not name the "
                                f"rule ({_one_of(cut)}; the ids all start PLACE-)")
            if not (isinstance(report.get("notVerified"), list) and report["notVerified"]):
                problems.append(f"{mine} says its objection was left out by the cap, and notVerified is empty: it "
                                f"says how many findings were left out")
            # Gate 8, correctness 5. The cap never cuts an automatic HIGH, and
            # it cuts nothing from a report that is not full.
            for rule in UI_GATE_AUTOMATIC_HIGH:
                if names(rule):
                    problems.append(f"{mine} says its objection was left out by the cap, and its note names {rule}, "
                                    f"which is automatic HIGH (references/ui-rules.md): an automatic HIGH is never left "
                                    f"out by the cap, so it is a finding of this report")
            held = _held(report)
            if held < UI_GATE_MOST_FINDINGS:
                problems.append(f"{mine} says its objection was left out by the cap, and {_holds(held)}: the cap "
                                f"leaves nothing out of a report that is not full, so the objection is a finding")
        elif fits and pointer is not None:
            problems.append(f"{mine} fits, and carries a finding: a block that fits points at none (null)")
        elif not fits:
            about = findings.get(pointer) if isinstance(pointer, int) and not isinstance(pointer, bool) else None
            if about is None or _text(about.get("domain")).casefold() != "placement":
                candidates = [label[n] for n, f in read if _text(f.get("domain")).casefold() == "placement"]
                problems.append(
                    f"{mine} does not fit, and its finding "
                    + (f"is {label[pointer]}, which is not a Placement finding" if about is not None
                       else "is not the number of a finding in this report")
                    + ": it is the place, counted from 1, of the Placement finding that says why"
                    + (f" ({_all_of(candidates)})" if candidates else ""))
            else:
                pointed.add(pointer)
        if not _text(entry.get("note")):
            problems.append(f"{mine} has no note: one sentence on where the block sits and why that fits or does not")
        if blocks is not None and block:
            theirs = [b for b in blocks if _text(b.get("name")) == block]
            if not theirs:
                problems.append(f"{mine} is for {block}, and the screen file has no such block")
            elif entry.get("serves") != theirs[0].get("serves", []):
                problems.append(f"{mine} says it serves {json.dumps(entry.get('serves'))}, and the screen file says "
                                f"{json.dumps(theirs[0].get('serves', []))}")
    for block in dict.fromkeys(_text(b.get("name")) for b in blocks or []):
        times = len(named.get(block, []))
        if times == 0:
            problems.append(f"placement has no entry for the block {block}: every block of the screen file is stated, "
                            f"not only the ones that do not fit")
        elif times > 1:
            problems.append(f"placement has {times} entries for the block {block}, and each block is stated once")
    # Gate 8, as ruled. A block that does not fit is a Placement finding,
    # in the report or cut from it: Placement's coverage is never `clear`.
    unfit = [f"placement entry {at}" + (f" ({_text(e.get('block'))})" if _text(e.get("block")) else "")
             for at, e in enumerate(entries, 1) if isinstance(e, dict) and e.get("fits") is False]
    rows = report.get("coverage") if isinstance(report.get("coverage"), list) else []
    for row in rows:
        if unfit and isinstance(row, dict) and _text(row.get("domain")).casefold() == "placement" \
                and row.get("result") in UI_GATE_RESULTS and row["result"] != "findings":
            problems.append(f"coverage gives Placement the result {row['result']}, and {unfit[0]} does not fit"
                            + (f" (and {len(unfit) - 1} more)" if len(unfit) > 1 else "")
                            + ": a block that does not fit makes Placement's result findings")
    for at, f in read:
        # A ruled Placement finding is no block's reason not to fit: the block
        # fits by the ruling, and the finding stays in the report.
        if _text(f.get("domain")).casefold() == "placement" and at not in pointed and f.get("ruling") is None:
            problems.append(f"{label[at]} is a Placement finding, and no placement entry points at it: a placement "
                            f"finding names its block")
    return problems

def _screen_problems(report, measurements, screen_file):
    """S5, D and E. The screen the report is about: its declared jobs, and
    the measured failures a finding has to answer."""
    problems, states = [], []
    screen = _text(report.get("screen"))
    if not screen:
        problems.append("the report names no screen")
    named = _text(report.get("screenFile"))
    if not named:
        problems.append("the report has no screenFile: the screen's file, a path from the repository root "
                        "(ui-gate/<screen>.json)")
    elif _leaves(named):
        problems.append(f"the screen file {named} is outside the repository: it is a path from the repository root, "
                        f"with no .. and not absolute")
    else:
        held = screen_file(named) if screen_file else None
        if held is None:
            problems.append(f"the screen file {named} does not exist")
        elif isinstance(held, NotRead):
            problems.append(f"the screen file {named} {held}")
        elif isinstance(held, Unreadable):
            problems.append(f"the screen file {named} is not JSON that can be read ({held})")
        elif not isinstance(held, dict):
            problems.append(f"the screen file {named} is not a JSON object")
        else:
            jobs = held.get("jobs")
            if not isinstance(jobs, list) or not jobs or not all(_text(job) for job in jobs):
                problems.append(f"the jobs are missing from the screen file {named}: it must list what a person "
                                f"comes to the screen to do, each in words")
            if screen and _text(held.get("screen")) != screen:
                problems.append(f"the screen file {named} is for the screen {_text(held.get('screen')) or '(none)'}, "
                                f"and the report is for {screen}")
            elif isinstance(held.get("states"), list):
                states = list(dict.fromkeys(_text(state) for state in held["states"] if _text(state)))
    if not screen:
        return problems
    named_it = [(name, held) for name, held in sorted((measurements or {}).items())
                if isinstance(held, dict) and _text(held.get("screen")) == screen]
    if not named_it:
        problems.append(f"the screen {screen} was not measured: no measurement file names it")
        return problems
    # Gate 6. A file that is less than a measurement does not count, and is named.
    mine = []
    for name, held in named_it:
        wrong = check_measurement(held)
        if wrong:
            problems.append(f"measurement file {name} is not a measurement: {'; '.join(wrong)}")
        else:
            mine.append((name, held))
    if not mine:
        return problems
    # Gate 6. Every state the screen file lists has a measurement of this screen.
    measured = {_text(held.get("state")) for _, held in mine}
    for state in states:
        if state not in measured:
            problems.append(f"the state {state} of the screen {screen} was not measured: {named} lists it, and no "
                            f"measurement file of the screen has that state")
    # S6. Each measured failure is accounted for, not just each rule: every
    # distinct (rule, where) that failed in any state or context of the screen
    # is listed in the `answers` of a measured finding with that rule. And a
    # measured finding belongs to its screen: each place it answers is one
    # where its rule failed here.
    failed = {}
    for name, held in mine:
        contexts = held.get("contexts") if isinstance(held.get("contexts"), dict) else {}
        for context in contexts.values():
            failures = context.get("failures") if isinstance(context, dict) else None
            for failure in failures if isinstance(failures, list) else []:
                rule = _text(failure.get("rule")) if isinstance(failure, dict) else ""
                if rule:
                    failed.setdefault((rule, _text(failure.get("where"))), name)
    failed_rules = {rule for rule, _ in failed}
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    answered = set()
    for at, f in enumerate(findings, 1):
        if not isinstance(f, dict) or f.get("source") != "measured":
            continue
        rule, answers = _text(f.get("rule")), f.get("answers")
        if not rule or not isinstance(answers, list) or not answers or not all(_text(entry) for entry in answers):
            continue   # check_report has said what is wrong with it
        label = f"finding {at} ({rule})"
        if rule not in failed_rules:
            problems.append(f"{label} is marked measured, and no measurement of the screen {screen} failed {rule}: "
                            f"a finding read from the code has the source source")
            continue
        for entry in dict.fromkeys(_text(entry) for entry in answers):
            if (rule, entry) in failed:
                answered.add((rule, entry))
            else:
                problems.append(f"{label} answers {entry}, and the screen's measurements measured no {rule} "
                                f"failure there")
    for (rule, where), name in failed.items():
        if (rule, where) not in answered:
            problems.append(f"the measured failure {rule} at {where or 'no place given'} is not answered: no "
                            f"finding with the source measured and that rule lists it in its answers ({name})")
    return problems

def check_report(report, rulings=None, measurements=None, screen_file=None, file_lines=None, file_there=None):
    """What is wrong with a UI gate report, as plain lines; none when it is
    well formed. Pure: nothing is read or written. A finding is named by its
    place in the list, counted from 1, and its rule.

    S5. The report is checked against what the caller read (`ui_gate_context`):
    `rulings`, the project's settled choices; `measurements`, the run's
    measurement files by name; `screen_file`, which answers what a screen file
    holds (None when there is none, an `Unreadable` when it does not parse);
    `file_lines`, which answers the lines a file holds (None when there is
    none). A finding whose `ruling` is not null counts as ruled, and stays in
    the report.

    Gate 8. `file_there` answers whether a path from the repository root is a
    file inside it; with it, a finding's location has to name one."""
    if not isinstance(report, dict):
        return ["the report is not a JSON object"]
    problems = []
    text = _text

    findings = report.get("findings")
    if not isinstance(findings, list):
        problems.append("findings is not a list (it must be there, and may be empty)")
        findings = []
    if len(findings) > UI_GATE_MOST_FINDINGS:
        problems.append(f"{len(findings)} findings, and a report carries at most {UI_GATE_MOST_FINDINGS}")
    label, read = {}, []
    for at, f in enumerate(findings, 1):
        if not isinstance(f, dict):
            problems.append(f"finding {at} is not a JSON object")
            continue
        label[at] = f"finding {at} ({text(f.get('rule'))})" if text(f.get("rule")) else f"finding {at}"
        read.append((at, f))
        for field in UI_GATE_FIELDS:
            if not text(f.get(field)):
                problems.append(f"{label[at]} has no {field}")
        if text(f.get("severity")) and f["severity"] not in UI_GATE_SEVERITIES:
            problems.append(f"{label[at]} has the severity {f['severity']}, which is not {_one_of(UI_GATE_SEVERITIES)}")
        # Gate 6. The rule is one the rule book defines, and one it marks
        # automatic HIGH is HIGH: a confirmed finding of it is never averaged down.
        if text(f.get("rule")) and text(f["rule"]) not in UI_GATE_RULES:
            problems.append(f"{label[at]} names a rule that references/ui-rules.md does not define")
        elif text(f.get("rule")) in UI_GATE_AUTOMATIC_HIGH and f.get("severity") in UI_GATE_SEVERITIES \
                and f["severity"] != "HIGH":
            problems.append(f"{label[at]} has the severity {f['severity']}, and {text(f['rule'])} is automatic HIGH "
                            f"(references/ui-rules.md): a confirmed finding of it is HIGH")
        if text(f.get("source")) and f["source"] not in UI_GATE_SOURCES:
            problems.append(f"{label[at]} has the source {f['source']}, which is not {_one_of(UI_GATE_SOURCES)}")
        if text(f.get("location")) and not UI_GATE_LOCATION.fullmatch(f["location"]):
            problems.append(f"{label[at]} has the location {f['location']}, which is not shaped path:line "
                            f"(the line a whole number, 1 or more)")
        elif text(f.get("location")) and file_there is not None:
            path = f["location"].rsplit(":", 1)[0]
            if _leaves(path) or not file_there(path):
                problems.append(f"{label[at]} is at {path}, which is not a file in the repository: a location is a "
                                f"path from the repository root to a file that is there")
        # S6. A measured finding says which measured failures it accounts for.
        answers = f.get("answers")
        if f.get("source") == "measured":
            if answers is None or answers == []:
                problems.append(f"{label[at]} has the source measured and no answers: the list of the measured "
                                f"places (each failure's where) it accounts for")
            elif not isinstance(answers, list) or not all(text(entry) for entry in answers):
                problems.append(f"{label[at]} has answers that are not a list of the measured places it accounts "
                                f"for, each as text")
        elif f.get("source") == "source" and answers is not None:
            problems.append(f"{label[at]} has the source source and carries answers: only a finding with the source "
                            f"measured answers measured failures")
        if f.get("ruling") is not None and not text(f.get("ruling")):
            problems.append(f"{label[at]} has a ruling that is neither null nor a ruling id")
        elif f.get("ruling") is not None:
            problems.extend(_ruling_problems(label[at], f, rulings, file_lines))
    domain_of = lambda f: text(f.get("domain")).casefold()
    unruled = [at for at, f in read if f.get("severity") == "HIGH" and f.get("ruling") is None]

    if "notVerified" not in report:
        problems.append("notVerified is missing: it must be there, and may be empty")
    elif not isinstance(report["notVerified"], list) or not all(text(entry) for entry in report["notVerified"]):
        problems.append("notVerified is not a list of the checks that could not be made, each in words")
    else:
        for i, entry in enumerate(report["notVerified"], 1):
            for at, f in read:
                for field in ("why", "before"):
                    if text(f.get(field)) and text(f[field]).casefold() == text(entry).casefold():
                        problems.append(f"notVerified entry {i} is also {label[at]}'s {field}: a check that was not "
                                        f"rendered is not a finding")

    coverage = report.get("coverage")
    rows = [row for row in coverage if isinstance(row, dict)] if isinstance(coverage, list) else []
    if not isinstance(coverage, list):
        problems.append("coverage is not a list")
    elif len(rows) != len(coverage):
        problems.append("coverage has a row that is not a JSON object")
    known = {domain.casefold(): domain for domain in UI_GATE_DOMAINS}
    # S5, F. A finding belongs to one of the seven.
    for at, f in read:
        if domain_of(f) and domain_of(f) not in known:
            problems.append(f"{label[at]} names the domain {text(f['domain'])}, which is not one of the seven domains "
                            f"({', '.join(UI_GATE_DOMAINS)})")
    named = [text(row.get("domain")).casefold() for row in rows]
    for domain in UI_GATE_DOMAINS:
        times = named.count(domain.casefold())
        if times == 0:
            problems.append(f"coverage does not name {domain}")
        elif times > 1:
            problems.append(f"coverage names {domain} {times} times, and each domain is named once")
    not_reviewed = []
    for row in rows:
        domain = text(row.get("domain"))
        if domain.casefold() not in known:
            problems.append(f"coverage names {domain or 'a row with no domain'}, which is not one of the seven domains "
                            f"({', '.join(UI_GATE_DOMAINS)})")
            continue
        domain, result = known[domain.casefold()], row.get("result")
        if not text(result):
            problems.append(f"coverage gives {domain} no result ({_one_of(UI_GATE_RESULTS)})")
        elif result not in UI_GATE_RESULTS:
            problems.append(f"coverage gives {domain} the result {result}, which is not {_one_of(UI_GATE_RESULTS)}")
        naming = [at for at, f in read if domain_of(f) == domain.casefold()]
        if result == "clear" and naming:
            problems.append(f"coverage says {domain} is clear, and {label[naming[0]]} names it"
                            + (f" (and {len(naming) - 1} more)" if len(naming) > 1 else ""))
        # Gate 7. A domain whose findings were all cut by the cap says so, in
        # these words, in what it inspected; then `findings` with none stands.
        if result == "findings" and not naming and UI_GATE_CUT not in text(row.get("inspected")).casefold():
            problems.append(f"coverage says {domain} has findings, and no finding names it (when the cap cut them "
                            f"all, its inspected says \"{UI_GATE_CUT}\")")
        # Gate 8, correctness 5. And the cap cut them only from a report that
        # is full, or one that blocks whatever was cut: otherwise "left out
        # by the cap" would let a report Approve what it says it found.
        elif result == "findings" and not naming and report.get("verdict") != "Block" \
                and _held(report) < UI_GATE_MOST_FINDINGS:
            problems.append(f"coverage says the findings of {domain} were {UI_GATE_CUT}, and {_holds(_held(report))}: "
                            f"the cap leaves nothing out of a report that is not full, so they are findings of this "
                            f"report")
        # S5, F. A domain nobody looked at has no findings, and one somebody
        # looked at says what was looked at.
        if result == "not-reviewed":
            for at in naming:
                problems.append(f"{label[at]} names {domain}, which coverage says was not reviewed")
        if result in ("clear", "findings") and not text(row.get("inspected")):
            problems.append(f"coverage says {domain} {'is clear' if result == 'clear' else 'has findings'}, and does "
                            f"not say what was inspected")
        if result == "not-reviewed" and domain not in not_reviewed:
            not_reviewed.append(domain)

    verdict = report.get("verdict")
    if verdict == "Approve":
        for at in unruled:
            problems.append(f"the verdict is Approve, and {label[at]} is HIGH with no ruling")
        for domain in not_reviewed:
            problems.append(f"the verdict is Approve, and {domain} was not reviewed")
    elif verdict == "Block":
        if not unruled:
            problems.append("the verdict is Block, and no HIGH finding is without a ruling")
    else:
        problems.append("the verdict is neither Approve nor Block"
                        + (f" (it is {verdict})" if text(verdict) else ""))
    problems.extend(_placement_problems(report, read, label, _screen_blocks(report, screen_file)))
    problems.extend(_screen_problems(report, measurements, screen_file))
    return problems

def ui_gate_problems(run):
    """What Phase 8 still needs from the UI gate."""
    root, slug = run["root"], run["slug"]
    surfaces, unknown = ui_gate_surfaces(run)
    if unknown:
        return [unknown]
    names = surface_names(surfaces)
    gate = run.get("ui_gate") if isinstance(run.get("ui_gate"), dict) else {}
    skip = gate.get("skip")
    pattern = ui_gate_pattern(run)
    hits = ui_gate_files(run, "report")
    if surfaces and not hits and skip:
        return [Problem(f"the recorded ui-gate skip cannot stand, because these files are rendered surfaces: {names}; "
                        f"a UI gate report is needed ({root}/{pattern})",
                        f"the UI gate skip cannot stand, because the run changed rendered surfaces: {names}")]
    if surfaces and not hits:
        return [Problem(f"UI gate report missing ({root}/{pattern}): a UI gate report is needed, because this run "
                        f"changed rendered surfaces: {names}",
                        f"a UI gate report is needed, because the run changed rendered surfaces: {names}")]
    # Gate 6. A skip never switches the check off: with a report there, the
    # report is checked like any other, skip or no skip.
    if not surfaces and skip and not hits:
        return []
    if not hits:
        return [Problem(f"no changed file is a rendered surface, and the run does not say so: run "
                        f"ui-gate skip {slug} --reason \"<why>\" (or write a UI gate report, {root}/{pattern})",
                        "no changed file is drawn on a screen, and the run has not recorded that it skips the UI gate")]
    # S5. Every report, one a screen: each is checked, and each has to approve.
    reports, _ = ui_gate_reports(run)
    context, shared = ui_gate_context(run, reports)
    problems = [Problem(f"UI gate: {line}", f"the UI gate: {line}") for line in shared]
    for name, report, unreadable in reports:
        if unreadable:
            problems.append(unreadable)
            continue
        # S2. The report's form first, so an Approve that does not follow from
        # what the report itself says closes nothing.
        problems.extend(Problem(f"UI gate report {name}: {line}", f"the UI gate report {name}: {line}")
                        for line in check_report(report, **context))
        if isinstance(report, dict) and report.get("verdict") == "Block":
            problems.append(Problem(f"UI gate report {name} has the verdict Block",
                                    f"the UI gate report {name} says Block"))
    return problems

# ---------- ALYDAS-174, S1: Gate 8's review record ----------
#
# The lead writes `measurements/<date>-<slug>-review.json` beside the prose
# review, and `review record <slug>` turns it into Phase 8's `review`: the
# record's own parts as written (trimmed), each changed file's lines from
# `git diff --numstat <base>...HEAD` filed into the first area whose pattern
# matches it (or "Other", never dropped), tests marked by the UI gate's own
# patterns, each area's totals, and the commits reviewed. The pure parts take
# the numstat and the JSON and touch neither git nor a file; the command is
# the edge. The record is refused whole, never cut: past any of its bounds in
# LEDGER_BOUNDS, or past REVIEW_MOST_BYTES once built. `trim_ledger` leaves it
# alone (it cuts only the keys it names) and counts it (`ledger_bytes`).

REVIEW_MOST_BYTES = 48 * 1024
# Gate 8 ruling (Ask fc55dcae, "fold"). An area lists at most this many files:
# the ones with the most lines changed (added and removed, ties by path), in
# path order. The rest fold into the area's `more`, their count and totals,
# drawn as one line ("and 258 more files, +4,100 -0"). The area's own totals
# still count every file, and so do the record's. The desk's
# `LEDGER_BOUNDS.areaFiles` is this number, which the parity pin holds.
REVIEW_AREA_FILES = 40
REVIEW_COUNTS = ("added", "removed", "testAdded", "testRemoved")
REVIEW_GROUPS = ("needs-you", "fixed", "later")
REVIEW_OTHER = "Other"
# What the desk refuses as a control character: tabs and line breaks pass, so
# a test excerpt keeps its lines.
REVIEW_CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f]")
REVIEW_FINDING_TEXTS = (("title", "findingTitle"), ("plain", "findingText"), ("why", "findingText"),
                        ("how", "findingText"), ("by", "findingBy"))

def _review_clean(value):
    """Every string in a review trimmed as the desk trims, its bidi controls
    taken out as `plain` takes them; lists and objects walked. Pure."""
    if isinstance(value, str):
        return re.sub(r"^[\s﻿]+|[\s﻿]+$", "", BIDI_CONTROL.sub("", value))
    if isinstance(value, list):
        return [_review_clean(v) for v in value]
    if isinstance(value, dict):
        return {k: _review_clean(v) for k, v in value.items()}
    return value

def _whole(value, low, high):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high

def review_problems(review, recorded=True):
    """What is wrong with a review, as lines; none when it is well formed.
    Pure. `recorded` False checks the file the lead wrote, before the script
    has added the files, the totals and the commits; True checks the record
    as the ledger holds it and the desk takes it."""
    probs = []
    if not isinstance(review, dict):
        return ["a review is a JSON object with areas, findings, proofs, tests and a verdict"]
    b = LEDGER_BOUNDS

    def text(value, where, bound, need=True):
        if value is None and not need:
            return
        if not isinstance(value, str) or (need and not said(value)):
            probs.append(f"{where} is missing: it is text that says something")
        elif len(value) > b[bound]:
            probs.append(f"{where} is {len(value):,} characters, over the {b[bound]:,} it may be")
        elif REVIEW_CONTROL.search(value):
            # ALYDAS-174, Gate 6, M39. The desk refuses the whole beat for one,
            # so the refusal names it and says what to do.
            found = REVIEW_CONTROL.search(value).group(0)
            probs.append(f"{where} has a control character in it (U+{ord(found):04X}): keep it to plain "
                         f"text, where only tabs and line breaks may stay, and record it again")

    def listed(value, where, bound, need=True):
        if value is None and not need:
            return []
        if not isinstance(value, list):
            probs.append(f"{where} is not a list")
            return []
        if len(value) > b[bound]:
            probs.append(f"{where} has {len(value)}, over the {b[bound]} a review may have")
            return []
        return value

    def count(value, where, low=0):
        if not _whole(value, low, b["lines"]):
            probs.append(f"{where} is not a whole number from {low} to {b['lines']:,}")

    if recorded:
        for key in ("base", "head"):
            # ALYDAS-174, Gate 6, M38. The desk's `reviewCommit` (COMMIT_SHAPE in
            # @alydash/core/text) refuses the whole beat for one that is not.
            if not isinstance(review.get(key), str) or not COMMIT_SHAPE.fullmatch(review.get(key)):
                probs.append(f"the review's {key} is {json.dumps(review.get(key))[:60]}, not a commit: a review "
                             f"names its base and head each as the 40 or 64 lowercase hex characters git "
                             f"prints, so record it again from the branch's checkout")
    names = set()
    for i, area in enumerate(listed(review.get("areas"), "the review's areas", "areas"), start=1):
        if not isinstance(area, dict):
            probs.append(f"area {i} is not an object with a name and its patterns")
            continue
        text(area.get("name"), f"area {i}'s name", "areaName")
        where = f"area {area.get('name')!r}" if isinstance(area.get("name"), str) and said(area.get("name")) else f"area {i}"
        if area.get("name") in names:
            probs.append(f"{where} is named twice")
        names.add(area.get("name"))
        text(area.get("about"), f"{where}'s about line", "areaAbout", need=False)
        for pattern in listed(area.get("patterns"), f"{where}'s patterns", "patterns", need=recorded):
            text(pattern, f"a pattern of {where}", "pattern")
        if not recorded:
            continue
        files = listed(area.get("files"), f"{where}'s files", "areaFiles")
        for f in files:
            if not isinstance(f, dict):
                probs.append(f"a file of {where} is not an object")
                continue
            text(f.get("path"), f"a file's path in {where}", "filePath")
            count(f.get("added"), f"{f.get('path')}'s lines added")
            count(f.get("removed"), f"{f.get('path')}'s lines removed")
            if not isinstance(f.get("test"), bool):
                probs.append(f"{f.get('path')} does not say whether it is a test")
            if "binary" in f and f["binary"] is not True:
                probs.append(f"{f.get('path')}'s binary is true or left out")
        for key in REVIEW_COUNTS:
            count(area.get(key), f"{where}'s {key}")
        if "more" in area:
            more = area["more"]
            if not isinstance(more, dict):
                probs.append(f"{where}'s more is not an object with the folded files' count and totals")
            else:
                count(more.get("files"), f"{where}'s folded files", low=1)
                for key in REVIEW_COUNTS:
                    count(more.get(key), f"{where}'s folded {key}")
                if isinstance(area.get("files"), list) and len(area["files"]) != REVIEW_AREA_FILES:
                    probs.append(f"{where} folds files but lists {len(area['files'])}: an area folds only past "
                                 f"{REVIEW_AREA_FILES} files, and then lists exactly {REVIEW_AREA_FILES}")
    ids = set()
    for i, finding in enumerate(listed(review.get("findings"), "the review's findings", "findings"), start=1):
        if not isinstance(finding, dict):
            probs.append(f"finding {i} is not an object")
            continue
        text(finding.get("id"), f"finding {i}'s id", "findingId")
        where = f"finding {finding.get('id')}" if isinstance(finding.get("id"), str) and said(finding.get("id")) else f"finding {i}"
        if finding.get("id") in ids:
            probs.append(f"{where} is named twice: each finding has its own id")
        ids.add(finding.get("id"))
        for key, bound in REVIEW_FINDING_TEXTS:
            text(finding.get(key), f"{where}'s {key}", bound)
        text(finding.get("file"), f"{where}'s file", "filePath", need=False)
        if finding.get("line") is not None:
            count(finding.get("line"), f"{where}'s line", low=1)
            if finding.get("file") is None:
                probs.append(f"{where} names a line but no file: a line is a line of a file")
        if finding.get("group") not in REVIEW_GROUPS:
            probs.append(f"{where}'s group is {json.dumps(finding.get('group'))}, and a finding's group is "
                         f"needs-you, fixed or later")
        if not isinstance(finding.get("blocks"), bool):
            probs.append(f"{where} does not say whether it blocks the merge (blocks: true or false)")
    for i, proof in enumerate(listed(review.get("proofs"), "the review's proofs", "proofs"), start=1):
        if not isinstance(proof, dict):
            probs.append(f"proof {i} is not an object")
            continue
        if not _whole(proof.get("criterion"), 1, b["criterion"]):
            probs.append(f"proof {i}'s criterion is not a criterion's number, from 1 to {b['criterion']}")
        text(proof.get("text"), f"proof {i}'s text", "proofText")
        text(proof.get("how"), f"proof {i}'s how", "proofText")
    tests = review.get("tests")
    if not isinstance(tests, dict):
        probs.append("the review's tests are missing: the command, and how many passed, failed and were skipped")
    else:
        text(tests.get("command"), "the tests' command", "testCommand")
        for key in ("passed", "failed", "skipped"):
            count(tests.get(key), f"the tests' {key}")
        text(tests.get("excerpt"), "the tests' excerpt", "testExcerpt", need=False)
    text(review.get("verdict"), "the review's verdict", "verdict")
    return probs

def parse_numstat(out):
    """`git diff --numstat -z -M` as (path, added, removed, binary).
    Pure. A binary file is counted as no lines, and says so. Gate 8, CR-4: a
    rename is one row under its new path with only its changed lines, as
    GitHub's compare lists it. In the `-z` form its entry has an empty path
    and the old and new paths follow as two fields of their own, so no
    `old => new` or `{a => b}` text is parsed, and a name holding either is
    read whole."""
    rows = []
    fields = iter(out.split("\0"))
    for entry in fields:
        if not entry:
            continue
        added, removed, path = entry.split("\t", 2)
        if path == "":
            next(fields)  # the old path, which the compare does not list
            path = next(fields)
        binary = added == "-" and removed == "-"
        rows.append((path, 0 if binary else int(added), 0 if binary else int(removed), binary))
    return rows

def review_areas(areas, numstat, ignore):
    """The areas with their files and totals. Pure. Each file goes to the first
    area with a pattern it matches (`_glob_match`, the UI gate's), in the order
    the areas are written; a file no pattern matches goes to "Other", an area
    of that name when the review has one, else one added last. A file is a
    test when it matches one of `ignore`, the UI gate's test patterns."""
    out = [dict(area, files=[]) for area in areas]
    other = next((a for a in out if a.get("name") == REVIEW_OTHER), None)
    for path, added, removed, binary in sorted(numstat):
        f = {"path": path, "added": added, "removed": removed,
             "test": any(_glob_match(path, pattern) for pattern in ignore)}
        if binary:
            f["binary"] = True
        home = next((a for a in out if any(_glob_match(path, p) for p in a.get("patterns") or [])), None)
        if home is None:
            if other is None:
                other = {"name": REVIEW_OTHER, "patterns": [], "files": []}
                out.append(other)
            home = other
        home["files"].append(f)
    for area in out:
        area.update(_review_totals(area["files"]))
        if len(area["files"]) > REVIEW_AREA_FILES:
            # Gate 8 ruling (Ask fc55dcae): the most changed kept, ties by path.
            ranked = sorted(area["files"], key=lambda f: (-(f["added"] + f["removed"]), f["path"]))
            folded = ranked[REVIEW_AREA_FILES:]
            area["files"] = sorted(ranked[:REVIEW_AREA_FILES], key=lambda f: f["path"])
            area["more"] = dict(files=len(folded), **_review_totals(folded))
    return out

def _review_totals(files):
    """Lines added and removed over `files`, and the tests' share apart. Pure."""
    return {"added": sum(f["added"] for f in files), "removed": sum(f["removed"] for f in files),
            "testAdded": sum(f["added"] for f in files if f["test"]),
            "testRemoved": sum(f["removed"] for f in files if f["test"])}

def review_record(raw, numstat, ignore, base, head):
    """The record Phase 8 keeps, and None; or None and the lines saying why
    not. Pure. `raw` is the parsed review.json."""
    given = _review_clean(raw)
    probs = review_problems(given, recorded=False)
    if not probs:
        for key, sha in (("base", base), ("head", head)):
            if given.get(key) is not None and given.get(key) != sha:
                probs.append(f"the review names {key} {given.get(key)}, and the commits reviewed are "
                             f"{base}...{head}: record it again for what was reviewed")
    if probs:
        return None, probs
    finding_keys = ("id", "title", "plain", "why", "how", "by", "file", "line", "group", "blocks")
    record = {
        "base": base, "head": head,
        "areas": review_areas([{k: a[k] for k in ("name", "about", "patterns") if a.get(k) is not None}
                               for a in given["areas"]], numstat, ignore),
        "findings": [{k: f[k] for k in finding_keys if f.get(k) is not None} for f in given["findings"]],
        "proofs": [{k: p[k] for k in ("criterion", "text", "how")} for p in given.get("proofs") or []],
        "tests": {k: given["tests"][k] for k in ("command", "passed", "failed", "skipped", "excerpt")
                  if given["tests"].get(k) is not None},
        "verdict": given["verdict"],
    }
    probs = review_problems(record)
    size = review_bytes(record)
    if size > REVIEW_MOST_BYTES:
        probs.append(f"the record is {size:,} bytes, over the {REVIEW_MOST_BYTES:,} bytes a review may be: it is "
                     f"refused rather than cut, so shorten the findings or the excerpt")
    return (None, probs) if probs else (record, [])

def review_bytes(review):
    """The record's size as the beat sends it, as `ledger_bytes` counts."""
    return len(json.dumps(review, ensure_ascii=False).encode("utf-8"))

def ledger_review(review):
    """Phase 8's review for the beat: the record when it is still well formed
    and within its size, else None."""
    if not isinstance(review, dict) or review_problems(review) or review_bytes(review) > REVIEW_MOST_BYTES:
        return None
    return review

def review_close_problems(run):
    """Phase 8's check on the record: there, well formed, and made at HEAD."""
    slug = run["slug"]
    review = phase_of(run, 8).get("review")
    if review is None:
        return [Problem(f"no review record: write measurements/<date>-{slug}-review.json (areas, findings, proofs, "
                        f"tests, verdict), then run review record {slug}",
                        "the review is not recorded for the desk")]
    wrong = review_problems(review)
    if review_bytes(review) > REVIEW_MOST_BYTES:
        wrong.append(f"it is over {REVIEW_MOST_BYTES:,} bytes")
    if wrong:
        return [Problem(f"the review record on the ledger is not well formed ({'; '.join(wrong[:3])}): run review "
                        f"record {slug} again", "the recorded review is not well formed")]
    # Gate 8, CR-1 / W-1. A record of an older commit is a review of another
    # delivery: a fix pass after a send-back, or an amend. A send-back keeps
    # the record on the ledger; this check is what makes it count only for the
    # HEAD it was made at, and it covers a reopen by hand as well. Outside a
    # repository there is no HEAD to move (and `review record` refuses there),
    # so nothing is compared.
    now = head_commit()
    if now is not None and now != review["head"]:
        return [Problem(f"the review was recorded at {review['head'][:12]}, and HEAD is {now[:12]}: "
                        f"run review record {slug} again",
                        "the recorded review is of an older commit than the branch's")]
    return []

def cmd_review(a):
    """ALYDAS-174, S1. `review record <slug>`: Gate 8's review.json, checked,
    counted and written on Phase 8. Exit 2, nothing written, when it is
    refused. It beats, as `ui-gate record` does, and pushes nothing."""
    path = find_run(a.slug, a.root)
    run = load(path)
    slug = run["slug"]
    # Gate 8, W-4. A review is recorded while Phase 8 runs or is held, never
    # before it starts (nothing was reviewed) or after it closed.
    status = phase_of(run, 8).get("status")
    if status == "pending":
        die(f"review record: Phase 8 has not started (it is pending), so there is no review to record yet: "
            f"run phase {slug} 8 start first", 2)
    if status not in ("active", "blocked"):
        die(f"review record: Phase 8 is {status}, so its review is no longer recorded: "
            f"run phase {slug} 8 start to review again", 2)
    hits = _find(run["root"], f"measurements/*-{slug}-review.json")
    if not hits:
        die(f"review record: there is no {run['root']}/measurements/<date>-{slug}-review.json to record", 2)
    name = os.path.basename(hits[-1])
    try:
        with open(hits[-1], encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        die(printable(f"review record: {name} is not JSON that can be read ({e}); nothing was recorded"), 2)
    try:
        if git_here("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            die(f"review record: {NO_REPO_REASON}, so nothing says what was reviewed", 2)
        base, why = default_base()
        if base is None:
            die(printable(f"review record: the merge-base could not be found ({why}); nothing was recorded"), 2)
        head = git_here("rev-parse", "HEAD").stdout.strip()
        if base == head:
            base = recorded_base(run) or base
        diff = git_here("diff", "--numstat", "-z", "-M", f"{base}...HEAD", "--", errors="replace")
        if diff.returncode != 0:
            die(printable("review record: git could not count the lines: " + diff.stderr.strip()[:200]), 2)
        config = ui_gate_config(run, base)
    except (OSError, subprocess.SubprocessError) as e:
        die(f"review record: git could not be run ({type(e).__name__}); nothing was recorded", 2)
    except ValueError as e:
        die(printable(f"review record: {UI_GATE_CONFIG} {e}, so nothing says which files are tests"), 2)
    record, probs = review_record(raw, parse_numstat(diff.stdout), config["ignore"], base, head)
    if probs:
        for line in probs:
            print(printable(f"✗ {name}: {line}"), file=sys.stderr)
        die(f"review record: nothing was recorded, because {name} is refused (above)", 2)
    phase_of(run, 8)["review"] = record
    files = sum(len(area["files"]) + area.get("more", {}).get("files", 0) for area in record["areas"])
    blocking = sum(1 for f in record["findings"] if f["blocks"])
    said_it = (f"recorded the review: {files} file{'' if files == 1 else 's'} in {len(record['areas'])} "
               f"area{'' if len(record['areas']) == 1 else 's'}, {len(record['findings'])} "
               f"finding{'' if len(record['findings']) == 1 else 's'}"
               + (f", {blocking} blocking the merge" if blocking else ", none blocking the merge")
               + f", at {head[:12]}")
    log_event(path, 8, "review", said_it)
    save(path, run)
    refresh(path)
    beat = beat_the_write(path)
    print(printable(f"review record: {said_it}"))
    over_budget(obey_the_write(path, beat))

# ALYDAS-553. Gate 7's browser suite totals: one line that names the suite,
# `Browser suite (`, and carries a number before `passed`.
BROWSER_SUITE_TOTALS = re.compile(r"^[^\n]*\bbrowser suite \([^\n]*?\b\d[\d,]* passed\b", re.I | re.M)
# Gate 6 (alydas-545). The line names the commit it ran on (a template's
# `<sha>` does not), more than nought passed, and a failure it carries is
# listed as unverified; `no browser suite` is the clause's end, not words
# inside another sentence ("no browser suite failure").
BROWSER_SUITE_COMMIT = re.compile(r"\b[0-9a-f]{7,40}\b", re.I)
BROWSER_SUITE_PASSED = re.compile(r"\b(\d[\d,]*) passed\b", re.I)
BROWSER_SUITE_FAILED = re.compile(r"\b(\d[\d,]*) failed\b", re.I)
NO_BROWSER_SUITE = re.compile(r"\bno browser suite\b(?=\s*(?:[.;:,)`'\"]|$))", re.I | re.M)

UNVERIFIED_HEADING = re.compile(r"^(#+)[^\n]*\bunverified\b[^\n]*$", re.I | re.M)

def unverified_sections(text):
    """The text under each heading that says unverified, to the next heading
    of the same or a higher level."""
    for heading in UNVERIFIED_HEADING.finditer(text):
        level = len(heading.group(1))
        rest = text[heading.end():]
        stop = re.search(r"^#{1,%d}\s" % level, rest, re.M)
        yield rest[:stop.start()] if stop else rest

def failures_listed(text, failed):
    """Gate 8 (alydas-545), W2. A failing totals line is excused only where
    its failures are listed as unverified: a line that says unverified and
    gives their number, or a section under an Unverified heading that names
    the browser suite or their number. The word anywhere else is not it."""
    count = re.compile(r"\b%d\b" % failed)
    if any("unverified" in line.lower() and count.search(line) for line in text.split("\n")):
        return True
    return any(re.search(r"\bbrowser suite\b", section, re.I) or count.search(section)
               for section in unverified_sections(text))

def browser_suite_problem(text):
    """Gate 7's browser suite check (ALYDAS-553): None when the evidence doc
    gives good totals, or has no totals line and says there is no browser
    suite; else what is wrong, in words. A doc with a totals line is judged
    by it, and `no browser suite` beside a totals line excuses nothing
    (Gate 8, W1 and W2)."""
    told = []
    for line in BROWSER_SUITE_TOTALS.finditer(text):
        said = line.group(0) + text[line.end():].split("\n", 1)[0]
        passed = int(BROWSER_SUITE_PASSED.search(said).group(1).replace(",", ""))
        failed_at = BROWSER_SUITE_FAILED.search(said)
        failed = int(failed_at.group(1).replace(",", "")) if failed_at else 0
        if not BROWSER_SUITE_COMMIT.search(said):
            told.append("names no commit (a template's <sha> is not one): put the commit the suite ran on in it")
        elif passed == 0:
            told.append("says 0 passed: run the suite on the final tree and paste what it printed")
        elif failed > 0 and not failures_listed(text, failed):
            told.append(f"says {failed} failed and they are not listed as unverified: fix them, or show they fail on "
                        f"main too and list them under an '## Unverified' heading that names the browser suite")
        else:
            return None
    if told:
        return "evidence doc's browser suite totals line " + told[0]
    if NO_BROWSER_SUITE.search(text):
        return None
    return ("evidence doc has no browser suite totals: add a line like "
            "'Browser suite (pnpm e2e, final tree at <sha>): 160 passed, 0 failed', "
            "or say 'no browser suite'")

def run_checks(run, n):
    root, slug = run["root"], run["slug"]
    p = phase_of(run, n)
    probs = []
    # ALYDAS-173, S1. Every phase, before its own artifacts: the story is
    # written as the run goes. A ledger from before the field reads as blank.
    # Gate 6: `said`, not `.strip()`, which keeps U+FEFF, so the check and the
    # beat agree on what a blank line is.
    if not said(p.get("plain") or ""):
        probs.append(Problem(f'no plain line: write one with note {slug} {n} --plain "..."', "it has no plain line"))
    # ALYDAS-225, K1. Every phase: a step still to-do is work nobody accounted
    # for, so the phase does not close over it. Each is named with both ways
    # out. A step being done is not one: the close finishes it (`stamp_phase`).
    for i, step in enumerate(p.get("steps") or [], start=1):
        if step_state(step) == "todo":
            text = step.get("text", "")
            probs.append(Problem(f'step {i} is still to do ("{text}"): tick it with step {slug} {n} {i} done, '
                                 f'or drop it with step {slug} {n} {i} dropped --sub "<why>"',
                                 f"step {i} is still to do ({text})"))
    def need(pattern, what, words=None):
        hits = _find(root, pattern)
        if not hits:
            probs.append(Problem(f"{what} missing ({root}/{pattern})", words or f"the {what} is missing"))
        return hits
    if n == 0:
        hits = need(f"specs/*{slug}*goal.md", "goal doc")
        if hits and "observed" not in open(hits[-1]).read().lower():
            probs.append(Problem("goal doc names no observation ('Observed by' per evidence item)",
                                 "the goal doc does not say who observes each piece of evidence"))
    elif n == 1:
        need(f"specs/*{slug}*design.md", "design doc")
    elif n == 2:
        hits = need(f"specs/*{slug}*design.md", "design doc")
        text = open(hits[-1]).read() if hits else ""
        if "phase 2 verdict" not in text.lower():
            probs.append(Problem("design doc has no '## Phase 2 verdict' section", "the design doc has no Phase 2 verdict"))
        if not any(e["path"] for e in p["produced"]):
            probs.append(Problem("no prototype artifact recorded (note --produced <path>); skip needs `phase ... skipped --note`",
                                 "no prototype is recorded"))
    elif n == 3:
        hits = need(f"plans/*{slug}*.md", "plan")
        if hits and not re.search(r"test (written|to write) first|red condition|first test", open(hits[-1]).read(), re.I):
            probs.append(Problem("plan does not name the test written first per slice",
                                 "the plan does not name the test written first for each slice"))
    elif n == 4:
        hits = need(f"plans/*{slug}*.md", "plan")
        if hits:
            text = open(hits[-1]).read()
            if "brief verification" not in text.lower():
                probs.append(Problem("plan has no '## Brief verification' section", "the plan has no brief verification"))
            if not re.search(r"\b(CONFIRMED|PARTIALLY CONFIRMED|UNVERIFIABLE)\b", text):
                probs.append(Problem("no verdicts (CONFIRMED / PARTIALLY CONFIRMED / NOT CONFIRMED) in the plan",
                                     "the plan gives no verdicts"))
            if re.search(r"\bNOT CONFIRMED\b", text) and not forced_by_hand(p):
                probs.append(Problem("a NOT CONFIRMED verdict is present: escalate, then close with --force \"<the user's decision>\"",
                                     "a verdict is NOT CONFIRMED, and waits on your decision"))
    elif n == 5:
        hits = need(f"measurements/*{slug}*report*.md", "task reports", "there are no task reports")
        bad = [os.path.basename(h) for h in hits if not _has_red_output(open(h).read())]
        if bad:
            probs.append(Problem("task reports without pasted red output in a fenced block: " + ", ".join(bad),
                                 "a task report shows no failing run: " + ", ".join(bad)))
    elif n == 6:
        hits = need(f"measurements/*{slug}*residuals*.md", "residuals doc")
        if hits and not any(_has_red_output(open(h).read()) for h in hits):
            probs.append(Problem("residuals doc has no pasted red output; a gate that produced no red did not run",
                                 "the residuals doc shows no failing run"))
    elif n == 7:
        hits = need(f"measurements/*{slug}*evidence*.md", "evidence doc")
        if hits:
            text = open(hits[-1]).read()
            if not (re.search(r"\.(png|jpe?g|webp|gif)\b", text, re.I) or "no runnable surface" in text.lower()):
                probs.append(Problem("evidence doc references no screenshot/image and does not declare 'no runnable surface'",
                                     "the evidence doc has no screenshot, and does not say there is nothing to run"))
            # ALYDAS-553. The browser suite's totals, on a line that names the
            # suite, or the words saying the project has none: the
            # instruction alone was skipped.
            suite = browser_suite_problem(text)
            if suite is not None:
                probs.append(Problem(suite, "the evidence doc's browser suite totals do not show it ran green, "
                                            "or are missing and it does not say there is none"))
    elif n == 8:
        hits = need(f"measurements/*{slug}*review*.md", "review doc")
        if hits and not re.search(r"\b(blocks|fix-now|defer)\b", open(hits[-1]).read(), re.I):
            probs.append(Problem("review doc rules nothing blocks / fix-now / defer",
                                 "the review doc rules nothing as blocking, fix now or deferred"))
        # ALYDAS-174, S1. Beside the review doc's ruling word, not in its
        # place: the review recorded on the ledger, well formed, which is what
        # the desk draws Gate 8 from.
        probs.extend(review_close_problems(run))
        # ALYDAS-223, S1. After the review doc: a run that changed a rendered
        # surface needs an approving UI gate report, and one that did not says so.
        probs.extend(ui_gate_problems(run))
    elif n == 9:
        need(f"handoffs/*{slug}*resume*.md", "resume doc")
    return probs

# ---------- status block ----------

def status_block(run, n):
    p = phase_of(run, n)
    label = "Gate" if p["kind"] == "gate" else "Phase"
    state = {"done": "PASSED" if p["kind"] == "gate" else "DONE", "active": "RUNNING", "failed": "FAILED",
             "skipped": "SKIPPED", "blocked": "BLOCKED", "pending": "PENDING"}[p["status"]]
    if p.get("forced"):
        state += " (forced)"
    t = p.get("telemetry") or {}
    tok = t.get("tokens") or {}
    took = "–"
    if t:
        parts = [fmt_dur(t.get("wall_s")) + (f" ({fmt_dur(t['active_s'])} active)" if t.get("active_s") else "")]
        if tok:
            parts.append(f"{fmt_k(tok.get('output'))} out / {fmt_k(tok.get('cache_read', 0) + tok.get('input', 0) + tok.get('cache_write', 0))} in")
        if t.get("cost_known"):
            parts.append("~" + fmt_usd(t.get("cost_usd")))
        parts.append(f"{t.get('agents', 0)} agent" + ("s" if t.get("agents", 0) != 1 else ""))
        took = " · ".join(parts)
    lines = []
    head = f"{label} {n} · {p['name']} — {state}"
    lines.append(head)
    lines.append("─" * max(44, len(head)))
    rows = [("Took", took)]
    for e in p["produced"]:
        rows.append(("Wrote", e["path"] + (f"  ({e['note']})" if e["note"] else "")))
    rows.append(("Board", board_line(run)))
    if p["summary"]:
        rows.append(("Result", p["summary"]))
    if p["status"] == "skipped" and p["skip_reason"]:
        rows.append(("Why", p["skip_reason"]))
    for w in p["wrong"][:2]:
        rows.append(("Wrong", w))
    for o in p["overruled"][:1]:
        rows.append(("Overruled", o))
    if p["next"]:
        rows.append(("Next", p["next"]))
    rows.append(("Resume", f"/feature-pipeline resume {run['slug']}"))
    clear = "yes" if p["status"] in ("done", "skipped") and n != 0 else ("no, phase still open" if p["status"] == "active" else "after the doc is committed")
    rows.append(("Clear", clear))
    for k, v in rows:
        lines.append(f"{k:<9} {v}")
    return "\n".join(lines)

# ---------- render ----------

def render(run):
    if not os.path.exists(TEMPLATE):
        die(f"template missing: {TEMPLATE}")
    tpl = open(TEMPLATE).read()
    # ALYDAS-173, S2. A phase not yet started has no purpose in the ledger; the
    # board draws the skill's own, as the beat sends it. The ledger is not changed.
    shown = dict(run, phases=[p if said(p.get("purpose")) or p.get("n") not in PHASE_PURPOSE
                              else dict(p, purpose=PHASE_PURPOSE[p["n"]]) for p in run.get("phases") or []])
    payload = json.dumps(shown, ensure_ascii=False).replace("</", "<\\/")
    if "/*__RUN__*/" not in tpl:
        die("template has no /*__RUN__*/ marker")
    return tpl.replace("/*__RUN__*/", payload).replace("__TITLE__", html_escape(run["title"]))

def html_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))

# ---------- the desk heartbeat ----------
#
# The board is a file on this laptop. The desk is a page with a Now strip on it,
# so at every boundary the ledger already stamps, one POST says what this run is
# doing and a person can see it from a phone without the terminal.
#
# Three rules, and they are why this section is as dull as it is. It is a no-op
# unless there is a desk and a token (`desk_credentials`), so a machine that has
# never heard of the desk behaves exactly as it did before. (ALYDAS-379, S5: and
# a no-op on a desk without runs, `desk_target`.) No exception escapes and the
# exit code is never touched, because the ledger is the thing the run cannot
# lose and a desk that is down must not be able to stop a phase closing. And it
# reads the run rather than writing it: nothing here changes a board file.

URL_VARIABLE = "ALYDASH_URL"
TOKEN_VARIABLE = "ALYDASH_SESSION_TOKEN"
CREDENTIALS_VARIABLE = "ALYDASH_CREDENTIALS"
# ALYDAS-245. The two ways a run comes to have a desk, said the same way by
# `hold` and `wait` when it has neither. "Can use": an entry for another
# repository, an expired one, or one for a desk other than an exported
# ALYDASH_URL is no credential to this run.
DESK_WAYS = (f"{URL_VARIABLE} and {TOKEN_VARIABLE}, or a credential in the alydash plugin's store "
             f"that this run can use")
WORK_ITEM_VARIABLE = "ALYDASH_WORK_ITEM"
# ALYDAS-127, S8. A session a runner started from the desk is told its start
# and its budget. `beat_start` and `beat_budget` read them.
RUN_START_VARIABLE = "ALYDASH_RUN_START"
RUN_BUDGET_VARIABLE = "ALYDASH_RUN_BUDGET"
HEARTBEAT_PATH = "/api/sessions/heartbeat"
# ALYDAS-379, S5. The scope a desk without runs does not grant, the plugin's
# HEARTBEAT_SCOPE (`lib/credentials.mjs`).
HEARTBEAT_SCOPE = "heartbeat:AgentSession"
HEARTBEAT_TIMEOUT_S = 2
AGENT_NAME = "feature-pipeline"

# The desk's own table, HEARTBEAT_BOUNDS in packages/modules/runs/src/heartbeat-body.ts,
# counted in code points after trimming. Kept on this side too so a long branch
# name is cut here rather than earning a 400 nobody is looking at.
HEARTBEAT_BOUNDS = {"run": 200, "agent": 80, "statusLine": 200, "phase": 80,
                    "branch": 200, "boardUrl": 2000, "title": 200, "goal": 1000,
                    "inSession": 128}

# ALYDAS-264. The Claude Code session a beat is sent from, which Claude Code
# puts in this variable for every command a session runs. The alydash plugin's
# hook reports the same session as a run of its own, under the same token, and
# the desk treats the two as one run when this id is that run's: one card on
# Now, one Stop, one slot of the run cap. Sent on every beat. Outside a
# session, or with an id that is not the desk's shape for one (`SESSION_KEY`
# in packages/modules/runs/src/heartbeat-body.ts), it is sent blank, which tells the desk
# this run is no session's now. A desk from before ALYDAS-264 ignores the key.
SESSION_VARIABLE = "CLAUDE_CODE_SESSION_ID"
SESSION_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}")

def beat_session():
    """The session id this process runs in, or "" when it runs in none."""
    session = (os.environ.get(SESSION_VARIABLE) or "").strip()
    return session if SESSION_KEY.fullmatch(session) else ""

# What the status line calls a boundary, and what it calls a phase that is
# merely sitting there when something other than a boundary reports.
EVENT_WORD = {"start": "running", "done": "done", "failed": "failed",
              "skipped": "skipped", "blocked": "blocked"}
STATUS_WORD = {"active": "running", "done": "done", "failed": "failed",
               "skipped": "skipped", "blocked": "blocked", "pending": "pending"}

# The desk's `BIDI_CONTROL` (packages/core/src/text.ts): embeddings, overrides and
# isolates, which draw a line other than it is stored.
BIDI_CONTROL = re.compile("[\u202a-\u202e\u2066-\u2069]")

def plain(value, field):
    """Trimmed, free of control characters, cut to the desk's bound for that field.

    The desk refuses a control character in any string, and the middle dot in a
    status line is not one. Cutting is by code point, which is how the bound is
    counted at the other end.
    """
    text = "" if value is None else str(value)
    text = "".join(" " if ord(c) < 32 or ord(c) == 127 else c for c in text)
    # ALYDAS-173 Gate 8, S-1 (Sachit: strip in the pipeline). None leaves on a
    # beat, so "gpj.hs" cannot be drawn as "sh.jpg". A zero-width joiner stays,
    # since emoji and Persian need it.
    text = BIDI_CONTROL.sub("", text)
    # Gate 6. Trimmed of what JavaScript's `trim` takes too: Python's `strip`
    # keeps U+FEFF, which the desk trims before it counts or asks whether a
    # required string is empty.
    text = re.sub(r"^[\s\ufeff]+|[\s\ufeff]+$", "", text)
    bound = HEARTBEAT_BOUNDS.get(field)
    return text[:bound] if bound else text

def beat_cost(run):
    """S3. (cost, exact) for the beat: Claude Code's own session total and True
    when it covers the run (`session_cost_exact`), else the computed figure and
    False, which the desk reads as "at least". (None, None) when unknown."""
    t = run.get("telemetry") or {}
    total = t.get("session_cost_usd")
    if t.get("session_cost_exact") and isinstance(total, (int, float)) and not isinstance(total, bool):
        return max(0.0, round(float(total), 4)), True
    cost = t.get("cost_usd")
    if t.get("cost_known") is False or not isinstance(cost, (int, float)) or isinstance(cost, bool):
        return None, None
    return max(0.0, round(float(cost), 4)), False

def beat_totals(run):
    """Tokens and cost for the whole run, off the same telemetry `usage` prints.

    `tokens` is one number on a card, so it is every token the run has spent:
    input, both cache halves and output, which is the two halves the status
    block prints as "out / in" added together. Cost is the priced total, or None
    when it is unknown (S4): the desk prints 0.0 as "$0.00", which was a false
    figure for every live run, so nothing is sent rather than a zero.
    """
    t = run.get("telemetry") or {}
    tokens = 0
    for v in (t.get("tokens") or {}).values():
        if isinstance(v, (int, float)):
            tokens += v
    return max(0, int(round(tokens))), beat_cost(run)[0]

# The work item a run names by itself. With ALYDASH_WORK_ITEM unset, a key in
# the run's slug, then one in its branch, names the item: `joone-2-confirm` is
# JOONE-2, and so is a branch `feature/joone-2-confirm`. The rule is the
# plugin's for a session (`branchItems` in its lib/session.mjs): an app's
# prefix of two to six letters in either case, a dash and the item's number
# with no leading zero, standing clear of letters and digits on both sides, so
# `release-2026` and `v2-10` name none, and at most three keys are ever tried.
# A key is a guess. The desk refuses a beat that names an item it cannot link,
# so a guess it refuses is kept in the ledger (`item_guess_refused`) and never
# sent again, and the beat goes again without it (`heartbeat`). The variable
# is a person's word and not a guess: it is sent as it is, refused or not.
ITEM_KEY = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,6})-([1-9]\d{0,14})(?![A-Za-z0-9])")
ITEM_KEYS_MAX = 3

def named_items(run):
    """The keys the run's slug and then its branch carry, each once, three at most."""
    keys = []
    for text in (run.get("slug"), run.get("branch")):
        for found in ITEM_KEY.finditer(text if isinstance(text, str) else ""):
            key = f"{found.group(1).upper()}-{found.group(2)}"
            if key not in keys:
                keys.append(key)
    return keys[:ITEM_KEYS_MAX]

def beat_item(run):
    """`(item, guessed)`: the work item a beat names, and whether it is a guess.

    The variable when it is set; else the first key the run names that the desk
    has not refused; else `(None, False)`."""
    stated = (os.environ.get(WORK_ITEM_VARIABLE) or "").strip()
    if stated:
        return stated, False
    refused = run.get("item_guess_refused")
    refused = refused if isinstance(refused, list) else []
    for key in named_items(run):
        if key not in refused:
            return key, True
    return None, False

def forget_item_guess(run, path, key):
    """The desk refused a beat naming `key`: keep that, in memory and in the ledger."""
    refused = run.get("item_guess_refused")
    refused = list(refused) if isinstance(refused, list) else []
    if key not in refused:
        refused.append(key)
    run["item_guess_refused"] = refused
    if path:
        try:
            stored = load(path)
            stored["item_guess_refused"] = refused
            save(path, stored)
        except Exception as e:  # never a failed phase over a link
            print(f"pipeline: the refused work item was not kept: {printable(e)}", file=sys.stderr)

# ALYDAS-303, S9 (c8). The kind of subject a beat names its work item as:
# `WORK_ITEM` in packages/modules/runs/src/integrations/work.ts, which
# `test_the_subject_kind_is_the_kind_the_desk_links` pins.
SUBJECT_KIND = "work_item"

def beat_subject(item):
    """The beat's `subject` for a work item's key."""
    return {"kind": SUBJECT_KIND, "key": plain(item, None)}

def beat_start():
    """ALYDAS-127, S8. The desk's start this session was started for, or None.

    Sent on every beat while ALYDASH_RUN_START is set: the desk joins the run
    to its start only on the beat that inserts the run, as it reads
    `budgetUsd` only there, and this side keeps no record of which beat that
    is."""
    return (os.environ.get(RUN_START_VARIABLE) or "").strip() or None

def beat_budget(run):
    """The run's budget for a beat: the ledger's, else ALYDAS-127 S8's
    ALYDASH_RUN_BUDGET when it is a budget `init --budget` would take, else
    None. A malformed value is no budget, said nowhere: the run reports as it
    would with none."""
    budget = run_budget(run)
    if budget is not None:
        return budget
    stated = (os.environ.get(RUN_BUDGET_VARIABLE) or "").strip()
    if not stated:
        return None
    try:
        return parse_budget(stated)
    except ValueError:
        return None

def beat_body(run, n, event=None, ended=False):
    p = None
    for candidate in run.get("phases") or []:
        if candidate.get("n") == n:
            p = candidate
    p = p or {}
    label = "Gate" if p.get("kind") == "gate" else "Phase"
    name = p.get("name") or ""
    word = EVENT_WORD.get(event) or STATUS_WORD.get(p.get("status") or "", "running")
    tokens, cost = beat_totals(run)
    body = {
        "run": plain(run.get("slug"), "run"),
        "agent": AGENT_NAME,
        "statusLine": plain(f"{label} {n} · {name} · {word}", "statusLine"),
        "phase": plain(f"{n} · {name}", "phase"),
        "branch": plain(run.get("branch"), "branch"),
        "tokens": tokens,
        "costUsd": cost,
        "ended": bool(ended),
    }
    # ALYDAS-154, S2. The run's title and goal, off the ledger, cut to the
    # desk's bounds. Absent when blank: the desk keeps what the run has.
    for key in ("title", "goal"):
        text = plain(run.get(key), key)
        if text:
            body[key] = text
    # Absent rather than null when nobody said one, which is the shape the desk
    # asks for: an item is named by the key a person says out loud, ALY-7.
    # ALYDAS-303, S9 (c8): as the beat's subject, of the kind runs' Work
    # integration links, in place of `workItem`.
    item = beat_item(run)[0]
    if item:
        body["subject"] = beat_subject(item)
    # ALYDAS-264. Always present, blank outside a session (`beat_session`).
    body["inSession"] = beat_session()
    # S3. Whether `costUsd` is Claude Code's own bill or a floor computed from
    # the transcripts. Only beside a cost: an unknown one sends neither.
    exact = beat_cost(run)[1]
    if cost is not None and exact is not None:
        body["costExact"] = exact
    body["ledger"] = beat_ledger(run)
    # ALYDAS-175, S5. The phases that wait for a person, so the page can say so.
    body["holds"] = run_holds(run)
    # ALYDAS-303, S9 (c6). And the phases the run has, which they are within.
    body["phaseRange"] = run_phase_range(run)
    # ALYDAS-17, S5. The run's budget, absent rather than null when none was
    # stated, like `subject`; a ledger value that is not one is not sent.
    # ALYDAS-127, S8: else the runner's, when the session was started from the desk.
    budget = beat_budget(run)
    if budget is not None:
        body["budgetUsd"] = budget
    # ALYDAS-127, S8. The desk's start, which joins this run to the press that
    # started it. Absent rather than null when the session was not started there.
    start = beat_start()
    if start:
        body["start"] = start
    # S6. The commit this beat describes, which the desk reads produced files
    # at. Absent outside a repository, never empty. S8: and absent unless
    # origin has it, so GitHub can serve a file at it.
    commit = commit_on_github()
    if commit:
        body["commit"] = commit
    return body

# ALYDAS-135. The desk's `LEDGER_BOUNDS` in packages/modules/runs/src/ledger-bounds.ts,
# number for number (`test_the_ledger_bounds_table_is_the_desks_ledger_bounds_table`
# pins the two wherever the desk has one). One phase over any bound is a 400
# for the whole beat, so every string and every list is cut here, the way
# `plain` cuts a branch. Strings are counted in code points after trimming,
# lists by their length, and a cut list keeps its first entries.
LEDGER_BOUNDS = {
    "phases": 20, "n": 99,
    "name": 80, "summary": 1_000, "next": 1_000, "skipReason": 1_000, "forced": 1_000,
    "plain": 300,
    # ALYDAS-173, S2. A phase's story: what it is for, where the story is,
    # Frame's backstory, and Frame's two lists, each entry `storyEntry` long.
    "purpose": 600, "scene": 1_100, "backstory": 2_000,
    "fixedMeans": 12, "wontChange": 12, "storyEntry": 500,
    # ALYDAS-173, S3. The Story view's packets: the lede, and five lists whose
    # longer texts are each `storyEntry` long; a number's figure and an option's
    # name are short. The run's terms: how many, a code, and its plain name.
    "lede": 1_000, "numbers": 12, "figure": 80, "options": 12, "optionName": 200,
    "assumed": 12, "leftOut": 12, "problems": 12,
    "terms": 80, "termCode": 40, "termPlain": 200,
    # ALYDAS-173, S4. A phase's steps: how many, a step's text and the line under it.
    "steps": 30, "stepText": 200, "stepSub": 300,
    "decided": 30, "measured": 30, "wrong": 30, "overruled": 30, "entry": 1_000,
    "produced": 30, "path": 500, "note": 500,
    # ALYDAS-173, S5. A produced file named as a document: its title, the line
    # on what is in it, and the short summary the desk's drawer opens with.
    "title": 120, "about": 300, "inShort": 600,
    # ALYDAS-173, S6. The run's short version: what happened (lines, and how
    # many), where it stands, and what is waiting on Sachit (what, and why);
    # and the run log: how many entries, and each one's kind and text.
    # ALYDAS-175, Gate 8 (C-2): logText and scene hold a hold's whole answer,
    # "Decision: Answered on the desk: <a 200 label>. Note: <a 1,000 note>"
    # and "Sent back: <a 1,000 note>", so neither is cut on the desk.
    "happened": 8, "happenedLine": 300, "stands": 300, "needsYou": 8, "needWhat": 200, "needWhy": 500,
    "log": 200, "logKind": 20, "logText": 1_300,
    "columns": 12, "column": 80, "rows": 50, "cell": 500,
    "wallS": 2_147_483_647, "activeS": 2_147_483_647, "agents": 2_147_483_647,
    "inputTokens": 2_147_483_647, "cacheWriteTokens": 2_147_483_647,
    "cacheReadTokens": 2_147_483_647, "outputTokens": 2_147_483_647,
    "costUsd": 99_999_999,
    "deferred": 50, "item": 1_000, "needs": 500,
    # ALYDAS-174, S1. Gate 8's review record on Phase 8 (`review record`):
    # its areas (a name, a line on what it is, its path patterns, and the
    # files the script filed in it), its findings (an id, a title, three
    # paragraphs of what it means, why it matters and how it was reproduced,
    # who found it, and a file), the proofs (the criterion by number, its
    # text and how it was proven), the tests (the command and an excerpt of
    # what it printed) and the verdict. `lines` bounds every count of lines
    # and a finding's line number, as the integer columns are bounded. The
    # record is refused whole past a bound, and past `REVIEW_MOST_BYTES`.
    # Gate 8 ruling (Ask fc55dcae): `areaFiles` is REVIEW_AREA_FILES, what an
    # area lists before the rest fold into its `more` (300 before the ruling).
    "areas": 20, "areaName": 80, "areaAbout": 300, "patterns": 20, "pattern": 200,
    "areaFiles": REVIEW_AREA_FILES, "filePath": 500, "lines": 2_147_483_647,
    "findings": 50, "findingId": 40, "findingTitle": 200, "findingText": 1_000, "findingBy": 120,
    "proofs": 30, "proofText": 1_000, "criterion": 999,
    "testCommand": 500, "testExcerpt": 2_000, "verdict": 300,
}

# ALYDAS-135, S3 (Sachit, 2026-09-26). The desk refuses a heartbeat body over
# 256 KB (`HEARTBEAT_BODY_MAX` in apps/desk/lib/request-bounds.ts), and a
# refused beat takes the run off the Now card and breaks Stop. The bounds
# above allow about 9 MB of ledger, so cutting to them is not enough: the
# serialized ledger is trimmed to this fixed budget before it is sent, which
# leaves 64 KB for the rest of the body. The largest real ledger measured is
# 37.9 KB, so a real run is never trimmed. `test_heartbeat` pins the two.
LEDGER_BUDGET_BYTES = 192 * 1024
# How short the first pass cuts a text before it starts dropping table rows.
TRIM_TEXT_FLOOR = 200

# The ledger's telemetry keys (compute_usage) and the four token kinds, by the
# name each has on the wire.
TOKEN_KINDS = {"inputTokens": "input", "cacheWriteTokens": "cache_write",
               "cacheReadTokens": "cache_read", "outputTokens": "output"}

def said(text):
    """Whether the desk reads `text` as saying something once it has trimmed it."""
    return isinstance(text, str) and not all(c.isspace() or c == "\ufeff" for c in text)

def ledger_text(value, field):
    text = plain(value, None)
    return text[:LEDGER_BOUNDS[field]]

def ledger_list(values, field):
    return list(values or [])[:LEDGER_BOUNDS[field]]

def ledger_count(value, field):
    """A whole number within the desk's bound, or None when the ledger has none."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, min(int(round(value)), LEDGER_BOUNDS[field]))

def ledger_stamp(value):
    """The ledger's ISO stamp (`now_iso`), or None for the empty string it keeps
    for "not yet". The desk bounds a time by its shape, not a length."""
    return plain(value, None) or None

def ledger_table(table):
    if not isinstance(table, dict):
        return None
    width = LEDGER_BOUNDS["columns"]
    return {
        "columns": [ledger_text(c, "column") for c in ledger_list(table.get("columns"), "columns")],
        "rows": [[ledger_text(c, "cell") for c in list(row or [])[:width]]
                 for row in ledger_list(table.get("rows"), "rows")],
    }

def ledger_telemetry(t):
    """ALYDAS-135, S2. A phase's telemetry by the wire's names.

    A phase `usage` has not reached has none, so every number is None. The
    cost is None whenever `cost_known` is false: compute_usage writes 0.0 for a
    phase it could not price, and the desk draws 0 as "$0.00", which reads as
    free rather than unknown. How the cost is worked out is S4's, not this.
    """
    t = t or {}
    tokens = t.get("tokens") or {}
    out = {
        "wallS": ledger_count(t.get("wall_s"), "wallS"),
        "activeS": ledger_count(t.get("active_s"), "activeS"),
        "agents": ledger_count(t.get("agents"), "agents"),
    }
    for wire, kind in TOKEN_KINDS.items():
        out[wire] = ledger_count(tokens.get(kind), wire)
    cost = t.get("cost_usd")
    known = t.get("cost_known") and isinstance(cost, (int, float)) and not isinstance(cost, bool)
    out["costUsd"] = min(max(0.0, round(float(cost), 4)), LEDGER_BOUNDS["costUsd"]) if known else None
    return out

def ledger_phase_number(n):
    """`n` when the desk takes it as a phase's number (a whole number from 0 to
    LEDGER_BOUNDS["n"], and not a bool, which JSON would send as true), else None."""
    if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= LEDGER_BOUNDS["n"]:
        return None
    return n

def ledger_story(p):
    """ALYDAS-173, S2. A phase's story as the desk takes it: the purpose (the one
    stamped at start, else the skill's own for a phase not yet started), the
    scene, and Frame's backstory and two lists, each cut to its bound, an empty
    key left out, and None when nothing is left."""
    n = p.get("n")
    purpose = p.get("purpose")
    if not said(purpose):
        purpose = PHASE_PURPOSE.get(n) if isinstance(n, int) and not isinstance(n, bool) else None
    story = {}
    for wire, value in (("purpose", purpose), ("scene", p.get("scene")), ("backstory", p.get("backstory"))):
        text = ledger_text(value, wire)
        if said(text):
            story[wire] = text
    for wire, key in (("fixedMeans", "fixed_means"), ("wontChange", "wont_change"), ("leftOut", "left_out")):
        entries = [e for e in (ledger_text(x, "storyEntry") for x in ledger_list(p.get(key), wire)) if said(e)]
        if entries:
            story[wire] = entries
    # ALYDAS-173, S3. The lede, and the packets whose entries are objects: each
    # half cut to its bound, an entry whose first half says nothing left out
    # (the desk refuses it), and an optional half that says nothing left out.
    lede = ledger_text(p.get("lede"), "lede")
    if said(lede):
        story["lede"] = lede
    for wire, key in (("numbers", "numbers"), ("options", "options"), ("assumed", "assumed"), ("problems", "problems"),
                      ("steps", "steps")):
        entries = [e for e in (story_entry(wire, x) for x in ledger_list(p.get(key), wire)) if e is not None]
        if entries:
            story[wire] = entries
    return story or None

# ALYDAS-173, S3. Each object packet's halves: the one it cannot leave out,
# then the optional one, with the bound each is cut to.
STORY_ENTRY_FIELDS = {
    "numbers": (("figure", "figure"), ("means", "storyEntry")),
    "options": (("name", "optionName"), ("why", "storyEntry")),
    "assumed": (("text", "storyEntry"), ("later", "storyEntry")),
    "problems": (("what", "storyEntry"), ("did", "storyEntry")),
    # ALYDAS-173, S4. A step's text, then the line under it; its state rides beside.
    "steps": (("text", "stepText"), ("sub", "stepSub")),
}

def story_entry(packet, entry, cut=None):
    """One entry of an object packet as the desk takes it, or None when its
    first half says nothing. `cut` shortens each text instead of the bound (the
    trim's); an option keeps whether it was chosen, as true or false."""
    if not isinstance(entry, dict):
        return None
    (need, need_bound), (may, may_bound) = STORY_ENTRY_FIELDS[packet]
    fit = cut or (lambda text, bound: ledger_text(text, bound))
    first = fit(entry.get(need), need_bound)
    if not said(first):
        return None
    out = {need: first}
    if packet == "options":
        out["chosen"] = entry.get("chosen") is True
    # ALYDAS-173, S4. A state the desk does not know is sent as todo: the desk
    # refuses the whole beat for a word outside its list. ALYDAS-225, K2:
    # dropped is on the list, here and on the desk, and goes as it is.
    if packet == "steps":
        out["state"] = step_state(entry)
    second = fit(entry.get(may), may_bound)
    if said(second):
        out[may] = second
    return out

def ledger_terms(run):
    """ALYDAS-173, S3. The run's terms as the desk takes them: each code and
    plain name cut to its bound, a term with either blank left out, and the
    first `terms` of those kept."""
    out = {}
    for code, name in (run.get("terms") or {}).items() if isinstance(run.get("terms"), dict) else []:
        code, name = ledger_text(code, "termCode"), ledger_text(name, "termPlain")
        if said(code) and said(name) and code not in out:
            out[code] = name
        if len(out) == LEDGER_BOUNDS["terms"]:
            break
    return out

# ALYDAS-173, S5. A produced file's document fields: the ledger's key, and the
# wire's, which is also its bound's.
DOC_FIELDS = (("title", "title"), ("about", "about"), ("in_short", "inShort"))

def ledger_produced(entry):
    """One produced file as the desk takes it: its path and note, and each
    document field cut to its bound and sent only when it says something."""
    out = {"path": ledger_text(entry.get("path"), "path"), "note": ledger_text(entry.get("note"), "note")}
    for key, wire in DOC_FIELDS:
        text = ledger_text(entry.get(key), wire)
        if said(text):
            out[wire] = text
    return out

def beat_ledger(run):
    """ALYDAS-135. Every phase of the ledger, in `n` order, and the run's deferrals.

    The whole ledger rather than the phase that moved, because the ledger is
    edited backwards (a note on an earlier phase, a forced close, a resume) and
    the desk replaces what it holds with each beat's snapshot. The status is the
    status line's word, so a phase that is `active` here is `running` there.
    S1 sent n, name, status and summary; S2 sends every field the board keeps.
    """
    # Gate 6. The desk refuses the whole beat for more than LEDGER_BOUNDS
    # phases, a number that is not whole or not 0 to 99, or one number twice,
    # so those are fitted here like every other bound: a phase with no number
    # the desk takes is left out, a repeat keeps the first phase to carry the
    # number, and the first `phases` by number are sent.
    numbered, seen = [], set()
    for p in sorted((p for p in run.get("phases") or [] if ledger_phase_number(p.get("n")) is not None),
                    key=lambda p: p["n"]):
        if p["n"] not in seen:
            seen.add(p["n"])
            numbered.append(p)
    sent = []
    for p in numbered[:LEDGER_BOUNDS["phases"]]:
        phase = {
            "n": p.get("n"),
            # Required, so never empty: a board with a blank name sends an ellipsis.
            "name": ledger_text(p.get("name"), "name") or "\u2026",
            "status": STATUS_WORD.get(p.get("status") or "", "pending"),
            # ALYDAS-154, S2. Phase or gate; anything else is sent as a phase,
            # because the desk refuses the whole beat for a third word.
            "kind": "gate" if p.get("kind") == "gate" else "phase",
            "summary": ledger_text(p.get("summary"), "summary"),
            "next": ledger_text(p.get("next"), "next"),
            # ALYDAS-173, S1. Blank for a ledger written before the field.
            "plain": ledger_text(p.get("plain"), "plain"),
            "story": ledger_story(p),
        }
        for key in ("decided", "measured", "wrong", "overruled"):
            phase[key] = [e for e in (ledger_text(x, "entry") for x in ledger_list(p.get(key), key)) if said(e)]
        # A produced entry with no path, or a deferral with no item, says
        # nothing the desk can draw and is refused there, so it is left out.
        phase["produced"] = [
            ledger_produced(x) for x in (p.get("produced") or []) if isinstance(x, dict) and plain(x.get("path"), None)
        ][:LEDGER_BOUNDS["produced"]]
        phase["table"] = ledger_table(p.get("table"))
        # ALYDAS-174, S1. Gate 8's review record, as `review record` wrote it
        # (already within every bound), and only when it still is: a record
        # spoiled on the ledger by hand is left off rather than sent to be
        # refused, since the desk refuses the whole beat for one bad field.
        review = ledger_review(p.get("review"))
        if review is not None:
            phase["review"] = review
        phase["skipReason"] = ledger_text(p.get("skip_reason"), "skipReason")
        phase["forced"] = ledger_text(p.get("forced"), "forced")
        phase["startedAt"] = ledger_stamp(p.get("started"))
        phase["endedAt"] = ledger_stamp(p.get("ended"))
        phase.update(ledger_telemetry(p.get("telemetry")))
        sent.append(phase)
    deferred = [
        {"item": ledger_text(d.get("item"), "item"), "needs": ledger_text(d.get("needs"), "needs")}
        for d in (run.get("deferred") or []) if isinstance(d, dict) and plain(d.get("item"), None)
    ][:LEDGER_BOUNDS["deferred"]]
    # ALYDAS-173, S6. The run's short version and its log, beside the terms.
    return {"phases": sent, "deferred": deferred, "terms": ledger_terms(run),
            "story": ledger_run_story(run), "log": ledger_log(run)}

def ledger_run_story(run):
    """ALYDAS-173, S6. The run's short version as the desk takes it: each line
    of what happened, where it stands, and each thing waiting (what, and why
    when said), cut to their bounds, what says nothing left out, and None
    when nothing is left."""
    story = run.get("story") if isinstance(run.get("story"), dict) else {}
    out = {}
    happened = [e for e in (ledger_text(x, "happenedLine") for x in ledger_list(story.get("happened"), "happened")) if said(e)]
    if happened:
        out["happened"] = happened
    stands = ledger_text(story.get("stands"), "stands")
    if said(stands):
        out["stands"] = stands
    needs = []
    for need in ledger_list(story.get("needs_you"), "needsYou"):
        if not isinstance(need, dict):
            continue
        what, why = ledger_text(need.get("what"), "needWhat"), ledger_text(need.get("why"), "needWhy")
        if said(what):
            needs.append({"what": what, "why": why} if said(why) else {"what": what})
    if needs:
        out["needsYou"] = needs
    return out or None

# ALYDAS-173, S6. What each event is called in the desk's log, read by a
# person: a phase's own events name the phase ("Phase 3 · Plan started"), and
# every other kind is its words around the event's note ("Decided: ...",
# "Wrote docs/plan.md: The plan"). None is the note alone, for a kind whose
# note already says it in words (`story`, and a plain `log` line). A kind not
# named here opens with the kind itself, then the note.
LOG_PHASE_WORDS = {"start": "started", "done": "done", "failed": "failed", "skipped": "skipped",
                   "blocked": "is waiting on you", "check": "could not close yet"}
LOG_WORDS = {
    "result": "Summary: {}", "plain": "In a sentence: {}", "scene": "Where the story is: {}",
    "backstory": "Backstory: {}", "fixed-means": '"Fixed" means: {}', "wont-change": "Won't change: {}",
    "lede": "Story: {}", "number": "A number: {}", "option": "Weighed: {}", "assumed": "Assumed: {}",
    "left-out": "Left out: {}", "decided": "Decided: {}", "measured": "Measured: {}", "wrong": "Went wrong: {}",
    "overruled": "Overruled: {}", "wrote": "Wrote {}", "dropped": "Took off {}", "term": "Term: {}", "row": "Table row: {}",
    "deferred": "Left for later: {}", "set": "Set {}", "escalated": "Escalated: {}", "dispatched": "Dispatched: {}",
    "landed": "Landed: {}", "decision": "Decision: {}", "story": None, "log": None,
    "ui-gate": "UI gate {}",
}

def log_phase_label(run, n):
    """`Phase 3 · Plan` or `Gate 4 · Verify the brief`, off the ledger's phase."""
    for p in run.get("phases") or []:
        if p.get("n") == n:
            return f"{'Gate' if p.get('kind') == 'gate' else 'Phase'} {n} · {p.get('name') or ''}".rstrip()
    return f"Phase {n}"

def log_words(run, ev):
    """One event as the desk's log reads it."""
    kind = str(ev.get("event") or "log")
    note = plain(ev.get("note"), None)
    n = ev.get("phase")
    if kind in LOG_PHASE_WORDS and ledger_phase_number(n) is not None:
        if kind == "check" and note.startswith("cannot close: "):
            note = note[len("cannot close: "):]
        text = f"{log_phase_label(run, n)} {LOG_PHASE_WORDS[kind]}" + (f": {note}" if note and kind != "start" else "")
        forced = plain(ev.get("forced"), None) if isinstance(ev.get("forced"), str) else ""
        return text + (f" (closed by hand: {forced})" if forced else "")
    if kind == "step":
        return note[:1].upper() + note[1:]
    words = LOG_WORDS.get(kind, kind[:1].upper() + kind[1:] + ": {}")
    if words is None:
        return note
    return words.format(note) if note else words.replace("{}", "").rstrip(": ").strip()

def log_stamp(value):
    """An event's time as an ISO instant in UTC (`...Z`), or None for one with
    no time or no zone, which the desk would refuse."""
    at = parse_ts(value) if isinstance(value, str) else None
    if at is None or at.tzinfo is None:
        return None
    return at.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def ledger_log(run):
    """ALYDAS-173, S6. The run log for the desk: the newest `log` of the events
    and the agents the transcripts saw start and land, oldest first. Each is
    its time, its phase (None when it names none the desk takes), its kind
    and its words, the kind and words cut to their bounds by code point. An
    event with no time the desk takes, or with nothing to say, is left out."""
    entries = []
    def add(at, n, kind, text):
        stamp = log_stamp(at)
        kind, text = ledger_text(kind, "logKind"), ledger_text(text, "logText")
        if stamp is None or not said(text):
            return
        entries.append({"at": stamp, "phase": ledger_phase_number(n), "kind": kind if said(kind) else "log", "text": text})
    for ev in run.get("events") or []:
        if isinstance(ev, dict):
            add(ev.get("ts"), ev.get("phase"), ev.get("event"), log_words(run, ev))
    for row in (run.get("telemetry") or {}).get("agent_rows") or []:
        if isinstance(row, dict):
            what = plain(row.get("description"), None) or "an agent"
            add(row.get("first"), row.get("phase"), "dispatched", f"Agent dispatched: {what}")
            add(row.get("last"), row.get("phase"), "landed", f"Agent landed: {what}")
    # Stable, so events stamped alike keep the order they were written in.
    entries.sort(key=lambda e: e["at"])
    return entries[-LEDGER_BOUNDS["log"]:]

def ledger_bytes(ledger):
    """The ledger's size as the beat sends it: `json.dumps`, as `heartbeat` does."""
    return len(json.dumps(ledger, ensure_ascii=False).encode("utf-8"))

def clip(text, width):
    """`text` cut to `width` code points, the cut marked with an ellipsis."""
    if not isinstance(text, str) or len(text) <= width:
        return text
    return text[:width - 1] + "\u2026" if width > 0 else ""

LIST_KEYS = ("decided", "measured", "wrong", "overruled", "produced")
# A phase's own texts the trim shortens. ALYDAS-173, S1: the plain line with them.
TRIMMED_TEXTS = ("summary", "next", "skipReason", "forced", "plain")
# ALYDAS-173, S2. A story's texts and lists the trim shortens with them.
STORY_TEXTS = ("purpose", "scene", "backstory", "lede")
STORY_LISTS = ("fixedMeans", "wontChange", "leftOut")

def shaped_story(story, width):
    """A phase's story with every text and entry cut to `width`, what is cut to
    nothing left out, and None when nothing is left."""
    if not isinstance(story, dict):
        return None
    out = {}
    for key in STORY_TEXTS:
        text = clip(story.get(key), width)
        if said(text):
            out[key] = text
    for key in STORY_LISTS:
        entries = [c for c in (clip(e, width) for e in story.get(key) or []) if said(c)]
        if entries:
            out[key] = entries
    # ALYDAS-173, S3. The object packets, each half cut to the width (and never
    # past its own bound), an entry whose first half is cut to nothing dropped.
    for key in STORY_ENTRY_FIELDS:
        entries = [e for e in (story_entry(key, x, lambda text, _: clip(text, width)) for x in story.get(key) or [])
                   if e is not None]
        if entries:
            out[key] = entries
    return out or None

def shaped_terms(terms, width):
    """ALYDAS-173, S3. The terms with each plain name cut to `width`; a code is
    not cut, since a code cut is no code, and a term cut to nothing is dropped."""
    out = {}
    for code, name in (terms or {}).items():
        cut = clip(name, width)
        if said(cut):
            out[code] = cut
    return out

def shaped_produced(entry, width):
    """ALYDAS-173, S5. A produced file with its note and document fields cut to
    `width`, a document field cut to nothing left out; the path never cut."""
    out = {"path": entry.get("path"), "note": clip(entry.get("note"), width)}
    for _, wire in DOC_FIELDS:
        text = clip(entry.get(wire), width)
        if said(text):
            out[wire] = text
    return out

def shaped_run_story(story, width):
    """ALYDAS-173, S6. The run's short version with every text cut to `width`,
    a line or a thing waiting cut to nothing left out, and None when nothing is left."""
    if not isinstance(story, dict):
        return None
    out = {}
    happened = [c for c in (clip(e, width) for e in story.get("happened") or []) if said(c)]
    if happened:
        out["happened"] = happened
    stands = clip(story.get("stands"), width)
    if said(stands):
        out["stands"] = stands
    needs = []
    for need in story.get("needsYou") or []:
        what, why = clip(need.get("what"), width), clip(need.get("why"), width)
        if said(what):
            needs.append({"what": what, "why": why} if said(why) else {"what": what})
    if needs:
        out["needsYou"] = needs
    return out or None

def shaped(ledger, width, rows_dropped, entries_dropped):
    """The ledger with every shortenable text cut to `width`, the oldest
    `rows_dropped` table rows gone and the oldest `entries_dropped` list
    entries gone. Oldest is the ledger's own order: phases by `n`, a table's
    rows and a list's entries in the order they were written, the run's
    deferrals after every phase. Never touched: a phase's `n`, `name` and
    `status`, its times and telemetry, and a produced file's path, which
    names a file and is no use cut."""
    rows_left, entries_left = rows_dropped, entries_dropped
    phases = []
    for p in ledger["phases"]:
        q = dict(p)
        for key in TRIMMED_TEXTS:
            q[key] = clip(p.get(key), width)
        if "story" in p:
            q["story"] = shaped_story(p.get("story"), width)
        for key in LIST_KEYS:
            entries = list(p.get(key) or [])
            gone = min(entries_left, len(entries)); entries_left -= gone
            entries = entries[gone:]
            if key == "produced":
                q[key] = [shaped_produced(e, width) for e in entries]
            else:
                # Gate 6. An entry cut to nothing says nothing, so it is not sent.
                q[key] = [c for c in (clip(e, width) for e in entries) if said(c)]
        table = p.get("table")
        if isinstance(table, dict):
            rows = list(table.get("rows") or [])
            gone = min(rows_left, len(rows)); rows_left -= gone
            q["table"] = {"columns": [clip(c, width) for c in table.get("columns") or []],
                          "rows": [[clip(c, width) for c in row] for row in rows[gone:]]}
        phases.append(q)
    deferred = list(ledger.get("deferred") or [])
    gone = min(entries_left, len(deferred))
    # Gate 6. A deferral's item is required, and the desk refuses the whole beat
    # for one it reads as empty, so a deferral cut to nothing is dropped.
    deferred = [{"item": clip(d.get("item"), width), "needs": clip(d.get("needs"), width)} for d in deferred[gone:]]
    deferred = [d for d in deferred if said(d["item"])]
    out = {**ledger, "phases": phases, "deferred": deferred, "terms": shaped_terms(ledger.get("terms"), width)}
    if "story" in ledger:
        out["story"] = shaped_run_story(ledger.get("story"), width)
    return out

def _least(low, high, fits):
    """The least k in [low, high] with fits(k), given fits(high); fits is monotone."""
    while low < high:
        mid = (low + high) // 2
        if fits(mid):
            high = mid
        else:
            low = mid + 1
    return low

def trim_ledger(ledger, budget=None):
    """ALYDAS-135, S3. The ledger cut to fit `budget` bytes, and what was cut.

    Deterministic, in four steps, each taken only if the one before it did
    not fit: (1) the longest texts are shortened, all to one width, the widest
    that fits, down to TRIM_TEXT_FLOOR; (2) the oldest table rows are dropped;
    (3) texts are shortened below the floor; (4) the oldest list entries and
    deferrals are dropped. Each step drops or cuts as little as fits. A ledger
    already inside the budget comes back as it was, and the report is None.
    """
    budget = LEDGER_BUDGET_BYTES if budget is None else budget
    before = ledger_bytes(ledger)
    if before <= budget:
        return ledger, None
    # ALYDAS-173, S6. Before any of the four, the oldest log entries go, as few
    # as fit: the log is the run's diary, and a phase's own words matter more.
    # When the whole log is not enough it all goes and the four steps follow.
    log = list(ledger.get("log") or [])
    dropped_log = 0
    if log:
        without = lambda d: {**ledger, "log": log[d:]}
        if ledger_bytes(without(len(log))) <= budget:
            dropped_log = _least(0, len(log), lambda d: ledger_bytes(without(d)) <= budget)
            out = without(dropped_log)
            return out, {"before": before, "after": ledger_bytes(out), "budget": budget, "width": None,
                         "rows": 0, "entries": 0, "log": dropped_log}
        ledger, dropped_log = without(len(log)), len(log)
    texts = []
    for p in ledger["phases"]:
        texts += [p.get(k) for k in TRIMMED_TEXTS]
        story = p.get("story") or {}
        texts += [story.get(k) for k in STORY_TEXTS] + [e for k in STORY_LISTS for e in (story.get(k) or [])]
        # ALYDAS-173, S3. Both halves of every object packet's entries.
        texts += [e.get(f) for k, fields in STORY_ENTRY_FIELDS.items() for e in (story.get(k) or []) for f, _ in fields]
        texts += [e for k in LIST_KEYS if k != "produced" for e in (p.get(k) or [])]
        texts += [e.get(f) for e in (p.get("produced") or []) for f in ("note",) + tuple(w for _, w in DOC_FIELDS)]
        table = p.get("table") or {}
        texts += list(table.get("columns") or []) + [c for row in table.get("rows") or [] for c in row]
    texts += [t for d in ledger.get("deferred") or [] for t in (d.get("item"), d.get("needs"))]
    texts += list((ledger.get("terms") or {}).values())
    # ALYDAS-173, S6. The run's short version.
    short = ledger.get("story") or {}
    texts += list(short.get("happened") or []) + [short.get("stands")]
    texts += [t for need in short.get("needsYou") or [] for t in (need.get("what"), need.get("why"))]
    widest = max([len(t) for t in texts if isinstance(t, str)] + [0])
    rows = sum(len((p.get("table") or {}).get("rows") or []) for p in ledger["phases"])
    entries = sum(len(p.get(k) or []) for p in ledger["phases"] for k in LIST_KEYS) + len(ledger.get("deferred") or [])
    fits = lambda w, r, e: ledger_bytes(shaped(ledger, w, r, e)) <= budget

    floor = min(TRIM_TEXT_FLOOR, widest)
    width, dropped_rows, dropped_entries = floor, 0, 0
    if fits(floor, 0, 0):
        # The widest width that fits: the least number of characters taken off.
        width = widest - _least(0, widest - floor, lambda cut: fits(widest - cut, 0, 0))
    elif fits(floor, rows, 0):
        dropped_rows = _least(0, rows, lambda r: fits(floor, r, 0))
    else:
        dropped_rows = rows
        if fits(0, rows, 0):
            width = floor - _least(0, floor, lambda cut: fits(floor - cut, rows, 0))
        else:
            width = 0
            dropped_entries = _least(0, entries, lambda e: fits(0, rows, e))
    out = shaped(ledger, width, dropped_rows, dropped_entries)
    return out, {"before": before, "after": ledger_bytes(out), "budget": budget, "width": width,
                 "rows": dropped_rows, "entries": dropped_entries, "log": dropped_log}

def trim_words(report):
    """The run log's line for a trim. ALYDAS-173, S6: the log entries dropped
    first, and "no text cut" when dropping them was enough."""
    head = (f"ledger trimmed for the desk from {report['before']:,} to {report['after']:,} bytes, "
            f"to fit its {report['budget'] // 1024} KB budget: {report.get('log', 0)} oldest log entries dropped")
    if report["width"] is None:
        return head + ", no text cut"
    return (head + f", texts cut to {report['width']} characters, "
            f"{report['rows']} oldest table rows and {report['entries']} oldest list entries dropped")

def token_renderings(token):
    """Every spelling of the token an exception message could carry.

    The raw string, the string as repr escapes it, and the same for the bytes
    the header is built from. A token is whatever the environment holds: the
    read strips the outer whitespace and nothing looks at the middle, so a
    token with a newline in it is a token, and the message that quotes it back
    spells it with a backslash. Longest first, so a shorter rendering cannot
    cut a longer one in half before it is reached.
    """
    if not token:
        return []
    spellings = {
        token,
        repr(token)[1:-1],
        repr(token.encode("utf-8", "replace"))[2:-1],
    }
    return sorted((s for s in spellings if s), key=len, reverse=True)

# G8W3, S2. Where the bearer may go. The token is the run's only acting
# credential, so it is sent over https, or over http to this machine and
# nowhere else: http to a host on the network hands it to anybody on the path.
# The host is compared exactly, so localhost.example.com is not localhost.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")

def under_localhost(host):
    """ALYDAS-381, Gate 8. True when `host` (lowered) ends in `.localhost`
    with something before it. RFC 6761, 6.3 makes every name under localhost
    this machine, so the name alone decides and is never handed to a resolver,
    here or when `desk_request` connects: S3 asked one and then let urllib ask
    again, and a resolver that said loopback the first time and elsewhere the
    second was handed the bearer. `a.localhost.` and `localhost.example.com`
    do not end in `.localhost`, so neither is this machine."""
    return host.endswith(".localhost") and len(host) > len(".localhost")

# Gate 8 (ALYDAS-245), C-4. The address refused is the exported one or a stored
# entry's, and this is not told which, so the sentence names both places a
# person can look rather than blaming the variable for a desk the store named.
DESK_ADDRESS = f"the desk's address ({URL_VARIABLE}, or the desk in the alydash plugin's store)"

def url_refusal(url):
    """None when the token may be sent to this URL, and the sentence when it may not.

    ALYDAS-381: plain http also goes to a `*.localhost` name
    (`under_localhost`), which no resolver is asked about.

    C-4: the sentence quotes the URL's scheme and host, as the plugin's own
    refusal does, and nothing else of it. A user, a password, a path and a
    query can each be a secret that is not the token, which redacting the
    token does not hide; a URL with no scheme or no host is not quoted at all."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return f"{DESK_ADDRESS} is not a URL the token can be sent to"
    scheme = parts.scheme.lower()
    if scheme == "https" and host:
        return None
    if scheme == "http" and (host in LOCAL_HOSTS or under_localhost(host)):
        return None
    return (f"{DESK_ADDRESS} must be https, or http on localhost, *.localhost, 127.0.0.1 or ::1, "
            f"each with no trailing dot, so the token was not sent to {scheme_and_host(url, parts)}")

def scheme_and_host(url, parts):
    """`scheme://host[:port]` of a URL, in the case it was written in, or "it".

    As written, because the callers take the token out of the sentence by its
    spelling, and urlsplit lowers the scheme and the host name. The port only
    when it is digits: a password with a slash in it ends the host early, and
    what follows the colon is then the password's start."""
    written = url.strip()[:len(parts.scheme)]
    scheme = written if written.lower() == parts.scheme else parts.scheme
    host = parts.netloc.rpartition("@")[2]
    name, colon, port = host.rpartition(":")
    if colon and not port.isdigit() and not host.endswith("]"):
        host = name
    return f"{scheme}://{host}" if scheme and host else "it"

class RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """S2. A redirect is not followed. urllib rebuilds a redirected request from
    the original headers, authorization included, so following one hands the
    token to whatever host the Location names. The answer is an error instead,
    which the heartbeat prints as its one line."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, "the desk answered with a redirect, which is not followed", headers, fp)

# build_opener swaps its default redirect handler for the subclass above.
OPENER = urllib.request.build_opener(RefuseRedirect)

# ALYDAS-381, Gate 8. The opener for a *.localhost desk, which `desk_request`
# sends to 127.0.0.1. No proxy: the request is to this machine, and a proxy the
# environment names would be somewhere else. It refuses a redirect too.
THIS_MACHINE_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), RefuseRedirect)
THIS_MACHINE = "127.0.0.1"

# ALYDAS-18, S5. The plugin's credential store. Inside a Claude Code session
# the alydash plugin puts the path of its store in ALYDASH_CREDENTIALS, so a run
# reports without a token exported in a shell profile. The file, written by the
# plugin (mode 600) and only read here, is:
#
#   {"entries": [{"desk": "https://desk.example",        the desk the token is for
#                 "appId": "...", "appName": "...",     the app it was minted for
#                 "repo": "owner/name",                 the repository connect was run in, not the app's
#                 "token": "...",                       the session token, never printed
#                 "expiresAt": "2026-10-29T00:00:00.000Z"}]}
#
# `/alydash:connect` files the credential under the GitHub repository it was
# run in, so the entry taken is the one whose `repo` is this repository's
# GitHub origin, compared without regard to case, and whose `expiresAt` is
# still ahead; with several, the one that expires last. An entry with no
# readable expiry is taken as expired. An exported ALYDASH_SESSION_TOKEN always
# wins, and goes to the exported ALYDASH_URL. A stored token goes only to the
# desk its entry names (Gate 8, D1): with an exported ALYDASH_URL, only an
# entry for that same desk is looked at (Gate 8 of ALYDAS-245, C-2), so an
# entry for another desk is neither used nor in the way. A store that is
# missing or unreadable, or has no entry for this repository, is the same as
# no store: not configured, and nothing is said, the way an unset variable
# says nothing.

# Gate 8, D2. The origin is read by a hand copy of the plugin's rule,
# `normaliseRepo` in plugin/scripts/lib/repo.mjs, which is itself a copy of
# core's `parseGitHubRepo`: a repository the plugin connects is one this finds.
# `http(s)://[user[:pass]@](www.)github.com/owner/name(.git)` with the
# credential part dropped, `git@github.com:owner/name(.git)` and
# `ssh://git@github.com/owner/name(.git)`.
#
# Gate 8 (ALYDAS-245), C-1. Nothing here pins the copy by itself, and it had
# drifted: the plugin and core took an underscore in an owner (an Enterprise
# Managed User's login) and this did not, so such a person connected and never
# reported. test_credentials.py carries the plugin's fixture list with its
# answers, and reads those answers off the plugin again wherever a desk
# checkout is to hand (ALYDASH_REPO); without one the table is all there is.
REPO_PATH = re.compile(r"/([A-Za-z0-9_-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?")
BACKSLASH = "\\"
DOT_SEGMENTS = {".": 1, "%2e": 1, "..": 2, ".%2e": 2, "%2e.": 2, "%2e%2e": 2}

def resolved_path(path):
    """A URL path with its `.` and `..` segments resolved, as a WHATWG URL parser does."""
    out = []
    segments = path.split("/")[1:] if path.startswith("/") else path.split("/")
    for i, segment in enumerate(segments):
        dots = DOT_SEGMENTS.get(segment.lower())
        if dots == 2 and out:
            out.pop()
        if dots:
            if i == len(segments) - 1:
                out.append("")
            continue
        out.append(segment)
    return "/" + "/".join(out)

def repo_from_https(url):
    """Core's rule, as the plugin copies it: `http(s)://(www.)github.com/owner/name`.

    A WHATWG parser reads a backslash as a slash in an http(s) URL, and any
    number of slashes after the scheme, so those are read the same way here."""
    special = re.fullmatch(r"(?is)(https?):[/\\]*([^?#]*)(.*)", url)
    if special:
        url = f"{special.group(1)}://{special.group(2).replace(BACKSLASH, '/')}{special.group(3)}"
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        parts.port  # an unreadable port is no URL, as it is to the plugin
    except ValueError:
        return None
    if parts.scheme.lower() not in ("https", "http") or host not in ("github.com", "www.github.com"):
        return None
    found = REPO_PATH.fullmatch(resolved_path(parts.path or "/"))
    return f"{found.group(1)}/{found.group(2)}" if found else None

def repo_from_ssh_path(path):
    """The path an SSH remote names, read as the same path on https, or None when resolving it changes it."""
    literal = REPO_PATH.fullmatch(path)
    if not literal:
        return None
    read = repo_from_https("https://github.com" + path)
    return read if read == f"{literal.group(1)}/{literal.group(2)}" else None

def normalise_repo(url):
    """`owner/name` for a GitHub origin, or None; never the origin's user or password."""
    given = str(url).strip()
    scp = re.fullmatch(r"git@github\.com:([^/].*)", given)
    if scp:
        return repo_from_ssh_path("/" + scp.group(1))
    if given.startswith("ssh://"):
        # User `git`, no password, host github.com, no port, no query or
        # fragment; an empty password, port, query or fragment is none at all.
        found = re.fullmatch(r"ssh://git:?@github\.com:?(/[^?#]*)?(\?)?(#)?", given)
        if not found:
            return None
        return repo_from_ssh_path(resolved_path(found.group(1) or "/"))
    return repo_from_https(given)

def origin_repo():
    """This repository's GitHub `owner/name`, lower-cased, off `git remote get-url origin`, or None."""
    try:
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        out = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True,
                             timeout=10, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    found = normalise_repo(out.stdout) if out.returncode == 0 else None
    return found.lower() if found else None

def expiry_of(value):
    """The entry's `expiresAt` as an aware instant, or None when it cannot be read."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        when = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)

def sends_heartbeat(entry):
    """ALYDAS-379, S5. Whether a stored entry's desk takes a heartbeat, by the
    plugin's own rule (`sendsHeartbeat`, `lib/credentials.mjs`): a `scopes`
    list of strings without the heartbeat's scope is a desk without runs, and
    no `scopes`, or one that is not a list of strings, beats as every entry did."""
    scopes = entry.get("scopes")
    if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
        return True
    return HEARTBEAT_SCOPE in scopes

def stored_credential(path, pinned=""):
    """(desk, token, beats) of the store's live entry for this repository, or None.

    C-2: with `pinned`, the desk an exported ALYDASH_URL names, only an entry
    for that same desk is looked at, so another desk's entry that expires
    later is not picked over it and then dropped for being the wrong desk.
    ALYDAS-379, S5: `beats` is `sends_heartbeat` of the entry taken."""
    try:
        with open(path, encoding="utf-8") as handle:
            entries = json.load(handle).get("entries")
    except Exception:
        return None
    repo = origin_repo()
    if not repo or not isinstance(entries, list):
        return None
    now = dt.datetime.now(dt.timezone.utc)
    live = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("repo"), str):
            continue
        token = entry.get("token")
        expires = expiry_of(entry.get("expiresAt"))
        desk = entry["desk"].strip() if isinstance(entry.get("desk"), str) else ""
        if pinned and not same_desk(pinned, desk):
            continue
        if (entry["repo"].strip().lower() == repo and isinstance(token, str) and token.strip()
                and expires is not None and expires > now):
            live.append((expires, desk, token.strip(), sends_heartbeat(entry)))
    if not live:
        return None
    return max(live, key=lambda found: found[0])[1:]

def desk_credentials():
    """(url, token, beats) this run reports with, url or token "" when there is none.

    The one reader of both variables and of the plugin's store: the heartbeat,
    the run link and the evidence poster all ask here. The token is never
    printed by this or by anything it hands the pair to. ALYDAS-379, S5:
    `beats` is False only for a stored entry whose desk has no runs; the
    variables carry no scopes, so a token there beats as it always has."""
    url = (os.environ.get(URL_VARIABLE) or "").strip()
    token = (os.environ.get(TOKEN_VARIABLE) or "").strip()
    if token:
        return url, token, True
    store = (os.environ.get(CREDENTIALS_VARIABLE) or "").strip()
    # Gate 8, D1. A stored token goes to the desk it was minted on and no
    # other, so an exported URL is handed to the store's reader, which then
    # answers with an entry for that desk or with nothing.
    stored = stored_credential(store, url) if store else None
    return stored or (url, "", True)

def same_desk(a, b):
    """Whether two desk URLs name one desk: scheme and host without regard to
    case, the port, and the path without its trailing slash."""
    try:
        one, two = urllib.parse.urlsplit(a), urllib.parse.urlsplit(b)
        return ((one.scheme.lower(), (one.hostname or "").lower(), one.port, one.path.rstrip("/"))
                == (two.scheme.lower(), (two.hostname or "").lower(), two.port, two.path.rstrip("/")))
    except ValueError:
        return False

def desk_configured():
    """A desk and a token, the one condition under which a beat is sent."""
    return desk_target() is not None

def desk_target():
    """(url, token) this run reports with, each stripped, or None when either
    is missing. ALYDAS-175, S2: the one target for the heartbeat and for
    `hold`'s Ask. ALYDAS-245: the pair is `desk_credentials()`'s, so a
    credential in the plugin's store counts as much as the two variables.
    ALYDAS-379, S5: None too on a desk without runs (`desk_without_runs`),
    which answers a heartbeat 404, so nothing is sent and nothing is said."""
    url, token, beats = desk_credentials()
    return (url, token) if url and token and beats else None

def desk_without_runs():
    """ALYDAS-379, S5. Whether the run's credential is for a desk without runs:
    `hold` and `wait` wait on the desk by beating, so each says so and exits 4."""
    url, token, beats = desk_credentials()
    return bool(url and token and not beats)

NO_RUNS = ("pipeline: the desk this run reports to has no runs, so it does not show this run "
           "and nobody can answer there (connect again once it has runs)")

class DeskUnsafe(Exception):
    """The URL is one the token may not be sent to (`url_refusal`'s sentence)."""

def desk_request(url, token, method, route, body=None, timeout=HEARTBEAT_TIMEOUT_S):
    """ALYDAS-175, S2. One request to the desk with the bearer, answering the
    reply's raw bytes. The heartbeat and `hold` both send through here, so the
    rules are one: the token goes only where `url_refusal` allows (DeskUnsafe
    otherwise), a redirect is refused rather than followed (`OPENER`), and a
    body is JSON. Raises what urllib raises; the caller keeps the token out of
    whatever it prints."""
    refusal = url_refusal(url)
    if refusal:
        raise DeskUnsafe(refusal)
    headers = {"authorization": "bearer " + token}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["content-type"] = "application/json"
    full = url.rstrip("/") + route
    parts = urllib.parse.urlsplit(full)
    host = (parts.hostname or "").lower()
    opener = OPENER
    if parts.scheme.lower() == "http" and under_localhost(host):
        # ALYDAS-381, Gate 8. A *.localhost desk is this machine (RFC 6761,
        # 6.3), so the request connects to 127.0.0.1 on the URL's port and the
        # name goes only in the Host header, which urllib keeps when it is set
        # (http.client then adds none of its own). The name never reaches a
        # resolver, so no resolver can send the bearer anywhere else.
        name = f"{host}:{parts.port}" if parts.port else host
        full = urllib.parse.urlunsplit(("http", f"{THIS_MACHINE}:{parts.port or 80}", parts.path, parts.query, ""))
        headers["host"] = name
        opener = THIS_MACHINE_OPENER
    req = urllib.request.Request(full, data=data, method=method, headers=headers)
    with opener.open(req, timeout=timeout) as answer:
        return answer.read()

def heartbeat(run, n, event=None, ended=False, status_word=None, path=None):
    """Report one boundary to the desk. A no-op without a desk and a token,
    and (ALYDAS-379, S5) on a desk without runs.

    S3: `path` is the ledger's, so a beat that had to trim the ledger to the
    desk's budget says so in the run's own log.

    ALYDAS-124: answers the reply's directive (a dict), or None when nothing
    was sent, the desk did not take it, or the reply carried none. The reply
    used to be read and thrown away."""
    token = ""
    try:
        target = desk_target()
        if target is None:
            return
        url, token = target
        # Before the body is built, so a refused URL writes no trim line.
        refusal = url_refusal(url)
        if refusal:
            say_not_taken(refusal, token)
            return
        # ALYDAS-173, S6. The log is the run's `events`, which `refresh` copies
        # off the events file before every beat.
        body = beat_body(run, n, event, ended)
        # S4. An unknown cost is no key at all, never 0.0 and never null.
        if body.get("costUsd") is None:
            body.pop("costUsd", None)
        if status_word:
            body["statusLine"] = plain(body["statusLine"].rsplit(" · ", 1)[0] + " · " + status_word, "statusLine")
        # S3. A body over the desk's ceiling is refused whole, so the ledger is
        # cut to its fixed budget first, and the log says so.
        body["ledger"], trimmed = trim_ledger(body["ledger"])
        if trimmed and path:
            log_event(path, n, "trimmed", trim_words(trimmed))
        # A work item read off the slug or the branch is a guess, and the desk
        # refuses a beat naming one it cannot link. Each refused guess is kept
        # and the beat goes again with the next, then with none, so a branch
        # that only looks like a key never keeps a run off the desk.
        while True:
            try:
                raw = desk_request(url, token, "POST", HEARTBEAT_PATH, body)
                break
            except urllib.error.HTTPError as e:
                item, guessed = beat_item(run)
                if not guessed or e.code not in (400, 403, 404) or body.get("subject") != beat_subject(item):
                    raise
                forget_item_guess(run, path, item)
                print(f"pipeline: the desk could not link this run to {printable(item)}, read off its name; "
                      f"set {WORK_ITEM_VARIABLE} to name its item", file=sys.stderr)
                following = beat_item(run)[0]
                if following:
                    body["subject"] = beat_subject(following)
                else:
                    body.pop("subject", None)
    except Exception as e:  # the desk must never be able to stop a phase closing
        say_not_taken(f"{type(e).__name__}: {e}", token)
        return None
    remember_run_id(run, path, run_id_of(raw))
    global _desk_budget
    _desk_budget = session_budget_of(raw)
    return directive_of(raw)

# ALYDAS-17, S5. The budget the desk's last reply named for this run
# (`session.budgetUsd`), or None; `wait` says it when the budget is raised.
_desk_budget = None

def session_budget_of(raw):
    try:
        reply = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        value = reply["session"]["budgetUsd"]
    except Exception:
        return None
    return value if amount(value) and value > 0 else None

# ---------- ALYDAS-135, S8: the Board line names the run on the desk ----------
#
# The board used to be published as a Claude artifact and its URL set on the
# ledger, which the Board line printed and every beat carried as `boardUrl`.
# The desk now draws the whole run on a page of its own (ALYDAS-154), so the
# Board line names that: `<desk origin>/runs/<id>`, with or without a work
# item. The id is the desk's, and only its reply knows it (`session.id`), so it
# is kept in the ledger as `desk_run_id` the first time the desk says it. With
# no desk or no id yet, the line names the local HTML board, which is still
# rendered at every write.

RUN_ID_SHAPE = re.compile(r"[A-Za-z0-9_-]{1,128}")

def run_id_of(raw):
    """The run id in a reply body's `session.id`, when it is a plain id, else None.

    It is printed inside a link, so nothing but letters, digits, `-` and `_`."""
    try:
        reply = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        run_id = reply["session"]["id"]
    except Exception:
        return None
    return run_id if isinstance(run_id, str) and RUN_ID_SHAPE.fullmatch(run_id) else None

def remember_run_id(run, path, run_id):
    """Keep the desk's id for this run, in memory and in the ledger, when it is new."""
    if not run_id or run.get("desk_run_id") == run_id:
        return
    run["desk_run_id"] = run_id
    if path:
        try:
            stored = load(path)
            stored["desk_run_id"] = run_id
            save(path, stored)
        except Exception as e:  # never a failed phase over a link
            print(f"pipeline: the desk's run id was not kept: {printable(e)}", file=sys.stderr)

def desk_run_link(run_id):
    """`<desk origin>/runs/<id>`, or None without a desk the token may go to, or an id.

    ALYDAS-154, S2: a run has its own page, so the link no longer needs a work
    item. Built from the URL's scheme, host and port only: a token pasted into
    ALYDASH_URL as a user, a query or a fragment is never printed, and Gate 8,
    D3, nor is its path, which can carry a secret too."""
    url, token, _ = desk_credentials()
    if not url or not token or not run_id or url_refusal(url):
        return None
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{parts.port}" if parts.port else "")
    base = urllib.parse.urlunsplit((parts.scheme.lower(), netloc, "", "", ""))
    return f"{base}/runs/{urllib.parse.quote(str(run_id), safe='')}"

def board_line(run):
    """What the boundary block's Board line says: the run on the desk, or the local board."""
    link = desk_run_link(run.get("desk_run_id"))
    if link:
        return link
    return os.path.join(run.get("root") or "docs/superpowers", "boards", f"{run['date']}-{run['slug']}.html")

# ---------- ALYDAS-124: what the desk says back ----------
#
# The heartbeat's reply carries a directive beside the session: continue, stop,
# or a message a person sent this run from its Now card. A desk from before
# ALYDAS-124 sends no directive, and anything this script cannot read as one is
# treated as continue, so an old desk, a garbled body or a kind added later all
# leave the run exactly as it was.
#
# Two things change here on purpose, against this section's rule that the desk
# cannot change the exit code. A person pressing Stop is the one thing a desk
# says that must reach the agent driving this run even if nobody reads the
# terminal, and a distinct exit code is the one signal a shell and a model both
# see. So a stop is obeyed: logged on the ledger, reported to the desk as the
# run's end, and the command exits STOP_EXIT after it has done everything else
# it was going to do (the boundary is stamped, the board rendered, the block
# printed). A message changes nothing but the output.

STOP_EXIT = 3

# ALYDAS-17, S5 (D1, D8). A stop whose `reason` is "budget" is the desk saying
# the run has spent past its budget and a person is being asked whether to
# extend it. It is not the run's end: nothing is ended on the desk. A command
# that writes (a boundary, a note, a step, a story, a log line) logs it, says
# what to do, and exits BUDGET_EXIT once its write has landed; the agent then
# runs `wait` in the background, which exits 0 when the budget is raised and
# STOP_EXIT when the run is ended. Inside `hold` and `wait` it is a wait, not
# an exit (D9), and it is logged once per process, not once per beat.
BUDGET_EXIT = 6
BUDGET_REASON = "budget"
_budget_heard = False

def directive_of(raw):
    """The directive in a reply body, as a dict with a known kind, or None."""
    try:
        reply = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except Exception:
        return None
    directive = reply.get("directive") if isinstance(reply, dict) else None
    if not isinstance(directive, dict) or directive.get("kind") not in ("continue", "stop", "message"):
        return None
    return directive

# Gate 8, L1: the desk refuses these too (`TERMINAL_UNSAFE` in
# packages/modules/runs/src/runs.ts), and a desk from another build may not. C0 and DEL,
# C1 (a terminal acts on U+009B as CSI and U+009D as OSC), and the bidi and
# zero-width marks that make a printed line read other than it is.
def unsafe(c):
    n = ord(c)
    return (n < 32 or 127 <= n <= 159 or 0x200B <= n <= 0x200F
            or 0x202A <= n <= 0x202E or 0x2066 <= n <= 0x2069)

def printable(text):
    """A line from the desk made safe for a terminal."""
    return "".join(" " if unsafe(c) else c for c in str(text)).strip()

STOPPED_WAITING = "Stopped from the desk while it waited on your review."

def amount(value):
    """A dollar amount from the desk, to the cent, or None when it is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < float("inf"):
        return None
    return usd(value)

def over_budget_words(directive):
    """"spent $5.12 of $5.00", from the stop's own amounts, or plainer words when
    the desk sent none a terminal may print."""
    spent, budget = amount(directive.get("spentUsd")), amount(directive.get("budgetUsd"))
    if spent and budget:
        return f"spent {spent} of {budget}"
    return f"spent more than its budget of {budget}" if budget else "spent more than its budget"

def obey_budget(path, run, n, directive, ended_already, waiting):
    """ALYDAS-17, S5. A budget stop: logged and said once, and answered
    BUDGET_REASON for the caller. Nothing is ended here and nothing exits: a
    writing command exits BUDGET_EXIT through `over_budget`, a waiting one
    waits on. A run that has already ended has nothing to wait for."""
    global _budget_heard
    if ended_already:
        return None
    if waiting and _budget_heard:
        return BUDGET_REASON
    _budget_heard = True
    words = over_budget_words(directive)
    log_event(path, n, "escalated", f"over budget: {words}; the desk has asked whether to extend it")
    refresh(path)
    if waiting:
        print(f"pipeline: over budget: {words}. The desk has asked whether to extend it; still waiting.")
    else:
        print(f"pipeline: over budget: {words}. The desk has asked whether to extend it. "
              f'Run "wait {run.get("slug")}" in the background, and do not start the next phase; '
              f"it exits 0 when the budget is raised and {STOP_EXIT} if the run is ended.")
    sys.stdout.flush()
    return BUDGET_REASON

def over_budget(heard):
    """Exit BUDGET_EXIT when the write's beat was answered with a budget stop.
    Called by the writing commands once their write has landed, never by `hold`
    or `wait`, which wait through it."""
    if heard == BUDGET_REASON:
        sys.exit(BUDGET_EXIT)

def obey(path, run, n, directive, ended_already, waiting=False):
    """Act on a directive after the boundary has been stamped and printed.
    Answers BUDGET_REASON for a budget stop (ALYDAS-17, S5), else None;
    `waiting` is true inside `hold` and `wait`."""
    if not directive:
        return None
    kind = directive.get("kind")
    if kind == "message":
        text = printable(directive.get("text") or "")
        if not text:
            return None
        log_event(path, n, "escalated", "message from the desk: " + text)
        refresh(path)
        print(f"pipeline: message from the desk: {text}")
        return None
    if kind != "stop":
        return None
    if directive.get("reason") == BUDGET_REASON:
        return obey_budget(path, run, n, directive, ended_already, waiting)
    # Gate 8, ST-1: Deny on the budget Ask sends this same plain stop, so the
    # line names both causes and claims neither.
    print("pipeline: the desk says stop. A person stopped this run, or declined to extend its budget: "
          "stop working on it, and do not start the next phase.")
    if ended_already:
        return None
    # Gate 8, D1. A phase a hold left waiting is not waiting any more once the
    # run is stopped: its scene says so, on the board and in the ended beat.
    # The status stays `blocked` (a stopped run's phase was not answered).
    run = load(path)
    p = next((q for q in run.get("phases") or [] if q.get("n") == n), None)
    if p and p.get("status") == "blocked" and isinstance(p.get("hold"), dict):
        p["scene"] = STOPPED_WAITING
        save(path, run)
    log_event(path, n, "escalated", "stopped from the desk")
    refresh(path)
    # The run's end, as the desk hears of it. Its reply is not read: a run that
    # has been told to stop has nothing left to be told.
    heartbeat(load(path), n, ended=True, status_word="stopped", path=path)
    sys.exit(STOP_EXIT)

def say_not_taken(line, token):
    """The one stderr line a heartbeat that went nowhere costs.

    The token is never printed. A message that repeated the secret would leave
    it in every terminal scrollback the run touched. Every rendering comes out,
    not only the raw one: urllib prints a URL and a header value through repr,
    so a token holding a control character reaches the message escaped and a
    replacement of the raw string walks past it. A refused URL is quoted back
    through here too, so a token pasted into ALYDASH_URL is taken out of it.
    """
    for rendering in token_renderings(token):
        if rendering in line:
            line = line.replace(rendering, "<token>")
    print(f"pipeline: the desk did not take the heartbeat ({line})", file=sys.stderr)

# ---------- ALYDAS-175, S2: the run waits for a person's review ----------
#
# `hold <slug> <n>` is `phase <n> done` with a person in the middle. The phase's
# own checks run first, as they would for `done`; then the phase is marked
# `blocked` (the desk hears it at once), a `decide` Ask is raised in the run
# with a choice per thing the person could say, and the command waits. Every
# BEAT_S it beats, so the run stays live and a Stop or a message is heard; every
# READ_S it reads the Ask back. An Approve or a variant closes the phase through
# the same stamp as `phase <n> done`; a send-back reopens the phase, running
# again with the note as its scene and the attempt moved on (S3); an Ask that
# expired is a stop.
#
# The agent runs it in the background, so Claude Code wakes when it exits. With
# no desk nobody can answer, so it says so at once and the agent asks in chat.

HOLD_BEAT_S = 20
# Gate 4. 120 read-backs an hour per run, so two holding runs on one standing
# token (240) stay under the desk's 300 an hour for `/api/asks`.
HOLD_READ_S = 30
# Gate 8, S-1. That budget is per token, shared by every run on it, so a busy
# desk answers 429 with how long to wait (Retry-After). The hold waits that
# long, beating on its usual clock meanwhile, and asks again; never longer than
# the desk's hourly window, whatever the header says.
HOLD_RETRY_MAX_S = 3600
HOLD_INTERVAL_VARIABLE = "PIPELINE_HOLD_INTERVAL"
HOLD_TIMEOUT_S = 10
ASKS_PATH = "/api/asks"
# An agent's Ask lands on its own token's app whatever it names (`raiseAsk`),
# but the body must name one, and a module the desk has switched on.
# ALYDAS-303, S9 (c8): runs, whose Asks a run's holds are. A desk from before
# ALYDAS-303 has no runs module and refuses the Ask.
ASK_MODULE = "runs"
NO_DESK_EXIT = 4
READ_REFUSED_EXIT = 5
# The desk's bounds for an Ask (`packages/core/src/asks.ts`) and its choices.
ASK_QUESTION_MAX = 200
ASK_DETAIL_MAX = 2_000
ASK_CHOICES_MAX = 8
ASK_LABEL_MAX = 200
# What a choice does, one line (`CHOICE_DETAIL_MAX`).
ASK_CHOICE_DETAIL_MAX = 200
APPROVE, SEND_BACK, VARIANT = "approve", "send-back", "variant-"
# ALYDAS-303, S9 (c6). A choice that cannot be taken without a note says so,
# and the desk refuses it with an empty one by this marker, not by its id.
NOTE_REQUIRED = "required"
# What the desk reads back for an Ask that lapsed unanswered (`EXPIRED_ANSWER`).
EXPIRED_ANSWER = "expired"
# Tab and newline are the two control characters the desk keeps in a detail.
DETAIL_UNSAFE = re.compile("[\x00-\x08\x0b-\x1f\x7f‪-‮⁦-⁩]")

def cut(text, width):
    """`text` to at most `width` code points, a cut marked with an ellipsis."""
    return text if len(text) <= width else text[:width - 1].rstrip() + "…"

def hold_label(head, name):
    """A choice's label: `head` then the option's name, cut to the desk's bound."""
    name = plain(name, None)
    return cut(head + name, ASK_LABEL_MAX) if name else head.rstrip(": ")

def hold_line(text):
    """A choice's detail: one line, free of control and bidi marks, cut to the desk's bound."""
    return cut(plain(text, None), ASK_CHOICE_DETAIL_MAX)

def hold_choices(options):
    """The review Ask's choices, from the phase's recorded options, and the
    names of any options left out. Pure: the options are read, never changed.

    Approve comes first and names the chosen option when there is one (Phase 2
    verdict, item 5: "Approve" and "Go with <the recommended one>" would be two
    choices for one answer). Then "Go with <name>" for each other option, its
    id `variant-<k>` with k its place among the recorded options (from 1), so
    an answer maps back to the option it names. Send it back comes last, and
    is the one choice that needs a note (`note: 'required'`).

    The desk takes eight choices at most, so at most six variants: past that
    the first six in recorded order are listed and the rest are answered in
    the note, their names handed back for the question's detail."""
    options = [o for o in options or [] if isinstance(o, dict)]
    chosen = next((o for o in options if o.get("chosen")), None)
    chosen_name = plain(chosen.get("name"), None) if chosen else ""
    approve = hold_label("Approve: ", chosen_name) if chosen else "Approve"
    # ALYDAS-175 (carried from S2). Each choice says what taking it does, in
    # plain words, for B's radio list.
    approve_does = (f"Build {chosen_name}, the option this phase recommends, and go on to the next phase."
                    if chosen_name else "Build what this phase recommends, and go on to the next phase.")
    others = [(k, o) for k, o in enumerate(options, start=1) if o is not chosen]
    room = ASK_CHOICES_MAX - 2
    variants = []
    for k, o in others[:room]:
        name = plain(o.get("name"), None)
        does = (f"Build {name} instead of the recommended option, and go on to the next phase." if name
                else "Build this option instead of the recommended one, and go on to the next phase.")
        variants.append({"id": f"{VARIANT}{k}", "label": hold_label("Go with ", name), "detail": hold_line(does)})
    left_out = [plain(o.get("name"), None) for _, o in others[room:]]
    return ([{"id": APPROVE, "label": approve, "detail": hold_line(approve_does)}] + variants
            + [{"id": SEND_BACK, "label": "Send it back",
                "detail": "Reopen this phase to work on it again, with your note as its brief.",
                "note": NOTE_REQUIRED}], left_out)

def hold_intervals():
    """(beat, read) in seconds: the constants, or both PIPELINE_HOLD_INTERVAL
    when it is a positive number (for tests)."""
    raw = (os.environ.get(HOLD_INTERVAL_VARIABLE) or "").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 0
    return (value, value) if value > 0 else (HOLD_BEAT_S, HOLD_READ_S)

def hold_question(run, p, given):
    """The Ask's question: `--question`, else one naming the phase, one line, capped."""
    question = plain(given, None)
    if not question:
        label = "Gate" if p.get("kind") == "gate" else "Phase"
        question = f"{label} {p['n']} · {p.get('name', '')} of {run.get('title') or run['slug']} is ready: go on?"
    return cut(question, ASK_QUESTION_MAX)

def hold_detail(p, left_out):
    """The Ask's detail: the phase's lede and plain line, and any options the
    choices had no room for, capped. Newlines kept, other control characters and
    bidi marks taken out."""
    parts = [DETAIL_UNSAFE.sub("", p.get(key) or "").strip() for key in ("lede", "plain")]
    if left_out:
        parts.append("Also weighed, not listed as choices (name one in the note): " + ", ".join(left_out) + ".")
    return cut("\n\n".join(part for part in parts if part), ASK_DETAIL_MAX)

def chat_close(slug, n):
    """Gate 8, ST-5. What to do when nobody can answer on the desk. A holding
    phase refuses a plain `phase n done` (`refuse_unheld`) and takes only
    `--force`, so the person's ruling from chat is the force reason."""
    return f'ask in chat, then close the phase with phase {slug} {n} done --force "<their ruling>"'

def hold_say(line, token):
    """One stderr line about the hold's own requests, the token never in it."""
    print(f"pipeline: {redacted(printable(line), token)}", file=sys.stderr)

def http_status(e):
    return e.code if isinstance(e, urllib.error.HTTPError) else None

def retry_after(headers, default):
    """Gate 8, S-1. The seconds a 429's Retry-After asks for, capped at
    HOLD_RETRY_MAX_S; `default` when it names none this reads (missing, not
    positive, or an HTTP date)."""
    try:
        value = float((headers or {}).get("Retry-After") or "")
    except (TypeError, ValueError):
        return default
    if not 0 < value < float("inf"):
        return default
    return min(value, HOLD_RETRY_MAX_S)

def hold_wait(path, n, until, beat_every, next_beat):
    """Gate 8, S-1. Wait until `until` (monotonic), beating whenever a beat is
    due on the way, so a Stop or a message is heard while a 429 is waited out.
    Answers when the next beat is due, and (ALYDAS-17, S5, for `wait`) what the
    last beat on the way was answered, None when there was none or no reply.
    Every beat here is a wait's, so a budget stop waits on."""
    directive = None
    while True:
        now = time.monotonic()
        if now >= until:
            return next_beat, directive
        time.sleep(max(0.0, min(next_beat, until) - now))
        if time.monotonic() >= next_beat:
            next_beat = time.monotonic() + beat_every
            directive = heartbeat(load(path), n, path=path)
            obey(path, load(path), n, directive, ended_already=False, waiting=True)
            sys.stdout.flush()

def refuse_unopenable(path, slug, n, status, paths):
    """ALYDAS-206. Exit 2, raising nothing, when the desk could not open a file
    phase n recorded: each file and its reason on the run log as a `check`
    event, and on stderr with what to do. Called after hold's own push."""
    bad = desk_cannot_open(paths, repo_facts())
    if not bad:
        return
    said = "; ".join(f"{p}: {why}" for p, why in bad)
    log_event(path, n, "check", f"the desk could not open what phase {n} would ask about: {said}")
    refresh(path)
    print(f"phase {n} was not held: the desk could not open what it would ask about, so nothing was asked "
          f"there and phase {n} is still {status}:", file=sys.stderr)
    for p, why in bad:
        print(f"  {printable(p)}: {printable(why)}", file=sys.stderr)
    # Gate 8, ST1. A "record" fix names the note that drops the recorded path
    # and records the spelling it names, keeping the file's note.
    notes = {plain(e.get("path"), None): e.get("note") or "" for e in phase_of(load(path), n).get("produced") or []
             if isinstance(e, dict)}
    def how(recorded, spelling):
        drop = f"note {slug} {n} --drop-produced {shlex.quote(recorded)}"
        if spelling is None:
            return f"{drop} then --produced the file's path from the repository root"
        note = notes.get(recorded, "")
        return f"{drop} --produced {shlex.quote(spelling + '=' + note if note else spelling)}"
    print(f"{'; '.join(fixes_for(bad, how))}, then run hold {slug} {n} again; or pass "
          f"--force \"reason\" to hold anyway (recorded on the board).", file=sys.stderr)
    sys.exit(2)

def went_past_unopenable(path, n, paths, force):
    """ALYDAS-206, S3. `hold --force`: what the check would have refused, gone
    past. Each file and its reason on the run log, carrying the force (amber on
    the board), and on stderr. Raises nothing, exits nothing."""
    bad = desk_cannot_open(paths, repo_facts())
    if not bad:
        return
    said = "; ".join(f"{p}: {why}" for p, why in bad)
    log_event(path, n, "check", f"held anyway: the desk could not open what phase {n} asks about: {said}",
              {"forced": force})
    print(f"phase {n} is held anyway (--force): the desk could not open:", file=sys.stderr)
    for p, why in bad:
        print(f"  {printable(p)}: {printable(why)}", file=sys.stderr)

def cmd_hold(a):
    path = find_run(a.slug, a.root)
    run = load(path)
    p = phase_of(run, a.n)
    # S2's follow-up. A phase a hold already blocked (a crash, or an exit 4 or
    # 5 before the answer came) is held again on the same attempt: on the Ask
    # it recorded, read back first (S7, below), or raised afresh on the same
    # fingerprint when none was recorded. A phase blocked by hand, with no hold
    # behind it, is not a wait to resume.
    resuming = waits_on_a_hold(p)
    if p.get("status") != "active" and not resuming:
        print(f"phase {a.n} is not running (it is {p.get('status')}), so it cannot be held: "
              f"hold the phase the run is in", file=sys.stderr)
        sys.exit(2)
    refuse_unclosable(path, run, a.n, held=True)
    target = desk_target()
    if target is None:
        if desk_without_runs():
            print(f"{NO_RUNS}: " + chat_close(a.slug, a.n))
        else:
            print(f"pipeline: no desk is set ({DESK_WAYS}), so nobody can answer there: "
                  + chat_close(a.slug, a.n))
        sys.exit(NO_DESK_EXIT)
    url, token = target

    # The attempt names the Ask: a send-back (S3) moves it on, so the next hold
    # asks afresh; a hold raised again on the same attempt finds the open Ask.
    hold = p.get("hold") if isinstance(p.get("hold"), dict) else {}
    attempt = hold.get("attempt") if isinstance(hold.get("attempt"), int) and hold.get("attempt") > 0 else 1
    # ALYDAS-175, S7 (carried from S3). An Ask this attempt already raised is
    # read back before anything is raised (below).
    recorded = hold.get("ask") if isinstance(hold.get("ask"), str) and hold.get("ask") else None
    # ALYDAS-206, S3. `--force` goes past the desk-can-open check only; the
    # artifact checks above have run already. A hold resuming on the Ask it
    # recorded checks nothing, so the force has nothing to go past and is not
    # recorded.
    force = a.force or None
    if force and recorded:
        print(f"pipeline: phase {a.n} already asked on the desk, so it is not checked again and "
              f"--force changes nothing", file=sys.stderr)
        force = None

    # ALYDAS-206. About to raise a question on files the phase recorded: push
    # once, then check the desk can open each at the commit it will read. A
    # refusal writes only its check event, before the hold is saved or the
    # phase blocked, so the phase stays as it was. A recorded Ask raises nothing
    # new, so it is not checked.
    paths = recorded_paths(p)
    pushed = False
    if not recorded and paths:
        push_branch()
        pushed = True
        if force:
            went_past_unopenable(path, a.n, paths, force)
        else:
            refuse_unopenable(path, a.slug, a.n, p.get("status"), paths)
    choices, left_out = hold_choices(p.get("options"))
    body = {
        "appId": plain(run.get("repo"), None) or AGENT_NAME,
        "moduleId": ASK_MODULE,
        "kind": "decide",
        "question": hold_question(run, p, a.question),
        "detail": hold_detail(p, left_out),
        "fingerprint": f"hold:{a.n}:{attempt}",
        "run": plain(run.get("slug"), "run"),
        "notes": True,
        "choices": choices,
    }
    run = load(path)
    run_phase = phase_of(run, a.n)
    run_phase["hold"] = dict(hold, attempt=attempt)
    if force:
        # S3 (ruling). On the phase, where the board's amber note and the
        # desk's forced mark read it, and on the hold, so a send-back can tell
        # the hold's own force from one given by hand. Gate 6, P3: a force
        # given by hand before it is kept on the hold, and a send-back gives it back.
        prior = run_phase.get("forced")
        if prior and prior != hold.get("forced"):
            run_phase["hold"]["forced_over"] = prior
        run_phase["hold"]["forced"] = force
        run_phase["forced"] = force
    save(path, run)

    # Blocked first, and beaten: the desk sees the wait at once, and an Ask
    # names a run the desk has already heard from. Resuming, the phase is
    # blocked already, so it only beats: the boundary is not logged twice.
    if resuming:
        # Gate 6, P1. No stamp redraws the board here, so a force recorded on
        # the resume is drawn now, while the hold waits.
        refresh(path)
        directive = heartbeat(load(path), a.n, path=path)
        obey(path, load(path), a.n, directive, ended_already=False, waiting=True)
    else:
        stamp_phase(path, a.n, "blocked", note="waiting on your review on the desk", finish_step=True, waiting=True,
                    push=not pushed, force=force)

    # ALYDAS-175, S7 (carried from S3). An Ask this attempt already raised
    # (`recorded`, above) is read back before anything is raised. The desk
    # dedupes only an Ask nobody has answered (`byFingerprint(..., 'open')`), so
    # raising again after the person answered while no hold was running would
    # open a second Ask and the first answer would never be read. The first pass
    # of the loop reads it at once: answered is acted on, expired is a stop,
    # open is waited on.
    beat_every, read_every = hold_intervals()
    next_beat = time.monotonic() + beat_every
    ask_id = recorded
    while not ask_id:
        try:
            raised = json.loads(desk_request(url, token, "POST", ASKS_PATH, body, timeout=HOLD_TIMEOUT_S))
            ask_id = raised["ask"]["id"]
            if not isinstance(ask_id, str) or not ask_id:
                raise ValueError("the desk's reply named no Ask")
        except Exception as e:
            ask_id = None
            if http_status(e) == 429:
                # Gate 8, S-1. Busy, not refused: wait what it asks, beating, and raise again.
                wait = retry_after(getattr(e, "headers", None), read_every)
                hold_say(f"the desk is busy ({type(e).__name__}: {e}); asking again in {wait:g}s", token)
                next_beat, _ = hold_wait(path, a.n, time.monotonic() + wait, beat_every, next_beat)
                continue
            hold_say(f"the desk did not take the review question ({type(e).__name__}: {e})", token)
            print(f"pipeline: nobody can answer on the desk, and phase {a.n} is blocked: "
                  + chat_close(a.slug, a.n))
            sys.exit(NO_DESK_EXIT)
    if not recorded:
        run = load(path)
        phase_of(run, a.n)["hold"] = dict(phase_of(run, a.n).get("hold") or {}, attempt=attempt, ask=ask_id)
        save(path, run)
    print(f"pipeline: waiting on the desk for phase {a.n}: {printable(body['question'])}")
    sys.stdout.flush()

    query = urllib.parse.urlencode({"id": ask_id, "run": body["run"]})
    now = time.monotonic()
    next_read = now if recorded else now + read_every
    while True:
        time.sleep(max(0.0, min(next_beat, next_read) - time.monotonic()))
        now = time.monotonic()
        if now >= next_beat:
            next_beat = now + beat_every
            directive = heartbeat(load(path), a.n, path=path)
            obey(path, load(path), a.n, directive, ended_already=False, waiting=True)
            sys.stdout.flush()
        if now < next_read:
            continue
        next_read = now + read_every
        try:
            ask = json.loads(desk_request(url, token, "GET", f"{ASKS_PATH}?{query}", timeout=HOLD_TIMEOUT_S))["ask"]
        except Exception as e:
            status = http_status(e)
            if status is not None and status != 429 and status < 500:
                hold_say(f"the desk could not read the answer back ({type(e).__name__}: {e}); "
                         f"phase {a.n} is still blocked: " + chat_close(a.slug, a.n), token)
                sys.exit(READ_REFUSED_EXIT)
            if status == 429:
                # Gate 8, S-1. Not before the desk's Retry-After; the beats go on meanwhile.
                next_read = now + max(read_every, retry_after(getattr(e, "headers", None), read_every))
            hold_say(f"the answer was not read back this time, still waiting ({type(e).__name__}: {e})", token)
            continue
        answer = ask.get("answer") if isinstance(ask, dict) else None
        if not isinstance(answer, str) or not answer:
            continue
        hold_answered(path, a.n, answer, ask, choices)
        return

def hold_answered(path, n, answer, ask, choices):
    """An answer read back: an expired Ask is a stop; Approve or a variant
    closes the phase; a send-back reopens it (S3); an answer this hold did not
    offer is reported and the phase left blocked."""
    if answer == EXPIRED_ANSWER:
        print(f"pipeline: the review question for phase {n} expired on the desk, which is a stop")
        obey(path, load(path), n, {"kind": "stop"}, ended_already=False)
        return
    label = next((c["label"] for c in choices if c["id"] == answer), None) or plain(ask.get("answerLabel"), None) or answer
    note = ask.get("note") if isinstance(ask.get("note"), str) else ""
    note = note.strip()
    log_event(path, n, "decision", f"Answered on the desk: {label}." + (f" Note: {note}" if note else ""))
    refresh(path)
    print(f"pipeline: answered: {printable(answer)}")
    if note:
        print(f"pipeline: note: {printable(note)}")
    sys.stdout.flush()
    # ALYDAS-17, S5 (D9). These beats are the hold's, so a budget stop is not
    # an exit here: the hold ends 0 on its answer, and the next command hears it.
    if answer == APPROVE or answer.startswith(VARIANT):
        stamp_phase(path, n, "done", waiting=True)
    elif answer == SEND_BACK:
        # ALYDAS-175, S3. Running again, the note as its scene, the usual steps
        # afresh (cleared, so `phase start` seeds them), and the attempt moved
        # on, so the next hold raises a new Ask rather than finding this one.
        run = load(path)
        p = phase_of(run, n)
        p["scene"] = f"Sent back: {note}" if note else "Sent back, with no note."
        p["steps"] = []
        hold = p.get("hold") if isinstance(p.get("hold"), dict) else {}
        attempt = hold.get("attempt") if isinstance(hold.get("attempt"), int) and hold.get("attempt") > 0 else 1
        # ALYDAS-206, S3. The hold's own force goes with it, or the next pass
        # would start forced (and Gate 4 would go past a NOT CONFIRMED verdict
        # unasked). A force given by hand is not the hold's, and stays, or is
        # given back when the hold's own went over it (Gate 6, P3).
        if hold.get("forced") and p.get("forced") == hold.get("forced"):
            p["forced"] = hold.get("forced_over") or ""
        p["hold"] = {"attempt": attempt + 1}
        save(path, run)
        stamp_phase(path, n, "start", note="sent back for another pass", waiting=True)
    else:
        beat = beat_the_write(path)
        obey_the_write(path, beat, waiting=True)

# ---------- ALYDAS-17, S5: the run waits for more budget ----------
#
# A writing command that heard a budget stop exits BUDGET_EXIT, and the agent
# runs `wait <slug>` in the background (D8: blocking inside the command would
# hit a Bash call's ten-minute ceiling, and the agent could not see why). It
# beats as `hold` does, through `hold_wait`, so the run stays live on the desk
# and the budget Ask stays in the run's pile. `continue` means a person raised
# the budget: exit 0. A plain stop, a person's Stop or a denied extension, ends
# the run through `obey` and exits STOP_EXIT. A budget stop, a message, a 429,
# a 5xx or no answer at all waits on.

def cmd_wait(a):
    path = find_run(a.slug, a.root)
    if desk_target() is None:
        if desk_without_runs():
            print(f"{NO_RUNS}: ask in chat whether to go on.")
        else:
            print(f"pipeline: no desk is set ({DESK_WAYS}), so nobody can extend the budget "
                  f"there: ask in chat whether to go on.")
        sys.exit(NO_DESK_EXIT)
    beat_every, _ = hold_intervals()
    n = current_phase(load(path))
    print(f"pipeline: waiting on the desk for a person to extend the budget of {a.slug}")
    sys.stdout.flush()
    directive = heartbeat(load(path), n, path=path)
    obey(path, load(path), n, directive, ended_already=False, waiting=True)
    sys.stdout.flush()
    next_beat = time.monotonic() + beat_every
    while not (directive and directive.get("kind") == "continue"):
        next_beat, directive = hold_wait(path, n, next_beat, beat_every, next_beat)
    raised = amount(_desk_budget) if _desk_budget else None
    words = "budget extended on the desk" + (f", to {raised}" if raised else "")
    log_event(path, n, "decision", words)
    refresh(path)
    print(f"pipeline: {words}: go on with the run.")

# ---------- the evidence a finished slice puts on the board ----------
#
# Gate 7 already produces the two things a desk wants: screenshots of the real
# thing, and a judgement about which acceptance criteria they show. Until this
# existed, both stayed in the repository - the screenshots in a folder and the
# judgement in a report - and the board went on saying `0 of 5` on work that was
# finished. A person who had not been told they were owed a tick did not tick.
#
# So at Gate 7 the run posts what it proved: it attaches each screenshot for the
# criteria that screenshot shows, ticks those, and raises one Ask for the ones it
# could not show. A slice that showed everything raises nothing.
#
# **The plan is the model's to write and this function will not invent one.**
# Which criteria a run demonstrated is a judgement, and a script that guessed it
# would be the marking-its-own-homework failure at one remove. This only carries
# the plan to the poster the repository owns.
#
# It differs from the heartbeat above in one way, deliberately. A heartbeat that
# goes nowhere costs a Now strip nobody was watching, so it fails quiet. Evidence
# that goes nowhere is this whole feature failing, silently, in exactly the way
# it was built to stop - so it fails loud and answers non-zero. It still cannot
# close a phase or fail one: `phase ... done` is a different command, and a desk
# that is down must not be able to hold a finished gate open.

POSTER_PATH = "scripts/post-evidence.ts"
POSTER_SCRIPT = "post-evidence"
POSTER_TIMEOUT_S = 120
LOG_NOTE_MAX = 600

def poster_in(repo):
    """The repository's own poster, or None.

    Looked for rather than assumed: this skill runs against repositories that
    have never heard of a desk, and in those the whole step is nothing at all.
    """
    here = os.path.join(repo, POSTER_PATH)
    return here if os.path.isfile(here) else None

def poster_command(repo):
    """How this repository runs its poster.

    Its own package script when package.json names one, so the run uses the
    spelling a person would type, and `npx tsx` only as the fallback for a
    repository that carries the file without a script for it.
    """
    manifest = os.path.join(repo, "package.json")
    if os.path.isfile(manifest):
        try:
            with open(manifest, encoding="utf-8") as handle:
                scripts = json.load(handle).get("scripts") or {}
            if POSTER_SCRIPT in scripts:
                return ["pnpm", POSTER_SCRIPT]
        except Exception:
            pass  # a package.json this script cannot read is not this script's business
    return ["npx", "tsx", POSTER_PATH]

def poster_url(url):
    """The desk's address as the poster is handed it. ALYDAS-381, Gate 8: the
    poster is a program of its own, in whichever repository the run is in, and
    it asks its own resolver for whatever ALYDASH_URL names, so a *.localhost
    name would reach a resolver on the way to the bearer after all. Such a desk
    is this machine (RFC 6761, 6.3), so the poster is handed
    http://127.0.0.1 on the same port, with the same path and query; a user in
    the address is left out, as the poster drops it anyway. Any other address
    is handed over as it was given."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() != "http" or not under_localhost((parts.hostname or "").lower()):
        return url
    netloc = THIS_MACHINE + (f":{parts.port}" if parts.port else "")
    return urllib.parse.urlunsplit(("http", netloc, parts.path, parts.query, parts.fragment))

def cmd_evidence(a):
    repo = os.getcwd()
    poster = poster_in(repo)
    if poster is None:
        print(f"no {POSTER_PATH} in this repository, so there is no board to post to")
        return

    url, token, _ = desk_credentials()
    if not url or not token:
        # Said out loud rather than skipped quietly. This repository has a
        # poster, so this is a run that could have put its evidence on the
        # board and did not, which is the thing worth knowing.
        print(f"pipeline: {URL_VARIABLE} or {TOKEN_VARIABLE} is unset and the alydash plugin's store holds no "
              f"credential this run can use, so nothing was posted. "
              f"The evidence is still in the report; the board will not show it.", file=sys.stderr)
        raise SystemExit(0)

    # Gate 8, SEC-4. The poster is handed the token under the heartbeat's rule:
    # https, or http to this machine only. Loud, as every way this step does
    # not post is, and the token is taken out of the quoted URL.
    refusal = url_refusal(url)
    if refusal:
        raise SystemExit(redacted(f"pipeline: nothing was posted. {refusal}", token))

    if not os.path.isfile(a.plan):
        raise SystemExit(f"no plan at {a.plan}. The plan says which criteria this run showed and with which screenshots; it is yours to write.")

    command = poster_command(repo) + ["--plan", a.plan]
    try:
        # S5. The poster reads the two variables itself, so a pair that came
        # from the plugin's store is handed to it the same way.
        env = dict(os.environ, **{URL_VARIABLE: poster_url(url), TOKEN_VARIABLE: token})
        answer = subprocess.run(command, capture_output=True, text=True, timeout=POSTER_TIMEOUT_S, env=env)
    except Exception as e:
        raise SystemExit(redacted(f"the poster did not run ({type(e).__name__}: {e})", token))

    out = (answer.stdout or "") + (answer.stderr or "")
    print(redacted(out.strip(), token))

    # The whole answer goes in the log, clamped, rather than the line this
    # function guessed was the interesting one. A package manager prints its own
    # banner around the poster's output and it is not always last, so picking a
    # line is a heuristic that would put `Done in 363ms` on the board on the day
    # it mattered. Clamped because a run log is read, and a poster that decided
    # to print a file is not allowed to make it unreadable.
    path = find_run(a.slug, a.root)
    line = "posted its evidence to the board" if answer.returncode == 0 else "could not post its evidence to the board"
    said = " / ".join(part.strip() for part in out.strip().splitlines() if part.strip()) or "no output"
    # Gate 8 (ALYDAS-245), C-5. Redacted, then cut: cut first, a token lying
    # across the cut is no longer the whole token, and its start stays behind.
    log_event(path, 7, "landed" if answer.returncode == 0 else "escalated",
              redacted(f"Gate 7 {line}: {said}", token)[:LOG_NOTE_MAX])
    refresh(path)

    if answer.returncode != 0:
        raise SystemExit(f"pipeline: the desk did not take this run's evidence. The gate is not held open by it - "
                         f"close the phase and record the failure rather than claiming the board shows what it does not.")

def redacted(line, token):
    """The poster never prints the token and neither does this, in any spelling."""
    for rendering in token_renderings(token):
        if rendering in line:
            line = line.replace(rendering, "<token>")
    return line

# ---------- ALYDAS-299, S5: the run checks the desk before it says ready ----------
#
# A pull request's by-hand steps are on the desk's Needs you (the sync raises
# each one written under **By hand:** in What you'll notice), and a step
# before merging is the person's to do before the merge. So before a run tells
# the person a pull request is ready, `ready` reads the item's open steps off
# read_item (`GET /api/work/criteria?item=KEY`, which answers `steps:
# [{when, text, pullRequest, criterion, wordsHash, occurrence, answered}]`
# beside the criteria, the answered ones after the open since Gate 8 pass 2).
# With `--pr N` it also reads N's description with gh (`pull_request_body`). It reads through
# `desk_credentials`, not `desk_target`: a desk without runs takes no beat and
# still answers read_item. It never says ready without having read the desk:
# no credential, a refused URL, no answer, a refusal or an answer it cannot
# read are each READY_UNREAD_EXIT, and every line it prints goes through
# `redacted`.

CRITERIA_PATH = "/api/work/criteria"
STEP_TIMINGS = ("before", "after", "check")
READY_OPEN_EXIT = 3
READY_UNREAD_EXIT = NO_DESK_EXIT

def open_before_steps(answer, pull_request=None):
    """The before-merging steps of read_item's answer, of `pull_request` when it
    is named, in the order the desk gave them. Raises ValueError for an answer
    it cannot read, so a desk that answers no steps (one from before them, or
    a reply of another shape) is never read as one with none open."""
    steps = answer.get("steps") if isinstance(answer, dict) else None
    if not isinstance(steps, list):
        raise ValueError("the desk's answer has no list of steps")
    for step in steps:
        number = step.get("pullRequest") if isinstance(step, dict) else None
        if (not isinstance(step, dict) or step.get("when") not in STEP_TIMINGS or not isinstance(step.get("text"), str)
                or isinstance(number, bool) or not isinstance(number, int)):
            raise ValueError("a step in the desk's answer is not when, text and pull request")
    # Since Gate 8 pass 2 the desk lists the answered steps too, marked so.
    return [step for step in steps
            if step["when"] == "before" and step.get("answered") is not True
            and (pull_request is None or step["pullRequest"] == pull_request)]

def refusal_words(e):
    """What the desk said when it refused, or the exception's own words."""
    if isinstance(e, urllib.error.HTTPError):
        try:
            said = json.loads(e.read().decode("utf-8", "replace")).get("error")
        except Exception:
            said = None
        return f"{e.code} {said}" if isinstance(said, str) and said.strip() else f"{e.code}"
    return f"{type(e).__name__}: {e}"


# ---------- ALYDAS-299, Gate 8 pass 2: the pull request's own By hand part ----------
#
# Sachit's ruling (code #6, W2, W9): with `--pr N`, ready also reads the pull
# request's description, so a before-merging step written there that the desk
# has not read yet (no sync since it was written, or a repository with no
# webhook) is open, not "none open". This is the desk's reader's step rules,
# ported: `readMergeNotes` and `readSteps` in
# packages/modules/work/src/merge-notes.ts, and its key, `stepFingerprints` in
# by-hand-steps.ts. Only what decides which steps are kept and what each one
# says, since that is what the key is made of; a dropped step's sentence is the
# merge notes check's to say. JavaScript's `\s`, `.`, `$` and `trim` are
# written out, so a character Python reads as space and JavaScript does not
# (or the other way) cannot make the two keep different steps.

JS_SPACE = "\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_S, _NS, _DOT = f"[{JS_SPACE}]", f"[^{JS_SPACE}]", "[^\n\r\u2028\u2029]"
_RE = re.I | re.A

def js_trim(text):
    return re.sub(f"^{_S}+|{_S}+\\Z", "", text)

NOTES_HEADING = re.compile(f"^ {{0,3}}## +What you['\u2019]ll notice *\\Z", _RE)
NOTES_ANY_HEADING = re.compile(f"^ {{0,3}}#{{1,6}}(?:{_S}|\\Z)")
NOTES_SETEXT = re.compile(f"^ {{0,3}}(?:=+|-+){_S}*\\Z")
NOTES_FENCE = re.compile("^ {0,3}(?:```|~~~)")
NOTES_SHIPS_LINE = re.compile(f"^ {{0,3}}(?:[-*>#]+ *)?(?:\\*\\*|__)?ships(?:\\*\\*|__)? *:(?:\\*\\*|__)?({_DOT}*)\\Z", _RE)
NOTES_LABEL_LINE = re.compile(f"^ {{0,3}}\\*\\*([^*]+?):\\*\\* *({_DOT}*)\\Z")
NOTES_LABELS = [
    ("headline", re.compile(r"^headline\Z", _RE)),
    ("whatsNew", re.compile("^what['\u2019]s new\\Z", _RE)),
    ("tryIt", re.compile(r"^how to try it\Z", _RE)),
    ("notDone", re.compile("^what isn['\u2019]t done yet\\Z", _RE)),
    ("button", re.compile(r"^button\Z", _RE)),
    ("screenshot", re.compile(r"^screenshots?\Z", _RE)),
    ("byHand", re.compile(r"^by hand\Z", _RE)),
]
STEP_NONE = re.compile(r"^none\.?\Z", _RE)
STEP_BULLET = re.compile(f"^[-*+](?:{_S}+({_DOT}*))?\\Z")
STEP_CONTINUATION = re.compile(f"^(?: {{2,}}|\t)(?![-*+](?:{_S}|\\Z)){_NS}")
STEP_MARKS = [
    ("before", re.compile(f"^before merging *:({_DOT}*)\\Z", _RE)),
    ("check", re.compile(f"^after the deploy, *check *:({_DOT}*)\\Z", _RE)),
    ("after", re.compile(f"^after the deploy *:({_DOT}*)\\Z", _RE)),
]
STEP_NAMES_CRITERION = re.compile(f"\\(([A-Za-z]+-\\d+) +criterion +(\\d+)\\)[.,;:!?]*{_S}*\\Z", _RE)
STEP_NAMES_CRITERION_ANYWHERE = re.compile(r"\([A-Za-z]+-\d+ +criterion +\d+\)", _RE)
STEP_LINK = re.compile(f"\\[([^\\]]*)\\]\\(([^(){JS_SPACE}]*(?:\\([^(){JS_SPACE}]*)?)\\)")
STEP_ADDRESS = re.compile(f"\\b[a-z][a-z0-9+.-]*://[^{JS_SPACE}<>\"'`]+", _RE)
STEP_CONTROL_CHARACTER = re.compile("[\u0000-\u0008\u000b-\u001f\u007f]")
STEP_GITHUB = re.compile(r"^https://github\.com(?=[/?#]|\Z)", _RE)
STEP_MAX = 200
STEPS_MAX = 12
STEP_CRITERION_MAX = 999_999
# `DESK_PATHS` and `isDeskPath` in merge-notes.ts.
STEP_DESK_PATHS = ("/work", "/work/list", "/work/week", "/work/timeline", "/needs-you", "/needs-you/answered", "/apps",
                   "/modules", "/connections", "/settings", "/settings/account", "/settings/sign-in", "/runs")
STEP_ITEM_PATH = re.compile(r"^/work/[A-Za-z]+-\d+\Z", re.A)

def _without_tail(candidate):
    kept = candidate
    while True:
        last = kept[-1:] if kept else ""
        closes = {")": "(", "]": "[", "}": "{"}.get(last)
        if last != "" and last in ".,;:!?*":
            kept = kept[:-1]
        elif closes is not None and kept.count(closes) < kept.count(last):
            kept = kept[:-1]
        else:
            return kept

def _ships_keys(body):
    declared, fenced = [], False
    for line in re.split(r"\r?\n", body):
        if NOTES_FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = NOTES_SHIPS_LINE.match(line)
        if match:
            declared.append(match.group(1))
    keys = []
    for match in re.finditer(r"(?:^|[^A-Za-z0-9])([A-Za-z]+-\d+)(?![A-Za-z0-9])", " ".join(declared), re.A):
        key = match.group(1).upper()
        if key not in keys:
            keys.append(key)
    return keys

def _by_hand_part(body):
    """The By hand part's text, as `readMergeNotes` gives it, or None."""
    lines = re.split(r"\r?\n", body)
    fenced, start, end = False, -1, len(lines)
    for index, line in enumerate(lines):
        if NOTES_FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        if start == -1:
            if NOTES_HEADING.match(line):
                start = index
        elif end == len(lines):
            previous = lines[index - 1] if index > 0 else ""
            if NOTES_ANY_HEADING.match(line):
                end = index
            elif index - 1 > start and NOTES_SETEXT.match(line) and js_trim(previous) != "":
                end = index - 1
    if start == -1:
        return None
    texts, current, fenced = {}, None, False
    for line in lines[start + 1:end]:
        if NOTES_FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        if NOTES_SHIPS_LINE.match(line):
            current = None
            continue
        labelled = NOTES_LABEL_LINE.match(line)
        part = None
        if labelled:
            part = next((name for name, pattern in NOTES_LABELS if pattern.match(js_trim(labelled.group(1)))), None)
        if labelled and part:
            current = None if part in texts else part
            if current:
                texts[current] = [labelled.group(2)]
            continue
        if current:
            texts[current].append(line)
    joined = js_trim("\n".join(texts.get("byHand", [])))
    return joined or None

def _by_hand_step(line, ships):
    bullet = STEP_BULLET.match(line)
    if not bullet:
        return None
    words = js_trim(bullet.group(1) or "")
    marked = next(((when, pattern.match(words)) for when, pattern in STEP_MARKS if pattern.match(words)), None)
    if not marked:
        return None
    when, match = marked
    text = js_trim(match.group(1))
    if text == "" or len(text) > STEP_MAX or STEP_CONTROL_CHARACTER.search(text):
        return None
    key, criterion = ships[0], None
    names = STEP_NAMES_CRITERION.search(text)
    if STEP_NAMES_CRITERION_ANYWHERE.search(text[:names.start()] if names else text):
        return None
    if names:
        named = names.group(1).upper()
        if named not in ships:
            return None
        criterion = int(names.group(2))
        if criterion < 1 or criterion > STEP_CRITERION_MAX:
            return None
        key = named
    rest, at = "", 0
    for link in STEP_LINK.finditer(text):
        rest += text[at:link.start()] + " "
        at = link.end()
        label, href = js_trim(link.group(1)), link.group(2)
        if label == "" or STEP_ADDRESS.search(label):
            return None
        if href.startswith("/"):
            if not (href in STEP_DESK_PATHS or STEP_ITEM_PATH.match(href)):
                return None
        elif not STEP_GITHUB.match(href):
            return None
    rest += text[at:]
    for found in STEP_ADDRESS.finditer(rest):
        if not STEP_GITHUB.match(_without_tail(found.group(0))):
            return None
    return {"when": when, "text": text, "key": key, "criterion": criterion}

def by_hand_steps(body):
    """The steps a pull request's description leaves by hand, in the order
    written, each {when, text, key, criterion}: the ones the desk's reader
    keeps, and no others. Empty for no description, no section, `None.` or no
    Ships line."""
    if not body:
        return []
    part = _by_hand_part(body)
    if part is None or STEP_NONE.match(part):
        return []
    lines = []
    for raw in part.split("\n"):
        line = js_trim(raw)
        if line == "":
            continue
        if STEP_CONTINUATION.match(raw) and lines:
            lines[-1] += f" {line}"
        else:
            lines.append(line)
    ships = _ships_keys(body)
    if not ships:
        return []
    steps = []
    for line in lines[:STEPS_MAX]:
        step = _by_hand_step(line, ships)
        if step is not None:
            steps.append(step)
    return steps

def step_words_hash(text):
    """The first 16 hex of the SHA-256 of a step's words, as `stepFingerprint`
    hashes them (UTF-8, a lone surrogate as U+FFFD, as TextEncoder writes it)."""
    whole = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return hashlib.sha256(whole.encode("utf-8")).hexdigest()[:16]

def step_keys(steps):
    """Each step's key on the desk without its pull request: (when, criterion
    or None, words hash, occurrence), occurrence counting the steps before it
    with the same timing and words, from 1 (`stepFingerprints`)."""
    seen, keys = {}, []
    for step in steps:
        same = (step["when"], step["text"])
        seen[same] = seen.get(same, 0) + 1
        keys.append((step["when"], step["criterion"], step_words_hash(step["text"]), seen[same]))
    return keys

def desk_step_key(step):
    """A desk step's key, as read_item gives it since Gate 8 pass 2. Raises
    ValueError for a desk that gives none, so it is never read as no match."""
    criterion, words, occurrence = step.get("criterion"), step.get("wordsHash"), step.get("occurrence")
    if ((criterion is not None and (isinstance(criterion, bool) or not isinstance(criterion, int)))
            or not isinstance(words, str) or not re.fullmatch(r"[0-9a-f]{16}", words)
            or isinstance(occurrence, bool) or not isinstance(occurrence, int)):
        raise ValueError("the desk's answer gives no key for its steps (a desk from before they had one)")
    return (step["when"], criterion, words, occurrence)

GH_TIMEOUT_S = 30

def pull_request_body(number):
    """The pull request's description, read with `gh pr view N --json
    body,headRefName` in this repository. Answers (body, None), or (None, why)
    in plain words when gh is missing, is not signed in, cannot find it, or
    answers something else. gh is not handed the desk's variables."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("ALYDASH")}
    try:
        out = subprocess.run(["gh", "pr", "view", str(number), "--json", "body,headRefName"],
                             capture_output=True, text=True, timeout=GH_TIMEOUT_S, cwd=repo_root() or None, env=env)
    except FileNotFoundError:
        return None, "the gh CLI is not installed (https://cli.github.com), so its description was not read"
    except subprocess.TimeoutExpired:
        return None, f"gh took longer than {GH_TIMEOUT_S}s, so its description was not read"
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"gh could not be run ({type(e).__name__}: {e}), so its description was not read"
    said = [line.strip() for line in (out.stderr or "").splitlines() if line.strip()]
    if out.returncode == 4:
        return None, "gh is not signed in (gh auth login), so its description was not read"
    if out.returncode != 0:
        return None, f"gh could not read it ({said[-1] if said else f'gh exited {out.returncode}'})"
    try:
        answer = json.loads(out.stdout)
    except ValueError:
        return None, "gh's answer was not JSON, so its description was not read"
    if not isinstance(answer, dict) or "body" not in answer or not (answer["body"] is None or isinstance(answer["body"], str)):
        return None, "gh's answer has no description in it"
    return answer["body"] or "", None

def cmd_ready(a):
    # ALYDAS-299, Gate 8 (code #3). A pull request's steps land on each item
    # they name, so `ready` reads every key on its Ships line (`--item`, once
    # for each), and a run that names none is refused before anything is read.
    items = []
    for key in a.item or []:
        key = key.strip()
        if key and key not in items:
            items.append(key)
    if not items:
        print("pipeline: ready needs --item KEY, once for each key on the pull request's Ships line; "
              "nothing was read.", file=sys.stderr)
        sys.exit(2)
    path = find_run(a.slug, a.root)
    which = f" of #{a.pr}" if a.pr is not None else ""
    named = ", ".join(items)
    url, token, _ = desk_credentials()

    def unread(why):
        # Redacted before it is made printable, so a token spelled with a
        # character `printable` folds is still the token `redacted` finds.
        line = printable(redacted(f"pipeline: not ready: {why}, so this run does not say the pull request{which} "
                                  f"is ready. Read Needs you on the desk, or fix the desk and run ready again.", token))
        log_event(path, current_phase(load(path)), "escalated", line[:LOG_NOTE_MAX])
        refresh(path)
        print(line, file=sys.stderr)
        sys.exit(READY_UNREAD_EXIT)

    if not url or not token:
        unread(f"no desk is set ({DESK_WAYS}), so the steps on {named} were not read")

    # Gate 8 pass 2. With a pull request named, its own description too: the
    # before-merging steps written there, each to be found on the desk.
    written = []
    if a.pr is not None:
        body, why = pull_request_body(a.pr)
        if why is not None:
            unread(f"the description of #{a.pr} could not be read: {why}")
        steps_written = by_hand_steps(body)
        written = [(step, key) for step, key in zip(steps_written, step_keys(steps_written)) if step["when"] == "before"]
        for step, _ in written:
            if step["key"] not in items:
                items.append(step["key"])
        named = ", ".join(items)

    steps, desk_keys = [], {}
    for item in items:
        query = urllib.parse.urlencode({"item": item})
        try:
            answer = json.loads(desk_request(url, token, "GET", f"{CRITERIA_PATH}?{query}", timeout=HOLD_TIMEOUT_S))
            steps += [(item, step) for step in open_before_steps(answer, a.pr)]
            if written:
                desk_keys[item] = {desk_step_key(step) for step in answer["steps"]
                                   if step["when"] == "before" and step["pullRequest"] == a.pr}
        except Exception as e:
            unread(f"the desk could not be read for the steps on {item} ({refusal_words(e)})")
    unread_steps = [(step["key"], step) for step, key in written if key not in desk_keys.get(step["key"], set())]

    if not steps and not unread_steps:
        line = printable(redacted(f"pipeline: ready: no before-merging step{which} is open on {named}", token))
        log_event(path, current_phase(load(path)), "log", line[:LOG_NOTE_MAX])
        refresh(path)
        print(line)
        return
    count = len(steps) + len(unread_steps)
    on = ", ".join(dict.fromkeys([item for item, _ in steps] + [item for item, _ in unread_steps]))
    lines = [f"pipeline: not ready: {count} before-merging step{'s' if count != 1 else ''}{which} "
             f"still open on {on}, on the desk's Needs you or written in the pull request's description:"]
    lines += [f"  - #{step['pullRequest']} on {item}: {step['text']}" for item, step in steps]
    lines += [f"  - #{a.pr} on {item}: {step['text']} (written in the description; the desk has not read it yet)"
              for item, step in unread_steps]
    lines = [printable(redacted(line, token)) for line in lines]
    lines[1:] = ["  " + line for line in lines[1:]]
    said = "\n".join(lines)
    log_event(path, current_phase(load(path)), "escalated", " ".join(line.strip() for line in lines)[:LOG_NOTE_MAX])
    refresh(path)
    print(said)
    sys.exit(READY_OPEN_EXIT)

# ---------- main ----------

def main():
    ap = argparse.ArgumentParser(prog="pipeline.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default="docs/superpowers", help="artifact root (default docs/superpowers)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init"); s.add_argument("slug"); s.add_argument("--title", required=True); s.add_argument("--goal", required=True)
    s.add_argument("--repo"); s.add_argument("--branch"); s.add_argument("--goal-path"); s.add_argument("--date"); s.add_argument("--force", action="store_true")
    s.add_argument("--hold", type=hold_argument, default=list(INIT_HOLDS), metavar="2|2,8|none",
                   help="the phases that wait for your review on the desk before the run goes on (default 2,8)")
    # ALYDAS-17, S5. What the run may spend before the desk asks whether to go on.
    s.add_argument("--budget", type=budget_argument, metavar="USD",
                   help="what this run may spend, in dollars, before the desk stops it and asks a person whether to "
                        "extend it (default: none, or the app's own on the desk). The desk takes the amount from the "
                        "run's first beat only, so set it before the first boundary.")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("phase"); s.add_argument("slug"); s.add_argument("n", type=int)
    s.add_argument("event", choices=list(EVENT_TO_STATUS)); s.add_argument("--note"); s.add_argument("--force", metavar="REASON")
    s.set_defaults(fn=cmd_phase)

    s = sub.add_parser("note", help="write to a phase's story and record; every call beats"); s.add_argument("slug"); s.add_argument("n", type=int)
    s.add_argument("--summary"); s.add_argument("--next")
    s.add_argument("--plain", help="one sentence a newcomer understands; a phase cannot close without one")
    # ALYDAS-173, S2. Setting the scene.
    s.add_argument("--scene", help="where the story is as the phase starts: what we know coming in, what it answers")
    s.add_argument("--backstory", help="phase 0 only: why this run exists, in everyday terms")
    s.add_argument("--fixed-means", dest="fixed_means", action="append", help="phase 0 only: one thing \"fixed\" means")
    s.add_argument("--wont-change", dest="wont_change", action="append", help="phase 0 only: one thing that will not change")
    for key in ("decided", "measured", "wrong", "overruled", "produced"):
        s.add_argument("--" + key, action="append")
    # Gate 8, ST1 (ALYDAS-212). One recorded file taken off, by its path as recorded.
    s.add_argument("--drop-produced", dest="drop_produced", action="append", metavar="PATH",
                   help="take one recorded file off the phase, by its path exactly as recorded")
    # ALYDAS-173, S5. A produced file named as a document; records the file and beats.
    s.add_argument("--doc", action="append", metavar="PATH|TITLE|ABOUT|IN SHORT",
                   help="name a produced file: its title, a line on what is in it, and a short summary; upserts by path")
    # ALYDAS-173, S3. The Story view's packets.
    s.add_argument("--lede", help="the first paragraph of the phase's story")
    s.add_argument("--number", action="append", metavar="FIGURE|MEANS", help="a figure and what it means")
    s.add_argument("--option", action="append", metavar="NAME|chosen|WHY", help="an option weighed: chosen or rejected, and why")
    s.add_argument("--assumed", action="append", metavar="TEXT|LATER", help="what was assumed, and what came of it later")
    s.add_argument("--left-out", dest="left_out", action="append", help="one thing left out on purpose")
    s.add_argument("--clear", action="append", choices=["decided", "measured", "wrong", "overruled", "produced", "summary", "next", "plain",
                                                              "scene", "backstory", "fixed-means", "wont-change",
                                                              "lede", "numbers", "options", "assumed", "left-out", "problems",
                                                              "steps"])
    s.set_defaults(fn=cmd_note)
    # ALYDAS-173, S4. Tick a phase's step; every call beats.
    s = sub.add_parser("step", help="tick one of a phase's steps: todo, doing, done or dropped, with a line under it; every call beats")
    s.add_argument("slug"); s.add_argument("n", type=int)
    s.add_argument("which", help="the step's number (from 1) or its exact text; with --add, the new step's text")
    s.add_argument("state", nargs="?", choices=list(STEP_STATES), help="todo, doing, done or dropped; one step is doing at a time")
    s.add_argument("--sub", help="the line under the step: what it is doing, what came of it, or why it was dropped (dropped needs it)")
    s.add_argument("--add", action="store_true", help="add a new step with this text")
    s.set_defaults(fn=cmd_step)

    # ALYDAS-173, S3. The run's own story: the codes it uses. S6: and its short
    # version, the band at the top of its desk page. Every call beats.
    s = sub.add_parser("story", help="the run's short version and the codes it uses; every call beats")
    s.add_argument("slug")
    s.add_argument("--term", action="append", metavar="CODE=PLAIN", help="a code and its plain name; a later one for the code replaces it")
    s.add_argument("--happened", action="append", help="one line of what happened, in plain words (up to 8)")
    s.add_argument("--stands", help="where the run stands, in one line")
    s.add_argument("--needs-you", dest="needs_you", action="append", metavar="WHAT|WHY",
                   help="one thing waiting on Sachit, and why (up to 8)")
    s.add_argument("--clear", action="append", choices=["terms", "happened", "stands", "needs-you"])
    s.set_defaults(fn=cmd_story)

    s = sub.add_parser("table"); s.add_argument("slug"); s.add_argument("n", type=int)
    s.add_argument("--columns"); s.add_argument("--row", action="append"); s.add_argument("--replace", action="store_true")
    s.set_defaults(fn=cmd_table)

    s = sub.add_parser("defer"); s.add_argument("slug"); s.add_argument("--item", required=True); s.add_argument("--needs")
    s.set_defaults(fn=cmd_defer)

    # ALYDAS-223, S1. The run says it has no rendered surface.
    s = sub.add_parser("ui-gate", help="skip: record that this run has no rendered surface, and why; "
                                       "Phase 8 refuses the skip when a rendered file changed. "
                                       "check: each of the run's UI gate reports, checked (S2, S5). "
                                       "record: the reports' findings written into Phase 8's table (S6)")
    s.add_argument("action", choices=["skip", "check", "record"]); s.add_argument("slug")
    s.add_argument("--reason", help="skip: why this run has no rendered surface; shown in the run log")
    s.add_argument("--replace", action="store_true",
                   help="record: replace a table Phase 8 already has whose columns are not the record's")
    s.set_defaults(fn=cmd_ui_gate)

    # ALYDAS-174, S1. Gate 8's review.json, recorded on Phase 8 for the desk.
    s = sub.add_parser("review", help="record: Gate 8's measurements/<date>-<slug>-review.json, checked, each changed "
                                      "file's lines counted from the merge-base to HEAD and filed into its area, "
                                      "written on Phase 8; Phase 8 does not close or hold without it")
    s.add_argument("action", choices=["record"]); s.add_argument("slug")
    s.set_defaults(fn=cmd_review)

    s = sub.add_parser("set", help="set one ledger key: holds 2,8|none, budget USD (the desk takes a budget from "
                                   "the run's first beat only), or a scalar key")
    s.add_argument("slug"); s.add_argument("key"); s.add_argument("value"); s.set_defaults(fn=cmd_set)
    s = sub.add_parser("log", help="a line in the run log; every call beats"); s.add_argument("slug"); s.add_argument("message"); s.add_argument("--phase", type=int)
    s.add_argument("--kind", default="log", choices=["log", "dispatched", "landed", "decision", "wrong", "escalated"]); s.set_defaults(fn=cmd_log)
    s = sub.add_parser("usage"); s.add_argument("slug"); s.set_defaults(fn=cmd_usage)
    s = sub.add_parser("render"); s.add_argument("slug"); s.set_defaults(fn=cmd_render)

    s = sub.add_parser("evidence", help="post this run's Gate 7 evidence to the desk")
    s.add_argument("slug"); s.add_argument("--plan", required=True)
    s.set_defaults(fn=cmd_evidence)
    # ALYDAS-175, S2. The run waits on the desk for a person's review of phase n.
    s = sub.add_parser("hold", help="close phase n through a person's review on the desk: raise the question, wait, read the answer")
    s.add_argument("slug"); s.add_argument("n", type=int)
    s.add_argument("--question", help=f"what the person is asked, in one line (up to {ASK_QUESTION_MAX} characters)")
    s.add_argument("--force", metavar="REASON",
                   help="hold even when the desk could not open a file the phase recorded (recorded on the board)")
    s.set_defaults(fn=cmd_hold)
    # ALYDAS-17, S5. The run waits for a person to extend its budget.
    s = sub.add_parser("wait", help="after a command exits 6 (over budget): wait on the desk, beating, until a person "
                                    "extends the budget (exit 0) or ends the run (exit 3); run it in the background")
    s.add_argument("slug"); s.set_defaults(fn=cmd_wait)
    # ALYDAS-299, S5. Before saying a pull request is ready: no before-merging step open on the desk.
    s = sub.add_parser("ready", help="before saying a pull request is ready: read the item's open by-hand steps on the "
                                     "desk; exit 0 when none before merging is open, 3 listing each one, 4 when the "
                                     "desk cannot be read")
    s.add_argument("slug"); s.add_argument("--item", action="append", required=True,
                                           help="an item's key, as on the Ships line; once for each key the pull request ships")
    s.add_argument("--pr", type=int, help="only this pull request's steps; its description is read too, with gh pr view")
    s.set_defaults(fn=cmd_ready)
    s = sub.add_parser("status"); s.add_argument("slug"); s.add_argument("n", type=int, nargs="?"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("check"); s.add_argument("slug"); s.add_argument("n", type=int); s.set_defaults(fn=cmd_check)

    a = ap.parse_args()
    a.fn(a)

if __name__ == "__main__":
    main()
