#!/usr/bin/env python3
"""The plugin's credential file, read when the session token is not exported.

ALYDAS-18, S5. Inside a Claude Code session the alydash plugin writes the path
of its credential store into ALYDASH_CREDENTIALS. With ALYDASH_SESSION_TOKEN
unset or empty, pipeline.py takes the token of the entry whose `repo` is this
repository's GitHub origin, and sends it only to the entry's `desk`: an
ALYDASH_URL naming another desk leaves the entry unused (Gate 8, D1).

Every test runs pipeline.py as a subprocess in a scratch git repository under a
temporary directory, against the recording listener test_heartbeat.py uses, so
no test can reach a real desk.

  python3 -m unittest test_credentials -v

Python 3.9+, stdlib only.
"""
import datetime as dt
import importlib.util
import inspect
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
from unittest import mock

from test_heartbeat import (PIPELINE, PROXY_VARIABLES, TOKEN_VARIABLE, URL_VARIABLE, WORK_ITEM_VARIABLE,
                            DeskAbsent, Listener, closed_port, desk_constant, pipeline_module)

CREDENTIALS_VARIABLE = "ALYDASH_CREDENTIALS"
SECRET = "cr3d-s3cr3t-never-printed"
ENV_SECRET = "env-t0k3n"
REPO = "Sachitsac/Alydash"
# ALYDAS-379, S5. What the plugin asks a desk without runs for, and the one
# scope that desk does not grant (the plugin's SCOPES, `lib/credentials.mjs`).
HEARTBEAT_SCOPE = "heartbeat:AgentSession"
NO_RUNS_SCOPES = ("raise:Ask", "read:WorkItem", "attach:Attachment", "tick:Criterion")


def iso(days):
    """An ISO instant `days` from now, in UTC with a Z, the shape the plugin writes."""
    when = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def entry(desk, repo=REPO, token=SECRET, days=30, **extra):
    return dict({"desk": desk, "appId": "app_1", "appName": "alydash", "repo": repo,
                 "token": token, "expiresAt": iso(days)}, **extra)


class CredentialsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        self.git("init", "-q")
        self.origin("https://github.com/sachitsac/alydash.git")
        self.root = os.path.join(self.repo, "docs")
        self.store = os.path.join(self.tmp.name, "credentials.json")
        out = self.run_pipeline("init", "scratch", "--title", "Scratch", "--goal", "A scratch run.",
                                "--branch", "s5", "--date", "2026-09-29")
        self.assertEqual(out.returncode, 0, out.stderr)

    def git(self, *args):
        subprocess.run(["git", "-C", self.repo] + list(args), check=True, capture_output=True)

    def origin(self, url):
        subprocess.run(["git", "-C", self.repo, "remote", "remove", "origin"], capture_output=True)
        self.git("remote", "add", "origin", url)

    def write_store(self, *entries):
        with open(self.store, "w") as handle:
            json.dump({"entries": list(entries)}, handle)

    def run_pipeline(self, *args, env=None):
        environment = dict(os.environ)
        for name in (URL_VARIABLE, TOKEN_VARIABLE, CREDENTIALS_VARIABLE, WORK_ITEM_VARIABLE,
                     "CLAUDE_CODE_SESSION_ID") + PROXY_VARIABLES:
            environment.pop(name, None)
        environment.update(env or {})
        return subprocess.run([sys.executable, PIPELINE, "--root", self.root] + list(args),
                              capture_output=True, text=True, cwd=self.repo, env=environment)

    def phase(self, env):
        out = self.run_pipeline("phase", "scratch", "0", "start", env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out

    def authorizations(self, desk):
        return [r["headers"].get("authorization") for r in desk.requests]

    # ---------- which token ----------

    def test_the_exported_token_wins_over_the_store(self):
        with Listener() as desk:
            self.write_store(entry(desk.url))
            self.phase({URL_VARIABLE: desk.url, TOKEN_VARIABLE: ENV_SECRET, CREDENTIALS_VARIABLE: self.store})
            self.assertEqual(self.authorizations(desk), ["bearer " + ENV_SECRET])

    def test_the_store_is_read_when_the_token_is_unset_or_empty(self):
        with Listener() as desk:
            self.write_store(entry(desk.url))
            for name, env in {
                "unset": {CREDENTIALS_VARIABLE: self.store},
                "empty": {TOKEN_VARIABLE: "", CREDENTIALS_VARIABLE: self.store},
                "blank": {TOKEN_VARIABLE: "  ", CREDENTIALS_VARIABLE: self.store},
            }.items():
                with self.subTest(name):
                    desk.clear()
                    self.phase(env)
                    self.assertEqual(self.authorizations(desk), ["bearer " + SECRET],
                                     f"{name}: the entry's token did not reach the entry's desk")
                    self.assertEqual(desk.requests[0]["path"], "/api/sessions/heartbeat")

    def test_a_stored_token_goes_only_to_the_desk_it_names(self):
        """Gate 8, D1 and SEC-4 (changed from the S5 test that pinned the
        opposite, `test_an_exported_url_is_the_desk_the_store_token_goes_to`).
        A stored credential was minted on one desk and goes to that desk only:
        an exported ALYDASH_URL naming another desk makes the entry unusable,
        so nothing is sent anywhere. The same desk spelled with a trailing
        slash or another case is still the same desk."""
        with Listener() as named, Listener() as stored:
            self.write_store(entry(stored.url))
            self.phase({URL_VARIABLE: named.url, CREDENTIALS_VARIABLE: self.store})
            self.assertEqual(self.authorizations(named), [], "the stored token went to a desk it was not minted on")
            self.assertEqual(stored.requests, [])
            for spelling in (stored.url + "/", stored.url.replace("http://", "HTTP://")):
                with self.subTest(spelling):
                    stored.clear()
                    self.phase({URL_VARIABLE: spelling, CREDENTIALS_VARIABLE: self.store})
                    self.assertEqual(self.authorizations(stored), ["bearer " + SECRET], spelling)
            # Gate 6 (ALYDAS-245), D5: the two desks above differ by port only,
            # so a rule that ignored the host stayed green on this path. One
            # listener under two host names, the same port: another desk.
            stored.clear()
            self.write_store(entry(f"http://localhost:{stored.port}"))
            self.phase({URL_VARIABLE: stored.url, CREDENTIALS_VARIABLE: self.store})
            self.assertEqual(self.authorizations(stored), [], "a desk on another host was taken for the same desk")

    def test_an_exported_url_picks_its_own_desks_entry_whichever_expires_last(self):
        """Gate 8 (ALYDAS-245), C-2: a person connected to two desks for one
        repository who pins one with ALYDASH_URL reported to neither whenever
        the other desk's entry expired later, because the entry was picked
        before the desk was looked at. The pinned desk's entry is the one
        taken, and the other desk hears nothing. With no URL exported the
        entry that expires last is still the one, and a URL that is neither
        desk's gets no token at all."""
        with Listener() as pinned, Listener() as other, Listener() as neither:
            sooner = entry(pinned.url, token="pinned-" + SECRET, days=1)
            later = entry(other.url, token="other-" + SECRET, days=9)
            both = {URL_VARIABLE: pinned.url, CREDENTIALS_VARIABLE: self.store}
            for name, entries, env, heard in (
                ("the pinned desk's entry expires sooner", (sooner, later), both, pinned),
                ("the same, the other way round in the store", (later, sooner), both, pinned),
                ("only the pinned desk's entry", (sooner,), both, pinned),
                ("no URL exported", (sooner, later), {CREDENTIALS_VARIABLE: self.store}, other),
                ("a URL that is neither desk's", (sooner, later),
                 {URL_VARIABLE: neither.url, CREDENTIALS_VARIABLE: self.store}, None),
            ):
                with self.subTest(name):
                    for desk in (pinned, other, neither):
                        desk.clear()
                    self.write_store(*entries)
                    out = self.phase(env)
                    token = {pinned: "pinned-" + SECRET, other: "other-" + SECRET}.get(heard)
                    for desk in (pinned, other, neither):
                        self.assertEqual(self.authorizations(desk), ["bearer " + token] if desk is heard else [],
                                         f"{name}: what the desk on port {desk.port} heard")
                    self.assertNotIn(SECRET, out.stdout + out.stderr)

    def test_an_exported_token_still_goes_to_the_exported_url_whatever_the_store_says(self):
        """The exported pair is unchanged by D1: it is the person's own, and it
        goes to the desk they exported with it."""
        with Listener() as named, Listener() as stored:
            self.write_store(entry(stored.url))
            self.phase({URL_VARIABLE: named.url, TOKEN_VARIABLE: ENV_SECRET, CREDENTIALS_VARIABLE: self.store})
            self.assertEqual(self.authorizations(named), ["bearer " + ENV_SECRET])
            self.assertEqual(stored.requests, [])

    def test_an_exported_token_alone_is_no_desk_and_an_exported_url_alone_sends_nothing(self):
        """Gate 6 (ALYDAS-245), P3 and P4: the pair is taken whole. A token
        exported with no URL is no desk, whatever the store holds: it is not
        sent to the stored desk, and the stored pair is not used in its place.
        A URL exported with no token and no entry for it hears nothing."""
        with Listener() as named, Listener() as stored:
            self.write_store(entry(stored.url))
            out = self.phase({TOKEN_VARIABLE: ENV_SECRET, CREDENTIALS_VARIABLE: self.store})
            self.assertEqual(self.authorizations(stored), [], "the token alone: something went to the stored desk")
            self.assertNotIn(ENV_SECRET, out.stdout + out.stderr)
            for name, env in {"no store": {URL_VARIABLE: named.url},
                              "a store with no entry for this repository": {
                                  URL_VARIABLE: named.url, CREDENTIALS_VARIABLE: self.store}}.items():
                with self.subTest(name):
                    named.clear()
                    self.write_store(entry(named.url, repo="sachitsac/other"))
                    self.phase(env)
                    self.assertEqual(named.requests, [], f"the URL alone, {name}")
                    self.assertEqual(stored.requests, [])

    def test_a_stored_entry_that_names_no_desk_is_not_used(self):
        """Gate 6 (ALYDAS-245), P5: a stored token goes to the desk its entry
        names, so an entry that names none goes nowhere, least of all to an
        exported ALYDASH_URL."""
        with Listener() as named:
            whole = entry(named.url)
            for name, stale in {
                "no desk": {k: v for k, v in whole.items() if k != "desk"},
                "an empty desk": dict(whole, desk=""),
                "a desk that is not text": dict(whole, desk=None),
            }.items():
                for where, env in {"with a URL exported": {URL_VARIABLE: named.url, CREDENTIALS_VARIABLE: self.store},
                                   "with none": {CREDENTIALS_VARIABLE: self.store}}.items():
                    with self.subTest(f"{name}, {where}"):
                        named.clear()
                        self.write_store(stale)
                        out = self.phase(env)
                        self.assertEqual(self.authorizations(named), [], f"{name}, {where}")
                        self.assertNotIn(SECRET, out.stdout + out.stderr)

    # ---------- which entry ----------

    def test_an_entry_for_another_repository_is_not_configured(self):
        with Listener() as desk:
            self.write_store(entry(desk.url, repo="sachitsac/other"), entry(desk.url, repo="someone/alydash"))
            self.phase({CREDENTIALS_VARIABLE: self.store})
            self.assertEqual(desk.requests, [])
            # Gate 6 (ALYDAS-245), R3: the repository is matched whole. A name
            # that only begins or ends as this one does is another repository,
            # read from either side, for the owner and for the name.
            for repo in ("sachitsac/alydash-two", "sachitsac/alydash/x", "sachitsac/alydas",
                         "my-sachitsac/alydash", "achitsac/alydash"):
                with self.subTest(repo):
                    desk.clear()
                    self.write_store(entry(desk.url, repo=repo))
                    self.phase({CREDENTIALS_VARIABLE: self.store})
                    self.assertEqual(desk.requests, [], f"an entry for {repo} was used in sachitsac/alydash")

    def test_an_expired_entry_is_not_configured(self):
        with Listener() as desk:
            for name, stale in {
                "expired": entry(desk.url, days=-1),
                # Gate 6 (ALYDAS-245), E6: nothing was nearer than a day past.
                "expired five seconds ago": entry(desk.url, days=-5 / 86400),
                "no expiry": {k: v for k, v in entry(desk.url).items() if k != "expiresAt"},
                "unreadable expiry": entry(desk.url, expiresAt="soon"),
            }.items():
                with self.subTest(name):
                    desk.clear()
                    self.write_store(stale)
                    self.phase({CREDENTIALS_VARIABLE: self.store})
                    self.assertEqual(desk.requests, [], f"{name}: a stale entry reached the desk")

    def test_the_live_entry_is_taken_beside_an_expired_one(self):
        with Listener() as desk:
            self.write_store(entry(desk.url, token="old-" + SECRET, days=-2), entry(desk.url))
            self.phase({CREDENTIALS_VARIABLE: self.store})
            self.assertEqual(self.authorizations(desk), ["bearer " + SECRET])

    def test_of_two_live_entries_the_one_that_expires_last_is_taken(self):
        """Gate 6 residual: taking the first live entry, or the one that expires
        first, stayed green, because every other case has one live entry."""
        with Listener() as desk:
            for name, entries in {
                "later second": (entry(desk.url, token="soon-" + SECRET, days=2), entry(desk.url, days=20)),
                "later first": (entry(desk.url, days=20), entry(desk.url, token="soon-" + SECRET, days=2)),
            }.items():
                with self.subTest(name):
                    desk.clear()
                    self.write_store(*entries)
                    self.phase({CREDENTIALS_VARIABLE: self.store})
                    self.assertEqual(self.authorizations(desk), ["bearer " + SECRET], name)

    def test_an_expiry_with_no_zone_is_read_as_utc_wherever_the_run_is(self):
        """Gate 6 residual: reading a zoneless `expiresAt` as local time stayed
        green, because the plugin writes a Z. Taken one hour ahead in UTC under
        a zone fourteen hours ahead, and refused one hour behind under a zone
        twelve hours behind, so either misreading goes red on any machine."""
        def naive(hours):
            when = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)
            return when.strftime("%Y-%m-%dT%H:%M:%S")
        with Listener() as desk:
            for name, hours, zone, taken in (("ahead, east", 1, "Etc/GMT-14", True),
                                             ("behind, west", -1, "Etc/GMT+12", False)):
                with self.subTest(name):
                    desk.clear()
                    self.write_store(entry(desk.url, expiresAt=naive(hours)))
                    self.phase({CREDENTIALS_VARIABLE: self.store, "TZ": zone})
                    self.assertEqual(self.authorizations(desk), ["bearer " + SECRET] if taken else [], name)

    def test_every_github_origin_shape_matches_ignoring_case(self):
        with Listener() as desk:
            self.write_store(entry(desk.url, repo="sachitsac/alydash"))
            for url in ("https://github.com/Sachitsac/Alydash.git", "https://github.com/sachitsac/alydash",
                        "git@github.com:SACHITSAC/alydash.git", "git@github.com:sachitsac/alydash",
                        "ssh://git@github.com/sachitsac/Alydash.git", "https://github.com/sachitsac/alydash/"):
                with self.subTest(url):
                    desk.clear()
                    self.origin(url)
                    self.phase({CREDENTIALS_VARIABLE: self.store})
                    self.assertEqual(self.authorizations(desk), ["bearer " + SECRET], f"{url} did not match")

    def test_an_owner_with_an_underscore_reports_as_the_plugin_connected_it(self):
        """Gate 8 (ALYDAS-245), C-1: GitHub gives an Enterprise Managed User a
        login with an underscore in it. The plugin and the desk read such an
        owner, so `/alydash:connect` files the credential, and the run that
        follows has to find it."""
        with Listener() as desk:
            self.write_store(entry(desk.url, repo="my_org/alydash"))
            for url in ("https://github.com/my_org/alydash.git", "git@github.com:my_org/alydash.git",
                        "ssh://git@github.com/My_Org/alydash"):
                with self.subTest(url):
                    desk.clear()
                    self.origin(url)
                    self.phase({CREDENTIALS_VARIABLE: self.store})
                    self.assertEqual(self.authorizations(desk), ["bearer " + SECRET], f"{url} did not match")

    def test_an_origin_that_is_not_that_github_repository_does_not_match(self):
        with Listener() as desk:
            self.write_store(entry(desk.url, repo="sachitsac/alydash"))
            for url in ("https://gitlab.com/sachitsac/alydash.git", "git@github.com:sachitsac/alydash-two.git",
                        "https://github.com/sachitsac/alydash/extra"):
                with self.subTest(url):
                    desk.clear()
                    self.origin(url)
                    self.phase({CREDENTIALS_VARIABLE: self.store})
                    self.assertEqual(desk.requests, [], f"{url} matched")

    def test_no_origin_or_no_store_is_not_configured(self):
        with Listener() as desk:
            self.write_store(entry(desk.url))
            for name, env in {
                "no such file": {CREDENTIALS_VARIABLE: os.path.join(self.tmp.name, "absent.json")},
                "empty path": {CREDENTIALS_VARIABLE: ""},
            }.items():
                with self.subTest(name):
                    desk.clear()
                    self.phase(env)
                    self.assertEqual(desk.requests, [])
            with open(self.store, "w") as handle:
                handle.write("{not json " + SECRET)
            desk.clear()
            out = self.phase({CREDENTIALS_VARIABLE: self.store})
            self.assertEqual(desk.requests, [])
            self.assertNotIn(SECRET, out.stdout + out.stderr)
            self.write_store(entry(desk.url))
            self.git("remote", "remove", "origin")
            desk.clear()
            self.phase({CREDENTIALS_VARIABLE: self.store})
            self.assertEqual(desk.requests, [], "a repository with no origin matched an entry")

    WRONG_SHAPES = {
        "a list": [SECRET],
        "an empty object": {},
        "entries that are null": {"entries": None, "note": SECRET},
        "entries that are an object": {"entries": {"a": SECRET}},
        "entries that are not objects": {"entries": [SECRET, 3, None, [SECRET]]},
        "an entry whose repo is not text": {"entries": [dict(entry("https://desk.example"), repo=7)]},
        "an entry whose token is not text": {"entries": [dict(entry("https://desk.example"), token=[SECRET])]},
        "an entry whose token is a number": {"entries": [dict(entry("https://desk.example"), token=7)]},
        "an entry whose token is null": {"entries": [dict(entry("https://desk.example"), token=None)]},
    }
    def test_a_store_of_the_wrong_shape_is_no_credential_and_does_not_stop_the_run(self):
        """Gate 6 (ALYDAS-245), S2, S4 and S5: the only store that was not as
        the plugin writes it was text that is not JSON. JSON of another shape
        is no credential either: the boundary closes as it does with no store,
        its Board line the local board, and nothing is said, least of all
        anything the store holds."""
        cases = {name: json.dumps(shape) for name, shape in self.WRONG_SHAPES.items()}
        cases["text that is not JSON"] = "{not json " + SECRET
        cases["a directory where the store should be"] = None
        for name, text in cases.items():
            with self.subTest(name):
                if text is None:
                    os.remove(self.store)
                    os.makedirs(self.store)
                else:
                    with open(self.store, "w") as handle:
                        handle.write(text)
                out = self.phase({CREDENTIALS_VARIABLE: self.store})
                self.assertEqual(out.stderr, "", name)
                self.assertNotIn(SECRET, out.stdout)
                self.assertIn(os.path.join("docs", "boards"), out.stdout, "the boundary block lost its Board line")

    def test_a_good_entry_beside_entries_of_the_wrong_shape_is_still_taken(self):
        """S5 from the other side: what is not an entry is passed over, not a
        reason to drop the store."""
        with Listener() as desk:
            with open(self.store, "w") as handle:
                json.dump({"entries": ["x", 3, None, {"repo": 7}, dict(entry(desk.url), token=7), entry(desk.url)]},
                          handle)
            self.phase({CREDENTIALS_VARIABLE: self.store})
            self.assertEqual(self.authorizations(desk), ["bearer " + SECRET])

    # ---------- never printed ----------

    def test_the_store_token_is_never_printed(self):
        """Neither on a beat the desk takes nor on one refused that quotes the URL."""
        # A stored desk that carries the token as its scheme: the refusal line
        # quotes the URL, so the token is in the message unless it is taken
        # out. `<token>` in the output proves the case reached it. First, so no
        # run id is known yet and the Board line names no desk.
        self.write_store(entry(SECRET + "://127.0.0.1/x"))
        refused = self.phase({CREDENTIALS_VARIABLE: self.store})
        said = refused.stdout + refused.stderr
        self.assertNotIn(SECRET, said)
        self.assertIn("<token>", said, f"the refusal did not quote the URL, so this proves nothing: {said!r}")
        with Listener() as desk:
            self.write_store(entry(desk.url))
            taken = self.phase({CREDENTIALS_VARIABLE: self.store})
            self.assertEqual(len(desk.requests), 1)
            self.assertNotIn(SECRET, taken.stdout + taken.stderr)
            # The phase after the desk named the run prints a Board link to it.
            done = self.run_pipeline("phase", "scratch", "0", "done", "--force", "scratch",
                                     env={CREDENTIALS_VARIABLE: self.store})
            self.assertIn(f"{desk.url}/runs/s1", done.stdout)
            self.assertNotIn(SECRET, done.stdout + done.stderr)

    def test_a_failed_beat_that_quotes_the_stored_desk_prints_the_store_token_taken_out(self):
        """Gate 6 residual: redacting the exported token instead of the one the
        beat was sent with stayed green, because only a refusal quoted a stored
        desk. Here the send itself fails and its message quotes the port."""
        self.write_store(entry("http://127.0.0.1:" + SECRET))
        out = self.phase({CREDENTIALS_VARIABLE: self.store})
        said = out.stdout + out.stderr
        self.assertNotIn(SECRET, said)
        self.assertIn("did not take the heartbeat", out.stderr)
        self.assertIn("<token>", out.stderr, f"the failure did not quote the desk, so this proves nothing: {said!r}")

    # ---------- one reader ----------

    def stray_mentions(self, source):
        """Where `source` names a desk variable, or the store's reader, outside
        desk_credentials: (function, line) pairs, and how many times
        desk_credentials names a variable itself."""
        import ast
        tree = ast.parse(source)
        names = {"URL_VARIABLE", "TOKEN_VARIABLE", "CREDENTIALS_VARIABLE"}
        values = {URL_VARIABLE, TOKEN_VARIABLE, CREDENTIALS_VARIABLE}
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

        def above(node):
            while node in parents:
                node = parents[node]
                yield node

        def enclosing(node):
            return next((n.name for n in above(node) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))),
                        "<module>")

        def harmless(node):
            parent = parents[node]
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                return True  # the constant's own declaration
            if isinstance(node, ast.Constant) and isinstance(parent, ast.Assign) and enclosing(node) == "<module>":
                return True  # and the string it is declared as
            if any(isinstance(n, ast.FormattedValue) for n in above(node)):
                return True  # named in a message
            return isinstance(parent, ast.Dict) and node in parent.keys  # written for the poster, not read

        stray, inside = [], 0
        for node in ast.walk(tree):
            variable = ((isinstance(node, ast.Name) and node.id in names)
                        or (isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in values))
            reader = isinstance(node, ast.Name) and node.id == "stored_credential"
            if not (variable or reader) or harmless(node):
                continue
            if enclosing(node) == "desk_credentials":
                inside += variable
            else:
                stray.append((enclosing(node), f"{node.lineno}: {ast.unparse(parents[node])}"))
        return stray, inside

    def test_no_function_but_desk_credentials_names_a_desk_variable_or_the_stores_reader(self):
        """Gate 6 (ALYDAS-245). The test this replaces looked for six spellings
        of a read, and a second reader written through an alias walked past
        all six. A read has to name the variable however it reaches the
        environment, so this looks for the name: outside desk_credentials the
        three variables are named only where they are declared, in a message,
        or as a key of the environment handed to the poster, and
        `stored_credential` is not called at all.

        What this does not see: a name built from pieces ("ALYDASH_" + "URL"),
        and a dict literal read back. What a reader then does with the pair is
        held by the behaviour tests, one reader at a time."""
        with open(pipeline_module().__file__, encoding="utf-8") as handle:
            source = handle.read()
        stray, inside = self.stray_mentions(source)
        self.assertEqual(stray, [], "a desk variable or the store's reader is named outside desk_credentials")
        self.assertEqual(inside, 3, "desk_credentials should name each of the three variables once")

    def test_a_second_reader_is_seen_through_each_alias_the_breaker_used(self):
        """The guard above, shown to bite: the spellings Gate 6 walked past the
        old one with, each written into `cmd_wait`, and each found there."""
        with open(pipeline_module().__file__, encoding="utf-8") as handle:
            source = handle.read()
        anchor = "def cmd_wait(a):\n"
        self.assertEqual(source.count(anchor), 1)
        before, _ = self.stray_mentions(source)
        for name, line in {
            "a plain read": "os.environ.get(URL_VARIABLE)",
            "a name bound to os.environ": "e = os.environ; e.get(TOKEN_VARIABLE)",
            "a copy of os.environ": "os.environ.copy().get(TOKEN_VARIABLE)",
            "a dict of os.environ": "dict(os.environ)[CREDENTIALS_VARIABLE]",
            "os.getenv by the string": 'os.getenv("ALYDASH_SESSION_TOKEN")',
            "a name for the variable": "v = URL_VARIABLE",
            "the store read directly": 'stored_credential("x")',
        }.items():
            with self.subTest(name):
                stray, _ = self.stray_mentions(source.replace(anchor, f"{anchor}    {line}\n"))
                found = [where for where, _ in stray]
                for where, _ in before:  # none as the file stands
                    found.remove(where)
                self.assertEqual(found, ["cmd_wait"], stray)

    def test_desk_credentials_holds_the_two_lines_that_read_the_url_and_the_token_off_os_environ(self):
        """A line grep, and no more than that: the file has two lines that read
        ALYDASH_URL or ALYDASH_SESSION_TOKEN off `os.environ` by name, and both
        are in desk_credentials. It was titled as if it checked every reader;
        it checks none. The readers are held by their own behaviour tests."""
        pipeline = pipeline_module()
        with open(pipeline.__file__, encoding="utf-8") as handle:
            source = handle.read()
        reads = [line.strip() for line in source.splitlines()
                 if re.search(r"os\.environ(?:\.get\(|\[)\s*(?:TOKEN|URL)_VARIABLE", line)]
        self.assertEqual(len(reads), 2, f"the variables are read outside desk_credentials: {reads}")
        body = source.split("def desk_credentials(", 1)[1].split("\ndef ", 1)[0]
        for line in reads:
            self.assertIn(line, body)

    def test_the_evidence_poster_is_handed_the_stored_credential(self):
        """The poster reads the two variables itself, so a credential from the
        store reaches it through its environment, and its output stays redacted."""
        seen = os.path.join(self.tmp.name, "poster-saw.txt")
        os.makedirs(os.path.join(self.repo, "scripts"), exist_ok=True)
        open(os.path.join(self.repo, "scripts", "post-evidence.ts"), "w").close()
        with open(os.path.join(self.repo, "package.json"), "w") as handle:
            json.dump({"name": "scratch", "scripts": {"post-evidence":
                       f"printf '%s %s' \"$ALYDASH_URL\" \"$ALYDASH_SESSION_TOKEN\" > {seen}; "
                       f"echo posted with \"$ALYDASH_SESSION_TOKEN\""}}, handle)
        with open(os.path.join(self.repo, "plan.json"), "w") as handle:
            handle.write("{}")
        self.write_store(entry("https://desk.example"))
        out = self.run_pipeline("evidence", "scratch", "--plan", "plan.json", env={CREDENTIALS_VARIABLE: self.store})
        with open(seen) as handle:
            self.assertEqual(handle.read(), "https://desk.example " + SECRET)
        self.assertNotIn(SECRET, out.stdout + out.stderr)
        self.assertIn("posted with <token>", out.stdout)

    def test_the_run_link_uses_the_stored_desk(self):
        pipeline = pipeline_module()
        self.write_store(entry("https://desk.example"))
        saved = {k: os.environ.get(k) for k in (URL_VARIABLE, TOKEN_VARIABLE, CREDENTIALS_VARIABLE)}
        here = os.getcwd()
        try:
            os.environ.pop(URL_VARIABLE, None)
            os.environ.pop(TOKEN_VARIABLE, None)
            os.environ[CREDENTIALS_VARIABLE] = self.store
            os.chdir(self.repo)
            self.assertTrue(pipeline.desk_configured())
            self.assertEqual(pipeline.desk_run_link("r_1"), "https://desk.example/runs/r_1")
        finally:
            os.chdir(here)
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    # ---------- ALYDAS-245: `hold` and `wait` read the store, and no message says only the variables do ----------

    HOLD_INTERVAL = "0.2"
    ASK = {"id": "ask-1", "kind": "decide", "question": "q", "answer": None, "answerLabel": None}

    def start(self, *args, env):
        """A command that waits on the desk, as a process this test ends itself."""
        environment = dict(os.environ)
        for name in (URL_VARIABLE, TOKEN_VARIABLE, CREDENTIALS_VARIABLE, WORK_ITEM_VARIABLE,
                     "CLAUDE_CODE_SESSION_ID") + PROXY_VARIABLES:
            environment.pop(name, None)
        environment.update(env, PIPELINE_HOLD_INTERVAL=self.HOLD_INTERVAL)
        proc = subprocess.Popen([sys.executable, PIPELINE, "--root", self.root] + list(args),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                cwd=self.repo, env=environment)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    def ready_to_hold(self):
        """Phase 0 running with all a close asks of it, so `hold` gets as far as the desk."""
        self.phase({})
        specs = os.path.join(self.root, "specs")
        os.makedirs(specs, exist_ok=True)
        with open(os.path.join(specs, "2026-09-29-scratch-goal.md"), "w") as handle:
            handle.write("# Goal\n\nE1. It works. Observed by: a test.\n")
        for args in (("note", "scratch", "0", "--plain", "The run says what it is for."),
                     ("step", "scratch", "0", "2", "done"), ("step", "scratch", "0", "3", "done"),
                     ("step", "scratch", "0", "4", "done")):
            out = self.run_pipeline(*args)
            self.assertEqual(out.returncode, 0, out.stderr)

    def heard(self, proc, desk, method, path, timeout=20):
        """The first request of that kind the desk heard, the command stopped once it has."""
        deadline = time.monotonic() + timeout
        found = []
        while time.monotonic() < deadline and not found and proc.poll() is None:
            found = [r for r in desk.requests if r["method"] == method and r["path"].split("?", 1)[0] == path]
            time.sleep(0.05)
        proc.kill()
        out, err = proc.communicate()
        self.assertTrue(found, f"the desk never heard {method} {path}: exit {proc.returncode}, {out!r} {err!r}")
        self.assertNotIn(SECRET, out + err)
        return found[0]

    def test_hold_asks_on_the_stored_desk_with_the_stored_token(self):
        """`hold` is a reader the plugin's copy never had. With neither variable
        exported and a credential in the store, its question goes to the
        entry's desk rather than falling back to chat."""
        self.ready_to_hold()
        with Listener(routes={("POST", "/api/asks"): (201, {"ask": dict(self.ASK)}),
                              ("GET", "/api/asks"): (200, {"ask": dict(self.ASK)})}) as desk:
            self.write_store(entry(desk.url))
            proc = self.start("hold", "scratch", "0", env={CREDENTIALS_VARIABLE: self.store})
            asked = self.heard(proc, desk, "POST", "/api/asks")
        self.assertEqual(asked["headers"].get("authorization"), "bearer " + SECRET)

    def test_wait_waits_on_the_stored_desk_with_the_stored_token(self):
        """`wait` is the other. With a stored credential it beats to the
        entry's desk and goes on waiting, rather than exiting 4 at once."""
        with Listener() as desk:
            self.write_store(entry(desk.url))
            proc = self.start("wait", "scratch", env={CREDENTIALS_VARIABLE: self.store})
            beat = self.heard(proc, desk, "POST", "/api/sessions/heartbeat")
        self.assertEqual(beat["headers"].get("authorization"), "bearer " + SECRET)

    def settled(self, proc, *desks, timeout=15):
        """The command's exit code and what it said, once it has ended or a
        desk has heard from it; ended here if it is still going by then."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and proc.poll() is None and not any(d.requests for d in desks):
            time.sleep(0.05)
        code = proc.poll()
        proc.kill()
        out, err = proc.communicate()
        return code, out + err

    def test_hold_does_not_ask_another_desk_with_the_stored_token(self):
        """Gate 6 (ALYDAS-245), A8: D1 was pinned for the heartbeat and for
        evidence, and `hold` sends the token itself. An exported ALYDASH_URL
        for one desk and a credential minted on another is no desk to `hold`:
        exit 4, the chat fallback, and neither desk hears a thing."""
        self.ready_to_hold()
        routes = {("POST", "/api/asks"): (201, {"ask": dict(self.ASK)}),
                  ("GET", "/api/asks"): (200, {"ask": dict(self.ASK)})}
        with Listener(routes=routes) as named, Listener(routes=routes) as stored:
            self.write_store(entry(stored.url))
            proc = self.start("hold", "scratch", "0", env={URL_VARIABLE: named.url, CREDENTIALS_VARIABLE: self.store})
            code, said = self.settled(proc, named, stored)
            self.assertEqual(self.authorizations(named), [], "the stored token went to a desk it was not minted on")
            self.assertEqual(stored.requests, [])
        self.assertEqual(code, 4, said)
        self.assertIn("no desk is set", said)
        self.assertNotIn(SECRET, said)

    def test_wait_does_not_wait_on_another_desk_with_the_stored_token(self):
        """The same for `wait`, which asks whether there is a desk and then
        beats: with the credential's desk and the exported one apart it exits
        4 at once and neither desk hears a thing."""
        with Listener() as named, Listener() as stored:
            self.write_store(entry(stored.url))
            proc = self.start("wait", "scratch", env={URL_VARIABLE: named.url, CREDENTIALS_VARIABLE: self.store})
            code, said = self.settled(proc, named, stored)
            self.assertEqual(self.authorizations(named), [], "the stored token went to a desk it was not minted on")
            self.assertEqual(stored.requests, [])
        self.assertEqual(code, 4, said)
        self.assertIn("no desk is set", said)
        self.assertNotIn(SECRET, said)

    # ---------- ALYDAS-379, S5: a desk without runs hears no heartbeat ----------
    #
    # The plugin connects to a desk without runs asking only for the scopes it
    # offers, so the stored entry's `scopes` lacks heartbeat:AgentSession and
    # the desk answers the heartbeat 404. The plugin's hook reads that and does
    # not beat (`sendsHeartbeat`, S2); the run must read it the same way.

    def no_runs(self, desk_url):
        return entry(desk_url, scopes=list(NO_RUNS_SCOPES))

    def test_a_stored_entry_without_the_heartbeat_scope_beats_nothing_and_says_nothing_about_it(self):
        """A phase boundary and a write between boundaries each beat today; on
        a desk without runs neither sends the desk a request, nothing printed
        mentions a heartbeat, and each command still does its work."""
        with Listener(status=404, reply={"error": "Not found."}) as desk:
            self.write_store(self.no_runs(desk.url))
            env = {CREDENTIALS_VARIABLE: self.store}
            for args in (("phase", "scratch", "0", "start"), ("log", "scratch", "a line"),
                         ("note", "scratch", "0", "--plain", "The run says what it is for.")):
                with self.subTest(args[0]):
                    desk.clear()
                    out = self.run_pipeline(*args, env=env)
                    self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                    self.assertEqual([r["path"] for r in desk.requests], [], f"{args[0]} sent the desk a request")
                    self.assertNotIn("heartbeat", (out.stdout + out.stderr).lower())
                    self.assertNotIn(SECRET, out.stdout + out.stderr)

    def test_an_entry_with_no_scopes_or_scopes_of_another_shape_beats_as_it_always_has(self):
        """The same rule as the plugin's `sendsHeartbeat`: only a list of
        strings without the heartbeat's scope keeps the beat back. An entry
        filed before scopes were kept, a malformed value, and a list that has
        the scope all send the one beat they send today."""
        for name, extra in {"no scopes": {}, "scopes null": {"scopes": None},
                            "scopes a string": {"scopes": "raise:Ask"},
                            "scopes not all strings": {"scopes": ["raise:Ask", 1]},
                            "scopes with the heartbeat's": {"scopes": [HEARTBEAT_SCOPE] + list(NO_RUNS_SCOPES)}}.items():
            with self.subTest(name), Listener() as desk:
                self.write_store(entry(desk.url, **extra))
                self.run_pipeline("phase", "scratch", "0", "start", env={CREDENTIALS_VARIABLE: self.store})
                self.assertEqual([r["path"] for r in desk.requests], ["/api/sessions/heartbeat"], name)
                self.assertEqual(self.authorizations(desk), ["bearer " + SECRET])

    def test_a_token_in_the_variables_beats_whatever_the_store_says(self):
        """The variables carry no scopes, so a run given ALYDASH_URL and a
        token beats as today, a store entry without the scope beside it or not."""
        with Listener() as desk:
            self.write_store(self.no_runs(desk.url))
            self.phase({URL_VARIABLE: desk.url, TOKEN_VARIABLE: ENV_SECRET, CREDENTIALS_VARIABLE: self.store})
            self.assertEqual(self.authorizations(desk), ["bearer " + ENV_SECRET])

    def test_a_desk_that_later_adds_runs_is_picked_up_by_connecting_again(self):
        """Connecting again after the desk adds runs replaces the entry with
        one that asked for the heartbeat's scope, and the next write beats."""
        with Listener() as desk:
            self.write_store(self.no_runs(desk.url))
            self.phase({CREDENTIALS_VARIABLE: self.store})
            self.assertEqual([r["path"] for r in desk.requests], [])
            self.write_store(entry(desk.url, scopes=[HEARTBEAT_SCOPE] + list(NO_RUNS_SCOPES)))
            self.run_pipeline("log", "scratch", "connected again", env={CREDENTIALS_VARIABLE: self.store})
            self.assertEqual([r["path"] for r in desk.requests], ["/api/sessions/heartbeat"])

    def test_hold_and_wait_on_a_desk_without_runs_take_the_no_desk_exit(self):
        """Both wait on the desk by beating and reading back, which a desk
        without runs cannot answer: exit 4 at once, the desk hears nothing, and
        the line says this desk has no runs and to ask in chat instead."""
        self.ready_to_hold()
        routes = {("POST", "/api/asks"): (201, {"ask": dict(self.ASK)}),
                  ("GET", "/api/asks"): (200, {"ask": dict(self.ASK)})}
        for name, args in {"hold": ("hold", "scratch", "0"), "wait": ("wait", "scratch")}.items():
            with self.subTest(name), Listener(status=404, reply={"error": "Not found."}, routes=routes) as desk:
                self.write_store(self.no_runs(desk.url))
                proc = self.start(*args, env={CREDENTIALS_VARIABLE: self.store})
                code, said = self.settled(proc, desk)
                self.assertEqual([r["path"] for r in desk.requests], [], f"{name} sent the desk a request")
                self.assertEqual(code, 4, said)
                self.assertIn("has no runs", said)
                self.assertIn("ask in chat", said)
                self.assertNotIn("heartbeat", said.lower())
                self.assertNotIn(SECRET, said)

    def refuse_the_opposite(self, text):
        """Gate 6 (ALYDAS-245), W6 to W8: naming the store is not saying it is
        read. The three message tests below passed a text that named it to say
        it is never read, so they refuse that sentence by its words. Still a
        check of words: another way of saying the opposite would pass."""
        flat = " ".join(text.split()).lower()
        for phrase in ("never read", "only way"):
            self.assertNotIn(phrase, flat)

    def test_hold_and_wait_with_no_desk_name_the_store_as_well_as_the_variables(self):
        """Each used to say no desk is set and name the two variables alone,
        which a plugin session reads as: export a token. Still exit 4, still
        the same opening words, and the store is named beside the variables."""
        self.ready_to_hold()
        for name, args in {"hold": ("hold", "scratch", "0"), "wait": ("wait", "scratch")}.items():
            with self.subTest(name):
                out = self.run_pipeline(*args)
                self.assertEqual(out.returncode, 4, out.stdout + out.stderr)
                self.assertIn("no desk is set", out.stdout)
                for word in (URL_VARIABLE, TOKEN_VARIABLE, "the alydash plugin's store"):
                    self.assertIn(word, out.stdout)
                self.refuse_the_opposite(out.stdout)

    def test_evidence_with_no_desk_names_the_store_as_well_as_the_variables(self):
        """The same for the poster, in both ways it finds no desk: nothing set
        at all, and a stored credential an exported URL makes unusable (D1),
        where "is unset" alone would send a person looking for a variable."""
        self.with_recording_poster()
        self.write_store(entry("https://desk.example"))
        for name, env in {"nothing set": {},
                          "a stored credential for another desk": {
                              URL_VARIABLE: "https://other.example", CREDENTIALS_VARIABLE: self.store}}.items():
            with self.subTest(name):
                out = self.run_pipeline("evidence", "scratch", "--plan", "plan.json", env=env)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertIn("nothing was posted", out.stderr)
                for word in (URL_VARIABLE, TOKEN_VARIABLE, "the alydash plugin's store"):
                    self.assertIn(word, out.stderr)
                self.refuse_the_opposite(out.stderr)

    def test_the_help_and_the_skill_say_a_stored_credential_reports_too(self):
        """The two places a person reads how a run comes to report: `--help`
        and SKILL.md. Neither may say the two variables are the only way, and
        the help may not say that with both unset nothing is sent."""
        said = self.run_pipeline("--help").stdout
        self.assertNotIn("Both unset and nothing is sent", " ".join(said.split()))
        with open(os.path.join(os.path.dirname(os.path.dirname(PIPELINE)), "SKILL.md"), encoding="utf-8") as handle:
            skill = handle.read()
        for name, text in {"--help": said, "SKILL.md": skill}.items():
            with self.subTest(name):
                flat = " ".join(text.split())
                self.assertIn("alydash plugin", flat)
                self.assertIn(CREDENTIALS_VARIABLE, flat)
                self.refuse_the_opposite(flat)

    def test_the_skill_tells_nobody_to_export_a_desk_or_a_token(self):
        """ALYDAS-283, criterion 4. SKILL.md told its reader to set the desk's
        address and a session token in the shell, and its example board line was
        one person's production desk. A run reports with what `/alydash:connect`
        stored, so the skill names neither variable and no desk of its own; the
        help still documents both for a machine with no plugin."""
        with open(os.path.join(os.path.dirname(os.path.dirname(PIPELINE)), "SKILL.md"), encoding="utf-8") as handle:
            skill = " ".join(handle.read().split())
        for word in (URL_VARIABLE, TOKEN_VARIABLE, "export ", "vercel.app"):
            with self.subTest(word):
                self.assertNotIn(word, skill)
        self.assertIn("Nothing is set by hand: `/alydash:connect`, run once in the repository", skill)
        said = self.run_pipeline("--help").stdout
        for word in (URL_VARIABLE, TOKEN_VARIABLE):
            self.assertIn(word, said)

    # ---------- Gate 8: the evidence poster keeps the heartbeat's rules ----------

    def with_recording_poster(self):
        """A poster that writes down what it was handed, and the path it writes to."""
        seen = os.path.join(self.tmp.name, "poster-saw.txt")
        os.makedirs(os.path.join(self.repo, "scripts"), exist_ok=True)
        open(os.path.join(self.repo, "scripts", "post-evidence.ts"), "w").close()
        with open(os.path.join(self.repo, "package.json"), "w") as handle:
            json.dump({"name": "scratch", "scripts": {"post-evidence":
                       f"printf '%s %s' \"$ALYDASH_URL\" \"$ALYDASH_SESSION_TOKEN\" > {seen}"}}, handle)
        with open(os.path.join(self.repo, "plan.json"), "w") as handle:
            handle.write("{}")
        return seen

    def test_the_evidence_poster_is_not_handed_a_stored_token_for_another_desk(self):
        """D1 on the evidence path: the poster is the fourth reader, and a
        stored token must not reach it with a desk it was not minted on."""
        seen = self.with_recording_poster()
        self.write_store(entry("https://desk.example"))
        out = self.run_pipeline("evidence", "scratch", "--plan", "plan.json",
                                env={URL_VARIABLE: "https://other.example", CREDENTIALS_VARIABLE: self.store})
        self.assertFalse(os.path.exists(seen), f"the poster ran: {out.stdout!r} {out.stderr!r}")
        self.assertNotIn(SECRET, out.stdout + out.stderr)

    def test_the_evidence_poster_is_not_handed_a_url_the_heartbeat_would_refuse(self):
        """SEC-4. `evidence` applies url_refusal to the URL it hands over, as the
        heartbeat does, for a stored and for an exported token alike, and says
        so out loud with a non-zero answer, the token taken out."""
        seen = self.with_recording_poster()
        cases = {
            "stored, plain http off this machine": ({CREDENTIALS_VARIABLE: self.store}, SECRET),
            "exported, plain http off this machine": (
                {URL_VARIABLE: "http://desk.example.com", TOKEN_VARIABLE: ENV_SECRET}, ENV_SECRET),
        }
        self.write_store(entry("http://desk.example.com"))
        for name, (env, token) in cases.items():
            with self.subTest(name):
                out = self.run_pipeline("evidence", "scratch", "--plan", "plan.json", env=env)
                said = out.stdout + out.stderr
                self.assertFalse(os.path.exists(seen), f"{name}: the poster ran: {said!r}")
                self.assertNotEqual(out.returncode, 0, said)
                self.assertIn("must be https", out.stderr)
                self.assertNotIn(token, said)
        # Gate 6 (ALYDAS-245), T4: neither URL above holds its token, so the
        # last line could not fail. The refusal quotes the URL's scheme and
        # host (Gate 8, C-4: its path and query are no longer printed, so the
        # token moved out of them): here the host carries the token, and
        # `<token>` in the output proves it was quoted.
        self.write_store(entry(f"http://{SECRET}.example.com"))
        for name, (env, token) in {
            "stored, the token in the desk's host": ({CREDENTIALS_VARIABLE: self.store}, SECRET),
            "exported, the token in the URL's host": (
                {URL_VARIABLE: f"http://{ENV_SECRET}.example.com", TOKEN_VARIABLE: ENV_SECRET}, ENV_SECRET),
        }.items():
            with self.subTest(name):
                out = self.run_pipeline("evidence", "scratch", "--plan", "plan.json", env=env)
                said = out.stdout + out.stderr
                self.assertFalse(os.path.exists(seen), f"{name}: the poster ran: {said!r}")
                self.assertIn("must be https", out.stderr)
                self.assertNotIn(token, said)
                self.assertIn("<token>", out.stderr, f"the refusal did not quote the URL, so this proves nothing: {said!r}")

    def test_a_refused_stored_desk_is_named_by_its_scheme_and_host_and_not_blamed_on_the_variable(self):
        """Gate 8 (ALYDAS-245), C-4: the refusal printed the stored desk whole
        and said ALYDASH_URL must be https, with no such variable exported. A
        user, a password, a path and a query can each be a secret that is not
        the token, so redacting the token does not hide them. On the beat and
        on the evidence step alike."""
        self.with_recording_poster()
        self.write_store(entry("http://us3r:p4ssw0rd@desk.example.com:8080/pr1vate/p4th?k=qu3ry#fr4g"))
        for name, out in {
            "the heartbeat": self.phase({CREDENTIALS_VARIABLE: self.store}),
            "evidence": self.run_pipeline("evidence", "scratch", "--plan", "plan.json",
                                          env={CREDENTIALS_VARIABLE: self.store}),
        }.items():
            with self.subTest(name):
                self.assertIn("so the token was not sent to http://desk.example.com:8080", out.stderr)
                for part in ("us3r", "p4ssw0rd", "pr1vate", "p4th", "qu3ry", "fr4g"):
                    self.assertNotIn(part, out.stdout + out.stderr)
                self.assertIn("the desk's address", out.stderr)
                self.assertNotIn(f"{URL_VARIABLE} must be", out.stderr)
                self.assertIn("the alydash plugin's store", out.stderr)

    # ---------- Gate 8, D3: the Board line prints the desk's origin only ----------

    def test_the_board_line_prints_the_stored_desk_origin_and_the_run_only(self):
        """A path on the stored desk can carry a secret, and the Board line is
        printed at every boundary, so it is `<origin>/runs/<id>` and nothing of
        the path. The secret here is not the token, so redacting the token
        would not hide it."""
        path_secret = "p4th-s3cr3t"
        with Listener() as desk:
            self.write_store(entry(f"{desk.url}/{path_secret}"))
            self.phase({CREDENTIALS_VARIABLE: self.store})
            self.assertEqual(len(desk.requests), 1)
            done = self.run_pipeline("phase", "scratch", "0", "done", "--force", "scratch",
                                     env={CREDENTIALS_VARIABLE: self.store})
            board = [line for line in done.stdout.splitlines() if line.startswith("Board")]
            self.assertEqual(board, [f"Board     {desk.url}/runs/s1"], done.stdout)
            self.assertNotIn(path_secret, done.stdout + done.stderr)

    # ---------- Gate 8, D2: the plugin's origin rule ----------

    def test_every_origin_the_plugin_connects_is_found(self):
        """D2's reproduction: `/alydash:connect` files a credential under these
        origins, and a run in the repository sends its beat with it."""
        with Listener() as desk:
            self.write_store(entry(desk.url, repo="sachitsac/alydash"))
            for url in ("https://sachitsac@github.com/sachitsac/alydash.git",
                        "https://x-access-token:ghp_abc@github.com/sachitsac/alydash.git",
                        "https://www.github.com/sachitsac/alydash.git",
                        "http://github.com/sachitsac/alydash.git"):
                with self.subTest(url):
                    desk.clear()
                    self.origin(url)
                    self.phase({CREDENTIALS_VARIABLE: self.store})
                    self.assertEqual(self.authorizations(desk), ["bearer " + SECRET], f"{url} was not found")


# The plugin's own fixture list and its answers, copied from
# tests/plugin/repo.test.ts, where `normaliseRepo`
# (packages/modules/work/plugin/scripts/lib/repo.mjs) is pinned to core's
# `parseGitHubRepo`. The answers were read off `normaliseRepo` itself with node.
# Gate 8 (ALYDAS-245), C-1: they were read once, on alydas-18-s4, and the
# plugin then took an underscore in an owner (`o_x/r`) while this copy went on
# saying None, so the copy and the rule under it had drifted with every test
# green. OriginRuleCase now reads the answers off the plugin again wherever a
# desk checkout is to hand.
PLUGIN_ORIGINS = [
    ("https://github.com/sachitsac/alydash", "sachitsac/alydash"),
    ("https://github.com/sachitsac/alydash.git", "sachitsac/alydash"),
    ("https://github.com/sachitsac/alydash/", "sachitsac/alydash"),
    ("https://github.com/sachitsac/alydash.git/", "sachitsac/alydash"),
    ("https://www.github.com/sachitsac/alydash", "sachitsac/alydash"),
    ("http://github.com/sachitsac/alydash", "sachitsac/alydash"),
    ("https://github.com/Sachit-Sac/Aly.Dash_2", "Sachit-Sac/Aly.Dash_2"),
    ("https://github.com/o/r.git.git", "o/r.git"),
    ("https://sachitsac:ghp_SECRETSECRET@github.com/sachitsac/alydash.git", "sachitsac/alydash"),
    ("https://x-access-token:ghs_SECRETSECRET@github.com/sachitsac/alydash", "sachitsac/alydash"),
    ("https://ghp_SECRETSECRET@github.com/sachitsac/alydash", "sachitsac/alydash"),
    ("  https://github.com/sachitsac/alydash  ", "sachitsac/alydash"),
    ("https://github.com/sachitsac/alydash?tab=readme", "sachitsac/alydash"),
    ("https://github.com/sachitsac", None),
    ("https://github.com/sachitsac/alydash/tree/main", None),
    ("https://github.com/o/..", None),
    ("https://github.com/o/.", None),
    ("https://github.com/o_x/r", "o_x/r"),
    ("https://gitlab.com/sachitsac/alydash", None),
    ("https://github.com.evil.example/sachitsac/alydash", None),
    ("https://api.github.com/sachitsac/alydash", None),
    ("ftp://github.com/sachitsac/alydash", None),
    ("not a url", None),
    ("", None),
    ("git@github.com:sachitsac/alydash.git", "sachitsac/alydash"),
    ("git@github.com:sachitsac/alydash", "sachitsac/alydash"),
    ("ssh://git@github.com/sachitsac/alydash.git", "sachitsac/alydash"),
    ("ssh://git@github.com/sachitsac/alydash", "sachitsac/alydash"),
    ("git@gitlab.com:sachitsac/alydash.git", None),
    ("ssh://git@gitlab.com/sachitsac/alydash.git", None),
    ("git@github.com:sachitsac/alydash/extra.git", None),
    ("git@github.com:sachitsac/..", None),
    ("git@github.com:o/../evil/r", None),
    ("ssh://git:secret@github.com/sachitsac/alydash", None),
    ("/Users/sachit/alydash", None),
    ("ssh://git:SECRETSECRET@github.com/o/r", None),
]

# The Gate 6 D2 differential's origins that the list above does not already
# carry, with `normaliseRepo`'s answers, measured the same way.
GATE6_ORIGINS = [
    ("https://sachitsac@github.com/sachitsac/alydash.git", "sachitsac/alydash"),
    ("ssh://git@github.com:22/sachitsac/alydash.git", None),
    ("git@github-work:sachitsac/alydash.git", None),
    ("https://github.com/sachit.sac/alydash.git", None),
    ("https://github.com/sachitsac/alydash/extra", None),
]


class SameDeskCase(unittest.TestCase):
    """Gate 6 (ALYDAS-245), D2, D6 and D7: `same_desk` by its docstring, each
    part read both ways. The runs above have one host, 127.0.0.1, which has no
    case, and no two desks that differ by scheme or by path alone."""

    SAME = [
        ("https://desk.example", "https://desk.example"),
        ("https://Desk.Example", "https://desk.example"),
        ("HTTPS://desk.example", "https://desk.example"),
        ("https://desk.example/", "https://desk.example"),
        ("https://desk.example:8443/desk/", "https://DESK.example:8443/desk"),
    ]
    OTHER = [
        ("the scheme", "http://desk.example", "https://desk.example"),
        ("the host", "https://a.example", "https://b.example"),
        ("the host, by a prefix", "https://desk.example", "https://desk.example.evil.example"),
        ("the port", "https://desk.example:8443", "https://desk.example:8444"),
        ("a port against none", "https://desk.example:8443", "https://desk.example"),
        ("the path", "https://desk.example/a", "https://desk.example/b"),
        ("a path against none", "https://desk.example/a", "https://desk.example"),
        ("the path's case", "https://desk.example/Desk", "https://desk.example/desk"),
    ]

    def test_one_desk_is_the_same_desk_whatever_the_case_of_its_scheme_and_host(self):
        same_desk = pipeline_module().same_desk
        for a, b in self.SAME:
            with self.subTest(a=a, b=b):
                self.assertTrue(same_desk(a, b))
                self.assertTrue(same_desk(b, a))

    def test_a_desk_that_differs_by_scheme_host_port_or_path_is_another_desk(self):
        same_desk = pipeline_module().same_desk
        for name, a, b in self.OTHER:
            with self.subTest(name):
                self.assertFalse(same_desk(a, b), f"{a} and {b}")
                self.assertFalse(same_desk(b, a), f"{b} and {a}")


# Gate 8 (ALYDAS-245), C-1. An owner with an underscore, in each spelling.
UNDERSCORE_ORIGINS = [
    ("https://github.com/my_org/repo", "my_org/repo"),
    ("https://github.com/my_org/repo.git", "my_org/repo"),
    ("git@github.com:my_org/repo.git", "my_org/repo"),
    ("ssh://git@github.com/my_org/repo.git", "my_org/repo"),
    ("https://github.com/_/r", "_/r"),
    ("https://github.com/my_org/re_po", "my_org/re_po"),
]

PLUGIN_RULE = os.path.join("packages", "modules", "work", "plugin", "scripts", "lib", "repo.mjs")
PLUGIN_RULE_TEST = os.path.join("tests", "plugin", "repo.test.ts")
PLUGIN_RULE_SCRIPT = """
import { pathToFileURL } from 'node:url';
const { normaliseRepo } = await import(pathToFileURL(process.argv[1]).href);
process.stdout.write(JSON.stringify(JSON.parse(process.argv[2]).map((url) => normaliseRepo(url))));
"""


def desk_file(repo, named, relative, name):
    """The path of a file in the desk checkout, by `desk_constant`'s rule: absent skips or fails."""
    path = os.path.join(repo, relative)
    if not os.path.exists(path):
        raise DeskAbsent(f"{name} was not read: {relative} is not in {repo}")
    return path


class OriginRuleCase(unittest.TestCase):
    def test_the_origin_rule_answers_as_the_plugins_normalise_repo_does(self):
        normalise = pipeline_module().normalise_repo
        wrong = [(url, normalise(url), want) for url, want in PLUGIN_ORIGINS + GATE6_ORIGINS + UNDERSCORE_ORIGINS
                 if normalise(url) != want]
        self.assertEqual(wrong, [], "(origin, pipeline.py, plugin)")
        # Read both ways, so agreement is not a rule that answers None to everything.
        self.assertGreaterEqual(sum(1 for _, want in PLUGIN_ORIGINS if want), 10)
        self.assertGreaterEqual(sum(1 for _, want in PLUGIN_ORIGINS if want is None), 10)

    def test_the_answers_above_are_the_plugins_own_wherever_a_desk_checkout_is_to_hand(self):
        """Gate 8 (ALYDAS-245), C-1. The table is a hand copy, and a hand copy
        went stale once. With a desk checkout (ALYDASH_REPO), the plugin's
        `normaliseRepo` is run by node over every origin in the table and has
        to give the table's answer, and every origin the plugin's own suite
        names has to be in the table. Without one this skips, and says so."""
        rule = desk_constant(self, desk_file, PLUGIN_RULE, "normaliseRepo")
        suite = desk_constant(self, desk_file, PLUGIN_RULE_TEST, "the plugin's fixture list")
        table = PLUGIN_ORIGINS + GATE6_ORIGINS + UNDERSCORE_ORIGINS
        try:
            out = subprocess.run(["node", "--input-type=module", "-e", PLUGIN_RULE_SCRIPT, rule,
                                  json.dumps([url for url, _ in table])], capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            self.fail("node is not on the PATH, so the plugin's normaliseRepo could not be run")
        self.assertEqual(out.returncode, 0, f"node could not run the plugin's normaliseRepo: {out.stderr[-2000:]}")
        wrong = [(url, want, got) for (url, want), got in zip(table, json.loads(out.stdout)) if want != got]
        self.assertEqual(wrong, [], "(origin, this table, the plugin)")
        with open(suite, encoding="utf-8") as handle:
            source = handle.read()
        fixtures = re.search(r"const HTTPS = \[(.*?)\n\];", source, re.S)
        self.assertIsNotNone(fixtures, f"{suite} no longer declares its HTTPS list in a shape this pin can read")
        named = set(re.findall(r"'([^'\n]*)'", fixtures.group(1))) | set(re.findall(r"normaliseRepo\('([^']*)'\)", source))
        self.assertGreaterEqual(len(named), 30, f"too few origins were read out of {suite} for this to prove anything")
        self.assertEqual(sorted(named - {url for url, _ in PLUGIN_ORIGINS}), [],
                         "origins the plugin's suite names and this table does not")

    def test_the_credential_part_of_an_origin_is_never_in_the_answer(self):
        normalise = pipeline_module().normalise_repo
        for url, _ in PLUGIN_ORIGINS:
            read = normalise(url) or ""
            for part in ("SECRET", "@", ":"):
                self.assertNotIn(part, read, url)


class RefusalSentenceCase(unittest.TestCase):
    """Gate 8 (ALYDAS-245), C-4: `url_refusal` by what it prints and what it lets through."""

    def test_the_sentence_quotes_the_scheme_and_host_and_nothing_else_of_the_url(self):
        refusal = pipeline_module().url_refusal
        said = refusal("http://us3r:p4ssw0rd@Desk.Example.com:8080/pr1vate/p4th?k=qu3ry#fr4g")
        self.assertEqual(said, "the desk's address (ALYDASH_URL, or the desk in the alydash plugin's store) must be "
                               "https, or http on localhost, *.localhost, 127.0.0.1 or ::1, each with no trailing dot, so the token was "
                               "not sent to "
                               "http://Desk.Example.com:8080")
        for name, url in {"no host": "https:///pr1vate?k=qu3ry", "no scheme": "desk.example.com/pr1vate?k=qu3ry",
                          "another scheme": "ftp://us3r@desk.example.com/pr1vate",
                          "not a URL at all": "http://[::1/pr1vate?k=qu3ry"}.items():
            with self.subTest(name):
                said = refusal(url)
                self.assertIn("the desk's address", said)
                for part in ("us3r", "pr1vate", "qu3ry"):
                    self.assertNotIn(part, said)

    def test_a_password_with_a_slash_in_it_is_not_quoted_as_the_port(self):
        """The slash ends the host early, so what follows the first colon is
        read as a port and is the password's start. A port is quoted only when
        it is digits."""
        said = pipeline_module().url_refusal("http://us3r:p4ss/w0rd@desk.example.com")
        self.assertTrue(said.endswith("so the token was not sent to http://us3r"), said)

    def test_the_host_is_quoted_as_it_was_written_so_a_token_in_it_can_be_taken_out(self):
        """The callers take the token out of the sentence by its spelling, so
        the scheme and host are quoted in the case they were given in."""
        pipeline = pipeline_module()
        for url in ("http://T0k3N.example.com", "T0k3N://desk.example.com"):
            with self.subTest(url):
                self.assertIn("<token>", pipeline.redacted(pipeline.url_refusal(url), "T0k3N"))

    def test_https_anywhere_and_http_on_this_machine_are_not_refused(self):
        refusal = pipeline_module().url_refusal
        for url in ("https://alydash.vercel.app", "https://us3r@desk.example.com/desk", "HTTPS://desk.example.com",
                    "http://localhost:3000", "http://127.0.0.1:3000", "http://[::1]:3000", "http://LOCALHOST"):
            with self.subTest(url):
                self.assertIsNone(refusal(url))


def resolver_saying(address):
    """ALYDAS-381, Gate 8. A stand-in for socket.getaddrinfo, so no test here
    depends on what this machine's resolver says about a name under localhost.
    A name is answered with `address`, as a resolver that does not follow
    RFC 6761 might, or with a failure when `address` is None, and is kept in
    `asked`. A tuple is answered in turn, its last answer from then on, so
    ("127.0.0.1", "192.0.2.10") is a resolver that says loopback to a check
    and elsewhere to the connect after it. A numeric address goes to the real
    getaddrinfo, which does not resolve it, so a connect to 127.0.0.1 works."""
    real = socket.getaddrinfo
    asked = []
    said = list(address) if isinstance(address, tuple) else [address]
    def lookup(host, port, *rest, **named):
        text = host.decode() if isinstance(host, bytes) else str(host)
        try:
            ipaddress.ip_address(text)
        except ValueError:
            asked.append(text)
            address = said.pop(0) if len(said) > 1 else said[0]
            if address is None:
                raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided")
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            return [(family, socket.SOCK_STREAM, 6, "", (address, port or 0))]
        return real(host, port, *rest, **named)
    lookup.asked = asked
    return lookup


class RawDesk:
    """A desk on 127.0.0.1 that keeps each request's raw bytes, so a second
    Host header would show, and answers 200 with `{}` (or a redirect)."""

    def __init__(self, status=200, location=None):
        self.status, self.location = status, location
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.raw = []
        self.running = True
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except (socket.timeout, OSError):
                continue
            with conn:
                conn.settimeout(2)
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                head, _, rest = data.partition(b"\r\n\r\n")
                length = 0
                for line in head.split(b"\r\n")[1:]:
                    name, _, value = line.partition(b":")
                    if name.strip().lower() == b"content-length":
                        length = int(value.strip())
                while len(rest) < length:
                    rest += conn.recv(4096)
                self.raw.append((head.decode("latin-1"), rest.decode("utf-8")))
                extra = f"location: {self.location}\r\n" if self.location else ""
                conn.sendall(f"HTTP/1.1 {self.status} X\r\ncontent-type: application/json\r\ncontent-length: 2\r\n"
                             f"{extra}connection: close\r\n\r\n{{}}".encode())

    def heads(self):
        """Each request as (request line, [(header, value), ...], body), headers lowered by name."""
        out = []
        for head, body in self.raw:
            line, *rest = head.split("\r\n")
            out.append((line, [(n.strip().lower(), v.strip()) for n, _, v in (h.partition(":") for h in rest)], body))
        return out

    def close(self):
        self.running = False
        self.thread.join()
        self.sock.close()


class LocalhostNamesCase(unittest.TestCase):
    """ALYDAS-381, Gate 8. A local desk is served at http://<name>.localhost:<port>,
    and RFC 6761 6.3 makes every name under localhost this machine, so the name
    alone decides. S3 asked the resolver and then let urllib ask again; a
    resolver that said loopback the first time and elsewhere the second was
    handed the bearer. Now the name is never handed to a resolver: the check
    asks none, and the request connects to 127.0.0.1 with the name as its Host.
    The resolver is a stand-in everywhere here."""

    def test_a_name_under_localhost_is_this_machine_and_deciding_asks_no_resolver(self):
        pipeline = pipeline_module()
        self.assertEqual(list(inspect.signature(pipeline.url_refusal).parameters), ["url"])
        for address in ("10.0.0.5", ("127.0.0.1", "10.0.0.5"), "0.0.0.0", "::", None):
            for url in ("http://desk.localhost:4700", "http://A.Desk.LocalHost:4700/x", "http://desk.localhost"):
                with self.subTest(url, resolver=address):
                    lookup = resolver_saying(address)
                    with mock.patch("socket.getaddrinfo", lookup):
                        self.assertIsNone(pipeline.url_refusal(url))
                    self.assertEqual(lookup.asked, [])

    def test_a_name_that_only_looks_local_is_refused_without_a_lookup(self):
        pipeline = pipeline_module()
        for url in ("http://localhost.example.com", "http://a.localhost.example:4700", "http://evil.localhost.example",
                    "http://a.localhost.:4700", "http://localhost.:4700", "http://notlocalhost:4700",
                    "http://alocalhost:4700", "http://desk.localhost:4700@evil.example/x"):
            with self.subTest(url):
                lookup = resolver_saying("127.0.0.1")
                with mock.patch("socket.getaddrinfo", lookup):
                    said = pipeline.url_refusal(url)
                self.assertIsNotNone(said)
                self.assertIn("*.localhost, 127.0.0.1 or ::1, each with no trailing dot", said)
                self.assertEqual(lookup.asked, [])

    def test_https_and_the_three_exact_names_ask_nothing(self):
        refusal = pipeline_module().url_refusal
        for url in ("https://desk.example.com", "https://desk.localhost", "http://localhost:3000",
                    "http://127.0.0.1:3000", "http://[::1]:3000"):
            with self.subTest(url):
                lookup = resolver_saying("10.0.0.5")
                with mock.patch("socket.getaddrinfo", lookup):
                    self.assertIsNone(refusal(url))
                self.assertEqual(lookup.asked, [])

    def test_a_request_to_a_localhost_name_lands_on_127_0_0_1_with_the_name_as_its_host(self):
        """Whatever a resolver would say for the name, elsewhere, loopback and
        then elsewhere, an any address or nothing, it is never asked, and the
        request reaches the desk on 127.0.0.1 with one Host header: the name
        and the port, lowered. A proxy the environment names is not used for
        it either, since the request is to this machine. urllib reads the
        proxy when an opener is built, so the proxied run loads its own copy
        of pipeline.py with the proxy in the environment."""
        desk = RawDesk()
        proxy = RawDesk()
        self.addCleanup(desk.close)
        self.addCleanup(proxy.close)
        proxied = {"http_proxy": f"http://127.0.0.1:{proxy.port}", "HTTP_PROXY": f"http://127.0.0.1:{proxy.port}",
                   "no_proxy": "", "NO_PROXY": ""}
        for address in ("192.0.2.10", ("127.0.0.1", "192.0.2.10"), "0.0.0.0", "::", None):
            for env in ({}, proxied):
                with self.subTest(resolver=address, proxy=bool(env)):
                    desk.raw.clear()
                    proxy.raw.clear()
                    lookup = resolver_saying(address)
                    with mock.patch.dict(os.environ, env):
                        pipeline = fresh_pipeline() if env else pipeline_module()
                        with mock.patch("socket.getaddrinfo", lookup):
                            posted = pipeline.desk_request(f"http://Desk.LocalHost:{desk.port}/", "t0k3n", "POST",
                                                           "/api/heartbeat?x=1", {"a": 1}, timeout=2)
                            read = pipeline.desk_request(f"http://desk.localhost:{desk.port}", "t0k3n", "GET",
                                                         "/api/asks?id=1", timeout=2)
                    self.assertEqual((posted, read), (b"{}", b"{}"))
                    self.assertEqual(lookup.asked, [])
                    self.assertEqual(proxy.raw, [])
                    host = f"desk.localhost:{desk.port}"
                    self.assertEqual(
                        [(line, [v for n, v in headers if n == "host"],
                          [v for n, v in headers if n == "authorization"], body)
                         for line, headers, body in desk.heads()],
                        [("POST /api/heartbeat?x=1 HTTP/1.1", [host], ["bearer t0k3n"], '{"a": 1}'),
                         ("GET /api/asks?id=1 HTTP/1.1", [host], ["bearer t0k3n"], "")])

    def test_the_proxied_case_is_not_a_proxy_nobody_uses(self):
        """The premise of the proxied run above: with that environment, a
        request to 127.0.0.1 spelled as such does go to the proxy."""
        proxy = RawDesk()
        self.addCleanup(proxy.close)
        proxied = {"http_proxy": f"http://127.0.0.1:{proxy.port}", "HTTP_PROXY": f"http://127.0.0.1:{proxy.port}",
                   "no_proxy": "", "NO_PROXY": ""}
        with mock.patch.dict(os.environ, proxied):
            self.assertEqual(fresh_pipeline().desk_request("http://127.0.0.1:9", "t0k3n", "GET", "/x", timeout=2), b"{}")
        self.assertEqual(len(proxy.raw), 1)

    def test_a_localhost_name_still_follows_no_redirect(self):
        pipeline = pipeline_module()
        elsewhere = RawDesk()
        self.addCleanup(elsewhere.close)
        desk = RawDesk(status=302, location=f"http://127.0.0.1:{elsewhere.port}/elsewhere")
        self.addCleanup(desk.close)
        lookup = resolver_saying("192.0.2.10")
        with mock.patch("socket.getaddrinfo", lookup):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                pipeline.desk_request(f"http://desk.localhost:{desk.port}", "t0k3n", "POST", "/api/heartbeat", {}, timeout=2)
        self.assertEqual(caught.exception.code, 302)
        self.assertEqual((len(desk.raw), elsewhere.raw), (1, []))
        self.assertEqual(lookup.asked, [])


def fresh_pipeline():
    """pipeline.py loaded again as its own module, so its openers read the
    environment as it is now."""
    spec = importlib.util.spec_from_file_location("pipeline_fresh", PIPELINE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
