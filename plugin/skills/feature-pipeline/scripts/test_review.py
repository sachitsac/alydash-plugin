#!/usr/bin/env python3
"""ALYDAS-174, S1. `pipeline.py review record <slug>`: Gate 8's review as a
record on the ledger.

The lead writes `measurements/<date>-<slug>-review.json` beside the prose
review: the areas the change falls into (each a name, a line on what it is and
the path patterns that are its files), every finding (what it means, why it
matters, how it was reproduced, who found it and where, its group and whether
it blocks), the proofs of the criteria, the tests that ran and a verdict.
`review record` checks it, counts every changed file's lines with
`git diff --numstat <base>...HEAD` (the three-dot range GitHub's compare
shows), files each into the first area whose pattern it matches (or "Other",
never dropped), marks the tests by the UI gate's own patterns, and writes the
result on Phase 8 as `review`, which the heartbeat carries to the desk.
Phase 8 does not close, and is not held, without a well-formed one.

Every case runs pipeline.py as a subprocess in a real temporary repository on
a feature branch, with a bare clone beside it as `origin`, as test_ui_gate's
do.

  ALYDASH_REPO=<desk checkout> python3 -m unittest test_review -v

Python 3.9+, stdlib only.
"""
import copy
import json
import os
import subprocess
import unittest
from unittest import mock

from test_heartbeat import TOKEN_VARIABLE, URL_VARIABLE, HoldHelpers, Listener, desk_refusals, pipeline_module
from test_ui_gate import UiGateCase

pipeline = pipeline_module()

REVIEW = "2026-09-13-scratch-review.json"


def a_finding(**over):
    found = {
        "id": "F1", "title": "The count skips a renamed file",
        "plain": "A file that was renamed is counted once, as added.",
        "why": "The area totals read low, so the change looks smaller than it is.",
        "how": "Renamed a.ts to b.ts on a scratch branch and read the record.",
        "by": "correctness reviewer", "file": "src/a.ts", "line": 2,
        "group": "fixed", "blocks": False,
    }
    found.update(over)
    return found


def a_review(**over):
    review = {
        "areas": [
            {"name": "Counting", "about": "What reads the diff.", "patterns": ["src/a*.ts"]},
            {"name": "Helpers", "about": "What the counting leans on.", "patterns": ["src/b.ts"]},
        ],
        "findings": [a_finding(),
                     a_finding(id="F2", title="A slow read", group="later", file=None, line=None),
                     a_finding(id="F3", title="Needs a ruling", group="needs-you", blocks=True, line=None)],
        "proofs": [{"criterion": 1, "text": "Gate 8 shows what was reviewed.", "how": "Read the stored row back."}],
        "tests": {"command": "python3 -m unittest test_review", "passed": 9, "failed": 0, "skipped": 0,
                  "excerpt": "Ran 9 tests\n\nOK"},
        "verdict": "One finding needs you before the merge.",
    }
    for f in review["findings"]:
        for key in [k for k, v in f.items() if v is None]:
            del f[key]
    review.update(over)
    return review


def grow_past_the_cap(review):
    """A record on the ledger grown past REVIEW_MOST_BYTES by hand, each field within its bound."""
    long = "x" * pipeline.LEDGER_BOUNDS["findingText"]
    review["findings"] = [a_finding(id=f"F{i}", plain=long, why=long, how=long)
                          for i in range(pipeline.LEDGER_BOUNDS["findings"] // 2)]


def at_the_cap(cap):
    """A review.json whose record (no files) is exactly `cap` bytes, every
    field within its bound and the excerpt with room for one byte more."""
    b = pipeline.LEDGER_BOUNDS
    raw = a_review(findings=[], tests=dict(a_review()["tests"], excerpt="e"))

    def size():
        # Measured with the script's cap lifted, so the build owes nothing to it.
        with mock.patch.object(pipeline, "REVIEW_MOST_BYTES", 10 * cap):
            record, probs = pipeline.review_record(raw, [], [], "a" * 40, "b" * 40)
        assert not probs, probs
        return pipeline.review_bytes(record)

    long = "x" * b["findingText"]
    while True:
        raw["findings"].append(a_finding(id=f"F{len(raw['findings'])}", plain=long, why=long, how=long))
        if size() > cap:
            raw["findings"][-1].update(plain="x", why="x", how="x")
            break
    # The last finding's three texts, then the excerpt, take up what is left, ASCII byte for byte.
    need = cap - size()
    for key in ("plain", "why", "how"):
        more = min(need, b["findingText"] - 1)
        raw["findings"][-1][key] += "x" * more
        need -= more
    assert need <= b["testExcerpt"] - 2, need
    raw["tests"]["excerpt"] += "e" * need
    assert size() == cap
    return raw, need


class ReviewCase(UiGateCase):
    def lines(self, relative, count, word="line"):
        self.write(relative, "".join(f"{word} {i}\n" for i in range(count)))

    def a_branch(self):
        """origin's main holds src/b.ts (four lines); the feature branch adds
        src/a.ts (five lines) and src/a.test.ts (three), and rewrites one line
        of src/b.ts, in two commits."""
        self.repo_with_origin()
        self.lines("src/b.ts", 4)
        self.git("add", "src/b.ts")
        self.git("commit", "-q", "-m", "b on main")
        self.git("push", "-q", "origin", "main")
        self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", "-b", "feature/alydas-174-s1")
        self.lines("src/a.ts", 5)
        self.lines("src/a.test.ts", 3, "case")
        self.git("add", "src/a.ts", "src/a.test.ts")
        self.git("commit", "-q", "-m", "a and its test")
        with open(os.path.join(self.tmp.name, "src/b.ts"), "w", encoding="utf-8") as f:
            f.write("line 0\nchanged\nline 2\nline 3\n")
        self.git("commit", "-q", "-am", "b changed")

    def a_large_branch(self):
        """Gate 8 ruling (Ask fc55dcae). origin's main holds src/many/f05.ts
        (six lines); the branch deletes it, adds f00..f04 at six lines each
        (f03 a test) and f06..f44 at 10+i lines, and src/b.ts at four. So
        src/many/ is 45 files: the 39 big ones and one of the six tied at six
        lines, f00 (ties by path), are kept, and f01..f05 fold."""
        self.repo_with_origin()
        # Its own words, so git does not read an added file as its rename.
        self.lines("src/many/f05.ts", 6, "gone")
        self.git("add", "src")
        self.git("commit", "-q", "-m", "f05 on main")
        self.git("push", "-q", "origin", "main")
        self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", "-b", "feature/alydas-174-fold")
        self.git("rm", "-q", "src/many/f05.ts")
        for i in range(5):
            self.lines(f"src/many/f{i:02d}{'.test' if i == 3 else ''}.ts", 6)
        for i in range(6, 45):
            self.lines(f"src/many/f{i:02d}.ts", 10 + i)
        self.lines("src/b.ts", 4)
        self.git("add", "src")
        self.git("commit", "-q", "-m", "many files")

    def ready(self):
        """Everything Phase 8 needed before this slice: the plan, the review doc
        with a ruling word, a plain line, and the UI gate's skip."""
        self.a_branch()
        self.unrecorded()

    def unrecorded(self):
        """`phase_8_otherwise_ready` without the review it now records: these
        cases are about recording it. Gate 8, W-4: with Phase 8 running, since
        a review is recorded only then."""
        self.init_scratch()
        self.start_8()
        self.write(os.path.join("docs", "measurements", "2026-09-13-scratch-review.md"),
                   "# Review\n\nOne finding, ruled defer.\n")
        out = self.run_pipeline("note", "scratch", "8", "--plain", "The reviewers found one thing to do later.")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.skip()

    def start_8(self):
        """Phase 8 running with its steps ticked: Phase 2's hold marked answered
        on the ledger (its own hold is test_heartbeat's), then `phase 8 start`."""
        path = os.path.join(self.root, "boards", "2026-09-13-scratch.json")
        with open(path, encoding="utf-8") as f:
            run = json.load(f)
        run["phases"][2]["status"] = "done"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(run, f)
        out = self.run_pipeline("phase", "scratch", "8", "start")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        for i, step in enumerate(self.board()["phases"][8].get("steps") or [], start=1):
            if step.get("state") == "todo":
                self.assertEqual(self.run_pipeline("step", "scratch", "8", str(i), "done").returncode, 0)

    def review_file(self, body):
        self.write(os.path.join("docs", "measurements", REVIEW), body if isinstance(body, str) else json.dumps(body))

    def record(self, body=None):
        if body is not None:
            self.review_file(body)
        return self.run_pipeline("review", "record", "scratch")

    def recorded(self):
        return self.board()["phases"][8].get("review")

    def numstat(self):
        """What git itself says for the three-dot range, by path."""
        out = self.git("diff", "--numstat", "--no-renames", "origin/main...HEAD")
        rows = {}
        for line in out.splitlines():
            added, removed, path = line.split("\t", 2)
            rows[path] = (int(added), int(removed))
        return rows

    def assertRefusedWhole(self, out, *named):
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        for words in named:
            self.assertIn(words, out.stderr)


class TestReviewRecord(ReviewCase):
    def test_review_record_counts_each_area_from_the_three_dot_diff(self):
        self.ready()
        out = self.record(a_review())
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        review = self.recorded()
        self.assertIsNotNone(review, "review record wrote nothing on Phase 8")
        git = self.numstat()
        self.assertEqual(sorted(git), ["src/a.test.ts", "src/a.ts", "src/b.ts"])
        files = {f["path"]: f for area in review["areas"] for f in area["files"]}
        self.assertEqual(sorted(files), sorted(git), "every changed file is in one area")
        for path, (added, removed) in git.items():
            self.assertEqual((files[path]["added"], files[path]["removed"]), (added, removed), path)
        # The test is marked by the UI gate's own pattern, and the rest are code.
        self.assertEqual({p: f["test"] for p, f in files.items()},
                         {"src/a.ts": False, "src/a.test.ts": True, "src/b.ts": False})
        counting, helpers = review["areas"]
        self.assertEqual([f["path"] for f in counting["files"]], ["src/a.test.ts", "src/a.ts"])
        self.assertEqual([f["path"] for f in helpers["files"]], ["src/b.ts"])
        # Each area totals its files, with the tests' share apart.
        self.assertEqual((counting["added"], counting["removed"], counting["testAdded"], counting["testRemoved"]),
                         (8, 0, 3, 0))
        self.assertEqual((helpers["added"], helpers["removed"], helpers["testAdded"], helpers["testRemoved"]),
                         (1, 1, 0, 0))
        # The commits reviewed: the merge-base with origin's default, and HEAD.
        self.assertEqual(review["base"], self.git("merge-base", "HEAD", "origin/main"))
        self.assertEqual(review["head"], self.git("rev-parse", "HEAD"))
        # The rest is the file's, as written.
        self.assertEqual(review["findings"], a_review()["findings"])
        self.assertEqual(review["proofs"], a_review()["proofs"])
        self.assertEqual(review["tests"], a_review()["tests"])
        self.assertEqual(review["verdict"], a_review()["verdict"])

    def test_a_file_no_area_names_lands_in_other_and_is_never_dropped(self):
        self.ready()
        body = a_review(areas=[{"name": "Counting", "about": "What reads the diff.", "patterns": ["src/a.ts"]}])
        out = self.record(body)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        areas = {a["name"]: a for a in self.recorded()["areas"]}
        self.assertEqual(list(areas), ["Counting", "Other"])
        self.assertEqual([f["path"] for f in areas["Other"]["files"]], ["src/a.test.ts", "src/b.ts"])
        self.assertEqual(areas["Other"]["testAdded"], 3)

    def test_an_area_named_other_takes_what_no_pattern_matched(self):
        self.ready()
        body = a_review(areas=[{"name": "Other", "about": "The rest.", "patterns": ["src/b.ts"]},
                               {"name": "Counting", "patterns": ["src/a.ts"]}])
        self.assertEqual(self.record(body).returncode, 0)
        areas = {a["name"]: a for a in self.recorded()["areas"]}
        self.assertEqual(list(areas), ["Other", "Counting"])
        self.assertEqual([f["path"] for f in areas["Other"]["files"]], ["src/a.test.ts", "src/b.ts"])

    def test_the_first_area_whose_pattern_matches_takes_the_file(self):
        self.ready()
        body = a_review(areas=[{"name": "Tests", "patterns": ["*.test.ts"]}, {"name": "Code", "patterns": ["src/**"]}])
        self.assertEqual(self.record(body).returncode, 0)
        areas = {a["name"]: [f["path"] for f in a["files"]] for a in self.recorded()["areas"]}
        self.assertEqual(areas, {"Tests": ["src/a.test.ts"], "Code": ["src/a.ts", "src/b.ts"]})

    def test_a_project_s_own_ui_gate_ignore_list_decides_what_is_a_test(self):
        """Gate 4, correction 8: `ui_gate_config(run, base)["ignore"]`, read at
        the merge-base, not the constant."""
        self.repo_with_origin()
        self.write("ui-gate/config.json", json.dumps({"ignore": ["src/b.ts"]}))
        self.lines("src/b.ts", 4)
        self.git("add", "ui-gate/config.json", "src/b.ts")
        self.git("commit", "-q", "-m", "config on main")
        self.git("push", "-q", "origin", "main")
        self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", "-b", "feature/alydas-174-s1")
        self.lines("src/a.test.ts", 3)
        with open(os.path.join(self.tmp.name, "src/b.ts"), "a", encoding="utf-8") as f:
            f.write("more\n")
        self.git("add", "src/a.test.ts", "src/b.ts")
        self.git("commit", "-q", "-m", "change")
        self.unrecorded()
        self.assertEqual(self.record(a_review()).returncode, 0)
        files = {f["path"]: f["test"] for a in self.recorded()["areas"] for f in a["files"]}
        self.assertEqual(files, {"src/a.test.ts": False, "src/b.ts": True})

    def test_python_tests_count_as_tests_by_the_same_list_the_ui_gate_reads(self):
        """Gate 7, D1: `test_*.py` and `*_test.py` are tests, not code, and the
        review reads the very list the UI gate leaves out, so the two agree."""
        self.a_branch()
        self.lines("scripts/test_reminders.py", 4, "case")
        self.lines("pkg/reminders_test.py", 2, "case")
        self.lines("scripts/reminders.py", 3)
        self.lines("scripts/contest.py", 1)
        self.lines("scripts/testing.py", 1)
        self.git("add", "scripts", "pkg")
        self.git("commit", "-q", "-m", "python and its tests")
        self.unrecorded()
        body = a_review(areas=[{"name": "Tests", "about": "The Python tests.", "patterns": ["scripts/**", "pkg/**"]}])
        self.assertEqual(self.record(body).returncode, 0)
        [tests, _other] = self.recorded()["areas"]
        self.assertEqual({f["path"]: f["test"] for f in tests["files"]},
                         {"scripts/test_reminders.py": True, "pkg/reminders_test.py": True,
                          "scripts/reminders.py": False, "scripts/contest.py": False, "scripts/testing.py": False})
        self.assertEqual((tests["added"], tests["testAdded"]), (11, 6))
        ignore = pipeline.ui_gate_defaults()["ignore"]
        for path in ("scripts/test_reminders.py", "pkg/reminders_test.py"):
            with self.subTest(path=path):
                self.assertTrue(any(pipeline._glob_match(path, g) for g in ignore), "the UI gate does not leave it out")

    def test_a_rename_is_one_file_under_its_new_path_as_github_counts_it(self):
        """Gate 8, CR-4. GitHub's compare lists a rename once, under its new
        path, with only the lines that changed: a pure rename is 0/0, and a
        rename with an edit counts the edit. The old path is not in the record."""
        self.repo_with_origin()
        self.lines("src/big.ts", 300)
        self.lines("src/other.ts", 300, "row")
        self.git("add", "src")
        self.git("commit", "-q", "-m", "two files on main")
        self.git("push", "-q", "origin", "main")
        self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", "-b", "feature/alydas-174-renames")
        self.git("mv", "src/big.ts", "src/moved.ts")
        os.makedirs(os.path.join(self.tmp.name, "lib"), exist_ok=True)
        self.git("mv", "src/other.ts", "lib/edited.ts")
        with open(os.path.join(self.tmp.name, "lib/edited.ts"), "a", encoding="utf-8") as f:
            f.write("one more row\n")
        self.git("add", "lib/edited.ts")
        self.git("commit", "-q", "-m", "rename one, rename and edit the other")
        self.unrecorded()
        out = self.record(a_review(areas=[{"name": "Code", "patterns": ["src/**", "lib/**"]}]))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        files = {f["path"]: (f["added"], f["removed"]) for a in self.recorded()["areas"] for f in a["files"]}
        self.assertEqual(files, {"src/moved.ts": (0, 0), "lib/edited.ts": (1, 0)})
        # Control: git itself, with renames on, says the same.
        said = self.git("diff", "--numstat", "-M", "origin/main...HEAD")
        self.assertIn("0\t0\tsrc/{big.ts => moved.ts}", said)
        self.assertIn("1\t0\tsrc/other.ts => lib/edited.ts", said)

    def test_past_40_files_an_area_folds_the_rest_into_one_line_and_every_total_stays_exact(self):
        """Gate 8 ruling (Ask fc55dcae, "fold"). An area keeps the 40 files
        with the most lines changed (ties by path), listed in path order; the
        rest are `more`, their count and totals. The area's totals count every
        file, and an area of 40 or fewer has no `more`."""
        self.a_large_branch()
        self.unrecorded()
        out = self.record(a_review(areas=[{"name": "Many", "patterns": ["src/many/**"]},
                                          {"name": "Helpers", "patterns": ["src/b.ts"]}]))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        many, helpers = self.recorded()["areas"]
        git = {p: v for p, v in self.numstat().items() if p.startswith("src/many/")}
        self.assertEqual(len(git), 45)
        kept = [f["path"] for f in many["files"]]
        self.assertEqual(len(kept), pipeline.REVIEW_AREA_FILES)
        self.assertEqual(pipeline.REVIEW_AREA_FILES, 40)
        self.assertEqual(kept, ["src/many/f00.ts"] + [f"src/many/f{i:02d}.ts" for i in range(6, 45)])
        for f in many["files"]:
            self.assertEqual((f["added"], f["removed"]), git[f["path"]], f["path"])
        self.assertEqual(many["more"], {"files": 5, "added": 24, "removed": 6, "testAdded": 6, "testRemoved": 0})
        # The area's totals are every file's, folded or not, as git counts them.
        self.assertEqual((many["added"], many["removed"]),
                         (sum(a for a, _ in git.values()), sum(r for _, r in git.values())))
        self.assertEqual((many["testAdded"], many["testRemoved"]), (6, 0))
        self.assertEqual(many["added"], 30 + sum(10 + i for i in range(6, 45)))
        self.assertNotIn("more", helpers)
        self.assertEqual([f["path"] for f in helpers["files"]], ["src/b.ts"])
        self.assertIn("recorded the review: 46 files in 2 areas", out.stdout)

    def test_an_area_of_exactly_40_files_folds_nothing(self):
        areas = pipeline.review_areas([{"name": "A", "patterns": ["**"]}],
                                      [(f"x/{i:02d}.ts", 1, 0, False) for i in range(40)], [])
        self.assertEqual(len(areas[0]["files"]), 40)
        self.assertNotIn("more", areas[0])

    def test_a_binary_file_is_counted_as_no_lines_and_said_to_be_binary(self):
        self.ready()
        with open(os.path.join(self.tmp.name, "src/logo.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00binary")
        self.git("add", "src/logo.png")
        self.git("commit", "-q", "-m", "logo")
        self.assertEqual(self.record(a_review()).returncode, 0)
        files = {f["path"]: f for a in self.recorded()["areas"] for f in a["files"]}
        self.assertEqual(files["src/logo.png"], {"path": "src/logo.png", "added": 0, "removed": 0, "test": False,
                                                 "binary": True})

    def test_a_second_record_replaces_the_first(self):
        self.ready()
        self.assertEqual(self.record(a_review()).returncode, 0)
        self.assertEqual(self.record(a_review(verdict="Nothing blocks the merge.")).returncode, 0)
        self.assertEqual(self.recorded()["verdict"], "Nothing blocks the merge.")

    def test_a_commit_on_main_after_the_branch_is_not_counted(self):
        """Gate 6, M11. The range is three-dot from the merge-base: main moving
        on after the branch left it changes nothing in the record."""
        self.ready()
        self.git("checkout", "-q", "main")
        self.lines("src/c.ts", 7)
        self.git("add", "src/c.ts")
        self.git("commit", "-q", "-m", "c on main, after the branch")
        self.git("push", "-q", "origin", "main")
        self.git("checkout", "-q", "feature/alydas-174-s1")
        self.git("fetch", "-q", "origin")
        out = self.record(a_review())
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        review = self.recorded()
        files = sorted(f["path"] for area in review["areas"] for f in area["files"])
        self.assertEqual(files, ["src/a.test.ts", "src/a.ts", "src/b.ts"])
        self.assertEqual(review["base"], self.git("merge-base", "HEAD", "origin/main"))
        self.assertNotEqual(review["base"], self.git("rev-parse", "origin/main"))

    def test_a_branch_already_on_main_counts_from_the_base_init_recorded(self):
        """Gate 6, M12. Once origin's main holds the branch, the merge-base is
        HEAD and would count nothing: the base `init` recorded is used."""
        self.ready()
        started = self.board()["base"]
        self.assertNotEqual(started, self.git("rev-parse", "HEAD"))
        self.git("push", "-q", "origin", "HEAD:main")
        self.git("fetch", "-q", "origin")
        self.assertEqual(self.git("merge-base", "HEAD", "origin/main"), self.git("rev-parse", "HEAD"))
        out = self.record(a_review())
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        review = self.recorded()
        self.assertEqual(review["base"], started)
        self.assertEqual(sorted(f["path"] for area in review["areas"] for f in area["files"]),
                         ["src/a.test.ts", "src/a.ts", "src/b.ts"])

    def test_texts_are_trimmed_as_the_desk_trims(self):
        """Gate 6, M40."""
        self.ready()
        out = self.record(a_review(verdict="  Nothing blocks.\n ", findings=[a_finding(title=" Padded  ")]))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.recorded()["verdict"], "Nothing blocks.")
        self.assertEqual(self.recorded()["findings"][0]["title"], "Padded")


class TestReviewRecordRefuses(ReviewCase):
    def unchanged(self, body, *named, fresh=True):
        if fresh:
            self.ready()
        before = json.dumps(self.board(), sort_keys=True)
        out = self.record(body)
        self.assertRefusedWhole(out, *named)
        self.assertEqual(json.dumps(self.board(), sort_keys=True), before, "a refused record changed the ledger")
        self.assertNotIn("review", self.board()["phases"][8])

    def test_a_finding_whose_group_is_none_of_the_three_is_refused_and_the_ledger_is_untouched(self):
        body = a_review(findings=[a_finding(group="blocked")])
        self.unchanged(body, "F1", "needs-you, fixed or later")

    def test_a_finding_that_does_not_say_whether_it_blocks_is_refused(self):
        found = a_finding()
        del found["blocks"]
        self.unchanged(a_review(findings=[found]), "F1", "blocks")

    def test_a_finding_missing_what_it_means_why_or_how_is_refused(self):
        self.ready()
        for key in ("plain", "why", "how", "by", "title"):
            with self.subTest(key=key):
                found = a_finding()
                found[key] = "  "
                self.unchanged(a_review(findings=[found]), "F1", key, fresh=False)

    def test_a_line_without_a_file_is_refused(self):
        found = a_finding()
        del found["file"]
        self.unchanged(a_review(findings=[found]), "F1", "line")

    def test_two_findings_with_one_id_are_refused(self):
        self.unchanged(a_review(findings=[a_finding(), a_finding(title="Another")]), "F1", "twice")

    def test_a_record_over_48_kb_is_refused_with_its_size_named(self):
        """Refused, never trimmed: the cap is the record's, and `trim_ledger`
        does not cut a review."""
        long = "x" * pipeline.LEDGER_BOUNDS["findingText"]
        findings = [a_finding(id=f"F{i}", plain=long, why=long, how=long) for i in range(20)]
        body = a_review(findings=findings)
        self.ready()
        out = self.record(body)
        self.assertRefusedWhole(out, f"{pipeline.REVIEW_MOST_BYTES:,} bytes")
        size = [w for w in out.stderr.split() if w.replace(",", "").isdigit() and int(w.replace(",", "")) > 49152]
        self.assertTrue(size, f"the record's own size is not named: {out.stderr}")
        self.assertNotIn("review", self.board()["phases"][8])

    def test_a_text_over_its_bound_is_refused_with_the_bound(self):
        body = a_review(verdict="v" * (pipeline.LEDGER_BOUNDS["verdict"] + 1))
        self.unchanged(body, "verdict", str(pipeline.LEDGER_BOUNDS["verdict"]))

    def test_more_findings_than_the_bound_are_refused(self):
        findings = [a_finding(id=f"F{i}") for i in range(pipeline.LEDGER_BOUNDS["findings"] + 1)]
        self.unchanged(a_review(findings=findings), "findings", str(pipeline.LEDGER_BOUNDS["findings"]))

    def test_a_file_that_is_not_json_is_refused(self):
        self.unchanged('{"areas": [', "JSON")

    def test_a_record_before_phase_8_has_started_is_refused(self):
        """Gate 8, W-4. A pending Phase 8 has nothing reviewed yet: refused
        with a sentence, the ledger untouched; once started, it records."""
        self.a_branch()
        self.init_scratch()
        self.review_file(a_review())
        out = self.record()
        self.assertRefusedWhole(out, "review record: Phase 8 has not started (it is pending), so there is no "
                                     "review to record yet: run phase scratch 8 start first")
        self.assertIsNone(self.recorded())
        self.start_8()
        self.assertEqual(self.record().returncode, 0)
        self.assertIsNotNone(self.recorded())

    def test_no_review_file_is_refused(self):
        self.ready()
        out = self.run_pipeline("review", "record", "scratch")
        self.assertRefusedWhole(out, "review.json")

    def test_a_head_the_file_names_that_is_not_head_is_refused(self):
        self.unchanged(a_review(head="0" * 40), "head")

    def test_a_base_the_file_names_that_is_not_the_base_is_refused(self):
        """Gate 6, M37."""
        self.unchanged(a_review(base="0" * 40), "names base")

    def test_a_proof_without_its_text_or_with_a_criterion_out_of_range_is_refused(self):
        """Gate 6, M06 and M07. The desk refuses the whole beat for either."""
        self.ready()
        good = {"criterion": 1, "text": "t", "how": "h"}
        for proof, named in ((dict(good, text="  "), "proof 1's text"), (dict(good, how="  "), "proof 1's how"),
                             (dict(good, criterion=0), "criterion"), (dict(good, criterion=1000), "criterion"),
                             (dict(good, criterion=1.5), "criterion"), (dict(good, criterion=True), "criterion")):
            with self.subTest(proof=proof):
                self.unchanged(a_review(proofs=[proof]), "proof 1", named, fresh=False)

    def test_a_tests_count_that_is_not_a_whole_number_is_refused(self):
        """Gate 6, M09."""
        self.ready()
        for key in ("passed", "failed", "skipped"):
            for value in (-1, "9", True, 2.5, 2_147_483_648, None):
                with self.subTest(key=key, value=value):
                    tests = dict(a_review()["tests"], **{key: value})
                    self.unchanged(a_review(tests=tests), f"the tests' {key}", fresh=False)
        body = a_review()
        del body["tests"]
        self.unchanged(body, "tests are missing", fresh=False)

    def test_a_text_with_a_control_character_is_refused_with_what_to_do(self):
        """Gate 6, M39. The desk's CONTROL_CHARACTER (`@alydash/core/text`)
        refuses the whole beat; tabs and line breaks pass, so an excerpt keeps
        its lines."""
        self.ready()
        for body, named in ((a_review(findings=[a_finding(plain="a\x07b")]), "finding F1's plain"),
                            (a_review(verdict="Ship\x1b[31m it"), "the review's verdict"),
                            (a_review(proofs=[{"criterion": 1, "text": "t\x7f", "how": "h"}]), "proof 1's text"),
                            (a_review(areas=[{"name": "A\x00", "patterns": ["**"]}]), "area 1's name")):
            with self.subTest(named=named):
                self.unchanged(body, named, "control character", "keep it to plain text", fresh=False)
        tests = dict(a_review()["tests"], excerpt="Ran 9 tests\n\tok\nOK")
        out = self.record(a_review(tests=tests))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.recorded()["tests"]["excerpt"], "Ran 9 tests\n\tok\nOK")


class TestPhase8NeedsTheRecord(ReviewCase):
    def test_phase_8_done_without_a_record_is_refused_and_with_one_closes(self):
        self.ready()
        refused = self.close()
        self.assertRefused(refused, "review record")
        self.assertEqual(self.record(a_review()).returncode, 0)
        self.assertCloses(self.close())

    def test_a_record_spoiled_on_the_ledger_after_it_was_written_is_refused(self):
        self.ready()
        self.assertEqual(self.record(a_review()).returncode, 0)
        path = os.path.join(self.root, "boards", "2026-09-13-scratch.json")
        run = self.board()
        run["phases"][8]["review"]["findings"][0]["group"] = "maybe"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(run, f)
        self.assertRefused(self.close(), "review")

    def spoil(self, change):
        path = os.path.join(self.root, "boards", "2026-09-13-scratch.json")
        run = self.board()
        change(run["phases"][8]["review"])
        with open(path, "w", encoding="utf-8") as f:
            json.dump(run, f)

    def test_a_record_grown_past_48_kb_on_the_ledger_is_refused(self):
        """Gate 6, M31 and C2. Every field within its bound, the whole over the cap."""
        self.ready()
        self.assertEqual(self.record(a_review()).returncode, 0)
        self.spoil(grow_past_the_cap)
        self.assertRefused(self.close(), f"{pipeline.REVIEW_MOST_BYTES:,} bytes")

    def test_a_record_whose_head_is_spoiled_on_the_ledger_is_refused(self):
        """Gate 6, M38."""
        self.ready()
        self.assertEqual(self.record(a_review()).returncode, 0)
        self.spoil(lambda review: review.update(head="HEAD"))
        self.assertRefused(self.close(), "not a commit")

    def test_the_prose_review_s_ruling_check_still_runs_beside_the_record(self):
        self.ready()
        self.assertEqual(self.record(a_review()).returncode, 0)
        self.write(os.path.join("docs", "measurements", "2026-09-13-scratch-review.md"), "# Review\n\nNothing.\n")
        self.assertRefused(self.close(), "review doc")

    def assertStale(self, out, recorded, now):
        """Gate 8, CR-1 / W-1: the refusal names both commits and the fix."""
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn(f"the review was recorded at {recorded[:12]}, and HEAD is {now[:12]}: "
                      f"run review record scratch again", out.stderr)

    def test_a_commit_after_the_record_is_refused_until_it_is_recorded_again(self):
        """Gate 8, CR-1 / W-1. A fix pass after a send-back lands a commit:
        the record is of the delivery before it, so Phase 8 does not close."""
        self.ready()
        self.assertEqual(self.record(a_review()).returncode, 0)
        reviewed = self.recorded()["head"]
        self.lines("src/c.ts", 2)
        self.git("add", "src/c.ts")
        self.git("commit", "-q", "-m", "the fix pass")
        now = self.git("rev-parse", "HEAD")
        out = self.close()
        self.assertRefused(out)
        self.assertStale(out, reviewed, now)
        # Control: recorded again, at the new HEAD, it closes.
        self.assertEqual(self.record(a_review()).returncode, 0)
        self.assertEqual(self.recorded()["head"], now)
        self.assertCloses(self.close())

    def test_a_record_of_a_commit_amended_away_is_refused(self):
        """Gate 8, W-1: history rewritten under the record."""
        self.ready()
        self.assertEqual(self.record(a_review()).returncode, 0)
        reviewed = self.recorded()["head"]
        self.git("commit", "-q", "--amend", "-m", "b changed, reworded")
        now = self.git("rev-parse", "HEAD")
        self.assertNotEqual(reviewed, now)
        out = self.close()
        self.assertRefused(out)
        self.assertStale(out, reviewed, now)

    def test_force_still_closes_phase_8_over_a_stale_record(self):
        """The documented escape stays: `phase 8 done --force` closes, recorded."""
        self.ready()
        self.assertEqual(self.record(a_review()).returncode, 0)
        self.git("commit", "-q", "--allow-empty", "-m", "after the record")
        out = self.run_pipeline("phase", "scratch", "8", "done", "--force", "Sachit ruled the record stands")
        self.assertCloses(out)
        self.assertEqual(self.board()["phases"][8]["forced"], "Sachit ruled the record stands")

    def test_hold_8_on_a_stale_record_is_refused_before_it_asks(self):
        """Gate 8, CR-1 / W-1: the second hold, after a fix commit, asks
        nothing until the review is recorded again."""
        self.a_branch()
        self.init_scratch("--hold", "8")
        self.write(os.path.join("docs", "measurements", "2026-09-13-scratch-review.md"),
                   "# Review\n\nOne finding, ruled defer.\n")
        self.assertEqual(self.run_pipeline("phase", "scratch", "8", "start").returncode, 0)
        self.assertEqual(self.run_pipeline("note", "scratch", "8", "--plain", "Reviewed.").returncode, 0)
        for i in range(1, len(pipeline.PHASE_STEPS[8]) + 1):
            self.assertEqual(self.run_pipeline("step", "scratch", "8", str(i), "done").returncode, 0)
        self.skip()
        self.assertEqual(self.record(a_review()).returncode, 0)
        reviewed = self.recorded()["head"]
        self.git("commit", "-q", "--allow-empty", "-m", "the fix pass")
        out = self.run_pipeline("hold", "scratch", "8")
        self.assertStale(out, reviewed, self.git("rev-parse", "HEAD"))
        self.assertNotIn("no desk is set", out.stdout)
        self.assertEqual(self.record(a_review()).returncode, 0)
        out = self.run_pipeline("hold", "scratch", "8")
        self.assertEqual(out.returncode, 4, out.stdout + out.stderr)
        self.assertIn("no desk is set", out.stdout)

    def test_hold_8_without_a_record_is_refused_before_it_asks(self):
        self.a_branch()
        self.init_scratch("--hold", "8")
        self.write(os.path.join("docs", "measurements", "2026-09-13-scratch-review.md"),
                   "# Review\n\nOne finding, ruled defer.\n")
        self.assertEqual(self.run_pipeline("phase", "scratch", "8", "start").returncode, 0)
        self.assertEqual(self.run_pipeline("note", "scratch", "8", "--plain", "Reviewed.").returncode, 0)
        for i in range(1, len(pipeline.PHASE_STEPS[8]) + 1):
            self.assertEqual(self.run_pipeline("step", "scratch", "8", str(i), "done").returncode, 0)
        self.skip()
        out = self.run_pipeline("hold", "scratch", "8")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("review record", out.stderr)
        self.assertNotIn("no desk is set", out.stdout)
        self.assertEqual(self.record(a_review()).returncode, 0)
        out = self.run_pipeline("hold", "scratch", "8")
        self.assertEqual(out.returncode, 4, out.stdout + out.stderr)
        self.assertIn("no desk is set", out.stdout)


class TestGate8HoldsByDefault(HoldHelpers, ReviewCase):
    """ALYDAS-174, S6 (criterion 7; Ask 1126cfb8, "Hold at 2 and 8 by
    default"). A run begun with `init` and no `--hold` waits at Gate 8: Phase 8
    is not closed by hand, and `hold <slug> 8` asks on the desk. The desk's
    Send back with a note answers that Ask `send-back` with the note; the hold
    reads it back, prints both, and Phase 8 runs again with the note as its
    scene."""

    NOTE = "The Changes tab misses the renamed file. Count renames as one file."
    # init's own default, not the `--hold 2` the other review cases begin with.
    INIT_HOLD = ()

    def board(self):
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.json"), encoding="utf-8") as f:
            return json.load(f)

    def gate_8_ready(self):
        self.a_branch()
        self.init_scratch()
        self.assertEqual(self.board()["holds"], [2, 8])
        # Phase 2 answered already (its own hold is HoldCase's), so Phase 8 may start.
        path = os.path.join(self.root, "boards", "2026-09-13-scratch.json")
        run = self.board()
        run["phases"][2]["status"] = "done"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(run, f)
        self.write(os.path.join("docs", "measurements", "2026-09-13-scratch-review.md"),
                   "# Review\n\nOne finding, ruled defer.\n")
        self.assertEqual(self.run_pipeline("phase", "scratch", "8", "start").returncode, 0)
        self.assertEqual(self.run_pipeline("note", "scratch", "8", "--plain", "Reviewed.").returncode, 0)
        self.tick_steps(8)
        self.skip()
        out = self.record(a_review())
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_a_fresh_run_holds_at_gate_8_and_a_send_back_note_is_printed_and_reopens_it(self):
        self.gate_8_ready()
        refused = self.run_pipeline("phase", "scratch", "8", "done")
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("run hold scratch 8", refused.stderr)

        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk), n="8")
            self.reads_back(desk)
            self.assertEqual(self.board()["phases"][8]["status"], "blocked")
            desk.answer("GET", "/api/asks", 200, self.answered("send-back", "Send it back", self.NOTE))
            code, out, err = self.finish(proc)
        self.assertEqual(code, 0, out + err)
        raised = json.loads(self.of(desk, "POST", "/api/asks")[0]["raw"])
        self.assertEqual(raised["fingerprint"], "hold:8:1")
        self.assertEqual([c["id"] for c in raised["choices"] if c.get("note") == "required"], ["send-back"])
        self.assertIn("pipeline: answered: send-back", out)
        self.assertIn(f"pipeline: note: {self.NOTE}", out)
        phase = self.board()["phases"][8]
        self.assertEqual(phase["status"], "active", "Phase 8 runs again")
        self.assertEqual(phase["scene"], f"Sent back: {self.NOTE}")
        self.assertEqual(phase["hold"]["attempt"], 2)

    def test_a_held_gate_8_is_not_skipped_with_a_note_alone(self):
        """Gate 8, W-3. `skipped` is refused on a held phase as `done` is:
        only `--force "<reason>"` steps round the person, and it is recorded."""
        self.gate_8_ready()
        out = self.run_pipeline("phase", "scratch", "8", "skipped", "--note", "no screen, nothing to review")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("phase 8 waits for your review on the desk, so it is not skipped by hand: "
                      "run hold scratch 8", out.stderr)
        self.assertEqual(self.board()["phases"][8]["status"], "active")
        out = self.run_pipeline("phase", "scratch", "9", "start")
        self.assertEqual(out.returncode, 2, "Phase 9 waits on the hold: " + out.stdout + out.stderr)
        # The escape: forced, and the force on the board.
        out = self.run_pipeline("phase", "scratch", "8", "skipped", "--note", "no screen, nothing to review",
                                "--force", "Sachit ruled no review for this run")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        phase = self.board()["phases"][8]
        self.assertEqual((phase["status"], phase.get("forced")), ("skipped", "Sachit ruled no review for this run"))


class TestTheBeatCarriesTheReview(ReviewCase):
    def test_the_record_reaches_the_desk_on_phase_8_as_written(self):
        self.ready()
        self.review_file(a_review())
        with Listener() as desk:
            out = self.run_pipeline("review", "record", "scratch", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(len(desk.requests), 1, "review record did not beat")
            phases = {p["n"]: p for p in desk.bodies()[-1]["ledger"]["phases"]}
            self.assertEqual(phases[8]["review"], self.recorded())
            self.assertNotIn("review", phases[7])

    def test_a_phase_with_no_review_sends_none(self):
        self.ready()
        self.assertEqual(pipeline.beat_ledger(self.board())["phases"][8].get("review"), None)

    def test_a_review_spoiled_on_the_ledger_is_left_off_the_beat_rather_than_refused_whole(self):
        self.ready()
        self.assertEqual(self.record(a_review()).returncode, 0)
        run = self.board()
        run["phases"][8]["review"]["areas"] = "not a list"
        self.assertIsNone(pipeline.beat_ledger(run)["phases"][8].get("review"))

    def test_a_review_grown_past_48_kb_on_the_ledger_is_left_off_the_beat(self):
        """Gate 6, M23 and C2."""
        self.ready()
        self.assertEqual(self.record(a_review()).returncode, 0)
        run = self.board()
        grow_past_the_cap(run["phases"][8]["review"])
        self.assertEqual(pipeline.review_problems(run["phases"][8]["review"]), [], "every field is within its bound")
        self.assertGreater(pipeline.review_bytes(run["phases"][8]["review"]), pipeline.REVIEW_MOST_BYTES)
        self.assertIsNone(pipeline.beat_ledger(run)["phases"][8].get("review"))

    def test_a_review_whose_head_is_spoiled_on_the_ledger_is_left_off_the_beat(self):
        """Gate 6, M38."""
        self.ready()
        self.assertEqual(self.record(a_review()).returncode, 0)
        for spoiled in ("HEAD", "abc1234", "A" * 40, " " + "a" * 40):
            with self.subTest(head=spoiled):
                run = self.board()
                run["phases"][8]["review"]["head"] = spoiled
                self.assertIsNone(pipeline.beat_ledger(run)["phases"][8].get("review"))

    def test_the_desk_refuses_the_commits_and_control_characters_review_record_refuses(self):
        """Gate 6, M38 and M39, against the desk's own `heartbeatRefusal`: what
        the pipeline refuses the desk refuses (the whole beat), and what the
        desk takes the pipeline takes."""
        self.ready()
        self.review_file(a_review(tests=dict(a_review()["tests"], excerpt="Ran 9\n\tok")))
        with Listener() as desk:
            out = self.run_pipeline("review", "record", "scratch", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            body = desk.bodies()[-1]
        at = next(i for i, p in enumerate(body["ledger"]["phases"]) if p["n"] == 8)

        def spoiled(change):
            copied = copy.deepcopy(body)
            change(copied["ledger"]["phases"][at]["review"])
            return copied

        bodies = [body] + [spoiled(change) for change in (
            lambda r: r.update(head="HEAD"),
            lambda r: r.update(base="a" * 39),
            lambda r: r.update(base="A" * 40),
            lambda r: r["findings"][0].update(plain="a\x07b"),
            lambda r: r.update(verdict="Ship\x1b it"),
            lambda r: r["tests"].update(excerpt="a\x0bb"),
        )]
        theirs = desk_refusals(self, bodies)
        self.assertIsNone(theirs[0], "the desk refused the review as recorded")
        self.assertEqual(pipeline.review_problems(body["ledger"]["phases"][at]["review"]), [])
        for sent, refusal in zip(bodies[1:], theirs[1:]):
            review = sent["ledger"]["phases"][at]["review"]
            self.assertIsNotNone(refusal, f"the desk took {review['head']} {review['base']}")
            self.assertTrue(pipeline.review_problems(review), f"the pipeline takes what the desk refuses: {refusal}")


    def test_the_desk_takes_a_folded_record_and_refuses_the_folds_review_record_refuses(self):
        """Gate 8 ruling (Ask fc55dcae), against the desk's own
        `heartbeatRefusal`: a record that folded past 40 files is taken, and a
        fold that does not add up is refused by both."""
        self.a_large_branch()
        self.unrecorded()
        self.review_file(a_review(areas=[{"name": "Many", "patterns": ["src/many/**"]}]))
        with Listener() as desk:
            out = self.run_pipeline("review", "record", "scratch", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            body = desk.bodies()[-1]
        at = next(i for i, p in enumerate(body["ledger"]["phases"]) if p["n"] == 8)
        self.assertEqual(body["ledger"]["phases"][at]["review"]["areas"][0]["more"]["files"], 5)

        def spoiled(change):
            copied = copy.deepcopy(body)
            change(copied["ledger"]["phases"][at]["review"]["areas"][0])
            return copied

        bodies = [body] + [spoiled(change) for change in (
            lambda a: a["more"].update(files=0),
            lambda a: a["more"].update(added=1.5),
            lambda a: a["more"].update(removed=-1),
            lambda a: a["more"].pop("testRemoved"),
            lambda a: a.update(more="5 more"),
            lambda a: a["files"].pop(),
            lambda a: a["files"].append(dict(a["files"][0], path="src/many/zz.ts")),
        )]
        theirs = desk_refusals(self, bodies)
        self.assertIsNone(theirs[0], "the desk refused the folded review as recorded")
        for sent, refusal in zip(bodies[1:], theirs[1:]):
            area = sent["ledger"]["phases"][at]["review"]["areas"][0]
            self.assertIsNotNone(refusal, f"the desk took {len(area['files'])} files and more {area.get('more')}")
            self.assertTrue(pipeline.review_problems(sent["ledger"]["phases"][at]["review"]),
                            f"the pipeline takes what the desk refuses: {refusal}")


class TestTrimLeavesTheReview(unittest.TestCase):
    """Gate 4, correction 6. `trim_ledger` leaves `review` whole, and counts
    it, with its budget unchanged: both directions."""

    def ledger(self, rows):
        review = copy.deepcopy(a_review())
        review.update(base="a" * 40, head="b" * 40)
        for area in review["areas"]:
            area.update(files=[{"path": "src/a.ts", "added": 1, "removed": 0, "test": False}],
                        added=1, removed=0, testAdded=0, testRemoved=0)
        phase = {"n": 8, "name": "Review", "status": "running", "kind": "gate", "summary": "s", "next": "",
                 "plain": "p", "story": None, "decided": [], "measured": [], "wrong": [], "overruled": [],
                 "produced": [], "table": {"columns": ["c"], "rows": [["x" * 400] for _ in range(rows)]},
                 "skipReason": "", "forced": "", "startedAt": None, "endedAt": None, "review": review}
        return {"phases": [phase], "deferred": [], "terms": {}, "story": None, "log": []}

    def test_a_ledger_just_under_the_budget_with_a_review_comes_back_whole(self):
        ledger = self.ledger(10)
        budget = pipeline.ledger_bytes(ledger)
        out, report = pipeline.trim_ledger(ledger, budget)
        self.assertIsNone(report)
        self.assertEqual(out["phases"][0]["review"], ledger["phases"][0]["review"])

    def test_a_ledger_over_the_budget_is_trimmed_elsewhere_and_its_review_is_kept_whole(self):
        ledger = self.ledger(40)
        review = copy.deepcopy(ledger["phases"][0]["review"])
        budget = pipeline.ledger_bytes(ledger) - 4000
        out, report = pipeline.trim_ledger(ledger, budget)
        self.assertIsNotNone(report)
        # Gate 6, M27: measured apart from `ledger_bytes`, the function under test.
        self.assertLessEqual(len(json.dumps(out, ensure_ascii=False).encode("utf-8")), budget,
                             "the review was not counted in the budget")
        self.assertEqual(out["phases"][0]["review"], review)
        self.assertLess(sum(len(c) for row in out["phases"][0]["table"]["rows"] for c in row), 40 * 400)

    def test_the_ledgers_size_counts_its_review(self):
        """Gate 6, M27."""
        ledger = self.ledger(2)
        self.assertEqual(pipeline.ledger_bytes(ledger), len(json.dumps(ledger, ensure_ascii=False).encode("utf-8")))
        self.assertGreater(pipeline.ledger_bytes(ledger), pipeline.review_bytes(ledger["phases"][0]["review"]))
        bare = copy.deepcopy(ledger)
        del bare["phases"][0]["review"]
        self.assertEqual(pipeline.ledger_bytes(ledger) - pipeline.ledger_bytes(bare),
                         len(json.dumps(ledger["phases"][0]["review"], ensure_ascii=False).encode("utf-8"))
                         + len(', "review": '))

    def test_the_budget_is_the_ledgers_budget_unchanged(self):
        self.assertEqual(pipeline.LEDGER_BUDGET_BYTES, 192 * 1024)
        self.assertLess(pipeline.REVIEW_MOST_BYTES, pipeline.LEDGER_BUDGET_BYTES)


class TestPureParts(unittest.TestCase):
    """The parts with no git and no file: the numstat read, the areas, the size."""

    def test_numstat_with_nul_separators_reads_paths_whole(self):
        out = "3\t1\tsrc/a b.ts\x00-\t-\tlogo.png\x00"
        self.assertEqual(pipeline.parse_numstat(out),
                         [("src/a b.ts", 3, 1, False), ("logo.png", 0, 0, True)])

    def test_a_rename_in_the_nul_form_reads_as_its_new_path(self):
        """Gate 8, CR-4. With `-M -z`, a rename's entry leaves the path empty
        and gives the old and new paths as the next two fields; a name with
        ` => ` or braces in it is read whole, not taken for a rename."""
        out = ("0\t0\t\x00src/big.ts\x00src/moved.ts\x00"
               "1\t0\t\x00src/other.ts\x00lib/edited.ts\x00"
               "2\t0\tdocs/a => b.md\x00"
               "1\t1\tsrc/{x => y}.ts\x00"
               "-\t-\t\x00logo.png\x00img/logo.png\x00")
        self.assertEqual(pipeline.parse_numstat(out),
                         [("src/moved.ts", 0, 0, False), ("lib/edited.ts", 1, 0, False),
                          ("docs/a => b.md", 2, 0, False), ("src/{x => y}.ts", 1, 1, False),
                          ("img/logo.png", 0, 0, True)])

    def test_areas_are_filled_in_the_order_written_and_other_comes_last(self):
        areas = pipeline.review_areas(
            [{"name": "A", "patterns": ["x/*"]}],
            [("y/2.ts", 1, 0, False), ("x/1.ts", 2, 3, False), ("x/1.test.ts", 4, 0, False)],
            ["*.test.*"])
        self.assertEqual([a["name"] for a in areas], ["A", "Other"])
        self.assertEqual(areas[0]["files"], [
            {"path": "x/1.test.ts", "added": 4, "removed": 0, "test": True},
            {"path": "x/1.ts", "added": 2, "removed": 3, "test": False}])
        self.assertEqual((areas[0]["added"], areas[0]["testAdded"]), (6, 4))

    def test_no_other_area_when_every_file_has_one(self):
        areas = pipeline.review_areas([{"name": "A", "patterns": ["**"]}], [("y.ts", 1, 0, False)], [])
        self.assertEqual([a["name"] for a in areas], ["A"])

    def test_a_test_file_s_removed_lines_count_in_removed_and_test_removed(self):
        """Gate 6, M17 and M19."""
        [area] = pipeline.review_areas([{"name": "A", "patterns": ["**"]}],
                                       [("a.test.ts", 2, 5, False), ("a.ts", 1, 3, False)], ["*.test.*"])
        self.assertEqual((area["added"], area["removed"], area["testAdded"], area["testRemoved"]), (3, 8, 2, 5))

    def test_the_fold_keeps_the_most_changed_with_ties_by_path_in_path_order(self):
        """Gate 8 ruling (Ask fc55dcae). 41 files: the one folded is the
        smallest, and of two tied at the smallest, the later path."""
        rows = [(f"x/{i:02d}.ts", 5 + i, 1, False) for i in range(39)]
        rows += [("x/zz.test.ts", 1, 1, False), ("x/aa.ts", 1, 1, False)]
        [area] = pipeline.review_areas([{"name": "A", "patterns": ["**"]}], rows, ["*.test.*"])
        self.assertEqual([f["path"] for f in area["files"]], sorted(["x/aa.ts"] + [r[0] for r in rows[:39]]))
        self.assertEqual(area["more"], {"files": 1, "added": 1, "removed": 1, "testAdded": 1, "testRemoved": 1})
        self.assertEqual((area["added"], area["removed"]), (sum(r[1] for r in rows), 41))
        self.assertEqual(pipeline.review_problems(dict(base="a" * 40, head="b" * 40, areas=[area],
                                                       **{k: v for k, v in a_review().items() if k != "areas"})), [])

    def test_a_fold_that_does_not_add_up_to_a_folded_area_is_refused(self):
        """Gate 8 ruling (Ask fc55dcae). `more` is whole non-negative numbers,
        at least one file, and only on an area that kept exactly 40."""
        rows = [(f"x/{i:02d}.ts", 1, 0, False) for i in range(45)]
        [area] = pipeline.review_areas([{"name": "A", "patterns": ["**"]}], rows, [])
        base = {k: v for k, v in a_review().items() if k != "areas"}
        record = dict(base, base="a" * 40, head="b" * 40, areas=[area])
        self.assertEqual(pipeline.review_problems(record), [])
        spoils = {
            "no files folded": lambda a: a["more"].update(files=0),
            "a count that is not whole": lambda a: a["more"].update(added=1.5),
            "a negative count": lambda a: a["more"].update(removed=-1),
            "a missing count": lambda a: a["more"].pop("testAdded"),
            "not an object": lambda a: a.update(more=5),
            "39 kept": lambda a: a["files"].pop(),
            "41 kept": lambda a: a["files"].append(dict(a["files"][0], path="x/zz.ts")),
        }
        for name, spoil in spoils.items():
            with self.subTest(name):
                spoiled = copy.deepcopy(record)
                spoil(spoiled["areas"][0])
                self.assertTrue(pipeline.review_problems(spoiled), name)

    def test_a_record_of_exactly_48_kb_is_taken_and_one_byte_more_is_refused(self):
        """Gate 6, M21 and M22: the cap, from both sides."""
        # Built to the number written here, not to the constant, so a constant
        # moved either way is caught by the boundary itself.
        raw, need = at_the_cap(49_152)
        record, probs = pipeline.review_record(raw, [], [], "a" * 40, "b" * 40)
        self.assertEqual(probs, [], "a record of exactly 48 KB is refused")
        self.assertEqual(pipeline.review_bytes(record), 49_152)
        raw["tests"]["excerpt"] += "e"
        record, probs = pipeline.review_record(raw, [], [], "a" * 40, "b" * 40)
        self.assertEqual(probs, ["the record is 49,153 bytes, over the 49,152 bytes a review may be: it is refused "
                                 "rather than cut, so shorten the findings or the excerpt"])
        self.assertIsNone(record)
        self.assertEqual(pipeline.REVIEW_MOST_BYTES, 48 * 1024)

    def test_a_record_whose_commits_are_not_commits_is_refused_with_what_to_do(self):
        """Gate 6, M38. git gives the commits, so this is the pure part."""
        for base, head in (("HEAD", "b" * 40), ("a" * 40, "b" * 12), ("a" * 40, "B" * 40), ("a" * 40, "b" * 41)):
            with self.subTest(base=base, head=head):
                record, probs = pipeline.review_record(a_review(), [], [], base, head)
                self.assertIsNone(record)
                self.assertTrue(any("not a commit" in p and "40 or 64 lowercase hex" in p for p in probs), probs)
        record, probs = pipeline.review_record(a_review(), [], [], "a" * 64, "b" * 64)
        self.assertEqual(probs, [])


if __name__ == "__main__":
    unittest.main()
