#!/usr/bin/env python3
"""`pipeline.py evidence`, tested against a stub poster.

Every test runs pipeline.py as a subprocess in a scratch repository under a
temporary directory, so no test can reach a real desk or a real board. The
poster is a package script this file writes, because the unit under test is the
wiring - discovery, argument passing, output capture, redaction, the ledger
entry and the exit code - and the poster itself is a boundary whose behaviour is
proved against a real desk elsewhere.

  python3 -m unittest test_evidence -v

Python 3.9+, stdlib only.
"""
import json
import glob
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.join(HERE, "pipeline.py")

URL_VARIABLE = "ALYDASH_URL"
TOKEN_VARIABLE = "ALYDASH_SESSION_TOKEN"
SECRET = "sup3r-s3cr3t-do-not-print"
LOG_NOTE_MAX = 600  # pipeline.py's, which the cut test below finds out for itself


class EvidenceCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="evidence-")
        os.makedirs(os.path.join(self.dir, "scripts"), exist_ok=True)
        with open(os.path.join(self.dir, "plan.json"), "w") as handle:
            handle.write("{}")
        self.run_pipeline("init", "wiring", "--title", "Wiring", "--goal", "prove it")

    def run_pipeline(self, *args, env=None):
        environment = dict(os.environ)
        for name in (URL_VARIABLE, TOKEN_VARIABLE):
            environment.pop(name, None)
        environment.update(env or {})
        return subprocess.run([sys.executable, PIPELINE, *args], cwd=self.dir,
                              capture_output=True, text=True, env=environment)

    def with_poster(self, command):
        """A repository carrying a poster, run through its own package script."""
        open(os.path.join(self.dir, "scripts", "post-evidence.ts"), "w").close()
        with open(os.path.join(self.dir, "package.json"), "w") as handle:
            json.dump({"name": "scratch", "scripts": {"post-evidence": command}}, handle)

    def events(self):
        found = glob.glob(os.path.join(self.dir, "docs", "superpowers", "boards", "*-wiring.events.jsonl"))
        with open(found[0]) as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def evidence(self, **env):
        return self.run_pipeline("evidence", "wiring", "--plan", "plan.json", env=env)

    # ---- the three ways it does not post -------------------------------

    def test_a_repository_with_no_poster_does_nothing(self):
        """Most repositories have never heard of a desk, and are unaffected."""
        answer = self.evidence(**{URL_VARIABLE: "https://example.invalid", TOKEN_VARIABLE: SECRET})
        self.assertEqual(answer.returncode, 0)
        self.assertIn("no scripts/post-evidence.ts", answer.stdout)
        self.assertNotIn("landed", json.dumps(self.events()))

    def test_a_poster_with_no_credentials_says_so_out_loud(self):
        """Not silent: this is a run that could have posted and did not."""
        self.with_poster("echo should not run")
        answer = self.evidence()
        self.assertEqual(answer.returncode, 0, answer.stderr)
        self.assertIn("is unset", answer.stderr)
        self.assertNotIn("should not run", answer.stdout)

    def test_a_missing_plan_is_refused_and_says_whose_job_it_is(self):
        self.with_poster("echo should not run")
        answer = self.run_pipeline("evidence", "wiring", "--plan", "nope.json",
                                   env={URL_VARIABLE: "https://example.invalid", TOKEN_VARIABLE: SECRET})
        self.assertNotEqual(answer.returncode, 0)
        self.assertIn("it is yours to write", answer.stderr + answer.stdout)
        self.assertNotIn("should not run", answer.stdout)

    # ---- and the two ways it does ---------------------------------------

    def test_a_poster_that_succeeds_lands_on_the_ledger(self):
        self.with_poster("echo attached 1 screenshot and ticked 2 criteria")
        answer = self.evidence(**{URL_VARIABLE: "https://example.invalid", TOKEN_VARIABLE: SECRET})
        self.assertEqual(answer.returncode, 0, answer.stderr)
        self.assertIn("ticked 2 criteria", answer.stdout)
        landed = [e for e in self.events() if e.get("event") == "landed"]
        self.assertEqual(len(landed), 1, self.events())
        self.assertEqual(landed[0]["phase"], 7)
        self.assertIn("ticked 2 criteria", landed[0]["note"])

    def test_a_poster_that_fails_is_loud_and_answers_non_zero(self):
        """The heartbeat fails quiet; this must not. Evidence that goes nowhere
        is the whole feature failing in exactly the way it exists to stop."""
        self.with_poster("echo the desk refused this; exit 1")
        answer = self.evidence(**{URL_VARIABLE: "https://example.invalid", TOKEN_VARIABLE: SECRET})
        self.assertNotEqual(answer.returncode, 0)
        both = answer.stdout + answer.stderr
        self.assertIn("the desk refused this", both)
        self.assertIn("record the failure", both)
        escalated = [e for e in self.events() if e.get("event") == "escalated"]
        self.assertEqual(len(escalated), 1, self.events())
        self.assertIn("could not post", escalated[0]["note"])

    # ---- and the thing that must never happen ---------------------------

    def test_the_credential_is_never_printed_however_the_poster_prints_it(self):
        """A poster that prints the token must not put it in a terminal or a
        board file. Checked in both, because the ledger is the copy that lasts."""
        # Not an f-string: the poster is shell, and the braces are the shell's.
        self.with_poster('echo "leaked ${ALYDASH_SESSION_TOKEN} here"; exit 1')
        answer = self.evidence(**{URL_VARIABLE: "https://example.invalid", TOKEN_VARIABLE: SECRET})
        self.assertNotIn(SECRET, answer.stdout)
        self.assertNotIn(SECRET, answer.stderr)
        self.assertIn("<token>", answer.stdout + answer.stderr)
        self.assertNotIn(SECRET, json.dumps(self.events()))

    def test_a_token_the_log_note_is_cut_across_leaves_no_part_of_itself_behind(self):
        """Gate 8 (ALYDAS-245), C-5. The note was cut to its 600 characters and
        then redacted, so a token lying across the cut was no longer the whole
        token, was not found, and its first half stayed in the ledger."""
        # The package manager can print its own lines ahead of the poster's
        # (its first run in a folder installs, and says so), so the first run
        # is thrown away, the second finds where the poster's output starts in
        # the note, and the third pads it to put the token's middle on the
        # 600th character. Where the token landed is then measured, not trusted.
        def poster(script):
            with open(os.path.join(self.dir, "poster.sh"), "w") as handle:
                handle.write(script)
            self.with_poster("sh poster.sh")
            self.evidence(**{URL_VARIABLE: "https://example.invalid", TOKEN_VARIABLE: SECRET})
            return [e for e in self.events() if e.get("event") == "landed"][-1]["note"]

        poster("echo the first run\n")
        start = poster("echo OUTPUT-STARTS-HERE\n").index("OUTPUT-STARTS-HERE")
        padding = "x" * (LOG_NOTE_MAX - start - len(SECRET) // 2)
        note = poster(f'printf "%s%s%s\\n" "{padding}" "$ALYDASH_SESSION_TOKEN" "{"y" * 50}"\n')
        self.assertEqual(len(note), LOG_NOTE_MAX, "the note was not cut at 600, so the token is not on the cut")
        kept = LOG_NOTE_MAX - (note.index(padding) + len(padding))
        self.assertTrue(8 <= kept < len(SECRET), f"the token is not across the cut: {kept} characters past the padding")
        self.assertEqual(note[-kept:], ("<token>" + "y" * 50)[:kept])
        self.assertNotIn(SECRET[:8], json.dumps(self.events()))

    def test_the_log_note_keeps_every_line_rather_than_guessing_one(self):
        """A package manager prints a banner around the poster, and it is not
        always last. Picking a line would put `Done in 363ms` on the board on
        the day it mattered."""
        # The lines are ARITHMETIC, not literals. A package manager echoes the
        # command it is about to run, so any literal in the poster's script also
        # appears in the banner - and a first version of this test asserted two
        # literals, passed for the last-line heuristic it was written to catch,
        # and was only found by mutating the code under it. 42 and 64 appear in
        # the output and nowhere in the command.
        self.with_poster("echo $((6*7)); echo $((8*8))")
        self.evidence(**{URL_VARIABLE: "https://example.invalid", TOKEN_VARIABLE: SECRET})
        note = [e for e in self.events() if e.get("event") == "landed"][0]["note"]
        self.assertIn("42", note)
        self.assertIn("64", note)


STAND_IN_RESOLVER = """\
import os, socket
_real = socket.getaddrinfo
def _fake(host, port, *rest, **named):
    name = host.decode() if isinstance(host, bytes) else str(host)
    if name.lower().endswith(".localhost"):
        with open(os.environ["RESOLVER_LOG"], "a") as log:
            log.write(name + "\\n")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.10", port or 0))]
    return _real(host, port, *rest, **named)
socket.getaddrinfo = _fake
"""


class PosterAddressCase(unittest.TestCase):
    """ALYDAS-381, Gate 8. The poster is a program of its own, in whichever
    repository the run is in, and it asks its own resolver for whatever
    ALYDASH_URL names. So for a *.localhost desk the skill hands it
    http://127.0.0.1:<port>, the machine RFC 6761 6.3 says the name is, and the
    name never reaches a resolver on the way to the poster's bearer. Any other
    desk is handed over as it was given. A stand-in resolver (a sitecustomize
    on PYTHONPATH) answers every *.localhost name with 192.0.2.10 and writes
    down each ask; the poster writes down the address it was handed."""

    # EvidenceCase's scratch repository, without running its tests twice.
    setUp = EvidenceCase.setUp
    run_pipeline = EvidenceCase.run_pipeline
    with_poster = EvidenceCase.with_poster
    evidence = EvidenceCase.evidence

    def handed(self, url):
        seen = os.path.join(self.dir, "seen.txt")
        log = os.path.join(self.dir, "resolver.log")
        site = os.path.join(self.dir, "site")
        os.makedirs(site, exist_ok=True)
        with open(os.path.join(site, "sitecustomize.py"), "w") as handle:
            handle.write(STAND_IN_RESOLVER)
        for path in (seen, log):
            open(path, "w").close()
        self.with_poster(f'printf "%s" "$ALYDASH_URL" > "{seen}"; echo posted')
        answer = self.evidence(**{URL_VARIABLE: url, TOKEN_VARIABLE: SECRET, "RESOLVER_LOG": log,
                                  "PYTHONPATH": site})
        self.assertEqual(answer.returncode, 0, answer.stdout + answer.stderr)
        with open(seen) as handle, open(log) as asked:
            return handle.read(), asked.read()

    def test_a_localhost_desk_is_handed_to_the_poster_as_127_0_0_1(self):
        for url, expected in (("http://desk.localhost:4700", "http://127.0.0.1:4700"),
                              ("http://Desk.LocalHost:4700/base?x=1", "http://127.0.0.1:4700/base?x=1"),
                              ("http://us3r@desk.localhost:4700", "http://127.0.0.1:4700"),
                              ("http://desk.localhost", "http://127.0.0.1")):
            with self.subTest(url):
                self.assertEqual(self.handed(url), (expected, ""))

    def test_any_other_desk_is_handed_over_as_it_was_given(self):
        for url in ("https://desk.example.com/base", "https://desk.localhost:4700", "http://localhost:3000",
                    "http://127.0.0.1:3000", "http://[::1]:3000"):
            with self.subTest(url):
                self.assertEqual(self.handed(url), (url, ""))


if __name__ == "__main__":
    unittest.main()
