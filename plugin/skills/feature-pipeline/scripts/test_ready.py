#!/usr/bin/env python3
"""`pipeline.py ready`: the run checks the desk before it says a pull request is ready.

ALYDAS-299, S5 (design D5). A pull request's by-hand steps are on the desk's
Needs you, and a before-merging one is something the person does before the
merge. So before a run tells the person a pull request is ready, it reads the
item's open steps off `read_item` (`GET /api/work/criteria?item=KEY`, which
answers `steps: [{when, text, pullRequest}]` beside the criteria) through the
credential the run reports with, and:

  exit 0 and "ready" when no before-merging step is open (of `--pr N`, if named);
  exit 3 listing each open one;
  exit 4 when the desk cannot be read, so it never says ready without reading.

Every test runs pipeline.py as a subprocess in a scratch git repository under a
temporary directory, against the recording listener test_heartbeat.py uses, so
no test can reach a real desk.

  python3 -m unittest test_ready -v

Python 3.9+, stdlib only.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse

from test_credentials import CREDENTIALS_VARIABLE, NO_RUNS_SCOPES, entry
from test_heartbeat import PIPELINE, PROXY_VARIABLES, TOKEN_VARIABLE, URL_VARIABLE, WORK_ITEM_VARIABLE, Listener, closed_port

SECRET = "r3ady-s3cr3t-never-printed"
ROUTE = "/api/work/criteria"
KEY = "ALYDAS-299"

BEFORE_161 = {"when": "before", "text": "Set ALYDASH_SEALING_KEY on the preview.", "pullRequest": 161}
BEFORE_161_TOO = {"when": "before", "text": "Run the migration on the branch database.", "pullRequest": 161}
BEFORE_170 = {"when": "before", "text": "Delete criterion 1 on ALYDAS-447.", "pullRequest": 170}
AFTER_161 = {"when": "after", "text": "Run /plugin update.", "pullRequest": 161}
CHECK_161 = {"when": "check", "text": "Needs you shows the step.", "pullRequest": 161}


# A fake gh: answers the reply file's exit, stdout and stderr, and logs the
# call. Run by the interpreter the tests run under.
FAKE_GH = '''#!{python}
import json, os, sys
with open({log!r}, "a", encoding="utf-8") as f:
    f.write(json.dumps({{"args": sys.argv[1:], "desk": sorted(n for n in os.environ if n.startswith("ALYDASH"))}}) + "\\n")
with open({reply!r}, encoding="utf-8") as f:
    reply = json.load(f)
sys.stdout.write(reply.get("stdout", ""))
sys.stderr.write(reply.get("stderr", ""))
sys.exit(reply.get("exit", 0))
'''


def pr_view(body, head="alydas-299-by-hand-steps"):
    """What `gh pr view N --json body,headRefName` prints."""
    return {"exit": 0, "stdout": json.dumps({"body": body, "headRefName": head})}


def described(by_hand, ships=KEY):
    """A pull request description with a What you'll notice section."""
    return "\n".join([
        "## What you'll notice", "",
        "**Headline:** Steps", "",
        "**What's new:** A step.", "",
        "**How to try it:** Open Needs you.", "",
        "**What isn't done yet:** Nothing.", "",
        "**Screenshot:** Nothing visible changes. Asks only.", "",
        by_hand, "",
        "## For the reviewer", "",
        f"Ships: {ships}",
    ])


def words_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def on_desk(text, pull_request=161, when="before", criterion=None, occurrence=1, answered=False, shown=None):
    """A step as read_item answers it since Gate 8 pass 2, keyed as the desk keys it."""
    return {"when": when, "text": shown or text, "pullRequest": pull_request, "criterion": criterion,
            "wordsHash": words_hash(text), "occurrence": occurrence, "answered": answered}


def answer(*steps):
    return {"criteria": [{"id": "c1", "ordinal": 1, "text": "A criterion", "met": False}], "steps": list(steps)}


class ReadyHarness(unittest.TestCase):
    """A scratch run, a fake gh first on PATH, and the way to run ready."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        subprocess.run(["git", "-C", self.repo, "init", "-q"], check=True, capture_output=True)
        subprocess.run(["git", "-C", self.repo, "remote", "add", "origin", "https://github.com/sachitsac/alydash.git"],
                       check=True, capture_output=True)
        self.root = os.path.join(self.repo, "docs")
        self.store = os.path.join(self.tmp.name, "credentials.json")
        # Gate 8 pass 2: a fake `gh` first on PATH for every run, so no test
        # can reach the real gh or GitHub. It answers `self.gh_reply`, and logs
        # each call's arguments and whether a desk variable reached it.
        self.gh_dir = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.gh_dir)
        self.gh_log = os.path.join(self.tmp.name, "gh-calls.jsonl")
        self.gh_reply_path = os.path.join(self.tmp.name, "gh-reply.json")
        self.gh_says(pr_view(""))
        with open(os.path.join(self.gh_dir, "gh"), "w", encoding="utf-8") as f:
            f.write(FAKE_GH.format(python=sys.executable, log=self.gh_log, reply=self.gh_reply_path))
        os.chmod(os.path.join(self.gh_dir, "gh"), 0o755)
        self.path = self.gh_dir + os.pathsep + os.environ.get("PATH", "")
        out = self.run_pipeline("init", "scratch", "--title", "Scratch", "--goal", "A scratch run.",
                                "--branch", "s5", "--date", "2026-11-07")
        self.assertEqual(out.returncode, 0, out.stderr)

    def run_pipeline(self, *args, env=None):
        environment = dict(os.environ)
        for name in (URL_VARIABLE, TOKEN_VARIABLE, CREDENTIALS_VARIABLE, WORK_ITEM_VARIABLE,
                     "CLAUDE_CODE_SESSION_ID") + PROXY_VARIABLES:
            environment.pop(name, None)
        environment["PATH"] = self.path
        environment.update(env or {})
        return subprocess.run([sys.executable, PIPELINE, "--root", self.root] + list(args),
                              capture_output=True, text=True, cwd=self.repo, env=environment, timeout=60)

    def gh_says(self, reply):
        with open(self.gh_reply_path, "w", encoding="utf-8") as f:
            json.dump(reply, f)

    def gh_calls(self):
        if not os.path.exists(self.gh_log):
            return []
        with open(self.gh_log, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def stored(self, desk_url, **extra):
        """The plugin's store, holding one live entry for this repository's desk."""
        with open(self.store, "w") as handle:
            json.dump({"entries": [entry(desk_url, token=SECRET, **extra)]}, handle)
        return {CREDENTIALS_VARIABLE: self.store}

    def ready(self, env, *more):
        out = self.run_pipeline("ready", "scratch", "--item", KEY, *more, env=env)
        said = out.stdout + out.stderr
        # Whatever the outcome, the token is never printed.
        self.assertNotIn(SECRET, said)
        return out, said



class ReadyCase(ReadyHarness):
    # ---------- the three outcomes ----------

    def test_an_open_before_merging_step_is_not_ready_and_each_one_is_listed(self):
        with Listener() as desk:
            desk.answer("GET", ROUTE, 200, answer(BEFORE_161, AFTER_161, BEFORE_161_TOO, CHECK_161))
            out, said = self.ready(self.stored(desk.url))
            self.assertEqual(out.returncode, 3, said)
            self.assertIn(BEFORE_161["text"], out.stdout)
            self.assertIn(BEFORE_161_TOO["text"], out.stdout)
            self.assertIn("#161", out.stdout)
            # The after-deploy step and the check are not before merging.
            self.assertNotIn(AFTER_161["text"], said)
            self.assertNotIn(CHECK_161["text"], said)
            self.assertNotRegex(out.stdout, r"(?im)^pipeline: ready\b")
            # Read through the credential the plugin stored, at read_item's route, for the item named.
            self.assertEqual([(r["method"], r["path"]) for r in desk.requests], [("GET", f"{ROUTE}?item={KEY}")])
            self.assertEqual(desk.requests[0]["headers"].get("authorization"), "bearer " + SECRET)

    def test_none_open_is_ready(self):
        with Listener() as desk:
            for name, reply in {
                "no steps at all": answer(),
                "only after the deploy and checks": answer(AFTER_161, CHECK_161),
            }.items():
                with self.subTest(name):
                    desk.clear()
                    desk.answer("GET", ROUTE, 200, reply)
                    out, said = self.ready(self.stored(desk.url))
                    self.assertEqual(out.returncode, 0, said)
                    self.assertRegex(out.stdout, r"(?m)^pipeline: ready\b")
                    self.assertEqual(len(desk.requests), 1)

    def test_with_a_pull_request_named_only_its_own_before_steps_count(self):
        with Listener() as desk:
            desk.answer("GET", ROUTE, 200, answer(BEFORE_170, AFTER_161))
            out, said = self.ready(self.stored(desk.url), "--pr", "161")
            self.assertEqual(out.returncode, 0, said)
            out, said = self.ready(self.stored(desk.url), "--pr", "170")
            self.assertEqual(out.returncode, 3, said)
            self.assertIn(BEFORE_170["text"], out.stdout)
            # Named no pull request, any before step of the item's is open work.
            out, said = self.ready(self.stored(desk.url))
            self.assertEqual(out.returncode, 3, said)

    def test_a_desk_that_cannot_be_read_is_not_ready(self):
        cases = {
            "nothing listening": None,
            "a server error": (500, {"error": "boom"}),
            "refused": (403, {"error": 'This token was not given "Read items" (read:WorkItem).'}),
            "an item that is not here": (404, {"error": "That work item is not here, or this token does not speak for its app."}),
            "a desk from before steps, which answers criteria only": (200, {"criteria": []}),
            "steps of the wrong shape": (200, {"criteria": [], "steps": [{"when": "before"}]}),
            "steps that are not a list": (200, {"criteria": [], "steps": "none"}),
            "a reply that is not JSON": (200, "not an object"),
        }
        for name, reply in cases.items():
            with self.subTest(name):
                with Listener() as desk:
                    url = desk.url
                    if reply is None:
                        url = f"http://127.0.0.1:{closed_port()}"
                    else:
                        desk.answer("GET", ROUTE, *reply)
                    out, said = self.ready(self.stored(url))
                    self.assertEqual(out.returncode, 4, said)
                    self.assertNotRegex(out.stdout, r"(?im)^pipeline: ready\b")
                    self.assertIn("not ready", said)

    def test_no_desk_is_not_ready_and_sends_nothing(self):
        with Listener() as desk:
            for name, env in {
                "no credential at all": {},
                "a URL and no token": {URL_VARIABLE: desk.url},
                "a store for another repository": dict(self.stored(desk.url, repo="someone/else")),
            }.items():
                with self.subTest(name):
                    desk.clear()
                    out, said = self.ready(env)
                    self.assertEqual(out.returncode, 4, said)
                    self.assertEqual(desk.requests, [])

    def test_a_desk_without_runs_is_still_read(self):
        """The reason `ready` reads `desk_credentials` and not `desk_target`: a
        desk without runs beats nothing, and still answers read_item."""
        with Listener() as desk:
            desk.answer("GET", ROUTE, 200, answer(BEFORE_161))
            out, said = self.ready(self.stored(desk.url, scopes=list(NO_RUNS_SCOPES)))
            self.assertEqual(out.returncode, 3, said)
            self.assertEqual(len(desk.requests), 1)

    # ---------- every item the pull request ships (Gate 8, code #3) ----------

    def ready_items(self, env, *args):
        out = self.run_pipeline("ready", "scratch", *args, env=env)
        said = out.stdout + out.stderr
        self.assertNotIn(SECRET, said)
        return out, said

    def test_every_item_named_is_read_and_a_step_open_on_any_of_them_is_not_ready(self):
        """A pull request's steps land on each item they name, so `ready` reads
        every key on the Ships line: a before-merging step open on the second
        item is not ready, though the first has none."""
        other = "ALYDAS-447"
        replies = {KEY: answer(AFTER_161), other: answer(BEFORE_161, BEFORE_170)}
        reply = lambda path: replies[urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["item"][0]]
        with Listener() as desk:
            desk.answer("GET", ROUTE, 200, reply)
            out, said = self.ready_items(self.stored(desk.url), "--item", KEY, "--item", other, "--pr", "161")
            self.assertEqual(out.returncode, 3, said)
            self.assertIn(BEFORE_161["text"], out.stdout)
            self.assertIn(other, out.stdout)
            # Of #161 only: #170's step on the same item is not this pull request's.
            self.assertNotIn(BEFORE_170["text"], said)
            self.assertEqual([r["path"] for r in desk.requests], [f"{ROUTE}?item={KEY}", f"{ROUTE}?item={other}"])

            # None open on either: ready, naming both.
            replies[other] = answer(BEFORE_170)
            desk.clear()
            out, said = self.ready_items(self.stored(desk.url), "--item", KEY, "--item", other, "--pr", "161")
            self.assertEqual(out.returncode, 0, said)
            self.assertRegex(out.stdout, r"(?m)^pipeline: ready\b")
            self.assertIn(KEY, out.stdout)
            self.assertIn(other, out.stdout)
            self.assertEqual(len(desk.requests), 2)

    def test_one_item_the_desk_cannot_read_is_not_ready(self):
        other = "ALYDAS-447"
        with Listener() as desk:
            desk.answer("GET", ROUTE, 200, lambda path: answer() if path.endswith(KEY) else "not an object")
            out, said = self.ready_items(self.stored(desk.url), "--item", KEY, "--item", other, "--pr", "161")
            self.assertEqual(out.returncode, 4, said)
            self.assertIn(other, said)
            self.assertNotRegex(out.stdout, r"(?im)^pipeline: ready\b")

    def test_a_pull_request_with_no_item_named_is_refused_and_sends_nothing(self):
        with Listener() as desk:
            for args in (("--pr", "161"), ("--item", "", "--pr", "161"), ("--item", "  ", "--pr", "161")):
                with self.subTest(args):
                    desk.clear()
                    out, said = self.ready_items(self.stored(desk.url), *args)
                    self.assertEqual(out.returncode, 2, said)
                    self.assertNotRegex(out.stdout, r"(?im)^pipeline: ready\b")
                    self.assertEqual(desk.requests, [])

    # ---------- the token ----------

    def test_the_token_is_never_printed_even_when_the_desk_quotes_it_back(self):
        """Every error path prints through `redacted`: a desk (or anything
        between) that echoes the bearer back in its refusal, in a step's text,
        or a token in the variables rather than the store."""
        with Listener() as desk:
            for name, (status, reply) in {
                "in a refusal": (401, {"error": f"bearer {SECRET} is not a token"}),
                "in a step's text": (200, answer(dict(BEFORE_161, text=f"Paste {SECRET} into Settings."))),
                "in an unreadable reply": (200, {"criteria": [], "steps": [{"when": SECRET}]}),
            }.items():
                with self.subTest(name):
                    desk.clear()
                    desk.answer("GET", ROUTE, status, reply)
                    out, said = self.ready(self.stored(desk.url))
                    self.assertIn(out.returncode, (3, 4), said)
                    out, said = self.ready({URL_VARIABLE: desk.url, TOKEN_VARIABLE: SECRET})
                    self.assertIn(out.returncode, (3, 4), said)

    def test_the_token_never_reaches_the_run_files_either(self):
        """ALYDAS-299, Gate 6. The ledger and board `ready` writes are committed
        with the run, so a token the desk quotes back is redacted there as it
        is on the terminal: in a refusal (the unread line) and in a step's text
        (the open line)."""
        with Listener() as desk:
            for status, reply in ((401, {"error": f"bearer {SECRET} is not a token"}),
                                  (200, answer(dict(BEFORE_161, text=f"Paste {SECRET} into Settings.")))):
                desk.clear()
                desk.answer("GET", ROUTE, status, reply)
                out, said = self.ready(self.stored(desk.url))
                self.assertIn(out.returncode, (3, 4), said)
        written = ""
        for folder, _, names in os.walk(self.root):
            for name in names:
                with open(os.path.join(folder, name), errors="replace") as handle:
                    text = handle.read()
                self.assertNotIn(SECRET, text, os.path.join(folder, name))
                written += text
        # Not vacuous: both lines reached the run's files, redacted.
        self.assertGreaterEqual(written.count("<token>"), 2)

    def test_a_url_the_token_may_not_go_to_is_not_ready_and_the_token_is_not_sent(self):
        out, said = self.ready({URL_VARIABLE: f"http://desk.example/{SECRET}", TOKEN_VARIABLE: SECRET})
        self.assertEqual(out.returncode, 4, said)
        self.assertIn("not ready", said)


class ReadyReadsTheDescriptionCase(ReadyHarness):
    """Gate 8 pass 2 (code #6, W2, W9), Sachit's ruling: with `--pr N`, ready
    also reads the pull request's description (`gh pr view N --json
    body,headRefName`), and a before-merging step written there that is not
    answered on the desk is open, the desk's not having read it yet included."""

    def ready_pr(self, env, *more):
        return self.ready(env, "--pr", "161", *more)

    def test_a_written_step_the_desk_has_not_read_is_not_ready(self):
        self.gh_says(pr_view(described("**By hand:**\n- Before merging: Set the key on the preview.")))
        with Listener() as desk:
            desk.answer("GET", ROUTE, 200, answer())
            out, said = self.ready_pr(self.stored(desk.url))
            self.assertEqual(out.returncode, 3, said)
            self.assertIn("Set the key on the preview.", out.stdout)
            self.assertIn("the desk has not read it yet", out.stdout)
            self.assertNotRegex(out.stdout, r"(?im)^pipeline: ready\b")
        self.assertEqual([call["args"] for call in self.gh_calls()],
                         [["pr", "view", "161", "--json", "body,headRefName"]])
        # The desk's token is not handed to gh.
        self.assertEqual(self.gh_calls()[0]["desk"], [])

    def test_a_written_step_answered_on_the_desk_is_ready_and_one_open_is_listed_once(self):
        text = "Set the key on the preview."
        self.gh_says(pr_view(described(f"**By hand:**\n- Before merging: {text}")))
        with Listener() as desk:
            desk.answer("GET", ROUTE, 200, answer(on_desk(text, answered=True)))
            out, said = self.ready_pr(self.stored(desk.url))
            self.assertEqual(out.returncode, 0, said)
            self.assertRegex(out.stdout, r"(?m)^pipeline: ready\b")

            desk.answer("GET", ROUTE, 200, answer(on_desk(text)))
            out, said = self.ready_pr(self.stored(desk.url))
            self.assertEqual(out.returncode, 3, said)
            self.assertEqual(out.stdout.count(text), 1, out.stdout)
            self.assertNotIn("has not read", out.stdout)

            # Answered for another pull request, or with other words, is not this one.
            for other in (on_desk(text, pull_request=170, answered=True), on_desk("Set the key.", answered=True, shown=text)):
                desk.answer("GET", ROUTE, 200, answer(other))
                out, said = self.ready_pr(self.stored(desk.url))
                self.assertEqual(out.returncode, 3, said)
                self.assertIn("has not read it yet", out.stdout)

    def test_a_step_answered_before_200_other_asks_is_ready_as_read_item_now_answers_it(self):
        """ALYDAS-469, S3 (ALYDAS-476 c2). The step answered on the desk before
        more than 200 other Asks: read_item answered no step for it, so ready
        read it as unread (exit 3). The desk now answers it as route.test.ts's
        case asserts the real criteria route does, and ready exits 0."""
        text = "Set the flag on the preview before merging."
        # The hash route.test.ts reads off the real route for these words.
        self.assertEqual(words_hash(text), "9833ad54e9696a95")
        self.gh_says(pr_view(described(f"**By hand:**\n- Before merging: {text}")))
        with Listener() as desk:
            desk.answer("GET", ROUTE, 200, answer())
            out, said = self.ready(self.stored(desk.url), "--pr", "476")
            self.assertEqual(out.returncode, 3, said)
            self.assertIn("the desk has not read it yet", out.stdout)

            desk.answer("GET", ROUTE, 200, answer(on_desk(text, pull_request=476, answered=True)))
            out, said = self.ready(self.stored(desk.url), "--pr", "476")
            self.assertEqual(out.returncode, 0, said)
            self.assertRegex(out.stdout, r"(?m)^pipeline: ready\b")

    def test_steps_match_by_timing_criterion_words_and_occurrence(self):
        body = described("\n".join([
            "**By hand:**",
            "- Before merging: Twice.",
            "- Before merging: Twice.",
            "- After the deploy: Twice.",
            "- Before merging: Set",
            "  the key. (ALYDAS-447 criterion 2)",
        ]), ships=f"{KEY}, ALYDAS-447")
        self.gh_says(pr_view(body))
        other = "ALYDAS-447"
        joined = "Set the key. (ALYDAS-447 criterion 2)"
        replies = {
            KEY: answer(on_desk("Twice.", answered=True), on_desk("Twice.", when="after", answered=True)),
            other: answer(on_desk(joined, criterion=2, answered=True, shown="Set the key.")),
        }
        reply = lambda path: replies[urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["item"][0]]
        with Listener() as desk:
            desk.answer("GET", ROUTE, 200, reply)
            out, said = self.ready_pr(self.stored(desk.url))
            # The second "Twice." is not on the desk; the after one does not stand in for it.
            self.assertEqual(out.returncode, 3, said)
            self.assertEqual(out.stdout.count("has not read it yet"), 1, out.stdout)
            self.assertIn(f"on {KEY}: Twice.", out.stdout)
            # The step naming ALYDAS-447 was read off that item, though --item named only the first.
            self.assertEqual([r["path"] for r in desk.requests], [f"{ROUTE}?item={KEY}", f"{ROUTE}?item={other}"])

            replies[KEY] = answer(on_desk("Twice.", answered=True), on_desk("Twice.", occurrence=2, answered=True))
            desk.clear()
            out, said = self.ready_pr(self.stored(desk.url))
            self.assertEqual(out.returncode, 0, said)

            # The criterion is part of the key: the same words for criterion 3 are not it.
            replies[other] = answer(on_desk(joined, criterion=3, answered=True))
            out, said = self.ready_pr(self.stored(desk.url))
            self.assertEqual(out.returncode, 3, said)

    def test_what_the_reader_drops_or_does_not_count_is_not_open(self):
        for name, by_hand in {
            "None.": "**By hand:** None.",
            "after the deploy only": "**By hand:**\n- After the deploy: Run /plugin update.\n- After the deploy, check: It shows.",
            "a mark that is not one": "**By hand:**\n- During the deploy: watch it.",
            "a link off the desk": "**By hand:**\n- Before merging: Open [it](https://evil.example/x).",
            "a criterion named mid-step": f"**By hand:**\n- Before merging: Do (ALYDAS-299 criterion 1) then this.",
            "a key not on the Ships line": "**By hand:**\n- Before merging: Do it. (ALYDAS-9 criterion 1)",
            "too long": "**By hand:**\n- Before merging: " + "x" * 201,
            "a fenced example": "**By hand:**\n```\n- Before merging: An example.\n```\nNone.",
        }.items():
            with self.subTest(name):
                self.gh_says(pr_view(described(by_hand)))
                with Listener() as desk:
                    desk.answer("GET", ROUTE, 200, answer())
                    out, said = self.ready_pr(self.stored(desk.url))
                    self.assertEqual(out.returncode, 0, said)
        for name, body in {"no section": f"Ships: {KEY}", "no body": None, "empty": ""}.items():
            with self.subTest(name):
                self.gh_says(pr_view(body))
                with Listener() as desk:
                    desk.answer("GET", ROUTE, 200, answer())
                    out, said = self.ready_pr(self.stored(desk.url))
                    self.assertEqual(out.returncode, 0, said)

    def test_gh_missing_signed_out_or_the_pull_request_not_found_is_not_ready(self):
        cases = {
            "not signed in": {"exit": 4, "stderr": "To get started with GitHub CLI, please run:  gh auth login\n"},
            "not found": {"exit": 1, "stderr": "GraphQL: Could not resolve to a PullRequest with the number of 161.\n"},
            "not JSON": {"exit": 0, "stdout": "a pull request"},
            "no body field": {"exit": 0, "stdout": json.dumps({"headRefName": "x"})},
        }
        with Listener() as desk:
            desk.answer("GET", ROUTE, 200, answer())
            for name, reply in cases.items():
                with self.subTest(name):
                    desk.clear()
                    self.gh_says(reply)
                    out, said = self.ready_pr(self.stored(desk.url))
                    self.assertEqual(out.returncode, 4, said)
                    self.assertNotRegex(out.stdout, r"(?im)^pipeline: ready\b")
                    self.assertIn("not ready", said)
                    self.assertIn("#161", said)
                    self.assertEqual(len(said.strip().splitlines()), 1, said)
            with self.subTest("gh is not installed"):
                bare = os.path.join(self.tmp.name, "bare")
                os.makedirs(bare)
                os.symlink(shutil.which("git"), os.path.join(bare, "git"))
                self.path = bare
                out, said = self.ready_pr(self.stored(desk.url))
                self.assertEqual(out.returncode, 4, said)
                self.assertIn("gh", said)
                self.assertNotRegex(out.stdout, r"(?im)^pipeline: ready\b")

    def test_without_a_pull_request_gh_is_not_asked(self):
        with Listener() as desk:
            desk.answer("GET", ROUTE, 200, answer(on_desk("Answered.", answered=True)))
            out, said = self.ready(self.stored(desk.url))
            self.assertEqual(out.returncode, 0, said)
        self.assertEqual(self.gh_calls(), [])

    def test_a_desk_that_gives_no_key_for_its_steps_is_not_ready(self):
        self.gh_says(pr_view(described("**By hand:**\n- Before merging: Set the key.")))
        with Listener() as desk:
            # A step of #161 with when, text and pull request only.
            desk.answer("GET", ROUTE, 200, answer(BEFORE_161))
            out, said = self.ready_pr(self.stored(desk.url))
            self.assertEqual(out.returncode, 4, said)
            self.assertIn("no key for its steps", said)

    def test_the_token_written_in_the_description_is_never_printed_or_written(self):
        self.gh_says(pr_view(described(f"**By hand:**\n- Before merging: Paste {SECRET} into Settings.")))
        with Listener() as desk:
            desk.answer("GET", ROUTE, 200, answer())
            out, said = self.ready_pr(self.stored(desk.url))
            self.assertEqual(out.returncode, 3, said)
            self.assertIn("<token>", out.stdout)
        for folder, _, names in os.walk(self.root):
            for name in names:
                with open(os.path.join(folder, name), errors="replace") as handle:
                    self.assertNotIn(SECRET, handle.read(), os.path.join(folder, name))


class ByHandReaderCase(unittest.TestCase):
    """The port of the merge notes reader's step rules."""

    def setUp(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(PIPELINE)))
        import pipeline
        self.pipeline = pipeline

    def test_it_reads_the_steps_the_desk_reads(self):
        body = described("\n".join([
            "**By hand:**",
            "- Before merging: Open [Needs you](/needs-you) and [the run](https://github.com/sachitsac/alydash/actions/runs/1).",
            "- After the deploy: Run it.",
            "- After the deploy, check: It shows. (ALYDAS-447 criterion 3).",
            "-  Before merging:   Wrapped",
            "\tonto a tab line.",
            "  - Before merging: An indented bullet is its own step.",
        ]), ships=f"**{KEY}** and `ALYDAS-447`")
        steps = self.pipeline.by_hand_steps(body)
        self.assertEqual([(s["when"], s["text"], s["key"], s["criterion"]) for s in steps], [
            ("before", "Open [Needs you](/needs-you) and [the run](https://github.com/sachitsac/alydash/actions/runs/1).", KEY, None),
            ("after", "Run it.", KEY, None),
            ("check", "It shows. (ALYDAS-447 criterion 3).", "ALYDAS-447", 3),
            ("before", "Wrapped onto a tab line.", KEY, None),
            ("before", "An indented bullet is its own step.", KEY, None),
        ])

    def test_a_criterion_past_the_bound_and_a_thirteenth_step_are_dropped(self):
        lines = ["**By hand:**", "- Before merging: Big. (ALYDAS-299 criterion 1000000)",
                 "- Before merging: Edge. (ALYDAS-299 criterion 999999)"]
        lines += [f"- After the deploy: Step {n}." for n in range(3, 15)]
        steps = self.pipeline.by_hand_steps(described("\n".join(lines)))
        self.assertEqual(steps[0]["text"], "Edge. (ALYDAS-299 criterion 999999)")
        self.assertEqual(len(steps), 11)
        self.assertEqual(steps[-1]["text"], "Step 12.")


class OpenBeforeStepsCase(unittest.TestCase):
    """The pure filter `ready` decides with."""

    def setUp(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(PIPELINE)))
        import pipeline
        self.pipeline = pipeline

    def test_it_keeps_the_before_steps_in_the_order_the_desk_gave_them(self):
        steps = self.pipeline.open_before_steps(answer(BEFORE_170, AFTER_161, BEFORE_161, CHECK_161))
        self.assertEqual(steps, [BEFORE_170, BEFORE_161])
        self.assertEqual(self.pipeline.open_before_steps(answer(BEFORE_170, BEFORE_161), 161), [BEFORE_161])
        self.assertEqual(self.pipeline.open_before_steps(answer(AFTER_161, CHECK_161)), [])

    def test_an_answer_it_cannot_read_raises_rather_than_reading_as_none(self):
        for reply in ({"criteria": []}, {"steps": None}, {"steps": [{"when": "before", "text": "x"}]},
                      {"steps": [{"when": "later", "text": "x", "pullRequest": 1}]},
                      {"steps": [{"when": "before", "text": "x", "pullRequest": "1"}]},
                      {"steps": [{"when": "before", "text": "x", "pullRequest": True}]},
                      # Gate 6: a step with no text, or text that is not words.
                      {"steps": [{"when": "before", "pullRequest": 1}]},
                      {"steps": [{"when": "before", "text": 5, "pullRequest": 1}]}, [], "x", None):
            with self.subTest(reply=reply):
                with self.assertRaises(ValueError):
                    self.pipeline.open_before_steps(reply)


if __name__ == "__main__":
    unittest.main()
