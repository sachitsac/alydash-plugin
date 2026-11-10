#!/usr/bin/env python3
"""ALYDAS-223, S1, S2, S5 and S6. A run that touches a screen cannot close
Phase 8 without a UI gate report (S1), the report has to be well formed (S2),
and it has to meet the project's rulings, the measurements and the screen's
declared jobs (S5). S6: each measured failure is answered by a finding that
lists it, a measured finding belongs to its screen, and `ui-gate record`
writes the findings into Phase 8's table.

Every phase case runs pipeline.py as a subprocess in a real temporary
repository on a feature branch, with a bare clone beside it as `origin`, so
`origin/HEAD` resolves and the changed files are the ones git reports. Each
one also gives Phase 8 what it already needed (a plain line, a review doc
with a ruling word), so the only thing a refusal can be about is the new rule.

  python3 -m unittest test_ui_gate -v

Python 3.9+, stdlib only.
"""
import copy
import json
import os
import unittest

from test_heartbeat import ScratchRunCase, pipeline_module

pipeline = pipeline_module()

REPORT = "2026-09-13-scratch-ui-gate-report.json"
MEASURE = "2026-09-13-scratch-ui-measure-run-page-default.json"
SCREEN_FILE = "ui-gate/run-page.json"
RULINGS_FILE = "ui-gate/rulings.json"
CONTROLS = "apps/desk/app/work/run-controls.tsx"

DOMAINS = ("Accessibility", "Layout", "Writing", "Typography", "Colors", "UI", "Placement")


def finding(**over):
    """S6: read from the source by default. A finding marked measured has to
    list, in `answers`, the measured failures it accounts for (`answered`).
    Gate 6: its rule is one the rule book does not mark automatic HIGH, so a
    MEDIUM finding of it stands (it was A11Y-FOCUS, which is HIGH on sight)."""
    return dict({"severity": "MEDIUM", "domain": "Accessibility", "rule": "A11Y-STRUCTURE",
                 "location": "app/page.tsx:42", "before": "the focus ring is removed on the save button",
                 "after": "a 2px ring on :focus-visible", "why": "a keyboard user cannot see where they are",
                 "source": "source", "ruling": None}, **over)


def answered(*wheres, **over):
    """S6. A finding that says it was measured, and which failures it answers."""
    return finding(**dict({"source": "measured", "answers": list(wheres)}, **over))


def good_report(**over):
    """A report check_report passes: seven domains, one of them with a finding,
    one check declared not verified, and a verdict that follows."""
    report = {
        "screen": "run page",
        "screenFile": SCREEN_FILE,
        "coverage": [{"domain": d, "inspected": f"the run page, for {d.lower()}",
                      "result": "findings" if d == "Accessibility" else "clear"} for d in DOMAINS],
        "findings": [finding()],
        "placement": [placed()],
        "notVerified": ["tab order on the phone layout, which was not rendered"],
        "verdict": "Approve",
    }
    report.update(over)
    return copy.deepcopy(report)


def placed(**over):
    """G7. What the report states for the screen file's one block: the jobs it
    serves, and whether its place fits them."""
    return dict({"block": "state and controls", "serves": [1, 2], "fits": True, "finding": None,
                 "note": "first on the page at desktop and at phone, which fits jobs 1 and 2"}, **over)


def blocking_report():
    """Well formed, and it blocks: one HIGH finding nobody has ruled on."""
    return good_report(findings=[finding(severity="HIGH")], verdict="Block")


def screen_file(**over):
    """A screen file, shaped like the desk's ui-gate/run-page.json."""
    return dict({"screen": "run page", "route": "/runs/[id]", "root": ".run-page", "states": ["default"],
                 "jobs": ["See at a glance whether the run needs me", "Steer it: stop it, or send it a message"],
                 "blocks": [{"name": "state and controls", "selector": ".run-page-aside", "serves": [1, 2]}]}, **over)


FOUR = {"desktop-light": 1280, "desktop-dark": 1280, "phone": 390, "narrow": 320}


def measurement(failures=(), notes=({"rule": "A11Y-TARGET", "where": "input", "detail": "58x28, under 44px on touch"},),
                screen="run page", state="default"):
    """A measurement file, shaped like ui_measure.mjs writes one: the four
    contexts, each at its width. With no argument it holds one note and no
    failure, so it asks nothing of a report. The failures are the phone's."""
    contexts = {name: {"innerWidth": width, "failures": [], "notes": []} for name, width in FOUR.items()}
    contexts["phone"].update(failures=list(failures), notes=list(notes))
    return {"screen": screen, "route": "/runs/[id]", "state": state, "root": ".run-page", "contexts": contexts}


def failure(rule="COLOR-CONTRAST", where="button.btn 12.5px"):
    return {"rule": rule, "where": where, "detail": "4.27 to 1, rgb(255, 255, 255) on rgb(123, 100, 240)"}


def ruling(**over):
    """One ruling, shaped like the desk's stop-is-plain in ui-gate/rulings.json.
    ALYDAS-455: its where is the file alone, and its quote says the place."""
    return dict({"id": "stop-is-plain",
                 "choice": "Stop is drawn in ink, with no danger colour: stopping is a person's decision, not a failure",
                 "where": CONTROLS, "quote": "Stop is drawn in ink", "rules": ["COLOR-TREATMENT"],
                 "covers": [CONTROLS, "apps/desk/app/work/now-strip.css"]}, **over)


def page_ruling(id):
    """The ruling the S2 fixtures cite by id, now one that is recorded: it
    covers the good report's own finding, at app/page.tsx."""
    return ruling(id=id, choice="The save button keeps the desk's own focus ring", where="app/page.tsx", quote="app/page.tsx",
                  rules=["A11Y-STRUCTURE", "A11Y-ANNOUNCE"], covers=["app/page.tsx"])


RULINGS = [ruling(), page_ruling("ruling-1"), page_ruling("ruling-7")]
def lines_of(count, **at):
    """A file of `count` lines of code; `at` puts words on a line, as l12="..."."""
    lines = [f"const line{n} = {n};" for n in range(1, count + 1)]
    for key, words in at.items():
        lines[int(key[1:]) - 1] = words
    return lines


STOP_COMMENT = "// Stop is drawn in ink, with no danger colour: a person's decision, not a failure."
# The page holds its own name on its first line, as a file the integration
# cases commit does (`write`), so page_ruling's quote is in it once.
FILES = {CONTROLS: lines_of(170, l12=STOP_COMMENT), "app/page.tsx": lines_of(60, l1="app/page.tsx")}


def context(**over):
    """Everything check_report is handed beside the report, read by nobody:
    the rulings, the run's measurement files by name, what a screen file
    holds, and the lines a file holds."""
    return dict({"rulings": copy.deepcopy(RULINGS), "measurements": {MEASURE: measurement()},
                 "screen_file": {SCREEN_FILE: screen_file()}.get, "file_lines": FILES.get}, **over)


def full_report(**over):
    """G8. The good report, full: as many findings as a report carries, which
    is the only report the cap can have cut anything from."""
    return good_report(**dict({"findings": [finding(location=f"app/page.tsx:{at}")
                                            for at in range(1, pipeline.UI_GATE_MOST_FINDINGS + 1)]}, **over))


def cut_placement(report):
    """G8. Placement says its findings were cut by the cap: the row a block that does not fit needs."""
    for row in report["coverage"]:
        if row["domain"] == "Placement":
            row.update(result="findings", inspected="the placement table; one finding left out by the cap")
    return report


def one_finding(**over):
    """A good report whose one finding is `over`, with coverage that follows it."""
    f = finding(**over)
    report = good_report(findings=[f])
    for row in report["coverage"]:
        row["result"] = "findings" if row["domain"].casefold() == str(f["domain"]).casefold() else "clear"
    return report


def covered(report, domain, result):
    for row in report["coverage"]:
        if row["domain"] == domain:
            row["result"] = result
    return report


class UiGateCase(ScratchRunCase):
    # ALYDAS-174, S6. These cases close Phase 8 by hand (`phase 8 done`), as a
    # run begun with `--hold 2` does; init's own default now holds at Gate 8
    # too (`test_review.TestGate8HoldsByDefault` takes it, with `INIT_HOLD = ()`).
    INIT_HOLD = ("--hold", "2")

    def init_scratch(self, *extra):
        return super().init_scratch(*(extra or self.INIT_HOLD))

    def write(self, relative, text=None):
        full = os.path.join(self.tmp.name, relative)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(relative + "\n" if text is None else text)

    def commit_file(self, relative, text=None):
        self.write(relative, text)
        self.git("add", relative)
        self.git("commit", "-q", "-m", relative)

    def branch_changing(self, *committed, on_main=()):
        """A feature branch off origin's main that commits each of `committed`.
        `on_main` is committed on main and pushed to origin first, so the branch
        starts with those files tracked and unchanged."""
        self.repo_with_origin()
        for relative in on_main:
            # G6: a mapping gives each file its text.
            self.commit_file(relative, on_main[relative] if isinstance(on_main, dict) else None)
        if on_main:
            self.git("push", "-q", "origin", "main")
            self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", "-b", "feature/alydas-223-s1")
        for relative in committed:
            self.commit_file(relative)

    def phase_8_otherwise_ready(self):
        """The run, with everything Phase 8 needed before this slice."""
        self.init_scratch()
        self.write(os.path.join("docs", "measurements", "2026-09-13-scratch-review.md"),
                   "# Review\n\nOne finding, ruled defer.\n")
        out = self.run_pipeline("note", "scratch", "8", "--plain", "The reviewers found one thing to do later.")
        self.assertEqual(out.returncode, 0, out.stderr)
        # ALYDAS-174, S1. And the review recorded for the desk.
        self.record_a_review()

    def report(self, body, name=REPORT, measured=True, located=True):
        """S5: the report, and beside it what a report now needs to stand on:
        the screen file it names and one measurement of that screen. G8: and
        the file each finding is at, when nothing is there yet (`located`
        False leaves a location pointing at nothing)."""
        self.write(os.path.join("docs", "measurements", name),
                   body if isinstance(body, str) else json.dumps(body))
        if measured:
            self.measured()
        findings = body.get("findings") if isinstance(body, dict) and located else None
        for f in findings if isinstance(findings, list) else []:
            at = f.get("location") if isinstance(f, dict) else None
            path = at.rsplit(":", 1)[0] if isinstance(at, str) and pipeline.UI_GATE_LOCATION.fullmatch(at) else ""
            if path and not pipeline._leaves(path) and not os.path.lexists(os.path.join(self.tmp.name, path)):
                self.write(path)

    def measured(self, name=MEASURE, screen="run page", path=SCREEN_FILE, **over):
        self.write(path, json.dumps(screen_file(screen=screen)))
        self.write(os.path.join("docs", "measurements", name), json.dumps(measurement(screen=screen, **over)))

    def rulings(self, body=None):
        self.write(RULINGS_FILE, body if isinstance(body, str) else json.dumps({"rulings": body or RULINGS}))

    def skip(self, reason="a command line tool, nothing is drawn"):
        out = self.run_pipeline("ui-gate", "skip", "scratch", "--reason", reason)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out

    def close(self):
        return self.run_pipeline("phase", "scratch", "8", "done")

    def board(self):
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.json"), encoding="utf-8") as f:
            return json.load(f)

    def assertCloses(self, out):
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.board()["phases"][8]["status"], "done")

    def assertRefused(self, out, *named):
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("phase 8 cannot close", out.stderr)
        for words in named:
            self.assertIn(words, out.stderr)
        self.assertNotEqual(self.board()["phases"][8]["status"], "done")


class TestPhase8NeedsAUiGateReport(UiGateCase):
    # ---------- control ----------

    def test_control_no_surface_and_a_skip_closes(self):
        """Everything else Phase 8 needs is present, so the cases below are about the new rule only."""
        self.branch_changing("tool.py")
        self.phase_8_otherwise_ready()
        self.skip()
        self.assertCloses(self.close())

    # ---------- a rendered surface ----------

    def test_a_changed_page_with_no_report_is_refused_and_named(self):
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()
        out = self.close()
        self.assertRefused(out, "app/page.tsx", "UI gate report")
        # The run log a person reads says it in words too.
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.events.jsonl"), encoding="utf-8") as f:
            checks = [json.loads(line) for line in f if '"check"' in line]
        self.assertIn("app/page.tsx", checks[-1]["note"])

    def test_a_changed_page_with_an_approving_report_closes(self):
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()
        self.report(good_report())
        self.assertCloses(self.close())

    def test_a_changed_page_with_a_blocking_report_is_refused(self):
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()
        self.report(blocking_report())
        self.assertRefused(self.close(), "Block")

    def test_a_report_that_is_not_json_is_refused(self):
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()
        self.report('{"verdict": "Approve"')
        self.assertRefused(self.close(), REPORT, "JSON")

    def test_a_report_with_no_verdict_is_refused(self):
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()
        for body in ({}, {"verdict": "approve-ish"}, ["Approve"]):
            with self.subTest(body=body):
                self.report(body)
                self.assertRefused(self.close(), REPORT)

    def test_a_skip_cannot_stand_when_a_stylesheet_changed(self):
        self.branch_changing("app/site.css")
        self.phase_8_otherwise_ready()
        self.skip()
        self.assertRefused(self.close(), "app/site.css", "skip cannot stand")

    def test_at_most_five_surfaces_are_named(self):
        pages = [f"app/p{i}.tsx" for i in range(7)]
        self.branch_changing(*pages)
        self.phase_8_otherwise_ready()
        out = self.close()
        self.assertRefused(out, "app/p0.tsx", "app/p4.tsx", "2 more")
        self.assertNotIn("app/p5.tsx", out.stderr)

    def test_an_uncommitted_change_to_a_tracked_page_counts(self):
        self.branch_changing("tool.py", on_main=("app/page.tsx",))
        self.phase_8_otherwise_ready()
        self.skip()
        # Before the edit the checks pass (`check` runs them and closes nothing).
        out = self.run_pipeline("check", "scratch", "8")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.write("app/page.tsx", "edited, not committed\n")
        self.assertRefused(self.close(), "app/page.tsx")

    def test_an_untracked_page_counts(self):
        self.branch_changing("tool.py")
        self.phase_8_otherwise_ready()
        self.skip()
        self.write("app/new.tsx")
        self.assertRefused(self.close(), "app/new.tsx")

    # ---------- no rendered surface ----------

    def test_no_surface_with_neither_a_skip_nor_a_report_is_refused_and_names_the_command(self):
        self.branch_changing("tool.py")
        self.phase_8_otherwise_ready()
        self.assertRefused(self.close(), 'ui-gate skip scratch --reason')

    def test_no_surface_with_an_approving_report_closes(self):
        self.branch_changing("tool.py")
        self.phase_8_otherwise_ready()
        self.report(good_report())
        self.assertCloses(self.close())

    def test_a_test_file_is_not_a_surface(self):
        self.branch_changing("app/page.test.tsx")
        self.phase_8_otherwise_ready()
        self.skip()
        self.assertCloses(self.close())

    def test_a_page_under_the_runs_own_artifact_root_is_not_a_surface(self):
        """The run's board is an .html file under the root, and so is a prototype."""
        self.branch_changing("tool.py", "docs/prototypes/x.html")
        self.phase_8_otherwise_ready()
        self.skip()
        self.assertCloses(self.close())

    # ---------- the project's own list ----------

    def test_a_project_config_replaces_the_default_surfaces(self):
        """Gate 6: the config is the default branch's. It was written by the
        run, untracked, which is how a run exempted itself."""
        self.branch_changing("app/page.tsx", on_main={"ui-gate/config.json": json.dumps({"surfaces": ["web/**"]})})
        self.phase_8_otherwise_ready()
        self.skip()
        # Before the edit the checks pass (`check` runs them and closes nothing).
        out = self.run_pipeline("check", "scratch", "8")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.write("web/tool.py")
        self.assertRefused(self.close(), "web/tool.py")

    def test_a_project_config_that_cannot_be_read_is_refused(self):
        """Gate 6: on the default branch, as above."""
        self.branch_changing("tool.py", on_main={"ui-gate/config.json": "{not json"})
        self.phase_8_otherwise_ready()
        self.skip()
        self.assertRefused(self.close(), "ui-gate/config.json")

    # ---------- changed files unknown ----------

    def test_no_origin_and_no_local_main_or_master_is_refused(self):
        self.git("init", "-q", "-b", "trunk")
        self.commit("first.txt")
        self.git("checkout", "-q", "-b", "feature/alydas-223-s2")
        self.commit_file("tool.py")
        self.phase_8_otherwise_ready()
        self.skip()
        self.assertRefused(self.close(), "could not be worked out", "default branch")

    def test_outside_a_repository_is_refused(self):
        self.phase_8_otherwise_ready()
        self.skip()
        self.assertRefused(self.close(), "could not be worked out")

    # ---------- force ----------

    def test_force_still_closes_past_every_check(self):
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()
        out = self.run_pipeline("phase", "scratch", "8", "done", "--force", "Sachit looked at the page himself")
        self.assertCloses(out)


class TestPhase8ReadsTheReport(UiGateCase):
    """S2, B. Phase 8 runs check_report on the report it requires."""

    def test_a_malformed_approving_report_is_refused_and_each_problem_is_named(self):
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()
        report = good_report(findings=[finding(severity="HIGH", rule="A11Y-ANNOUNCE")])
        del report["notVerified"]
        self.report(report)
        self.assertRefused(self.close(), REPORT, "finding 1 (A11Y-ANNOUNCE) is HIGH with no ruling", "notVerified is missing")

    def test_the_same_report_mended_closes(self):
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()
        self.report(good_report(findings=[finding(severity="HIGH", rule="A11Y-ANNOUNCE", ruling="ruling-7")]))
        self.rulings()
        self.assertCloses(self.close())

    def test_a_malformed_report_on_a_run_with_no_surface_and_no_skip_is_refused(self):
        self.branch_changing("tool.py")
        self.phase_8_otherwise_ready()
        self.report(good_report(verdict="Approve", coverage=[]))
        self.assertRefused(self.close(), REPORT, "coverage does not name Layout")


class TestUiGateCheck(UiGateCase):
    """S2, B. `ui-gate check <slug>`: the report's form, by hand."""

    def check(self):
        return self.run_pipeline("ui-gate", "check", "scratch")

    def test_a_good_report_exits_0_with_the_verdict_and_the_counts(self):
        self.init_scratch()
        self.write("app/page.tsx")
        self.rulings()
        self.report(good_report(findings=[finding(severity="HIGH", ruling="ruling-1"), finding(), finding(),
                                          finding(severity="LOW")]))
        out = self.check()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("✓", out.stdout)
        self.assertIn(REPORT, out.stdout)
        self.assertIn("Approve", out.stdout)
        self.assertIn("1 HIGH, 2 MEDIUM, 1 LOW", out.stdout)
        self.assertNotIn("✗", out.stdout)

    def test_a_well_formed_blocking_report_exits_0_and_says_block(self):
        """`check` is about the report's form; Phase 8 is what a Block stops."""
        self.init_scratch()
        self.report(blocking_report())
        out = self.check()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("Block", out.stdout)
        self.assertIn("1 HIGH, 0 MEDIUM, 0 LOW", out.stdout)

    def test_each_problem_is_a_cross_line_and_the_exit_is_2(self):
        self.init_scratch()
        report = good_report(verdict="Perhaps")
        del report["notVerified"]
        self.report(report)
        out = self.check()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        crosses = [line for line in out.stdout.splitlines() if line.startswith("✗ ")]
        self.assertEqual(len(crosses), 2, out.stdout)
        self.assertIn("notVerified is missing", out.stdout)
        self.assertIn("neither Approve nor Block", out.stdout)
        self.assertNotIn("✓", out.stdout)

    def test_no_report_exits_2_and_says_where_one_goes(self):
        self.init_scratch()
        out = self.check()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("✗", out.stdout)
        self.assertIn("measurements/<date>-scratch-ui-gate-report[-<screen>].json", out.stdout)

    def test_a_report_that_is_not_json_exits_2(self):
        self.init_scratch()
        self.report('{"verdict": "Approve"')
        out = self.check()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("JSON", out.stdout)

    def test_every_report_is_read_and_each_gets_its_own_result(self):
        """S5, B. This was S2's test_the_last_report_when_sorted_is_the_one_read:
        a later good report no longer hides an earlier one that is wrong."""
        self.init_scratch()
        self.report(good_report(verdict="Perhaps"))
        later = "2026-09-14-scratch-ui-gate-report.json"
        self.report(good_report(), name=later)
        out = self.check()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        lines = out.stdout.splitlines()
        self.assertEqual([line for line in lines if line.startswith("✗ ")],
                         [f"✗ {REPORT}: the verdict is neither Approve nor Block (it is Perhaps)"])
        ticks = [line for line in lines if line.startswith("✓ ")]
        self.assertEqual(len(ticks), 1, out.stdout)
        self.assertIn(later, ticks[0])
        self.assertIn("Approve", ticks[0])

    def test_control_two_good_reports_exit_0_with_a_tick_each(self):
        self.init_scratch()
        self.report(good_report())
        self.report(good_report(), name="2026-09-14-scratch-ui-gate-report.json")
        out = self.check()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(len([line for line in out.stdout.splitlines() if line.startswith("✓ ")]), 2, out.stdout)

    def test_check_changes_nothing_on_the_run(self):
        self.init_scratch()
        self.report(good_report())
        before = self.board()
        self.check()
        self.assertEqual(self.board(), before)


class TestSkippingPhase8(UiGateCase):
    """S2, ruling 1. A run that touched a screen cannot skip the review."""

    def skipped(self, n="8"):
        return self.run_pipeline("phase", "scratch", n, "skipped", "--note", "nothing to review")

    def test_control_a_run_with_no_surface_still_skips_phase_8(self):
        self.branch_changing("tool.py")
        self.init_scratch()
        out = self.skipped()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.board()["phases"][8]["status"], "skipped")

    def test_a_run_that_changed_a_page_cannot_skip_phase_8(self):
        self.branch_changing("app/page.tsx")
        self.init_scratch()
        out = self.skipped()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("a run that touched a screen cannot skip the review", out.stderr)
        self.assertIn("app/page.tsx", out.stderr)
        self.assertNotEqual(self.board()["phases"][8]["status"], "skipped")

    def test_at_most_five_files_are_named(self):
        self.branch_changing(*[f"app/p{i}.tsx" for i in range(7)])
        self.init_scratch()
        out = self.skipped()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        for words in ("app/p0.tsx", "app/p4.tsx", "2 more"):
            self.assertIn(words, out.stderr)
        self.assertNotIn("app/p5.tsx", out.stderr)

    def test_unknown_changed_files_cannot_skip_phase_8(self):
        """Outside a repository nothing says the run did not touch a screen."""
        self.init_scratch()
        out = self.skipped()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("cannot skip the review", out.stderr)
        self.assertIn("could not be worked out", out.stderr)
        self.assertNotEqual(self.board()["phases"][8]["status"], "skipped")

    def test_another_phase_skips_as_before_on_a_run_that_changed_a_page(self):
        self.branch_changing("app/page.tsx")
        self.init_scratch()
        out = self.skipped("7")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.board()["phases"][7]["status"], "skipped")


class TestTheBaseWithNoOrigin(UiGateCase):
    """S2, ruling 2. No origin default: a local main, then master, is the base."""

    def local_only(self, default, *committed):
        self.git("init", "-q", "-b", default)
        self.commit("first.txt")
        self.git("checkout", "-q", "-b", "feature/alydas-223-s2")
        for relative in committed:
            self.commit_file(relative)

    def test_a_local_main_is_the_base_and_a_changed_page_is_named(self):
        self.local_only("main", "app/page.tsx")
        self.phase_8_otherwise_ready()
        out = self.close()
        self.assertRefused(out, "app/page.tsx", "UI gate report")
        self.assertNotIn("could not be worked out", out.stderr)

    def test_a_local_main_is_the_base_and_a_run_with_no_surface_closes(self):
        """first.txt is on main, so only tool.py is changed; were the base
        unknown this would be refused."""
        self.local_only("main", "tool.py")
        self.phase_8_otherwise_ready()
        self.skip()
        self.assertCloses(self.close())

    def test_a_local_master_is_the_base_when_there_is_no_main(self):
        self.local_only("master", "app/page.tsx")
        self.phase_8_otherwise_ready()
        out = self.close()
        self.assertRefused(out, "app/page.tsx", "UI gate report")
        self.assertNotIn("could not be worked out", out.stderr)

    def test_main_is_tried_before_master(self):
        """master holds the page already; against main the page is this run's change."""
        self.git("init", "-q", "-b", "main")
        self.commit("first.txt")
        self.git("checkout", "-q", "-b", "master")
        self.commit_file("app/page.tsx")
        self.git("checkout", "-q", "-b", "feature/alydas-223-s2")
        self.commit_file("tool.py")
        self.phase_8_otherwise_ready()
        self.skip()
        self.assertRefused(self.close(), "app/page.tsx", "skip cannot stand")

    def test_head_on_the_only_local_branch_is_unknown(self):
        """On main itself, main is no base: its merge-base with HEAD is HEAD."""
        self.git("init", "-q", "-b", "main")
        self.commit("first.txt")
        self.commit_file("app/page.tsx")
        self.phase_8_otherwise_ready()
        self.skip()
        self.assertRefused(self.close(), "could not be worked out")

    def test_control_a_repository_with_an_origin_still_uses_origin(self):
        """Local main is one page ahead of origin/main, never pushed. Against
        origin the page is this branch's change; against local main it is not."""
        self.repo_with_origin()
        self.commit_file("app/page.tsx")
        self.git("checkout", "-q", "-b", "feature/alydas-223-s2")
        self.commit_file("tool.py")
        self.phase_8_otherwise_ready()
        self.skip()
        self.assertRefused(self.close(), "app/page.tsx", "skip cannot stand")


class TestHoldAppliesTheGate(UiGateCase):
    """S2, ruling 3. `hold <slug> 8` runs the same checks before it asks, and
    with no desk set it stops straight after them (exit 4), so nothing waits."""

    NO_DESK = 4

    def phase_8_running(self):
        self.init_scratch("--hold", "8")
        self.write(os.path.join("docs", "measurements", "2026-09-13-scratch-review.md"),
                   "# Review\n\nOne finding, ruled defer.\n")
        self.assertEqual(self.run_pipeline("phase", "scratch", "8", "start").returncode, 0)
        out = self.run_pipeline("note", "scratch", "8", "--plain", "The reviewers found one thing to do later.")
        self.assertEqual(out.returncode, 0, out.stderr)
        # ALYDAS-225, K1. Its steps ticked: a hold refuses a step still to-do,
        # and these cases are about the UI gate.
        for i in range(1, len(pipeline.PHASE_STEPS[8]) + 1):
            self.assertEqual(self.run_pipeline("step", "scratch", "8", str(i), "done").returncode, 0)
        # ALYDAS-174, S1. And the review recorded for the desk.
        self.record_a_review()

    def hold(self):
        return self.run_pipeline("hold", "scratch", "8")

    def test_control_hold_8_on_a_run_with_no_surface_and_a_skip_passes_the_gate(self):
        self.branch_changing("tool.py")
        self.phase_8_running()
        self.skip()
        out = self.hold()
        self.assertEqual(out.returncode, self.NO_DESK, out.stdout + out.stderr)
        self.assertIn("no desk is set", out.stdout)
        self.assertNotIn("cannot close", out.stderr)

    def test_hold_8_on_a_changed_page_with_no_report_is_refused(self):
        self.branch_changing("app/page.tsx")
        self.phase_8_running()
        out = self.hold()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("phase 8 cannot close", out.stderr)
        self.assertIn("app/page.tsx", out.stderr)
        self.assertNotIn("is still to do", out.stderr)  # ALYDAS-225, K1: refused for the gate alone
        self.assertNotIn("no desk is set", out.stdout)

    def test_hold_8_on_a_changed_page_with_a_malformed_report_is_refused(self):
        self.branch_changing("app/page.tsx")
        self.phase_8_running()
        self.report(good_report(findings=[finding(severity="HIGH")]))
        out = self.hold()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("is HIGH with no ruling", out.stderr)
        self.assertNotIn("is still to do", out.stderr)  # ALYDAS-225, K1: refused for the report alone

    def test_hold_8_on_a_changed_page_with_a_good_report_passes_the_gate(self):
        self.branch_changing("app/page.tsx")
        self.phase_8_running()
        self.report(good_report())
        out = self.hold()
        self.assertEqual(out.returncode, self.NO_DESK, out.stdout + out.stderr)
        self.assertIn("no desk is set", out.stdout)


class CheckReportCase(unittest.TestCase):
    """check_report with the context a report now stands on (S5)."""

    def refused(self, report, *words, **over):
        problems = pipeline.check_report(report, **context(**over))
        self.assertEqual(len(problems), 1, problems)
        for word in words:
            self.assertIn(word, problems[0])
        return problems[0]

    def passes(self, report, **over):
        self.assertEqual(pipeline.check_report(report, **context(**over)), [])


class TestCheckReport(CheckReportCase):
    """S2, A. check_report: data in, a list of problems out. Every refusal is
    one report broken in exactly one way, and exactly one problem comes back."""

    def two_findings(self):
        return good_report(findings=[finding(), finding(rule="A11Y-ANNOUNCE", location="app/page.tsx:7",
                                                        before="the icon button has no name",
                                                        why="a screen reader says only button")])

    # ---------- the good report ----------

    def test_the_good_report_passes(self):
        self.passes(good_report())
        self.passes(good_report(findings=[], coverage=[{"domain": d, "inspected": "x", "result": "clear"} for d in DOMAINS],
                                notVerified=[]))

    def test_rulings_and_measurements_are_accepted(self):
        """S5: they are read now. With no measurement the screen was not measured."""
        self.assertEqual(pipeline.check_report(good_report(), **context(rulings=[])), [])
        self.assertEqual(pipeline.check_report(good_report(), **context(rulings=None)), [])
        for bare in (pipeline.check_report(good_report(), rulings=[], measurements={}),
                     pipeline.check_report(good_report(), None, None)):
            self.assertTrue(any("was not measured" in problem for problem in bare), bare)

    def test_the_seven_domains_are_the_seven(self):
        """A floor: the coverage loops below go over these."""
        self.assertEqual(tuple(pipeline.UI_GATE_DOMAINS), DOMAINS)

    # ---------- a finding's fields ----------

    def test_a_finding_missing_a_field_is_refused_by_index_and_rule(self):
        for field in ("severity", "domain", "location", "before", "after", "why", "source"):
            with self.subTest(field=field):
                report = self.two_findings()
                del report["findings"][1][field]
                self.refused(report, "finding 2 (A11Y-ANNOUNCE)", f"has no {field}")

    def test_a_finding_blank_in_a_field_is_refused(self):
        for field in ("severity", "domain", "location", "before", "after", "why", "source"):
            for blank in ("", "   ", None):
                with self.subTest(field=field, blank=blank):
                    report = self.two_findings()
                    report["findings"][1][field] = blank
                    self.refused(report, "finding 2 (A11Y-ANNOUNCE)", f"has no {field}")

    def test_a_finding_with_no_rule_is_refused_by_index(self):
        for change in ("missing", "", "  "):
            with self.subTest(change=change):
                report = self.two_findings()
                if change == "missing":
                    del report["findings"][1]["rule"]
                else:
                    report["findings"][1]["rule"] = change
                problem = self.refused(report, "finding 2", "has no rule")
                self.assertNotIn("A11Y", problem)

    def test_a_severity_outside_the_three_is_refused(self):
        for severity in ("CRITICAL", "high", "Medium"):
            with self.subTest(severity=severity):
                self.refused(good_report(findings=[finding(severity=severity)]),
                             "finding 1 (A11Y-STRUCTURE)", f"severity {severity}", "HIGH, MEDIUM or LOW")
        for severity in ("MEDIUM", "LOW"):
            self.passes(good_report(findings=[finding(severity=severity)]))

    def test_a_source_outside_the_two_is_refused(self):
        self.refused(good_report(findings=[finding(source="guessed")]),
                     "finding 1 (A11Y-STRUCTURE)", "source guessed", "measured or source")
        self.passes(good_report(findings=[finding(source="source")]))
        # S6: a finding marked measured stands on a measured failure it answers.
        self.passes(good_report(findings=[answered("button.save")]),
                    measurements={MEASURE: measurement(failures=[failure("A11Y-STRUCTURE", "button.save")])})

    def test_a_location_not_shaped_path_line_is_refused(self):
        for location in ("app/page.tsx", "app/page.tsx:", "app/page.tsx:0", "app/page.tsx:-3", "app/page.tsx:4a",
                         ":42", "app/page.tsx:4.5", "app/page.tsx:42:7 extra"):
            with self.subTest(location=location):
                self.refused(good_report(findings=[finding(location=location)]),
                             "finding 1 (A11Y-STRUCTURE)", "location", "path:line")
        for location in ("app/page.tsx:1", "a.css:12345", "app/[id]/page.tsx:9"):
            with self.subTest(location=location):
                self.passes(good_report(findings=[finding(location=location)]))

    def test_a_ruling_that_is_neither_null_nor_an_id_is_refused(self):
        for ruling in ("", "  ", 7, True):
            with self.subTest(ruling=ruling):
                self.refused(good_report(findings=[finding(ruling=ruling)]), "finding 1 (A11Y-STRUCTURE)", "ruling")

    # ---------- the cap ----------

    def test_fifteen_findings_pass_and_sixteen_are_refused(self):
        self.passes(good_report(findings=[finding(location=f"app/page.tsx:{i + 1}") for i in range(15)]))
        self.refused(good_report(findings=[finding(location=f"app/page.tsx:{i + 1}") for i in range(16)]),
                     "16 findings", "15")

    # ---------- notVerified ----------

    def test_a_missing_not_verified_is_refused_and_an_empty_one_passes(self):
        report = good_report()
        del report["notVerified"]
        self.refused(report, "notVerified is missing")
        self.passes(good_report(notVerified=[]))

    def test_a_not_verified_entry_that_is_also_a_finding_is_refused(self):
        for field in ("why", "before"):
            with self.subTest(field=field):
                report = good_report()
                report["notVerified"] = ["something else that was not rendered", "  " + report["findings"][0][field] + " "]
                self.refused(report, "notVerified entry 2", "finding 1 (A11Y-STRUCTURE)", field, "not rendered")

    def test_a_not_verified_that_is_not_a_list_of_text_is_refused(self):
        for value in ("none", None, [""], [3]):
            with self.subTest(value=value):
                self.refused(good_report(notVerified=value), "notVerified")

    # ---------- coverage ----------

    def test_each_domain_left_out_is_refused_by_name(self):
        for domain in DOMAINS[1:]:
            with self.subTest(domain=domain):
                report = good_report()
                report["coverage"] = [row for row in report["coverage"] if row["domain"] != domain]
                self.refused(report, f"coverage does not name {domain}")

    def test_a_domain_named_twice_is_refused(self):
        report = good_report()
        report["coverage"].append({"domain": "colors", "inspected": "again", "result": "clear"})
        self.refused(report, "coverage names Colors 2 times")

    def test_a_domain_that_is_not_one_of_the_seven_is_refused(self):
        report = good_report()
        report["coverage"].append({"domain": "Motion", "inspected": "x", "result": "clear"})
        self.refused(report, "Motion", "not one of the seven")

    def test_domains_match_whatever_their_case(self):
        report = good_report()
        for row in report["coverage"]:
            row["domain"] = row["domain"].upper()
        report["findings"][0]["domain"] = "accessibility"
        self.passes(report)

    def test_a_result_outside_the_three_is_refused(self):
        self.refused(covered(good_report(), "Layout", "fine"), "Layout", "result fine", "clear, findings or not-reviewed")
        report = good_report()
        del report["coverage"][1]["result"]
        self.refused(report, "Layout", "no result")

    def test_clear_while_a_finding_names_the_domain_is_refused(self):
        self.refused(covered(good_report(), "Accessibility", "clear"),
                     "Accessibility is clear", "finding 1 (A11Y-STRUCTURE)")

    def test_findings_with_no_finding_is_refused(self):
        self.refused(covered(good_report(), "Layout", "findings"), "Layout has findings", "no finding names it")

    # ---------- the verdict ----------

    def test_approve_with_an_unruled_high_is_refused(self):
        self.refused(good_report(findings=[finding(severity="HIGH")]),
                     "Approve", "finding 1 (A11Y-STRUCTURE) is HIGH with no ruling")

    def test_approve_with_a_ruled_high_passes(self):
        """S5: the id is looked up now, and the fixture's rulings record it."""
        self.passes(good_report(findings=[finding(severity="HIGH", ruling="ruling-7")]))

    def test_approve_with_a_domain_not_reviewed_is_refused(self):
        self.refused(covered(good_report(), "Layout", "not-reviewed"), "Approve", "Layout was not reviewed")

    def test_block_with_no_unruled_high_is_refused(self):
        self.refused(good_report(verdict="Block"), "Block", "no HIGH finding is without a ruling")
        self.refused(good_report(verdict="Block", findings=[finding(severity="HIGH", ruling="ruling-7")]),
                     "Block", "no HIGH finding is without a ruling")

    def test_block_with_an_unruled_high_passes(self):
        self.passes(blocking_report())
        self.passes(covered(blocking_report(), "Layout", "not-reviewed"))

    def test_a_verdict_that_is_neither_is_refused(self):
        for verdict in ("Perhaps", "approve", "", None, 1):
            with self.subTest(verdict=verdict):
                self.refused(good_report(verdict=verdict), "neither Approve nor Block")
        report = good_report()
        del report["verdict"]
        self.refused(report, "neither Approve nor Block")

    # ---------- not a report at all ----------

    def test_what_is_not_a_report_is_refused_and_never_raises(self):
        for report in (None, [], "Approve", {}, {"verdict": "Approve"}, good_report(findings="none"),
                       good_report(findings=["a string"]), good_report(coverage="all"), good_report(coverage=[7])):
            with self.subTest(report=report):
                self.assertTrue(pipeline.check_report(report))
                self.assertTrue(pipeline.check_report(report, **context()))


class TestCheckReportMeetsTheProject(CheckReportCase):
    """S5, C to F. The report against the project's rulings, the screen's
    measurements and its declared jobs. Same shape as above: one report wrong
    in exactly one way, exactly one problem back, and a control beside it."""

    def cited(self, **over):
        """A COLOR-TREATMENT finding on the Stop button, citing stop-is-plain.
        Gate 6: HIGH, as the rule book marks the rule (it was MEDIUM)."""
        return cited(**over)

    # ---------- C. rulings ----------

    def test_control_a_finding_citing_a_real_ruling_for_its_rule_and_file_passes(self):
        self.passes(self.cited())
        self.passes(self.cited(location="apps/desk/app/work/now-strip.css:40"))

    def test_a_forged_ruling_id_is_refused(self):
        problem = self.refused(self.cited(ruling="stop-is-fine"), "finding 1 (COLOR-TREATMENT)", "stop-is-fine",
                               "no such ruling is recorded")
        self.assertNotIn("stop-is-plain", problem)
        # No rulings at all, and the same id that passes above is forged too.
        for none in (None, []):
            with self.subTest(rulings=none):
                self.refused(self.cited(), "stop-is-plain", "no such ruling is recorded", rulings=none)

    def test_a_real_ruling_cited_for_the_wrong_rule_is_refused(self):
        """What stop-is-plain does not cover: no confirmation and no undo on Stop."""
        self.refused(self.cited(domain="Writing", rule="WRITE-DESTRUCTIVE", severity="HIGH"),
                     "finding 1 (WRITE-DESTRUCTIVE)", "stop-is-plain", "covers the rule COLOR-TREATMENT")

    def test_a_real_ruling_cited_for_the_wrong_file_is_refused(self):
        self.refused(self.cited(location="apps/desk/app/runs/[id]/run-page.css:12"),
                     "finding 1 (COLOR-TREATMENT)", "stop-is-plain", "apps/desk/app/runs/[id]/run-page.css",
                     "covers the files " + CONTROLS + " and apps/desk/app/work/now-strip.css")
        # A file that only starts like a covered one is another file.
        self.refused(self.cited(location=CONTROLS + ".bak:12"), "covers the files")

    def test_a_ruling_whose_where_names_a_missing_file_is_refused(self):
        self.refused(self.cited(), "finding 1 (COLOR-TREATMENT)", "stop-is-plain", "points nowhere", CONTROLS,
                     file_lines={"app/page.tsx": lines_of(60)}.get)
        self.refused(self.cited(), "points nowhere", file_lines=None)

    def test_a_ruling_whose_where_names_a_line_is_refused_and_says_the_file_alone(self):
        """ALYDAS-455. A line moves when a line is added above it, so a where is
        the file alone; one still written path:line is refused in words."""
        for where in (CONTROLS + ":12", CONTROLS + ":1"):
            with self.subTest(where=where):
                self.refused(self.cited(), "stop-is-plain", "points nowhere", where, "the file alone",
                             rulings=[ruling(where=where)])
        self.passes(self.cited(), rulings=[ruling(where=CONTROLS)])

    def test_a_ruling_whose_where_is_not_a_path_is_refused(self):
        for where in ("", "   ", 12, None, ["x"]):
            with self.subTest(where=where):
                self.refused(self.cited(), "stop-is-plain", "points nowhere",
                             rulings=[ruling(where=where)])

    def test_a_high_with_a_valid_ruling_and_the_verdict_approve_is_accepted(self):
        self.passes(self.cited(severity="HIGH"))
        # The same HIGH with no ruling is what Approve cannot carry.
        self.refused(self.cited(severity="HIGH", ruling=None), "is HIGH with no ruling")

    def test_a_report_citing_no_ruling_needs_no_rulings(self):
        for none in (None, []):
            with self.subTest(rulings=none):
                self.passes(good_report(), rulings=none, file_lines=None)

    # ---------- D. measured failures are answered ----------

    def measured(self, *failures, **over):
        return {"measurements": {MEASURE: measurement(failures=failures, **over)}}

    def test_an_unanswered_measured_failure_is_refused_by_rule_where_and_file(self):
        self.refused(good_report(), "COLOR-CONTRAST", MEASURE, "button.btn 12.5px", **self.measured(failure()))

    def test_each_distinct_unanswered_pair_is_refused_once(self):
        """S6, a. This was S5's test_each_distinct_unanswered_rule_is_refused_once,
        which counted one problem a rule. Now each (rule, where) is its own, and
        the same pair in two contexts and two files is still one."""
        first = measurement(failures=[failure(), failure(where="span.rail-badge 10px"),
                                      failure("A11Y-TARGET", "a.run-page-item")])
        first["contexts"]["desktop-light"]["failures"] = [failure()]
        second = "2026-09-13-scratch-ui-measure-run-page-live.json"
        problems = pipeline.check_report(good_report(), **context(
            measurements={MEASURE: first, second: measurement(failures=[failure()])}))
        self.assertEqual(len(problems), 3, problems)
        for rule, where in (("COLOR-CONTRAST", "button.btn 12.5px"), ("COLOR-CONTRAST", "span.rail-badge 10px"),
                            ("A11Y-TARGET", "a.run-page-item")):
            self.assertEqual(len([p for p in problems if rule in p and where in p]), 1, problems)

    def test_a_measured_failure_answered_by_a_measured_finding_is_accepted(self):
        self.passes(good_report(findings=[answered("button.save")]),
                    **self.measured(failure("A11Y-STRUCTURE", "button.save")))

    def test_a_measured_failure_answered_by_a_ruled_finding_is_accepted(self):
        self.passes(self.cited(source="measured", answers=["button.btn"]),
                    **self.measured(failure("COLOR-TREATMENT", "button.btn")))

    def test_a_measured_rule_answered_only_from_the_source_is_refused(self):
        """The finding names the rule, and says it was read in the code, not measured."""
        self.refused(good_report(findings=[finding(source="source")]), "A11Y-STRUCTURE", MEASURE, "button.save",
                     "measured", **self.measured(failure("A11Y-STRUCTURE", "button.save")))

    # ---------- S6, a. each measured failure, not each rule ----------

    def two_places(self):
        return self.measured(failure("A11Y-STRUCTURE", "button.save"), failure("A11Y-STRUCTURE", "a.run-page-item"))

    def test_a_rule_answered_in_one_place_and_failing_in_two_is_refused_for_the_other(self):
        """What S5 passed: one finding for the rule vouched for every place it failed."""
        problem = self.refused(good_report(findings=[answered("button.save")]),
                               "A11Y-STRUCTURE", "a.run-page-item", MEASURE, "not answered", **self.two_places())
        self.assertNotIn("button.save", problem)

    def test_control_both_places_answered_by_one_finding_or_by_two_passes(self):
        self.passes(good_report(findings=[answered("button.save", "a.run-page-item")]), **self.two_places())
        self.passes(good_report(findings=[answered("button.save"),
                                          answered("a.run-page-item", location="app/page.tsx:9")]),
                    **self.two_places())

    def test_a_place_answered_under_another_rule_is_not_answered(self):
        """The where is right and the rule is not: A11Y-ANNOUNCE answers nothing A11Y-STRUCTURE measured."""
        problems = pipeline.check_report(
            good_report(findings=[answered("button.save"), answered("a.run-page-item", rule="A11Y-ANNOUNCE")]),
            **context(**self.measured(failure("A11Y-STRUCTURE", "button.save"), failure("A11Y-STRUCTURE", "a.run-page-item"),
                                      failure("A11Y-ANNOUNCE", "input.search"))))
        self.assertEqual(len(problems), 3, problems)
        self.assertTrue(any("A11Y-STRUCTURE" in p and "a.run-page-item" in p and "not answered" in p for p in problems), problems)
        self.assertTrue(any("A11Y-ANNOUNCE" in p and "input.search" in p and "not answered" in p for p in problems), problems)
        self.assertTrue(any("finding 2 (A11Y-ANNOUNCE)" in p and "a.run-page-item" in p and "measured no" in p
                            for p in problems), problems)

    def test_an_answer_that_matches_no_measured_failure_of_its_rule_is_refused(self):
        self.refused(good_report(findings=[answered("button.save", "button.gone")]),
                     "finding 1 (A11Y-STRUCTURE)", "button.gone", "measured no", "A11Y-STRUCTURE",
                     **self.measured(failure("A11Y-STRUCTURE", "button.save")))
        # A note is not a failure: it cannot be what a finding answers.
        self.refused(good_report(findings=[answered("button.save", "input")]),
                     "finding 1 (A11Y-STRUCTURE)", "input", "measured no",
                     measurements={MEASURE: measurement(failures=[failure("A11Y-STRUCTURE", "button.save")],
                                                        notes=[failure("A11Y-STRUCTURE", "input")])})

    def test_a_measured_finding_with_no_answers_is_refused(self):
        for label, change in (("missing", lambda f: f.pop("answers")), ("empty", lambda f: f.update(answers=[])),
                              ("null", lambda f: f.update(answers=None))):
            with self.subTest(answers=label):
                f = answered("button.save")
                change(f)
                self.refused(good_report(findings=[f, answered("button.save", location="app/page.tsx:9")]),
                             "finding 1 (A11Y-STRUCTURE)", "measured", "answers",
                             **self.measured(failure("A11Y-STRUCTURE", "button.save")))

    def test_answers_that_are_not_a_list_of_text_are_refused(self):
        for value in ("button.save", ["button.save", ""], ["button.save", 3], {"where": "button.save"}):
            with self.subTest(answers=value):
                self.refused(good_report(findings=[answered(answers=value),
                                                   answered("button.save", location="app/page.tsx:9")]),
                             "finding 1 (A11Y-STRUCTURE)", "answers", "list",
                             **self.measured(failure("A11Y-STRUCTURE", "button.save")))

    def test_a_finding_read_from_the_source_carries_no_answers(self):
        self.refused(good_report(findings=[finding(answers=["button.save"])]),
                     "finding 1 (A11Y-STRUCTURE)", "source", "answers")
        self.refused(good_report(findings=[finding(answers=[])]), "finding 1 (A11Y-STRUCTURE)", "source", "answers")
        self.passes(good_report(findings=[finding()]))

    # ---------- S6, b. a measured finding belongs to its screen ----------

    def test_a_measured_finding_whose_rule_did_not_fail_on_this_screen_is_refused(self):
        """The rule failed, at that very place, on another screen: that is the
        other screen's failure. And a note of the rule here is not a failure."""
        shell = "2026-09-13-scratch-ui-measure-shell-default.json"
        there = measurement(failures=[failure("A11Y-STRUCTURE", "button.save")], screen="shell")
        self.refused(good_report(findings=[answered("button.save")]),
                     "finding 1 (A11Y-STRUCTURE)", "measured", "run page", "no measurement",
                     measurements={MEASURE: measurement(notes=[failure("A11Y-STRUCTURE", "button.save")]), shell: there})

    def test_control_the_same_finding_when_the_rule_failed_on_this_screen_passes(self):
        shell = "2026-09-13-scratch-ui-measure-shell-default.json"
        self.passes(good_report(findings=[answered("button.save")]),
                    measurements={MEASURE: measurement(failures=[failure("A11Y-STRUCTURE", "button.save")]),
                                  shell: measurement(screen="shell")})

    def test_a_note_needs_no_answer(self):
        self.passes(good_report(), **self.measured(notes=[failure("A11Y-TARGET", "input"), failure()]))

    def test_a_failure_in_any_context_or_file_of_the_screen_needs_an_answer(self):
        first = measurement()
        first["contexts"]["desktop-light"]["failures"] = [failure()]
        self.refused(good_report(), "COLOR-CONTRAST", MEASURE, measurements={MEASURE: first})
        second = "2026-09-13-scratch-ui-measure-run-page-live.json"
        self.refused(good_report(), "COLOR-CONTRAST", second,
                     measurements={MEASURE: measurement(), second: measurement(failures=[failure()])})

    def test_another_screen_s_failures_need_no_answer_here(self):
        shell = "2026-09-13-scratch-ui-measure-shell-default.json"
        self.passes(good_report(), measurements={MEASURE: measurement(),
                                                 shell: measurement(failures=[failure()], screen="shell")})

    def test_a_screen_never_measured_is_refused(self):
        shell = "2026-09-13-scratch-ui-measure-shell-default.json"
        for measurements in (None, {}, {shell: measurement(screen="shell")}):
            with self.subTest(measurements=measurements):
                self.refused(good_report(), "run page", "was not measured", measurements=measurements)

    def test_a_report_naming_no_screen_is_refused(self):
        for blank in ("", "  ", None, 7):
            with self.subTest(screen=blank):
                self.refused(good_report(screen=blank), "names no screen")

    # ---------- E. the jobs are declared ----------

    def screens(self, held):
        return {"screen_file": {SCREEN_FILE: held}.get}

    def test_no_screen_file_is_refused(self):
        report = good_report()
        del report["screenFile"]
        self.refused(report, "screenFile")
        for blank in ("", "  ", None, 7):
            with self.subTest(screenFile=blank):
                self.refused(good_report(screenFile=blank), "screenFile")

    def test_a_screen_file_that_does_not_exist_is_refused(self):
        self.refused(good_report(screenFile="ui-gate/run-pane.json"), "ui-gate/run-pane.json", "does not exist")
        self.refused(good_report(), SCREEN_FILE, "does not exist", screen_file=None)

    def test_a_screen_file_that_does_not_parse_is_refused(self):
        self.refused(good_report(), SCREEN_FILE, "is not JSON that can be read", "line 1",
                     **self.screens(pipeline.Unreadable("Expecting value: line 1 column 1 (char 0)")))
        self.refused(good_report(), SCREEN_FILE, "not a JSON object", **self.screens(["run page"]))

    def test_a_screen_file_with_no_jobs_is_refused(self):
        for jobs in ([], None, "see the run", [""], ["  ", 3]):
            with self.subTest(jobs=jobs):
                self.refused(good_report(), SCREEN_FILE, "the jobs are missing", **self.screens(screen_file(jobs=jobs)))
        held = screen_file()
        del held["jobs"]
        self.refused(good_report(), SCREEN_FILE, "the jobs are missing", **self.screens(held))
        self.passes(good_report(), **self.screens(screen_file(jobs=["See whether the run needs me"])))

    def test_a_screen_file_for_another_screen_is_refused(self):
        self.refused(good_report(), SCREEN_FILE, "shell", "run page", **self.screens(screen_file(screen="shell")))

    def test_approve_with_placement_not_reviewed_is_refused(self):
        self.refused(covered(good_report(), "Placement", "not-reviewed"), "Approve", "Placement was not reviewed")

    # ---------- F. two rulings on S2 ----------

    def test_a_finding_in_a_domain_outside_the_seven_is_refused(self):
        self.refused(good_report(findings=[finding(), finding(domain="Motion", rule="UI-MOTION")]),
                     "finding 2 (UI-MOTION)", "Motion", "not one of the seven domains")

    def test_a_finding_in_a_domain_not_reviewed_is_refused(self):
        report = good_report(findings=[finding(), finding(domain="layout", rule="LAYOUT-GROUP")], verdict="Block")
        report["findings"][0]["severity"] = "HIGH"
        self.refused(covered(report, "Layout", "not-reviewed"), "finding 2 (LAYOUT-GROUP)", "Layout",
                     "was not reviewed")
        self.passes(covered(report, "Layout", "findings"))

    def test_a_row_that_is_clear_or_has_findings_says_what_was_inspected(self):
        for domain, result in (("Layout", "clear"), ("Accessibility", "findings")):
            for blank in ("", "   ", None, "missing"):
                with self.subTest(result=result, inspected=blank):
                    report = good_report()
                    row = next(row for row in report["coverage"] if row["domain"] == domain)
                    self.assertEqual(row["result"], result)
                    if blank == "missing":
                        del row["inspected"]
                    else:
                        row["inspected"] = blank
                    self.refused(report, domain, result if result == "clear" else "has findings", "inspected")

    def test_a_row_not_reviewed_need_not_say_what_was_inspected(self):
        report = covered(blocking_report(), "Layout", "not-reviewed")
        report["coverage"][1]["inspected"] = ""
        self.passes(report)


class TestCheckRulings(unittest.TestCase):
    """S5, C. What a project's ui-gate/rulings.json has to hold. Pure."""

    def refused(self, data, *words):
        rulings, problems = pipeline.check_rulings(data)
        self.assertEqual(len(problems), 1, problems)
        for word in words:
            self.assertIn(word, problems[0])
        return rulings

    def test_a_good_file_gives_its_rulings(self):
        rulings, problems = pipeline.check_rulings({"rulings": RULINGS})
        self.assertEqual(problems, [])
        self.assertEqual([r["id"] for r in rulings], ["stop-is-plain", "ruling-1", "ruling-7"])
        self.assertEqual(pipeline.check_rulings({"rulings": []}), ([], []))

    def test_an_entry_missing_a_field_is_refused_by_name(self):
        for field in ("choice", "where", "quote", "rules", "covers"):
            for change in ("missing", "", None, []):
                with self.subTest(field=field, change=change):
                    entry = ruling()
                    if change == "missing":
                        del entry[field]
                    else:
                        entry[field] = change
                    kept = self.refused({"rulings": [page_ruling("ruling-1"), entry]},
                                        "ruling 2 (stop-is-plain)", field)
                    self.assertEqual([r["id"] for r in kept], ["ruling-1"])

    def test_an_entry_with_no_id_is_refused_by_its_place(self):
        entry = ruling()
        del entry["id"]
        self.refused({"rulings": [entry]}, "ruling 1", "id")

    def test_rules_and_covers_are_lists_of_text(self):
        for field in ("rules", "covers"):
            for value in ("COLOR-TREATMENT", ["COLOR-TREATMENT", ""], [7]):
                with self.subTest(field=field, value=value):
                    self.refused({"rulings": [ruling(**{field: value})]}, "ruling 1 (stop-is-plain)", field)

    def test_an_id_recorded_twice_is_refused(self):
        self.refused({"rulings": [ruling(), ruling(choice="another choice")]}, "stop-is-plain", "twice")

    def test_what_is_not_a_rulings_file_is_refused_and_never_raises(self):
        for data in (None, [], "rulings", {}, {"rulings": "none"}, {"rulings": ["stop-is-plain"]}):
            with self.subTest(data=data):
                self.assertTrue(pipeline.check_rulings(data)[1])


class TestTheReportsName(UiGateCase):
    """S5, A. The names: a report is `*ui-gate-report*`, a measurement file is
    `*ui-measure*`. Controls, on a run whose own slug contains `ui-gate`: its
    measurement files are not its report (they were not before the rename
    either; the bug this was written for did not exist)."""

    SLUG = "scratch-ui-gate"
    MEASURED = "2026-09-13-scratch-ui-gate-ui-measure-run-page-default.json"
    NAMED = "2026-09-13-scratch-ui-gate-ui-gate-report-run-page.json"

    def init(self):
        out = self.run_pipeline("init", self.SLUG, "--title", "Scratch", "--goal", "A run named like this one.",
                                "--branch", "work-desk-S8", "--date", "2026-09-13")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.measured(name=self.MEASURED)

    def check(self):
        return self.run_pipeline("ui-gate", "check", self.SLUG)

    def test_a_measurement_file_is_not_taken_for_the_report(self):
        self.init()
        out = self.check()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertEqual(out.stdout.splitlines(),
                         ["✗ UI gate report missing (" + self.root
                          + "/measurements/<date>-scratch-ui-gate-ui-gate-report[-<screen>].json)"])

    def test_phase_8_does_not_take_a_measurement_file_for_the_report(self):
        self.branch_changing("app/page.tsx")
        self.init()
        self.write(os.path.join("docs", "measurements", "2026-09-13-scratch-ui-gate-review.md"),
                   "# Review\n\nOne finding, ruled defer.\n")
        self.run_pipeline("note", self.SLUG, "8", "--plain", "The reviewers found one thing to do later.")
        out = self.run_pipeline("phase", self.SLUG, "8", "done")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("UI gate report missing", out.stderr)
        self.assertNotIn(self.MEASURED, out.stderr)

    def test_control_the_report_beside_the_measurement_is_the_one_read(self):
        self.init()
        self.report(good_report(), name=self.NAMED, measured=False)
        out = self.check()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(len(out.stdout.splitlines()), 1, out.stdout)
        self.assertIn(self.NAMED, out.stdout)


class TestSeveralScreens(UiGateCase):
    """S5, B. One report a screen: every one is checked, and Phase 8 closes
    only when every verdict is Approve."""

    SHELL = "2026-09-13-scratch-ui-gate-report-shell.json"

    def two_screens(self, shell):
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()
        self.report(good_report())
        self.report(shell, name=self.SHELL, measured=False)
        self.measured(name="2026-09-13-scratch-ui-measure-shell-default.json", screen="shell", path="ui-gate/shell.json")

    def shell(self, **over):
        return good_report(**dict({"screen": "shell", "screenFile": "ui-gate/shell.json"}, **over))

    def test_control_two_approving_reports_close(self):
        self.two_screens(self.shell())
        self.assertCloses(self.close())

    def test_two_reports_where_one_blocks_is_refused_and_the_one_is_named(self):
        self.two_screens(self.shell(findings=[finding(severity="HIGH")], verdict="Block"))
        out = self.close()
        self.assertRefused(out, self.SHELL, "Block")
        self.assertNotIn(REPORT, out.stderr)

    def test_the_blocking_one_is_refused_whichever_sorts_last(self):
        self.two_screens(self.shell())
        self.report(blocking_report())
        out = self.close()
        self.assertRefused(out, REPORT, "Block")
        self.assertNotIn(self.SHELL, out.stderr)

    def test_two_reports_where_one_is_malformed_is_refused(self):
        self.two_screens(self.shell(notVerified="none"))
        self.assertRefused(self.close(), self.SHELL, "notVerified")

    def test_a_report_for_a_screen_nobody_measured_is_refused(self):
        self.two_screens(self.shell(screen="board"))
        self.assertRefused(self.close(), self.SHELL, "board", "was not measured")


class TestTheGateReadsTheProject(UiGateCase):
    """S5, C to E, through the command: the files are really read."""

    def ready(self, report, **measured):
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()
        self.report(report, measured=False)
        self.measured(**measured)

    def ruled(self, **over):
        return good_report(findings=[finding(severity="HIGH", **dict({"ruling": "ruling-7"}, **over))])

    def test_a_cited_ruling_with_no_rulings_file_is_refused(self):
        self.ready(self.ruled())
        self.assertRefused(self.close(), REPORT, "ruling-7", "no such ruling is recorded")

    def test_a_ruling_naming_a_line_is_refused_and_the_file_alone_closes(self):
        """ALYDAS-455. app/page.tsx holds its own name once."""
        self.ready(self.ruled())
        self.rulings([dict(page_ruling("ruling-7"), where="app/page.tsx:1")])
        self.assertRefused(self.close(), REPORT, "ruling-7", "points nowhere", "app/page.tsx:1")
        self.rulings([dict(page_ruling("ruling-7"), where="app/page.tsx")])
        self.assertCloses(self.close())

    def test_a_ruling_pointing_at_a_file_that_is_not_there_is_refused(self):
        self.ready(self.ruled())
        for where in ("app/gone.tsx", "../outside.tsx", "app"):
            with self.subTest(where=where):
                self.rulings([dict(page_ruling("ruling-7"), where=where)])
                self.assertRefused(self.close(), "ruling-7", "points nowhere")

    def test_a_rulings_file_that_does_not_parse_is_refused_by_name(self):
        self.ready(good_report())
        self.rulings("{not json")
        self.assertRefused(self.close(), RULINGS_FILE, "JSON")

    def test_a_rulings_entry_missing_a_field_is_refused_by_name(self):
        self.ready(good_report())
        entry = ruling()
        del entry["covers"]
        self.rulings([entry])
        self.assertRefused(self.close(), RULINGS_FILE, "ruling 1 (stop-is-plain)", "covers")

    def test_an_unanswered_measured_rule_is_refused(self):
        self.ready(good_report(), failures=[failure()])
        self.assertRefused(self.close(), REPORT, "COLOR-CONTRAST", MEASURE, "button.btn 12.5px")

    def test_a_measurement_file_that_does_not_parse_is_refused_by_name(self):
        self.ready(good_report())
        self.write(os.path.join("docs", "measurements", "2026-09-13-scratch-ui-measure-run-page-live.json"), "{not")
        self.assertRefused(self.close(), "2026-09-13-scratch-ui-measure-run-page-live.json", "JSON")

    def test_a_measurement_file_in_a_subdirectory_of_measurements_is_not_read(self):
        """S6, ruling C. A fixture's measurements live in a folder of their
        own under measurements/, named like the run's and for the same screen:
        they are not this run's, and ask nothing of its report."""
        self.ready(good_report())
        seeded = os.path.join("docs", "measurements", "seeded", "2026-09-13-scratch-ui-measure-run-page-live.json")
        self.write(seeded, json.dumps(measurement(failures=[failure()])))
        self.assertCloses(self.close())

    def test_control_the_same_file_beside_the_others_is_read(self):
        self.ready(good_report())
        beside = "2026-09-13-scratch-ui-measure-run-page-live.json"
        self.write(os.path.join("docs", "measurements", beside), json.dumps(measurement(failures=[failure()])))
        self.assertRefused(self.close(), REPORT, "COLOR-CONTRAST", beside, "button.btn 12.5px")

    def test_a_screen_file_that_is_not_there_is_refused(self):
        self.ready(good_report(screenFile="ui-gate/run-pane.json"))
        self.assertRefused(self.close(), REPORT, "ui-gate/run-pane.json", "does not exist")

    def test_a_screen_file_that_does_not_parse_is_refused(self):
        self.ready(good_report())
        self.write(SCREEN_FILE, "{not json")
        self.assertRefused(self.close(), REPORT, SCREEN_FILE, "is not JSON that can be read")

    def test_a_screen_file_with_empty_jobs_is_refused(self):
        self.ready(good_report())
        self.write(SCREEN_FILE, json.dumps(screen_file(jobs=[])))
        self.assertRefused(self.close(), REPORT, SCREEN_FILE, "the jobs are missing")

    def test_check_says_the_same_and_exits_2(self):
        self.ready(good_report(), failures=[failure()])
        out = self.run_pipeline("ui-gate", "check", "scratch")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertEqual(len([line for line in out.stdout.splitlines() if line.startswith("✗ ")]), 1, out.stdout)
        self.assertIn("COLOR-CONTRAST", out.stdout)


RECORD_COLUMNS = ["Severity", "Domain", "Where", "Finding", "Standing"]
OWNED = "UI gate: "   # Gate 6: what marks a row as the record's own
SHELL_REPORT = "2026-09-13-scratch-ui-gate-report-shell.json"
LONG = "the label is cut " * 40   # 680 characters; 600 are kept below


def report_of(findings, **over):
    """A well formed report of these findings: coverage follows them, and the
    verdict follows whether a HIGH is left without a ruling."""
    has = {d for d in DOMAINS for f in findings if f["domain"].casefold() == d.casefold()}
    blocked = any(f["severity"] == "HIGH" and f["ruling"] is None for f in findings)
    # G7. A Placement finding is the reason its block does not fit.
    about = [at for at, f in enumerate(findings, 1) if f["domain"].casefold() == "placement"]
    over = dict({"placement": [placed(fits=False, finding=about[0], note="takes more room than its content")
                               if about else placed()]}, **over)
    return good_report(**dict({"findings": findings, "verdict": "Block" if blocked else "Approve",
                               "coverage": [{"domain": d, "inspected": f"the screen, for {d.lower()}",
                                             "result": "findings" if d in has else "clear"} for d in DOMAINS]}, **over))


def sixteen():
    """Nine findings on the run page and seven on the shell, out of order."""
    page = [
        finding(severity="LOW", domain="UI", rule="UI-RADIUS", location="app/page.tsx:1", before="page 1"),
        finding(severity="HIGH", ruling="ruling-1", location="app/page.tsx:2", before=LONG[:600]),
        finding(severity="MEDIUM", domain="Writing", rule="WRITE-LABEL", location="app/page.tsx:3", before="page 3"),
        finding(severity="MEDIUM", location="app/page.tsx:4", before="page 4"),
        finding(severity="LOW", location="app/page.tsx:5", before="page 5"),
        finding(severity="MEDIUM", domain="Placement", rule="PLACE-SHARE", location="app/page.tsx:6", before="page 6"),
        finding(severity="LOW", domain="Layout", rule="LAYOUT-GROUP", location="app/page.tsx:7", before="page 7"),
        finding(severity="MEDIUM", domain="Colors", rule="COLOR-TOKEN", location="app/page.tsx:8", before="page 8"),
        finding(severity="LOW", domain="Typography", rule="TYPE-SCALE", location="app/page.tsx:9", before="page 9"),
    ]
    shell = [
        finding(severity="MEDIUM", domain="UI", rule="UI-STATE", location="app/shell.tsx:1", before="shell 1"),
        finding(severity="HIGH", domain="Writing", rule="WRITE-DESTRUCTIVE", location="app/shell.tsx:2", before="shell 2"),
        finding(severity="LOW", domain="Writing", rule="WRITE-EMPTY", location="app/shell.tsx:3", before="shell 3"),
        finding(severity="MEDIUM", domain="Layout", rule="LAYOUT-CONTROL", location="app/shell.tsx:4", before="shell 4"),
        finding(severity="HIGH", domain="Layout", rule="LAYOUT-HIDDEN", location="app/shell.tsx:5", before="shell 5"),
        finding(severity="MEDIUM", location="app/shell.tsx:6", before="shell 6"),
        finding(severity="LOW", domain="colors", rule="COLOR-TOKEN", location="app/shell.tsx:7", before="shell 7"),
    ]
    return page, shell


class RecordCase(UiGateCase):
    def record(self, *extra):
        return self.run_pipeline("ui-gate", "record", "scratch", *extra)

    def table(self):
        return self.board()["phases"][8]["table"]

    def two_reports(self):
        self.init_scratch()
        self.write("app/page.tsx")
        self.rulings()
        page, shell = sixteen()
        self.report(report_of(page))
        self.report(report_of(shell, screen="shell", screenFile="ui-gate/shell.json"), name=SHELL_REPORT, measured=False)
        self.measured(name="2026-09-13-scratch-ui-measure-shell-default.json", screen="shell", path="ui-gate/shell.json")


class TestUiGateRecord(RecordCase):
    """S6. `ui-gate record <slug>`: every report's findings, in Phase 8's table."""

    def test_sixteen_findings_across_two_reports_are_sixteen_rows_in_order(self):
        self.two_reports()
        out = self.record()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        table = self.table()
        self.assertEqual(table["columns"], RECORD_COLUMNS)
        rows = table["rows"]
        self.assertEqual(len(rows), 16, rows)
        # Severity, then domain in the seven's order, then the reports in name order.
        self.assertEqual([(r[0], r[1], r[2]) for r in rows], [
            ("HIGH", OWNED + "Accessibility", "app/page.tsx:2"),
            ("HIGH", OWNED + "Layout", "app/shell.tsx:5"),
            ("HIGH", OWNED + "Writing", "app/shell.tsx:2"),
            ("MEDIUM", OWNED + "Accessibility", "app/shell.tsx:6"),   # ...report-shell.json sorts before ...report.json
            ("MEDIUM", OWNED + "Accessibility", "app/page.tsx:4"),
            ("MEDIUM", OWNED + "Layout", "app/shell.tsx:4"),
            ("MEDIUM", OWNED + "Writing", "app/page.tsx:3"),
            ("MEDIUM", OWNED + "Colors", "app/page.tsx:8"),
            ("MEDIUM", OWNED + "UI", "app/shell.tsx:1"),
            ("MEDIUM", OWNED + "Placement", "app/page.tsx:6"),
            ("LOW", OWNED + "Accessibility", "app/page.tsx:5"),
            ("LOW", OWNED + "Layout", "app/page.tsx:7"),
            ("LOW", OWNED + "Writing", "app/shell.tsx:3"),
            ("LOW", OWNED + "Typography", "app/page.tsx:9"),
            ("LOW", OWNED + "Colors", "app/shell.tsx:7"),
            ("LOW", OWNED + "UI", "app/page.tsx:1"),
        ])
        by_where = {r[2]: r for r in rows}
        self.assertEqual(by_where["app/shell.tsx:2"][3:], ["shell 2 [WRITE-DESTRUCTIVE]", "open"])
        self.assertEqual(by_where["app/page.tsx:2"][4], "ruled: ruling-1")
        self.assertEqual([r[4] for r in rows].count("open"), 15)
        self.assertIn("16", out.stdout)

    def test_a_600_character_before_is_cut_to_the_cell_and_keeps_its_rule(self):
        self.two_reports()
        self.assertEqual(self.record().returncode, 0)
        cell = next(r for r in self.table()["rows"] if r[2] == "app/page.tsx:2")[3]
        self.assertEqual(len(LONG[:600]), 600)
        self.assertEqual(len(cell), pipeline.LEDGER_BOUNDS["cell"])
        self.assertTrue(cell.startswith(LONG[:400]), cell)
        self.assertTrue(cell.endswith(" [A11Y-STRUCTURE]"), cell)
        for row in self.table()["rows"]:
            for value in row:
                self.assertLessEqual(len(value), pipeline.LEDGER_BOUNDS["cell"])

    def test_recording_again_replaces_the_record(self):
        self.two_reports()
        self.assertEqual(self.record().returncode, 0)
        page, _ = sixteen()
        self.report(report_of(page[:1]), measured=False)
        out = self.record()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        rows = self.table()["rows"]
        self.assertEqual(len(rows), 8, rows)
        self.assertEqual([r[2] for r in rows if r[2].startswith("app/page.tsx")], ["app/page.tsx:1"])

    def reviewed(self):
        out = self.run_pipeline("table", "scratch", "8", "--columns", "Finding,Reproduced,Ruling",
                                "--row", "the token is logged|yes, with a control|fix-now")
        self.assertEqual(out.returncode, 0, out.stderr)
        return self.table()

    def test_a_table_the_review_wrote_is_not_replaced_and_the_way_through_is_named(self):
        self.two_reports()
        before = self.reviewed()
        out = self.record()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("Finding, Reproduced, Ruling", out.stderr)
        self.assertIn("ui-gate record scratch --replace", out.stderr)
        self.assertEqual(self.table(), before)

    def test_replace_takes_the_review_s_table_and_says_so(self):
        self.two_reports()
        self.reviewed()
        out = self.record("--replace")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.table()["columns"], RECORD_COLUMNS)
        self.assertEqual(len(self.table()["rows"]), 16)
        self.assertIn("replaced", out.stdout)
        self.assertIn("Finding, Reproduced, Ruling", out.stdout)
        self.assertIn("1 row", out.stdout)

    def test_control_replace_with_no_table_there_records_and_says_nothing_was_replaced(self):
        self.two_reports()
        out = self.record("--replace")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(len(self.table()["rows"]), 16)
        self.assertNotIn("replaced", out.stdout)

    def test_record_refuses_what_check_refuses_and_writes_nothing(self):
        self.init_scratch()
        out = self.record()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("UI gate report missing", out.stdout + out.stderr)
        self.report(good_report(verdict="Perhaps"))
        out, checked = self.record(), self.run_pipeline("ui-gate", "check", "scratch")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertEqual(checked.returncode, 2)
        for line in checked.stdout.splitlines():
            self.assertIn(line, out.stdout + out.stderr)
        self.assertIn("neither Approve nor Block", out.stdout + out.stderr)
        self.assertIsNone(self.table())
        # Control: the same report mended is recorded.
        self.report(good_report(), measured=False)
        out = self.record()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.table()["rows"],
                         [["MEDIUM", OWNED + "Accessibility", "app/page.tsx:42",
                           "the focus ring is removed on the save button [A11Y-STRUCTURE]", "open"]])

    def test_a_blocking_report_is_recorded(self):
        """`check` passes a well formed Block, so its findings reach the board."""
        self.init_scratch()
        self.report(blocking_report())
        out = self.record()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual([r[0] for r in self.table()["rows"]], ["HIGH"])

    def test_record_is_in_the_run_log(self):
        self.two_reports()
        self.assertEqual(self.record().returncode, 0)
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.events.jsonl"), encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
        mine = [e for e in events if e["event"] == "ui-gate"]
        self.assertEqual(len(mine), 1, events)
        self.assertEqual(mine[0]["phase"], 8)
        self.assertIn("16", mine[0]["note"])


class TestUiGateRecordSharesTheTable(RecordCase):
    """S6, ruling B. On a UI run Phase 8 has one table with the record's
    columns, and the code review writes its own rows into it with `table`,
    under a Domain outside the seven. `record` owns the rows of the seven
    domains and leaves every other row where it found it."""

    FIRST = "HIGH|Code|lib/token.ts:3|the token is logged|fix-now"
    LATER = "LOW|Code|lib/sync.ts:9|a retry with no backoff|defer"

    def review(self, row, columns=True):
        args = ["table", "scratch", "8", "--row", row] + (["--columns", ",".join(RECORD_COLUMNS)] if columns else [])
        out = self.run_pipeline(*args)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_a_review_row_survives_a_record_and_no_replace_is_asked_for(self):
        self.two_reports()
        self.review(self.FIRST)
        out = self.record()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        rows = self.table()["rows"]
        self.assertEqual(len(rows), 17, rows)
        self.assertEqual(rows[0], self.FIRST.split("|"))
        self.assertEqual([r[1] for r in rows[1:]].count("Code"), 0)
        self.assertNotIn("replaced", out.stdout)
        self.assertIn("1 other row", out.stdout)

    def test_a_second_record_after_a_fix_removes_that_row_and_keeps_the_review_s_in_place(self):
        self.two_reports()
        self.review(self.FIRST)
        self.assertEqual(self.record().returncode, 0)
        self.review(self.LATER, columns=False)
        before = self.table()["rows"]
        self.assertEqual((before[0][1], before[-1][1], len(before)), ("Code", "Code", 18))
        # The run page's HIGH is fixed: its report now holds the other eight.
        page, _ = sixteen()
        self.report(report_of(page[:1] + page[2:]), measured=False)
        out = self.record()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        after = self.table()["rows"]
        self.assertEqual(len(after), 17, after)
        self.assertNotIn("app/page.tsx:2", [r[2] for r in after])
        self.assertEqual(after[0], self.FIRST.split("|"))
        self.assertEqual(after[-1], self.LATER.split("|"))
        self.assertEqual(after, [row for row in before if row[2] != "app/page.tsx:2"])

    def test_recording_twice_changes_nothing(self):
        self.two_reports()
        self.review(self.FIRST)
        self.assertEqual(self.record().returncode, 0)
        self.review(self.LATER, columns=False)
        self.assertEqual(self.record().returncode, 0)
        once = self.table()
        self.assertEqual(self.record().returncode, 0)
        self.assertEqual(self.table(), once)

    def test_a_review_row_named_like_a_ui_domain_in_another_case_is_the_review_s(self):
        """Gate 6, reversed: this was ..._is_the_record_s, and the row was
        taken. What record owns is marked `UI gate: `; `colors` is the review's."""
        self.two_reports()
        self.review("LOW|colors|lib/x.ts:1|written by hand|defer")
        self.assertEqual(self.record().returncode, 0)
        self.assertEqual(len(self.table()["rows"]), 17)
        self.assertEqual(self.table()["rows"][0], ["LOW", "colors", "lib/x.ts:1", "written by hand", "defer"])


class TestUiGateTableIsShared(unittest.TestCase):
    """S6, ruling B, as data: the bound counts every row, and the review's are kept."""

    def reports(self, count):
        findings = [finding(location=f"app/page.tsx:{at}", before=f"finding {at}") for at in range(1, count + 1)]
        return [(f"report-{at // 15}.json", report_of(findings[at:at + 15]), None) for at in range(0, count, 15)]

    def there(self, code, ui=()):
        rows = [["LOW", "Code", f"lib/a.ts:{at}", f"review {at}", "defer"] for at in range(1, code + 1)]
        return {"columns": list(RECORD_COLUMNS), "rows": rows[:2] + [list(r) for r in ui] + rows[2:]}

    def test_the_review_s_rows_are_kept_and_the_ui_rows_are_cut_to_the_room_left(self):
        table = pipeline.ui_gate_table(self.reports(16), self.there(45))
        rows = table["rows"]
        self.assertEqual(len(rows), pipeline.LEDGER_BOUNDS["rows"])
        self.assertEqual([r for r in rows if r[1] == "Code"], self.there(45)["rows"])
        ui = [r for r in rows if r[1] != "Code"]
        self.assertEqual([r[2] for r in ui[:4]], [f"app/page.tsx:{at}" for at in range(1, 5)])
        self.assertIn("12 more findings", ui[4][3])
        self.assertIn("left out", ui[4][3])

    def test_control_when_everything_fits_nothing_is_left_out(self):
        table = pipeline.ui_gate_table(self.reports(16), self.there(34))
        self.assertEqual(len(table["rows"]), 50)
        self.assertFalse(any("left out" in cell for row in table["rows"] for cell in row))

    def test_the_ui_rows_go_where_the_ui_rows_were(self):
        old = [["LOW", OWNED + "UI", "app/old.tsx:1", "gone [UI-RADIUS]", "open"]]
        table = pipeline.ui_gate_table(self.reports(2), self.there(4, ui=old))
        self.assertEqual([r[2] for r in table["rows"]],
                         ["lib/a.ts:1", "lib/a.ts:2", "app/page.tsx:1", "app/page.tsx:2", "lib/a.ts:3", "lib/a.ts:4"])

    def test_with_no_ui_row_there_the_ui_rows_go_after_the_review_s(self):
        table = pipeline.ui_gate_table(self.reports(1), self.there(3))
        self.assertEqual([r[2] for r in table["rows"]], ["lib/a.ts:1", "lib/a.ts:2", "lib/a.ts:3", "app/page.tsx:1"])

    def test_the_left_out_row_is_the_record_s_and_does_not_pile_up(self):
        once = pipeline.ui_gate_table(self.reports(16), self.there(45))
        twice = pipeline.ui_gate_table(self.reports(16), once)
        self.assertEqual(twice, once)
        self.assertEqual(len([r for r in twice["rows"] if "left out" in r[3]]), 1)

    def test_a_table_full_of_the_review_s_rows_is_refused(self):
        """Gate 6, reversed: this was ..._keeps_them_all, which returned the
        fifty rows and dropped the three findings without a word."""
        with self.assertRaises(pipeline.TableFull):
            pipeline.ui_gate_table(self.reports(3), self.there(50))


class TestUiGateTable(unittest.TestCase):
    """S6. The table `record` writes, as data: its bounds in both directions."""

    def reports(self, count):
        findings = [finding(location=f"app/page.tsx:{at}", before=f"finding {at}") for at in range(1, count + 1)]
        return [(f"report-{at // 15}.json", report_of(findings[at:at + 15]), None) for at in range(0, count, 15)]

    def test_fifty_findings_are_fifty_rows_and_none_is_left_out(self):
        table = pipeline.ui_gate_table(self.reports(50))
        self.assertEqual(len(table["rows"]), 50)
        self.assertEqual(table["rows"][-1][2], "app/page.tsx:50")
        self.assertFalse(any("left out" in cell for row in table["rows"] for cell in row))

    def test_more_than_fifty_keeps_forty_nine_and_says_how_many_were_left_out(self):
        for count, left in ((51, 2), (60, 11)):
            with self.subTest(findings=count):
                table = pipeline.ui_gate_table(self.reports(count))
                self.assertEqual(len(table["rows"]), pipeline.LEDGER_BOUNDS["rows"])
                self.assertEqual(table["rows"][48][2], "app/page.tsx:49")
                last = table["rows"][-1]
                self.assertEqual(len(last), len(RECORD_COLUMNS))
                self.assertIn(f"{left} more findings", last[3])
                self.assertIn("left out", last[3])

    def test_every_cell_is_cut_to_the_cell_bound(self):
        long = finding(location="app/" + "deep/" * 120 + "page.tsx:7", before="x" * 2000)
        table = pipeline.ui_gate_table([("r.json", report_of([long]), None)])
        self.assertEqual([len(cell) <= pipeline.LEDGER_BOUNDS["cell"] for cell in table["rows"][0]], [True] * 5)
        self.assertEqual(len(table["rows"][0][3]), pipeline.LEDGER_BOUNDS["cell"])
        self.assertTrue(table["rows"][0][3].endswith(" [A11Y-STRUCTURE]"))

    def test_a_short_finding_is_not_cut(self):
        table = pipeline.ui_gate_table([("r.json", report_of([finding(before="x" * 480)]), None)])
        self.assertEqual(table["rows"][0][3], "x" * 480 + " [A11Y-STRUCTURE]")

    def test_the_columns_fit_the_ledger(self):
        table = pipeline.ui_gate_table([])
        self.assertEqual(table, {"columns": RECORD_COLUMNS, "rows": []})
        self.assertLessEqual(len(table["columns"]), pipeline.LEDGER_BOUNDS["columns"])


class TestUiGateSkip(UiGateCase):
    def test_skip_records_the_reason_on_the_run_and_logs_it(self):
        self.init_scratch()
        out = self.skip("a ledger script, no screen")
        self.assertEqual(self.board()["ui_gate"], {"skip": "a ledger script, no screen"})
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.events.jsonl"), encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(events[-1]["event"], "ui-gate")
        self.assertIn("a ledger script, no screen", events[-1]["note"])

    def test_skip_needs_a_reason(self):
        self.init_scratch()
        for args in ((), ("--reason", ""), ("--reason", "   ")):
            with self.subTest(args=args):
                out = self.run_pipeline("ui-gate", "skip", "scratch", *args)
                self.assertNotEqual(out.returncode, 0)
                self.assertNotIn("ui_gate", self.board())


class TestIsRenderedSurface(unittest.TestCase):
    def config(self, **over):
        return dict(pipeline.ui_gate_defaults("docs/superpowers"), **over)

    def test_the_defaults_are_the_seven_kinds(self):
        """A floor, so the loop below cannot pass on an empty list."""
        self.assertEqual(set(pipeline.DEFAULT_SURFACES),
                         {"*.tsx", "*.jsx", "*.css", "*.scss", "*.html", "*.vue", "*.svelte"})

    def test_each_default_pattern_is_a_surface_at_any_depth(self):
        for pattern in pipeline.DEFAULT_SURFACES:
            for path in ("page" + pattern[1:], "app/deep/er/page" + pattern[1:]):
                with self.subTest(path=path):
                    self.assertTrue(pipeline.is_rendered_surface(path, self.config()))

    def test_the_default_test_patterns_take_python_s_shapes(self):
        """Gate 7, D1: the one list the UI gate leaves out and the review counts
        as tests holds Python's `test_*.py` and `*_test.py`, matched by name at
        any depth, and not their neighbours."""
        ignore = self.config()["ignore"]
        hit = lambda path: any(pipeline._glob_match(path, g) for g in ignore)
        for path in ("test_review.py", "scripts/test_review.py", "pkg/deep/reminders_test.py"):
            with self.subTest(path=path):
                self.assertTrue(hit(path))
        for path in ("contest.py", "scripts/testing.py", "test_review.pyc", "latest_test.py.bak", "tests/helpers.py"):
            with self.subTest(path=path):
                self.assertFalse(hit(path))

    def test_what_is_not_drawn_is_not_a_surface(self):
        for path in ("tool.py", "app/route.ts", "README.md", "app/tsx", "app/page.tsx.bak", "styles.css.map", "pagetsx"):
            with self.subTest(path=path):
                self.assertFalse(pipeline.is_rendered_surface(path, self.config()))

    def test_each_exclusion(self):
        for path in ("app/page.test.tsx", "app/page.spec.tsx", "page.test.jsx", "app/__tests__/page.tsx",
                     "__tests__/page.tsx", "app/__tests__/deep/site.css",
                     "docs/superpowers/prototypes/x.html", "docs/superpowers/boards/2026-09-13-scratch.html"):
            with self.subTest(path=path):
                self.assertFalse(pipeline.is_rendered_surface(path, self.config()))

    def test_an_exclusion_does_not_reach_its_neighbours(self):
        for path in ("app/contest.tsx", "app/my__tests__/page.tsx", "docs/superpowers-site/page.html",
                     "app/docs/superpowers/page.html", "app/latest.spec/page.tsx"):
            with self.subTest(path=path):
                self.assertTrue(pipeline.is_rendered_surface(path, self.config()))

    def test_no_artifact_root_inside_the_repository_excludes_nothing(self):
        self.assertTrue(pipeline.is_rendered_surface("docs/superpowers/x.html", self.config(artifact_root=None)))

    def test_a_config_s_lists_replace_the_defaults(self):
        config = self.config(surfaces=["web/**"], ignore=["web/vendor/**"])
        self.assertFalse(pipeline.is_rendered_surface("app/page.tsx", config))
        self.assertTrue(pipeline.is_rendered_surface("web/a/b/tool.py", config))
        self.assertTrue(pipeline.is_rendered_surface("web/page.test.tsx", config), "the default ignores were replaced")
        self.assertFalse(pipeline.is_rendered_surface("web/vendor/x.css", config))
        self.assertFalse(pipeline.is_rendered_surface("website/x.css", config))

    def test_a_star_stays_inside_one_folder_and_two_cross(self):
        self.assertTrue(pipeline.is_rendered_surface("web/x.css", self.config(surfaces=["web/*.css"])))
        self.assertFalse(pipeline.is_rendered_surface("web/a/x.css", self.config(surfaces=["web/*.css"])))
        self.assertTrue(pipeline.is_rendered_surface("web/a/x.css", self.config(surfaces=["web/**/*.css"])))
        self.assertTrue(pipeline.is_rendered_surface("web/x.css", self.config(surfaces=["web/**/*.css"])))


# ---------- ALYDAS-223, Gate 6: what the mutation pass found ----------

CONFIG_FILE = "ui-gate/config.json"
CONFIG_NOTE = "this run changed ui-gate/config.json"


def cited(**over):
    """A COLOR-TREATMENT finding on the Stop button, citing stop-is-plain."""
    return one_finding(**dict({"severity": "HIGH", "domain": "Colors", "rule": "COLOR-TREATMENT",
                               "location": CONTROLS + ":101", "before": "Stop is drawn in ink, like any other button",
                               "after": "a danger colour on Stop", "why": "Stop ends the run",
                               "source": "source", "ruling": "stop-is-plain"}, **over))


class TestASkipSwitchesNothingOff(UiGateCase):
    """G6, 1 (P07). A recorded skip and a report file together: the report is
    checked like any other, whether or not a surface changed."""

    def ready(self, changed, report):
        self.branch_changing(changed)
        self.phase_8_otherwise_ready()
        self.skip()
        self.report(report)

    def test_a_skip_and_a_blocking_report_on_a_changed_page_is_refused(self):
        self.ready("app/page.tsx", blocking_report())
        self.assertRefused(self.close(), REPORT, "Block")

    def test_a_skip_and_a_malformed_report_on_a_changed_page_is_refused(self):
        self.ready("app/page.tsx", good_report(verdict="Perhaps"))
        self.assertRefused(self.close(), REPORT, "neither Approve nor Block")

    def test_a_skip_and_a_blocking_report_on_a_run_with_no_surface_is_refused(self):
        self.ready("tool.py", blocking_report())
        self.assertRefused(self.close(), REPORT, "Block")

    def test_a_skip_and_a_malformed_report_on_a_run_with_no_surface_is_refused(self):
        self.ready("tool.py", good_report(verdict="Perhaps"))
        self.assertRefused(self.close(), REPORT, "neither Approve nor Block")

    def test_control_a_skip_and_an_approving_report_close(self):
        for changed in ("app/page.tsx", "tool.py"):
            with self.subTest(changed=changed):
                self.ready(changed, good_report())
                self.assertCloses(self.close())
                self.tmp.cleanup()
                self.setUp()


class TestARunCannotLoosenItsOwnGate(UiGateCase):
    """G6, 2 (hole F). The surfaces are judged by ui-gate/config.json as it is
    at the merge-base. What the run itself wrote there waits for the next run,
    and the check says, in one line that refuses nothing, that it was changed."""

    def test_a_config_the_run_wrote_and_never_committed_does_not_exempt_its_page(self):
        self.branch_changing("app/page.tsx")
        self.write(CONFIG_FILE, json.dumps({"surfaces": []}))
        self.phase_8_otherwise_ready()
        self.skip()
        out = self.close()
        self.assertRefused(out, "app/page.tsx", "skip cannot stand")
        self.assertEqual(len([line for line in out.stderr.splitlines() if CONFIG_NOTE in line]), 1, out.stderr)

    def test_a_config_the_run_committed_does_not_exempt_its_page(self):
        self.branch_changing("app/page.tsx")
        self.commit_file(CONFIG_FILE, json.dumps({"ignore": ["**"]}))
        self.phase_8_otherwise_ready()
        self.skip()
        out = self.close()
        self.assertRefused(out, "app/page.tsx", "skip cannot stand")
        self.assertIn(CONFIG_NOTE, out.stderr)

    def test_a_config_the_run_changed_is_read_as_the_default_branch_has_it(self):
        """main says web/** is what is drawn; the run narrows that to nothing."""
        self.branch_changing("web/tool.py", on_main={CONFIG_FILE: json.dumps({"surfaces": ["web/**"]})})
        self.write(CONFIG_FILE, json.dumps({"surfaces": ["nothing/**"]}))
        self.phase_8_otherwise_ready()
        self.skip()
        out = self.close()
        self.assertRefused(out, "web/tool.py", "skip cannot stand")
        self.assertIn(CONFIG_NOTE, out.stderr)

    def test_a_config_the_run_broke_is_not_the_config_it_is_judged_by(self):
        """The run's own file does not parse, and main has none: the defaults
        hold, the run closes, and the line still says the file was changed."""
        self.branch_changing("tool.py")
        self.write(CONFIG_FILE, "{not json")
        self.phase_8_otherwise_ready()
        self.skip()
        out = self.close()
        self.assertCloses(out)
        self.assertIn(CONFIG_NOTE, out.stderr)

    def test_control_a_run_that_left_the_config_alone_is_told_nothing(self):
        self.branch_changing("tool.py", on_main={CONFIG_FILE: json.dumps({"surfaces": ["web/**"]})})
        self.phase_8_otherwise_ready()
        self.skip()
        out = self.close()
        self.assertCloses(out)
        self.assertNotIn("this run changed", out.stdout + out.stderr)

    def test_ui_gate_check_says_so_too_in_one_line_and_still_exits_0(self):
        self.branch_changing("app/page.tsx")
        self.write(CONFIG_FILE, json.dumps({"surfaces": []}))
        self.init_scratch()
        self.report(good_report())
        out = self.run_pipeline("ui-gate", "check", "scratch")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(len([line for line in out.stdout.splitlines() if CONFIG_NOTE in line]), 1, out.stdout)
        self.assertNotIn("✗", out.stdout)

    def test_check_8_says_so_too(self):
        self.branch_changing("tool.py")
        self.write(CONFIG_FILE, json.dumps({"surfaces": []}))
        self.phase_8_otherwise_ready()
        self.skip()
        out = self.run_pipeline("check", "scratch", "8")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn(CONFIG_NOTE, out.stdout + out.stderr)


class TestARenamedPageStillCounts(UiGateCase):
    """G6, 3 (hole H). Both sides of a rename are changed files."""

    def test_a_page_renamed_to_a_file_no_browser_draws_is_a_surface_changed(self):
        self.branch_changing(on_main=("app/page.tsx",))
        self.git("mv", "app/page.tsx", "app/page.ts")
        self.git("commit", "-q", "-m", "rename")
        self.phase_8_otherwise_ready()
        self.skip()
        self.assertRefused(self.close(), "app/page.tsx", "skip cannot stand")

    def test_the_same_rename_staged_and_not_committed_counts(self):
        self.branch_changing("tool.py", on_main=("app/page.tsx",))
        self.git("mv", "app/page.tsx", "app/page.ts")
        self.phase_8_otherwise_ready()
        self.skip()
        self.assertRefused(self.close(), "app/page.tsx", "skip cannot stand")

    def test_both_sides_are_listed(self):
        self.branch_changing(on_main=("app/page.tsx",))
        self.git("mv", "app/page.tsx", "app/home.tsx")
        self.git("commit", "-q", "-m", "rename")
        self.phase_8_otherwise_ready()
        out = self.close()
        self.assertRefused(out, "app/home.tsx", "app/page.tsx")


class TestTheFilesAreThisRunsOnly(UiGateCase):
    """G6, 4 (hole E, P24). A report is `<date>-<slug>-ui-gate-report[-<anything>].json`
    and a measurement `<date>-<slug>-ui-measure-<anything>.json`, the slug exact."""

    def check(self):
        return self.run_pipeline("ui-gate", "check", "scratch")

    def lone(self, name, body):
        self.write(os.path.join("docs", "measurements", name), json.dumps(body))

    def test_a_report_that_is_another_run_s_is_not_read(self):
        self.init_scratch()
        self.measured()
        for name in ("2026-09-13-scratch-two-ui-gate-report.json", "2026-09-13-two-scratch-ui-gate-report.json",
                     "2026-09-13-scratch-ui-gate-reporting.json", "notes-scratch-ui-gate-report.json",
                     "scratch-ui-gate-report.json", "2026-09-13-scratchui-gate-report.json"):
            with self.subTest(name=name):
                self.lone(name, good_report())
                out = self.check()
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                self.assertIn("UI gate report missing", out.stdout)
                os.remove(os.path.join(self.root, "measurements", name))

    def test_control_each_name_a_report_of_this_run_can_have_is_read(self):
        self.init_scratch()
        self.measured()
        self.write("app/page.tsx")   # G8: the file the good report's finding is at
        for name in (REPORT, "2026-10-01-scratch-ui-gate-report-run-page.json", "2026-09-13-scratch-ui-gate-report-a.b.json"):
            with self.subTest(name=name):
                self.lone(name, good_report())
                out = self.check()
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                self.assertIn(name, out.stdout)
                os.remove(os.path.join(self.root, "measurements", name))

    def test_a_measurement_that_is_another_run_s_does_not_measure_this_run_s_screen(self):
        """P24. The same screen, measured for a run whose slug holds this one."""
        self.init_scratch()
        self.report(good_report(), measured=False)
        self.write(SCREEN_FILE, json.dumps(screen_file()))
        for name in ("2026-09-13-scratch-two-ui-measure-run-page-default.json",
                     "2026-09-13-two-scratch-ui-measure-run-page-default.json",
                     "2026-09-13-scratch-ui-measure.json", "scratch-ui-measure-run-page-default.json"):
            with self.subTest(name=name):
                self.lone(name, measurement())
                out = self.check()
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                self.assertIn("was not measured", out.stdout)
                self.assertNotIn(name, out.stdout)
                os.remove(os.path.join(self.root, "measurements", name))

    def test_another_run_s_failing_measurement_asks_nothing_of_this_run(self):
        self.init_scratch()
        self.report(good_report())
        self.lone("2026-09-13-scratch-two-ui-measure-run-page-default.json", measurement(failures=[failure()]))
        out = self.check()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_phase_8_is_not_closed_by_another_run_s_approving_report(self):
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()
        self.report(good_report(), name="2026-09-13-scratch-two-ui-gate-report.json")
        self.assertRefused(self.close(), "UI gate report missing", "app/page.tsx")


class TestAnAutomaticHighIsHigh(CheckReportCase):
    """G6, 5 (hole A). The script knows the rule book's ids and which of them
    are HIGH on sight; test_ui_rules.py holds both lists to ui-rules.md."""

    def test_the_lists_are_read(self):
        """A floor, so the loops below cannot pass on nothing."""
        self.assertEqual(len(pipeline.UI_GATE_RULES), 34)
        self.assertEqual(len(pipeline.UI_GATE_AUTOMATIC_HIGH), 12)
        self.assertLess(set(pipeline.UI_GATE_AUTOMATIC_HIGH), set(pipeline.UI_GATE_RULES))

    def test_an_automatic_high_rule_below_high_is_refused_and_the_rule_is_named(self):
        for rule in pipeline.UI_GATE_AUTOMATIC_HIGH:
            for severity in ("MEDIUM", "LOW"):
                with self.subTest(rule=rule, severity=severity):
                    self.refused(good_report(findings=[finding(rule=rule, severity=severity)]),
                                 f"finding 1 ({rule})", severity, f"{rule} is automatic HIGH")

    def test_control_the_same_rule_at_high_passes(self):
        for rule in pipeline.UI_GATE_AUTOMATIC_HIGH:
            with self.subTest(rule=rule):
                self.passes(good_report(findings=[finding(rule=rule, severity="HIGH")], verdict="Block"))

    def test_a_rule_that_is_not_automatic_high_takes_any_severity(self):
        others = sorted(set(pipeline.UI_GATE_RULES) - set(pipeline.UI_GATE_AUTOMATIC_HIGH))
        self.assertIn("WRITE-ERROR", others, "HIGH on the main path only: not automatic")
        self.assertIn("UI-MOTION", others)
        for rule in others:
            for severity in ("MEDIUM", "LOW"):
                with self.subTest(rule=rule, severity=severity):
                    self.passes(good_report(findings=[finding(rule=rule, severity=severity)]))

    def test_a_measured_contrast_failure_answered_by_a_low_finding_is_refused(self):
        """What the breaker built: the failure is answered, and waved through as LOW."""
        low = answered("button.btn 12.5px", rule="COLOR-CONTRAST", severity="LOW")
        self.refused(good_report(findings=[low]), "finding 1 (COLOR-CONTRAST)", "LOW", "automatic HIGH",
                     measurements={MEASURE: measurement(failures=[failure()])})

    def test_a_rule_the_rule_book_does_not_define_is_refused(self):
        for rule in ("NOT-A-RULE", "a11y-target", "A11Y-TARGETS", "A11Y"):
            with self.subTest(rule=rule):
                self.refused(good_report(findings=[finding(rule=rule)]), f"finding 1 ({rule})", "ui-rules.md",
                             "does not define")


def lacking(change):
    held = measurement()
    change(held)
    return held


LESS = {
    "no screen": (lambda m: m.pop("screen"), "screen"),
    "a blank screen": (lambda m: m.update(screen="  "), "screen"),
    "no state": (lambda m: m.pop("state"), "state"),
    "a null state": (lambda m: m.update(state=None), "state"),
    "no contexts": (lambda m: m.pop("contexts"), "contexts"),
    "contexts that are a list": (lambda m: m.update(contexts=[m["contexts"]["phone"]]), "contexts"),
    "a context that is a list": (lambda m: m["contexts"].update(narrow=[]), "narrow"),
    "failures that are not a list": (lambda m: m["contexts"]["phone"].update(failures="none"), "phone"),
    "no failures": (lambda m: m["contexts"]["desktop-dark"].pop("failures"), "desktop-dark"),
    "no innerWidth": (lambda m: m["contexts"]["narrow"].pop("innerWidth"), "narrow"),
    "an innerWidth that is words": (lambda m: m["contexts"]["phone"].update(innerWidth="390"), "phone"),
    "an innerWidth that is true": (lambda m: m["contexts"]["phone"].update(innerWidth=True), "phone"),
}
LESS.update({f"no {name} context": (lambda m, name=name: m["contexts"].pop(name), name) for name in FOUR})


class TestAMeasurementIsAMeasurement(CheckReportCase):
    """G6, 6 (holes B, C, D; P20). A measurement file counts when it has what
    the module writes. Every state the screen file lists is measured. And a
    screen is the screen its name spells, exactly."""

    def test_the_four_contexts_are_the_script_s(self):
        self.assertEqual(tuple(pipeline.UI_GATE_CONTEXTS), tuple(FOUR))

    def test_what_the_module_writes_is_a_measurement(self):
        self.assertEqual(pipeline.check_measurement(measurement()), [])
        self.assertEqual(pipeline.check_measurement(measurement(failures=[failure()])), [])

    def test_each_thing_less_is_refused_and_says_what_is_missing(self):
        for label, (change, word) in LESS.items():
            with self.subTest(less=label):
                wrong = pipeline.check_measurement(lacking(change))
                self.assertEqual(len(wrong), 1, wrong)
                self.assertIn(word, wrong[0])
        for held in (None, [], "run page", 7):
            with self.subTest(held=held):
                self.assertEqual(len(pipeline.check_measurement(held)), 1)
        self.assertEqual(len(pipeline.check_measurement({"screen": "run page"})), 2)
        self.assertEqual(len(pipeline.check_measurement({"screen": "run page", "state": "default", "contexts": {}})), 4)

    def test_a_file_that_is_less_does_not_measure_its_screen_and_is_named(self):
        for label, (change, word) in LESS.items():
            if word == "screen":
                continue   # with no screen it is nobody's: the command names it (below)
            with self.subTest(less=label):
                self.refused(good_report(), MEASURE, "is not a measurement", word,
                             measurements={MEASURE: lacking(change)})

    def test_a_failure_in_a_file_that_is_less_is_not_waved_through(self):
        """Hole B's `contexts: {}`: the report approved because nothing was read."""
        self.refused(good_report(), MEASURE, "is not a measurement",
                     measurements={MEASURE: {"screen": "run page", "state": "default", "contexts": {}}})
        second = "2026-09-13-scratch-ui-measure-run-page-live.json"
        self.refused(good_report(), second, "is not a measurement",
                     measurements={MEASURE: measurement(), second: {"screen": "run page"}})

    # ---------- states ----------

    def states(self, *states):
        return {"screen_file": {SCREEN_FILE: screen_file(states=list(states))}.get}

    def test_a_state_the_screen_file_lists_and_nobody_measured_is_refused_by_name(self):
        problem = self.refused(good_report(), "the state finished", "run page", "was not measured", SCREEN_FILE,
                               **self.states("default", "finished"))
        self.assertNotIn("the state default", problem)
        problems = pipeline.check_report(good_report(), **context(**self.states("default", "finished", "needs-you")))
        self.assertEqual(len(problems), 2, problems)

    def test_control_every_listed_state_measured_passes(self):
        live = "2026-09-13-scratch-ui-measure-run-page-finished.json"
        self.passes(good_report(), measurements={MEASURE: measurement(), live: measurement(state="finished")},
                    **self.states("default", "finished"))
        # A screen file that lists no states asks for none.
        held = screen_file()
        del held["states"]
        self.passes(good_report(), screen_file={SCREEN_FILE: held}.get)
        self.passes(good_report(), **self.states())

    def test_a_state_measured_for_another_screen_does_not_count(self):
        shell = "2026-09-13-scratch-ui-measure-shell-finished.json"
        self.refused(good_report(), "the state finished", "was not measured",
                     measurements={MEASURE: measurement(), shell: measurement(screen="shell", state="finished")},
                     **self.states("default", "finished"))

    def test_a_state_is_the_state_its_name_spells(self):
        other = "2026-09-13-scratch-ui-measure-run-page-finished.json"
        self.refused(good_report(), "the state finished", "was not measured",
                     measurements={MEASURE: measurement(), other: measurement(state="Finished")},
                     **self.states("default", "finished"))

    # ---------- the screen's name, exactly ----------

    def test_a_measurement_under_another_spelling_is_not_this_screen_s(self):
        """P20. Case and spacing are part of the name."""
        for spelling in ("Run Page", "RUN PAGE", "run  page", "run-page"):
            with self.subTest(spelling=spelling):
                self.refused(good_report(), "run page", "was not measured",
                             measurements={MEASURE: measurement(screen=spelling)})

    def test_a_measurement_no_report_reviews_is_refused(self):
        other = "2026-09-13-scratch-ui-measure-run-page-live.json"
        measurements = {MEASURE: measurement(), other: measurement(failures=[failure()], screen="Run Page")}
        problems = pipeline.unreviewed_measurements(measurements, {"run page"})
        self.assertEqual(len(problems), 1, problems)
        for word in (other, "Run Page", "measured but not reviewed"):
            self.assertIn(word, problems[0])
        self.assertEqual(pipeline.unreviewed_measurements(measurements, {"run page", "Run Page"}), [])
        self.assertEqual(len(pipeline.unreviewed_measurements(measurements, set())), 2)


class TestTheGateReadsTheMeasurements(UiGateCase):
    """G6, 6, through the command."""

    def ready(self, report):
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()
        self.report(report)

    def beside(self, name, body):
        self.write(os.path.join("docs", "measurements", name), body if isinstance(body, str) else json.dumps(body))

    def test_a_hand_written_one_line_measurement_is_refused_by_name(self):
        """Hole B, as the breaker ran it: a changed page, {"screen": "run page"}, an empty Approve."""
        self.ready(good_report(findings=[], notVerified=[],
                               coverage=[{"domain": d, "inspected": "x", "result": "clear"} for d in DOMAINS]))
        self.beside(MEASURE, {"screen": "run page"})
        self.assertRefused(self.close(), MEASURE, "is not a measurement", "state", "contexts")

    def test_a_file_with_no_screen_is_refused_by_name(self):
        self.ready(good_report())
        second = "2026-09-13-scratch-ui-measure-run-page-live.json"
        held = measurement()
        del held["screen"]
        self.beside(second, held)
        self.assertRefused(self.close(), second, "is not a measurement", "screen")

    def test_a_state_the_screen_file_lists_and_nobody_measured_is_refused(self):
        self.ready(good_report())
        self.write(SCREEN_FILE, json.dumps(screen_file(states=["default", "finished"])))
        self.assertRefused(self.close(), REPORT, "the state finished", "was not measured")
        self.beside("2026-09-13-scratch-ui-measure-run-page-finished.json", measurement(state="finished"))
        self.assertCloses(self.close())

    def test_a_failing_measurement_under_another_spelling_is_refused_as_not_reviewed(self):
        """Hole D. A clean file for "run page" beside a failing one for "Run Page"."""
        self.ready(good_report())
        second = "2026-09-13-scratch-ui-measure-run-page-live.json"
        self.beside(second, measurement(failures=[failure()], screen="Run Page"))
        out = self.close()
        self.assertRefused(out, second, "Run Page", "measured but not reviewed")
        check = self.run_pipeline("ui-gate", "check", "scratch")
        self.assertEqual(check.returncode, 2, check.stdout)
        self.assertIn("measured but not reviewed", check.stdout)


class TestARulingPointsAtWords(CheckReportCase):
    """G6, 7 (hole G, P16). A ruling's `quote` is found in the file its
    `where` names, and neither `where` nor `screenFile` leaves the repository.
    ALYDAS-455: in the whole file, exactly once. A line added above the ruled
    place moved a ruling off its comment while the file made the same choice,
    and a quote found twice does not say which place is ruled."""

    def lines(self, **at):
        return {"file_lines": {CONTROLS: lines_of(170, **at)}.get}

    def quoting(self, quote, **over):
        return {"rulings": [ruling(quote=quote, **over)]}

    def test_a_quote_found_anywhere_in_the_file_passes(self):
        for line in ("l1", "l12", "l90", "l170"):
            with self.subTest(line=line):
                self.passes(cited(), **self.lines(**{line: STOP_COMMENT}))

    def test_lines_added_above_the_quote_change_nothing(self):
        for added in (1, 3, 10, 150):
            with self.subTest(added=added):
                moved = {"file_lines": {CONTROLS: [""] * added + lines_of(170, l12=STOP_COMMENT)}.get}
                self.passes(cited(), **moved)

    def test_a_quote_found_at_the_line_passes_whatever_its_case_and_spacing(self):
        for quote in ("Stop is drawn in ink", "stop IS drawn\n   in  ink", "a person's decision, not a failure."):
            with self.subTest(quote=quote):
                self.passes(cited(), **self.quoting(quote))

    def test_a_quote_found_twice_is_refused_and_says_how_many_times(self):
        self.refused(cited(), "finding 1 (COLOR-TREATMENT)", "stop-is-plain", "quote", CONTROLS, "2 times",
                     **self.lines(l12=STOP_COMMENT, l90=STOP_COMMENT))
        self.refused(cited(), "stop-is-plain", "3 times",
                     **self.lines(l12=STOP_COMMENT, l13="// Stop is drawn in ink, again", l90=STOP_COMMENT))

    def test_a_quote_may_run_over_a_line_end(self):
        self.passes(cited(), **self.lines(l12="// the danger colour", l13="// is for what failed"),
                    **self.quoting("the danger colour // is for what failed"))

    def test_a_quote_that_is_not_in_the_file_is_refused(self):
        self.refused(cited(), "finding 1 (COLOR-TREATMENT)", "stop-is-plain", "quote", "Stop is red",
                     **self.quoting("Stop is red"))

    def test_a_ruling_with_no_quote_is_refused_since_it_names_no_place(self):
        bare = ruling()
        del bare["quote"]
        self.refused(cited(), "stop-is-plain", "quote", rulings=[bare])
        self.passes(cited())

    def test_a_where_that_leaves_the_repository_is_refused_though_a_file_is_there(self):
        anything = {"file_lines": lambda path: [STOP_COMMENT]}
        for where in ("../outside.tsx", "apps/../../outside.tsx", "/etc/hosts", "apps/desk/.."):
            with self.subTest(where=where):
                self.refused(cited(), "stop-is-plain", "points nowhere", "outside the repository",
                             rulings=[ruling(where=where)], **anything)
        self.passes(cited(), rulings=[ruling(where="apps/desk/..hidden/notes.md")], **anything)

    def test_a_screen_file_that_leaves_the_repository_is_refused_though_a_file_is_there(self):
        anything = {"screen_file": lambda path: screen_file()}
        for named in ("../run-page.json", "ui-gate/../../run-page.json", "/tmp/run-page.json"):
            with self.subTest(screenFile=named):
                self.refused(good_report(screenFile=named), named, "outside the repository", **anything)
        self.passes(good_report(screenFile="ui-gate/..run-page.json"), **anything)


class TestARulingsQuote(unittest.TestCase):
    def test_a_quote_is_required_and_it_is_text(self):
        """ALYDAS-455. The quote is the place now, so a ruling has one."""
        self.assertEqual(pipeline.check_rulings({"rulings": [ruling(quote="Stop is drawn in ink")]})[1], [])
        bare = ruling()
        del bare["quote"]
        for entry in [bare] + [ruling(quote=quote) for quote in ("", "   ", 7, None, ["Stop"])]:
            with self.subTest(quote=entry.get("quote", "missing")):
                kept, problems = pipeline.check_rulings({"rulings": [entry]})
                self.assertEqual(len(problems), 1, problems)
                self.assertIn("ruling 1 (stop-is-plain)", problems[0])
                self.assertIn("quote", problems[0])
                self.assertEqual(kept, [])


class TestNothingIsReadOutsideTheRepository(UiGateCase):
    """G6, 7 (P16), through the command, with real files: a path that leads
    out of the repository is not read, by `..`, by an absolute path or by a
    link."""

    def setUp(self):
        super().setUp()
        import tempfile
        self.outside = tempfile.TemporaryDirectory()
        self.addCleanup(self.outside.cleanup)
        with open(os.path.join(self.outside.name, "choice.md"), "w", encoding="utf-8") as f:
            f.write("The save button keeps the desk's own focus ring.\n")
        with open(os.path.join(self.outside.name, "run-page.json"), "w", encoding="utf-8") as f:
            json.dump(screen_file(), f)
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()

    def out_of(self, name):
        """The path from the repository to a file beside it."""
        return os.path.relpath(os.path.join(os.path.realpath(self.outside.name), name), os.path.realpath(self.tmp.name))

    def link(self):
        os.symlink(self.outside.name, os.path.join(self.tmp.name, "linked"))
        return "linked"

    def ruled(self, where):
        self.report(good_report(findings=[finding(severity="HIGH", ruling="ruling-7")]))
        self.rulings([dict(page_ruling("ruling-7"), where=where, quote="the desk's own focus ring")])

    def test_a_where_outside_is_refused(self):
        for label, where in (("..", lambda: self.out_of("choice.md")),
                             ("absolute", lambda: os.path.join(self.outside.name, "choice.md")),
                             ("a link", lambda: self.link() + "/choice.md")):
            with self.subTest(by=label):
                self.ruled(where() + ":1")
                self.assertRefused(self.close(), REPORT, "ruling-7", "points nowhere")

    def test_a_screen_file_outside_is_refused(self):
        for label, named in (("..", lambda: self.out_of("run-page.json")),
                             ("absolute", lambda: os.path.join(self.outside.name, "run-page.json")),
                             ("a link", lambda: self.link() + "/run-page.json")):
            with self.subTest(by=label):
                self.report(good_report(screenFile=named()))
                self.assertRefused(self.close(), REPORT, "screen file")

    def test_control_the_same_files_inside_the_repository_are_read(self):
        self.write("notes/choice.md", "The save button keeps the desk's own focus ring.\n")
        self.ruled("notes/choice.md")
        self.assertCloses(self.close())


class TestSmallBoundaries(CheckReportCase):
    """G6, 10 (P27)."""

    def test_a_not_verified_entry_that_repeats_a_finding_in_another_case_is_refused(self):
        for field in ("why", "before"):
            with self.subTest(field=field):
                report = good_report()
                report["notVerified"] = [report["findings"][0][field].upper()]
                self.refused(report, "notVerified entry 1", field, "not rendered")


class TestRecordOwnsOnlyItsOwnRows(RecordCase):
    """G6, 9 (question 5). The record's rows are the ones whose Domain starts
    `UI gate: `. Every other row is somebody else's, whatever it is called."""

    BY_HAND = ("UI", "layout", " Accessibility ", "Colors", "UI gate", "ui gate: Layout", "UI gate:Layout", "Placement")

    def by_hand(self):
        rows = [f"LOW|{domain}|lib/x{at}.ts:1|written by hand|defer" for at, domain in enumerate(self.BY_HAND)]
        out = self.run_pipeline("table", "scratch", "8", "--columns", ",".join(RECORD_COLUMNS),
                                *[arg for row in rows for arg in ("--row", row)])
        self.assertEqual(out.returncode, 0, out.stderr)
        return self.table()["rows"]

    def test_every_row_the_record_writes_is_marked(self):
        self.two_reports()
        self.assertEqual(self.record().returncode, 0)
        domains = [row[1] for row in self.table()["rows"]]
        self.assertEqual(len(domains), 16)
        self.assertEqual(set(domains), {OWNED + d for d in DOMAINS})
        self.assertTrue(pipeline.UI_GATE_OWNED == OWNED)

    def test_a_hand_written_row_is_never_taken_whatever_its_domain_says(self):
        self.two_reports()
        mine = self.by_hand()
        self.assertEqual(len(mine), len(self.BY_HAND))
        for at in (1, 2):
            with self.subTest(record=at):
                out = self.record()
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                rows = self.table()["rows"]
                self.assertEqual(rows[:len(mine)], mine)
                self.assertEqual(len(rows), len(mine) + 16)
                self.assertIn(f"{len(mine)} other rows", out.stdout)

    def test_a_full_table_of_other_rows_is_refused_and_nothing_is_written(self):
        self.two_reports()
        rows = [f"LOW|Code|lib/a.ts:{at}|review {at}|defer" for at in range(1, 51)]
        out = self.run_pipeline("table", "scratch", "8", "--columns", ",".join(RECORD_COLUMNS),
                                *[arg for row in rows for arg in ("--row", row)])
        self.assertEqual(out.returncode, 0, out.stderr)
        before = self.table()
        out = self.record()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        for words in ("50 rows", "nothing was recorded", "16 findings"):
            self.assertIn(words, out.stderr)
        self.assertEqual(self.table(), before)


class TestTheSharedTableHasABound(unittest.TestCase):
    """G6, 9, as data."""

    def reports(self, count):
        findings = [finding(location=f"app/page.tsx:{at}", before=f"finding {at}") for at in range(1, count + 1)]
        return [(f"report-{at // 15}.json", report_of(findings[at:at + 15]), None) for at in range(0, count, 15)]

    def there(self, code):
        return {"columns": list(RECORD_COLUMNS),
                "rows": [["LOW", "Code", f"lib/a.ts:{at}", f"review {at}", "defer"] for at in range(1, code + 1)]}

    def test_with_no_room_even_for_the_left_out_row_it_refuses(self):
        for code in (50, 60):
            with self.subTest(other_rows=code):
                with self.assertRaises(pipeline.TableFull) as raised:
                    pipeline.ui_gate_table(self.reports(3), self.there(code))
                self.assertIn("50 rows", str(raised.exception))
                self.assertIn("3 findings", str(raised.exception))

    def test_control_with_room_for_one_row_that_row_says_what_was_left_out(self):
        table = pipeline.ui_gate_table(self.reports(3), self.there(49))
        self.assertEqual(len(table["rows"]), 50)
        self.assertEqual(table["rows"][:49], self.there(49)["rows"])
        self.assertEqual(table["rows"][49][1], OWNED + "left out")
        self.assertIn("3 more findings", table["rows"][49][3])

    def test_control_a_full_table_and_nothing_to_record_is_left_as_it_is(self):
        self.assertEqual(pipeline.ui_gate_table([], self.there(50)), self.there(50))

    def test_the_rulings_row_is_the_record_s_first_row_and_does_not_pile_up(self):
        once = pipeline.ui_gate_table(self.reports(2), self.there(3), rulings=["ruling-1", "ruling-7"])
        self.assertEqual([row[1] for row in once["rows"]],
                         ["Code"] * 3 + [OWNED + "rulings", OWNED + "Accessibility", OWNED + "Accessibility"])
        row = once["rows"][3]
        self.assertEqual(len(row), len(RECORD_COLUMNS))
        self.assertEqual(row[2], RULINGS_FILE)
        self.assertIn("ruling-1, ruling-7", row[3])
        self.assertEqual(pipeline.ui_gate_table(self.reports(2), once, rulings=["ruling-1", "ruling-7"]), once)
        gone = pipeline.ui_gate_table(self.reports(2), once)
        self.assertEqual([row[1] for row in gone["rows"]].count(OWNED + "rulings"), 0)


class TestARunThatChangesRulingsSaysSo(RecordCase):
    """G6, 8 (hole F). The rulings are read as the run has them, and when the
    run itself added or changed one, `check` names it and `record` puts it on
    the board. Neither refuses."""

    def ready(self, on_main, now, commit=False):
        self.branch_changing("app/page.tsx", on_main={} if on_main is None else {RULINGS_FILE: json.dumps({"rulings": on_main})})
        self.init_scratch()
        if now is not None:
            (self.commit_file if commit else self.write)(RULINGS_FILE, json.dumps({"rulings": now}))
        self.report(good_report(findings=[finding(severity="HIGH", ruling="ruling-7")]))

    def check(self):
        return self.run_pipeline("ui-gate", "check", "scratch")

    def said(self, out):
        return [line for line in out.stdout.splitlines() if "rulings" in line and "this run" in line]

    def test_check_names_the_rulings_that_are_new_or_changed_and_exits_0(self):
        changed = dict(page_ruling("ruling-1"), choice="The save button has no ring at all")
        for commit in (False, True):
            with self.subTest(committed=commit):
                self.ready([ruling(), page_ruling("ruling-1")], [ruling(), changed, page_ruling("ruling-7")], commit=commit)
                out = self.check()
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                said = self.said(out)
                self.assertEqual(len(said), 1, out.stdout)
                self.assertIn("ruling-1, ruling-7", said[0])
                self.assertNotIn("stop-is-plain", said[0])
                self.tmp.cleanup()
                self.setUp()

    def test_a_rulings_file_the_run_wrote_from_nothing_names_every_ruling(self):
        self.ready(None, RULINGS)
        out = self.check()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("stop-is-plain, ruling-1, ruling-7", self.said(out)[0])

    def test_control_rulings_the_run_left_alone_are_not_named(self):
        self.ready(RULINGS, None)
        out = self.check()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.said(out), [])
        self.assertEqual(self.record().returncode, 0)
        self.assertEqual([row[1] for row in self.table()["rows"]], [OWNED + "Accessibility"])

    def test_record_writes_one_row_for_them_and_keeps_it_at_one(self):
        self.ready([ruling()], RULINGS)
        for at in (1, 2):
            with self.subTest(record=at):
                out = self.record()
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                rows = self.table()["rows"]
                self.assertEqual([row[1] for row in rows], [OWNED + "rulings", OWNED + "Accessibility"])
                self.assertEqual(rows[0][2], RULINGS_FILE)
                self.assertIn("ruling-1, ruling-7", rows[0][3])
                self.assertNotIn("stop-is-plain", rows[0][3])


# ---------- ALYDAS-223, Gate 7 ----------

from test_heartbeat import Listener, URL_VARIABLE, TOKEN_VARIABLE  # noqa: E402


class TestRecordReachesTheDesk(RecordCase):
    """G7, 1. `record` changes what the run page draws, so it beats, as `note`
    and `log` do, and it pushes nothing."""

    def env(self, desk):
        return {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}

    def test_record_sends_one_beat_carrying_phase_8_s_table(self):
        self.two_reports()
        with Listener() as desk:
            out = self.run_pipeline("ui-gate", "record", "scratch", env=self.env(desk))
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            [body] = desk.bodies()
        sent = next(p for p in body["ledger"]["phases"] if p["n"] == 8)["table"]
        self.assertEqual(sent["columns"], RECORD_COLUMNS)
        self.assertEqual(len(sent["rows"]), 16)
        self.assertEqual(sent["rows"], self.table()["rows"])

    def test_a_refused_record_and_a_check_send_nothing(self):
        self.init_scratch()
        self.report(good_report(verdict="Perhaps"))
        with Listener() as desk:
            self.assertEqual(self.run_pipeline("ui-gate", "record", "scratch", env=self.env(desk)).returncode, 2)
            self.assertEqual(self.run_pipeline("ui-gate", "check", "scratch", env=self.env(desk)).returncode, 2)
            self.assertEqual(desk.requests, [])

    def test_a_stop_answered_to_record_s_beat_exits_3(self):
        self.two_reports()
        with Listener(reply={"session": {"id": "s1"}, "directive": {"kind": "stop"}}) as desk:
            out = self.run_pipeline("ui-gate", "record", "scratch", env=self.env(desk))
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertEqual(len(self.table()["rows"]), 16, "the record landed before the stop was obeyed")

    def test_record_does_not_push_the_branch(self):
        origin = self.repo_with_origin()
        self.git("checkout", "-q", "-b", "feature/alydas-223-g7")
        pushes = os.path.join(os.path.dirname(origin), "pushes.log")
        hook = os.path.join(origin, "hooks", "post-receive")
        with open(hook, "w") as f:
            f.write(f"#!/bin/sh\necho pushed >> '{pushes}'\n")
        os.chmod(hook, 0o755)
        self.two_reports()
        self.commit("unpushed.txt")
        with Listener() as desk:
            out = self.run_pipeline("ui-gate", "record", "scratch", env=self.env(desk))
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(len(desk.requests), 1)
        self.assertFalse(os.path.exists(pushes), "record pushed the branch")


class TestTheCapAgainstCoverage(CheckReportCase):
    """G7, 3. A domain whose findings were all cut by the cap may say
    `findings` with none, when its `inspected` says so in the agreed words."""

    def cut(self, inspected):
        # G8: the cap cuts only from a full report (it was good_report(), one finding).
        report = full_report()
        for row in report["coverage"]:
            if row["domain"] == "Layout":
                row.update(result="findings", inspected=inspected)
        return report

    def test_findings_with_none_passes_when_inspected_says_left_out_by_the_cap(self):
        for inspected in ("run-page.css; 3 LOW findings left out by the cap", "Left out by the cap: two LOW"):
            with self.subTest(inspected=inspected):
                self.passes(self.cut(inspected))

    def test_findings_with_none_and_no_such_words_is_still_refused(self):
        for inspected in ("run-page.css", "left out", "the cap", "left out by the reviewer"):
            with self.subTest(inspected=inspected):
                self.refused(self.cut(inspected), "Layout has findings", "no finding names it", "left out by the cap")

    def test_the_words_excuse_nothing_else(self):
        """`clear` beside a finding is refused whatever `inspected` says."""
        report = good_report()
        report["coverage"][0].update(result="clear", inspected="left out by the cap")
        self.refused(report, "Accessibility is clear")


class TestTheLookAndTheOpposite(CheckReportCase):
    """G7, ruling on COLOR-MEANING. The opposite case stays COLOR-MEANING and
    HIGH on sight; the plain look is COLOR-TREATMENT, which a ruling on the
    look answers, and which may be MEDIUM."""

    def test_color_treatment_at_medium_or_low_is_accepted(self):
        self.assertIn("COLOR-TREATMENT", pipeline.UI_GATE_RULES)
        self.assertNotIn("COLOR-TREATMENT", pipeline.UI_GATE_AUTOMATIC_HIGH)
        for severity in ("MEDIUM", "LOW"):
            with self.subTest(severity=severity):
                self.passes(good_report(findings=[finding(rule="COLOR-TREATMENT", severity=severity)]))
                self.passes(cited(severity=severity))

    def test_color_meaning_at_medium_is_still_refused(self):
        self.assertIn("COLOR-MEANING", pipeline.UI_GATE_AUTOMATIC_HIGH)
        self.refused(good_report(findings=[finding(rule="COLOR-MEANING", severity="MEDIUM")]),
                     "finding 1 (COLOR-MEANING)", "MEDIUM", "COLOR-MEANING is automatic HIGH")

    def test_a_ruling_on_the_look_answers_neither_the_opposite_nor_the_missing_question(self):
        self.assertEqual(ruling()["rules"], ["COLOR-TREATMENT"])
        for domain, rule in (("Writing", "WRITE-DESTRUCTIVE"), ("Colors", "COLOR-MEANING")):
            with self.subTest(rule=rule):
                self.refused(cited(domain=domain, rule=rule, severity="HIGH"),
                             f"finding 1 ({rule})", "stop-is-plain", "covers the rule COLOR-TREATMENT")


class TestTheReportStatesEveryBlock(CheckReportCase):
    """G7, criterion 8. The placement review states, for each block of the
    screen file, which jobs it serves and whether its place fits them."""

    TWO = [{"name": "state and controls", "selector": ".run-page-aside", "serves": [1, 2]},
           {"name": "phase list", "selector": ".run-phases", "serves": [1]}]

    def two_blocks(self):
        return {"screen_file": {SCREEN_FILE: screen_file(blocks=self.TWO)}.get}

    def both(self, **second):
        return [placed(), placed(**dict({"block": "phase list", "serves": [1]}, **second))]

    def share(self):
        return finding(domain="Placement", rule="PLACE-SHARE", location="app/page.tsx:9",
                       before="the phase list takes half the first screen and is a third full")

    def unfit(self, **over):
        """A report with a Placement finding (finding 2) that says why the phase list does not fit."""
        report = good_report(findings=[finding(), self.share()],
                             placement=self.both(**dict({"fits": False, "finding": 2}, **over)))
        return covered(report, "Placement", "findings")

    def test_control_every_block_stated_passes(self):
        self.passes(good_report())
        self.passes(good_report(placement=self.both()), **self.two_blocks())
        self.passes(self.unfit(), **self.two_blocks())
        # A screen file with no blocks asks for an empty list.
        self.passes(good_report(placement=[]), screen_file={SCREEN_FILE: screen_file(blocks=[])}.get)

    def test_a_report_with_no_placement_list_is_refused(self):
        report = good_report()
        del report["placement"]
        self.refused(report, "placement is missing")
        for value in (None, "all fit", {"block": "state and controls"}):
            with self.subTest(placement=value):
                self.refused(good_report(placement=value), "placement is not a list")
        problems = pipeline.check_report(good_report(placement=["state and controls"]), **context())
        self.assertEqual(len(problems), 2, problems)   # and the block it did not state
        self.assertIn("placement entry 1 is not a JSON object", problems)

    def test_a_block_with_no_entry_is_refused_by_name(self):
        self.refused(good_report(), "placement", "phase list", "no entry", **self.two_blocks())
        self.refused(good_report(placement=[]), "state and controls", "no entry")

    def test_a_block_stated_twice_is_refused(self):
        self.refused(good_report(placement=[placed(), placed()]), "state and controls", "2 entries")

    def test_an_entry_for_a_block_the_screen_file_does_not_have_is_refused(self):
        self.refused(good_report(placement=self.both()), "placement entry 2", "phase list", "has no such block")
        self.refused(good_report(placement=[placed(), placed(block="State and Controls")]), "State and Controls",
                     "has no such block")

    def test_serves_that_differs_from_the_screen_file_s_is_refused(self):
        for serves in ([1], [2, 1], [1, 2, 3], [], "1, 2", None):
            with self.subTest(serves=serves):
                self.refused(good_report(placement=[placed(serves=serves)]), "placement entry 1 (state and controls)",
                             "serves", "[1, 2]")

    def test_fits_is_true_or_false(self):
        for fits in ("yes", 1, 0, None):
            with self.subTest(fits=fits):
                self.refused(good_report(placement=[placed(fits=fits)]), "placement entry 1 (state and controls)",
                             "fits", "true or false")
        report = good_report()
        del report["placement"][0]["fits"]
        self.refused(report, "fits", "true or false")

    def test_a_block_that_does_not_fit_points_at_a_placement_finding(self):
        # Two problems: the entry points nowhere, and so nothing points at the finding.
        problems = pipeline.check_report(self.unfit(finding=None), **context(**self.two_blocks()))
        self.assertEqual(len(problems), 2, problems)
        for word in ("placement entry 2 (phase list)", "does not fit", "finding 2 (PLACE-SHARE)"):
            self.assertIn(word, problems[0])
        self.assertIn("no placement entry points at it", problems[1])
        # Finding 1 is there, and it is not a Placement finding.
        problems = pipeline.check_report(self.unfit(finding=1), **context(**self.two_blocks()))
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(any("placement entry 2 (phase list)" in p and "finding 1" in p and "Placement" in p
                            for p in problems), problems)
        for pointer in (0, 3, "2", True, 2.0):
            with self.subTest(finding=pointer):
                problems = pipeline.check_report(self.unfit(finding=pointer), **context(**self.two_blocks()))
                self.assertTrue(any("placement entry 2 (phase list)" in p and "does not fit" in p for p in problems),
                                problems)

    def test_a_block_that_fits_points_at_no_finding(self):
        problems = pipeline.check_report(self.unfit(fits=True), **context(**self.two_blocks()))
        self.assertEqual(len(problems), 2, problems)
        self.assertTrue(any("placement entry 2 (phase list)" in p and "fits" in p and "finding" in p for p in problems))

    def test_a_blank_note_is_refused(self):
        for note in ("", "   ", None, 7):
            with self.subTest(note=note):
                self.refused(good_report(placement=[placed(note=note)]), "placement entry 1 (state and controls)", "note")

    def test_a_placement_finding_no_entry_points_at_is_refused(self):
        report = covered(good_report(findings=[finding(), self.share()], placement=self.both()), "Placement", "findings")
        self.refused(report, "finding 2 (PLACE-SHARE)", "Placement", "no placement entry", **self.two_blocks())

    def test_with_no_usable_screen_file_only_the_list_s_own_form_is_checked(self):
        """The screen file's own refusal stands alone; the blocks cannot be compared."""
        self.refused(good_report(), SCREEN_FILE, "does not exist", screen_file=None)


class TestAnObjectionTheCapLeftOut(CheckReportCase):
    """G7. A block that does not fit, whose Placement finding the cap cut,
    says so (`leftOut`), and is not made to say it fits."""

    def left(self, **over):
        # G8: the cap cuts only from a full report (it was good_report(), one finding),
        # and a block that does not fit makes Placement `findings` (it was `clear`).
        entry = dict({"fits": False, "finding": None, "leftOut": True,
                      "note": "half the first screen and a third full: PLACE-SHARE"}, **over)
        report = full_report(placement=[placed(**entry)])
        if entry["fits"] is False:
            cut_placement(report)
        return report

    def test_a_block_that_does_not_fit_with_its_objection_left_out_passes(self):
        self.passes(self.left())
        # G8: PLACE-MISSING was the first of these. It is automatic HIGH, which the cap never cuts.
        for rule in ("PLACE-OFFSCREEN", "PLACE-ORDER"):
            with self.subTest(rule=rule):
                self.passes(self.left(note=f"below the fold at phone ({rule})"))

    def test_the_note_names_a_placement_rule(self):
        for note in ("half the first screen and a third full", "PLACE-SHAPE", "COLOR-TOKEN", "place-share"):
            with self.subTest(note=note):
                self.refused(self.left(note=note), "placement entry 1 (state and controls)", "left out by the cap",
                             "note", "PLACE-")

    def test_not_verified_says_something_was_left_out(self):
        report = self.left()
        report["notVerified"] = []
        self.refused(report, "placement entry 1 (state and controls)", "left out by the cap", "notVerified")

    def test_left_out_with_fits_true_or_with_a_finding_is_refused(self):
        self.refused(self.left(fits=True), "placement entry 1 (state and controls)", "leftOut", "fits")
        share = finding(domain="Placement", rule="PLACE-SHARE", location="app/page.tsx:9", before="too much room")
        report = covered(good_report(findings=[finding(), share],
                                     placement=[placed(fits=False, finding=2, leftOut=True, note="PLACE-SHARE")]),
                         "Placement", "findings")
        # Two problems: the entry is refused, so nothing points at the finding either.
        problems = pipeline.check_report(report, **context())
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("placement entry 1 (state and controls) has leftOut true and a finding", problems[0])

    def test_left_out_is_true_or_false_and_false_is_as_if_absent(self):
        for value in ("yes", 1, None):
            with self.subTest(leftOut=value):
                self.refused(good_report(placement=[placed(leftOut=value)]), "leftOut", "true or false")
        self.passes(good_report(placement=[placed(leftOut=False)]))
        # Without it, a block that does not fit still needs its finding.
        self.refused(self.left(leftOut=False), "does not fit", "finding")
        report = self.left()
        del report["placement"][0]["leftOut"]
        self.refused(report, "does not fit", "finding")

    def test_several_blocks_may_point_at_one_finding(self):
        blocks = [{"name": "state and controls", "selector": ".a", "serves": [1, 2]},
                  {"name": "phase list", "selector": ".b", "serves": [1]}]
        share = finding(domain="Placement", rule="PLACE-ORDER", location="app/page.tsx:9", before="the two are swapped")
        report = covered(good_report(findings=[finding(), share], placement=[
            placed(fits=False, finding=2), placed(block="phase list", serves=[1], fits=False, finding=2)]),
            "Placement", "findings")
        self.passes(report, screen_file={SCREEN_FILE: screen_file(blocks=blocks)}.get)

    def test_a_block_whose_only_objection_is_ruled_fits_and_the_ruled_finding_needs_no_pointer(self):
        ruled = finding(domain="Placement", rule="PLACE-SHARE", location="app/page.tsx:9", before="a matched pair",
                        ruling="ruling-pair")
        rulings = [ruling(id="ruling-pair", rules=["PLACE-SHARE"], covers=["app/page.tsx"], where="app/page.tsx",
                          quote="app/page.tsx")]
        report = covered(good_report(findings=[finding(), ruled],
                                     placement=[placed(note="drawn as a matched pair, ruled: ruling-pair")]),
                         "Placement", "findings")
        self.passes(report, rulings=rulings)
        # The same finding with no ruling still has to be some block's reason.
        report["findings"][1]["ruling"] = None
        self.refused(report, "finding 2 (PLACE-SHARE)", "no placement entry points at it")


# ---------- Gate 8: the review's fixes ----------

FEATURE = "feature/alydas-223-s1"


class TestTheRunsOwnChange(UiGateCase):
    """G8, correctness 1. The gate judges the run's own change: `init` records
    the commit the run started from, and the changed files are read against
    it. So a branch already merged, or a run on the default branch that was
    pushed, is not read as a run that changed nothing."""

    def ledger(self):
        return os.path.join(self.root, "boards", "2026-09-13-scratch.json")

    def rebase(self, base):
        """The ledger with `base` as its recorded base; None takes the field out."""
        run = self.board()
        run.pop("base", None)
        if base is not None:
            run["base"] = base
        with open(self.ledger(), "w", encoding="utf-8") as f:
            json.dump(run, f)

    def merged(self):
        """The reviewer's G5: the branch goes into main by a merge commit,
        which is pushed and fetched, before Phase 8 closes."""
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "-m", "merge the branch", FEATURE)
        self.git("push", "-q", "origin", "main")
        self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", FEATURE)
        self.assertEqual(self.git("merge-base", "HEAD", "origin/main"), self.git("rev-parse", "HEAD"))

    def test_init_records_the_commit_the_run_started_from(self):
        self.branch_changing("tool.py")
        self.init_scratch()
        self.assertEqual(self.board()["base"], self.git("merge-base", "HEAD", "origin/main"))
        self.assertNotEqual(self.board()["base"], self.git("rev-parse", "HEAD"))

    def test_init_outside_a_repository_records_no_base(self):
        self.init_scratch()
        self.assertNotIn("base", self.board())

    def test_a_branch_merged_by_a_merge_commit_still_lists_its_files(self):
        self.branch_changing()
        self.phase_8_otherwise_ready()
        self.commit_file("app/page.tsx")
        self.skip()
        self.merged()
        self.assertRefused(self.close(), "app/page.tsx", "skip cannot stand")

    def test_a_run_on_the_default_branch_after_a_push_still_lists_its_files(self):
        """The reviewer's G4 and G4b."""
        self.repo_with_origin()
        self.phase_8_otherwise_ready()
        self.commit_file("app/page.tsx")
        self.git("push", "-q", "origin", "main")
        self.git("fetch", "-q", "origin")
        self.assertRefused(self.close(), "app/page.tsx", "UI gate report")
        self.skip()
        self.assertRefused(self.close(), "app/page.tsx", "skip cannot stand")

    def test_control_a_feature_branch_still_lists_its_files_and_one_with_no_surface_closes(self):
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()
        self.assertIn("base", self.board())
        self.assertRefused(self.close(), "app/page.tsx", "UI gate report")

    def test_control_a_merged_branch_that_drew_nothing_still_closes_on_its_skip(self):
        self.branch_changing()
        self.phase_8_otherwise_ready()
        self.commit_file("tool.py")
        self.skip()
        self.merged()
        self.record_a_review()  # Gate 8, CR-1: recorded again at the HEAD it closes on
        self.assertCloses(self.close())

    def test_a_rebased_branch_lists_only_the_run_s_own_files(self):
        """Ruling: the merge-base as it is now is the base while it is not
        HEAD, so a rebase does not pull in what the default branch changed."""
        self.branch_changing()
        self.phase_8_otherwise_ready()
        started = self.board()["base"]
        self.commit_file("tool.py")
        self.git("checkout", "-q", "main")
        self.commit_file("app/theirs.tsx")
        self.git("push", "-q", "origin", "main")
        self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", FEATURE)
        self.git("rebase", "-q", "origin/main")
        self.assertNotEqual(self.git("merge-base", "HEAD", "origin/main"), started)
        self.skip()
        self.record_a_review()  # Gate 8, CR-1: a rebase moves HEAD, so the review is recorded again
        self.assertCloses(self.close())

    def test_a_rebased_branch_that_drew_something_names_its_own_page_and_not_theirs(self):
        self.branch_changing()
        self.phase_8_otherwise_ready()
        self.commit_file("app/page.tsx")
        self.git("checkout", "-q", "main")
        self.commit_file("app/theirs.tsx")
        self.git("push", "-q", "origin", "main")
        self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", FEATURE)
        self.git("rebase", "-q", "origin/main")
        out = self.close()
        self.assertRefused(out, "app/page.tsx", "UI gate report")
        self.assertNotIn("app/theirs.tsx", out.stderr)

    def test_init_with_an_origin_that_cannot_be_reached_still_succeeds(self):
        """Ruling 5. No origin/HEAD, so origin is asked, and it is not there."""
        self.git("init", "-q", "-b", "trunk")
        self.commit("first.txt")
        self.git("remote", "add", "origin", os.path.join(self.tmp.name, "no-such-origin.git"))
        self.init_scratch()
        self.assertNotIn("base", self.board())

    def test_a_ledger_with_no_recorded_base_is_judged_by_the_merge_base(self):
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()
        self.rebase(None)
        self.assertRefused(self.close(), "app/page.tsx", "UI gate report")

    def test_a_recorded_base_git_does_not_know_or_that_is_no_commit_is_not_used(self):
        for base in ("0" * 40, "--output=pwn.tsx", 7, "HEAD"):
            with self.subTest(base=base):
                if not os.path.isdir(os.path.join(self.tmp.name, ".git")):
                    self.branch_changing("app/page.tsx")
                    self.phase_8_otherwise_ready()
                self.rebase(base)
                out = self.close()
                self.assertRefused(out, "app/page.tsx", "UI gate report")
                self.assertNotIn("could not be worked out", out.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "pwn.tsx")))

    def test_with_no_recorded_base_a_merged_branch_and_a_clean_tree_is_unknown_not_empty(self):
        """Nothing tells "changed nothing" from "already merged" when the base
        is HEAD and the tree is clean, so the skip does not close the phase."""
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()
        self.skip()
        self.rebase(None)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "the run's own files")
        self.merged()
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertRefused(self.close(), "could not be worked out", "nothing ahead of its base",
                           "already be merged", "default branch")


class TestTheNamesGitGives(UiGateCase):
    """G8, security 4 and correctness 8. A file's name is a name, whatever it
    spells: one named as the base commit's hash, and one that is not UTF-8."""

    def test_a_file_named_as_the_base_commit_does_not_stop_git_listing_the_files(self):
        self.branch_changing("app/page.tsx")
        self.commit_file(self.git("merge-base", "HEAD", "origin/main"))
        self.phase_8_otherwise_ready()
        out = self.close()
        self.assertRefused(out, "app/page.tsx", "UI gate report")
        self.assertNotIn("could not be worked out", out.stderr)

    def test_a_path_that_is_not_utf8_is_a_path_and_the_page_beside_it_is_still_seen(self):
        """A Latin-1 name in the base's tree. macOS cannot check it out, so it
        is put in the index by hand and shows as deleted."""
        import subprocess
        self.repo_with_origin()
        blob = self.git("hash-object", "-w", "first.txt")
        subprocess.run([b"git", b"update-index", b"--add", b"--cacheinfo",
                        b"100644," + blob.encode() + b",app/caf\xe9.css"], cwd=self.tmp.name, check=True)
        self.git("commit", "-q", "-m", "a name that is not UTF-8")
        self.git("push", "-q", "origin", "main")
        self.git("fetch", "-q", "origin")
        self.git("checkout", "-q", "-b", FEATURE)
        self.commit_file("app/page.tsx")
        self.phase_8_otherwise_ready()
        out = self.close()
        self.assertNotIn("Traceback", out.stderr)
        self.assertRefused(out, "app/page.tsx", "app/caf�.css", "UI gate report")


def holding(count, **over):
    """A well formed report of `count` findings, each at its own line."""
    return report_of([finding(severity="LOW", location=f"app/page.tsx:{at}") for at in range(1, count + 1)], **over)


LEFT_OUT = {"fits": False, "finding": None, "leftOut": True, "note": "half the first screen and a third full: PLACE-SHARE"}


class TestTheCapCutsOnlyAFullReport(CheckReportCase):
    """G8, correctness 5. The cap cannot have left anything out of a report
    that is not full, and it never leaves out an automatic HIGH: so neither
    `leftOut` nor "left out by the cap" lets a report Approve what it names."""

    MOST = pipeline.UI_GATE_MOST_FINDINGS

    def cut(self, count, **over):
        """Layout says its findings were all cut, in a report of `count`."""
        report = holding(count, **over)
        for row in report["coverage"]:
            if row["domain"] == "Layout":
                row.update(result="findings", inspected="run-page.css; 3 LOW findings left out by the cap")
        return report

    def test_the_reviewer_s_first_probe_left_out_naming_an_automatic_high_is_refused(self):
        """No finding, Placement clear, Approve, and a block whose note says PLACE-MISSING."""
        report = good_report(findings=[], placement=[placed(**dict(LEFT_OUT, note="no block for job 1: PLACE-MISSING"))])
        covered(report, "Accessibility", "clear")
        problems = pipeline.check_report(report, **context())
        self.assertTrue(any("PLACE-MISSING" in line and "automatic HIGH" in line and "never left out by the cap" in line
                            for line in problems), problems)

    def test_left_out_naming_an_automatic_high_is_refused_in_a_full_report_too(self):
        for rule in pipeline.UI_GATE_AUTOMATIC_HIGH:
            with self.subTest(rule=rule):
                note = f"takes half the first screen (PLACE-SHARE, and {rule})"
                self.refused(cut_placement(holding(self.MOST, placement=[placed(**dict(LEFT_OUT, note=note))])),
                             "placement entry 1 (state and controls)", rule, "automatic HIGH", "never left out by the cap")
        self.assertIn("PLACE-MISSING", pipeline.UI_GATE_AUTOMATIC_HIGH)

    def test_left_out_is_refused_unless_the_report_is_full(self):
        """The reviewer's second probe is the first of these."""
        for count in (0, 1, self.MOST - 1):
            with self.subTest(findings=count):
                # Placement is `findings`, as a block that does not fit needs, and stays out of the count.
                report = covered(holding(count, placement=[placed(**LEFT_OUT)]), "Placement", "findings")
                problems = [line for line in pipeline.check_report(report, **context()) if line.startswith("placement")]
                self.assertEqual(len(problems), 1, problems)
                for words in ("placement entry 1 (state and controls)", "left out by the cap",
                              f"the report holds {count} finding", f"at most {self.MOST}"):
                    self.assertIn(words, problems[0])
        self.passes(cut_placement(holding(self.MOST, placement=[placed(**LEFT_OUT)])))

    def test_coverage_left_out_by_the_cap_is_refused_unless_the_report_is_full_or_blocks(self):
        """The reviewer's third probe is the first of these."""
        for count in (0, 1, self.MOST - 1):
            with self.subTest(findings=count):
                self.refused(self.cut(count), "Layout", "left out by the cap", f"the report holds {count} finding",
                             f"at most {self.MOST}")
        self.passes(self.cut(self.MOST))
        # A Block is not waiting on the cap to be honest: it blocks already.
        blocking = self.cut(1)
        blocking["findings"][0]["severity"] = "HIGH"
        blocking["verdict"] = "Block"
        self.passes(blocking)
        blocking["verdict"] = "Approve"
        self.assertEqual(len(pipeline.check_report(blocking, **context())), 2)


MB = 1024 * 1024
SHELL_MEASURE = "2026-09-13-scratch-ui-measure-shell-default.json"


class TestTheGateReadsOnlyFilesInsideTheRepository(UiGateCase):
    """G8, security 2 and 6. A file the gate finds for itself is read only
    when its real path is inside the repository (or the run's root), it is a
    regular file, and it is not over 5 MB. Anything else is named, not read."""

    def setUp(self):
        super().setUp()
        import tempfile
        self.outside = tempfile.TemporaryDirectory()
        self.addCleanup(self.outside.cleanup)
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()

    def out(self, name, body):
        full = os.path.join(self.outside.name, name)
        with open(full, "w", encoding="utf-8") as f:
            f.write(body if isinstance(body, str) else json.dumps(body))
        return full

    def link(self, target, relative):
        full = os.path.join(self.tmp.name, relative)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        if os.path.lexists(full):
            os.remove(full)
        os.symlink(target, full)

    def check(self, timeout=30):
        """`ui-gate check`, with a bound: a pipe that is opened is never answered."""
        import subprocess, sys
        from test_heartbeat import PIPELINE
        environment = {k: v for k, v in os.environ.items() if k not in (URL_VARIABLE, TOKEN_VARIABLE)}
        return subprocess.run([sys.executable, PIPELINE, "--root", self.root, "ui-gate", "check", "scratch"],
                              capture_output=True, text=True, cwd=self.tmp.name, env=environment, timeout=timeout)

    def both(self, *named, never=()):
        """Phase 8 and `ui-gate check` both refuse, naming `named`, and neither says any of `never`."""
        closing = self.close()
        self.assertRefused(closing, *named)
        checking = self.check()
        self.assertEqual(checking.returncode, 2, checking.stdout + checking.stderr)
        for words in named:
            self.assertIn(words, checking.stdout)
        for words in never:
            self.assertNotIn(words, closing.stdout + closing.stderr + checking.stdout + checking.stderr)
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.events.jsonl"), encoding="utf-8") as f:
            log = f.read()
        for words in never:
            self.assertNotIn(words, log)

    # ---------- a link that leads out (the reviewer's reproduction) ----------

    def test_a_report_that_is_a_link_out_of_the_repository_is_named_and_not_read(self):
        self.measured()
        self.link(self.out("r.json", good_report(verdict="SECRET-VERDICT", screen="SECRET-SCREEN-VALUE")),
                  os.path.join("docs", "measurements", REPORT))
        self.both(REPORT, "outside the repository", "not read", never=("SECRET-VERDICT", "SECRET-SCREEN-VALUE"))

    def test_a_measurement_file_that_is_a_link_out_is_named_and_not_read(self):
        self.report(good_report())
        self.link(self.out("m.json", measurement(screen="SECRET-SCREEN-VALUE")),
                  os.path.join("docs", "measurements", SHELL_MEASURE))
        self.both(SHELL_MEASURE, "outside the repository", "not read", never=("SECRET-SCREEN-VALUE",))

    def test_a_rulings_file_that_is_a_link_out_is_named_and_not_read(self):
        self.report(good_report())
        self.link(self.out("rulings.json", {"rulings": [{"id": "SECRET-ID"}, dict(ruling(), id="SECRET-WHOLE")]}),
                  RULINGS_FILE)
        self.both(RULINGS_FILE, "outside the repository", "not read", never=("SECRET-ID", "SECRET-WHOLE"))

    def test_a_screen_file_that_is_a_link_out_is_named_and_not_read(self):
        self.report(good_report())
        self.link(self.out("run-page.json", screen_file(screen="SECRET-SCREEN-VALUE")), SCREEN_FILE)
        self.both(REPORT, SCREEN_FILE, "outside the repository", "not read", never=("SECRET-SCREEN-VALUE",))

    def test_control_a_link_to_a_file_inside_the_repository_is_read(self):
        self.report(good_report())
        os.makedirs(os.path.join(self.tmp.name, "kept"))
        for relative in (os.path.join("docs", "measurements", REPORT), os.path.join("docs", "measurements", MEASURE),
                         SCREEN_FILE):
            kept = os.path.join(self.tmp.name, "kept", os.path.basename(relative))
            os.rename(os.path.join(self.tmp.name, relative), kept)
            self.link(kept, relative)
        self.assertCloses(self.close())

    # ---------- not a regular file ----------

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no named pipes here")
    def test_a_pipe_is_named_and_never_opened(self):
        self.report(good_report())
        for label, relative in (("report", os.path.join("docs", "measurements", REPORT)),
                                ("measurement", os.path.join("docs", "measurements", SHELL_MEASURE)),
                                ("rulings", RULINGS_FILE)):
            with self.subTest(file=label):
                full = os.path.join(self.tmp.name, relative)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                if os.path.lexists(full):
                    os.remove(full)
                os.mkfifo(full)
                out = self.check()
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                self.assertIn(os.path.basename(relative), out.stdout)
                self.assertIn("not a regular file", out.stdout)
                os.remove(full)
                self.report(good_report())

    def test_a_link_to_a_device_is_named_and_never_opened(self):
        if not os.path.exists("/dev/zero"):
            self.skipTest("no /dev/zero here")
        self.measured()
        self.link("/dev/zero", os.path.join("docs", "measurements", REPORT))
        out = self.check()
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn(REPORT, out.stdout)
        self.assertIn("not read", out.stdout)

    # ---------- the size ceiling, both sides of it ----------

    def padded(self, body, size):
        text = json.dumps(body)
        return text + " " * (size - len(text))

    def test_a_json_file_over_5_mb_is_refused_by_name_and_one_of_5_mb_is_read(self):
        self.assertEqual(pipeline.UI_GATE_MOST_BYTES, 5 * MB)
        cases = (("report", os.path.join("docs", "measurements", REPORT), good_report(), REPORT),
                 ("measurement", os.path.join("docs", "measurements", MEASURE), measurement(), MEASURE),
                 ("rulings", RULINGS_FILE, {"rulings": RULINGS}, RULINGS_FILE),
                 ("screen file", SCREEN_FILE, screen_file(), SCREEN_FILE))
        for label, relative, body, name in cases:
            with self.subTest(file=label):
                self.report(good_report())
                self.write(relative, self.padded(body, 5 * MB + 1))
                self.assertRefused(self.close(), name, "over 5 MB", "not read")
                self.write(relative, self.padded(body, 5 * MB))
                out = self.run_pipeline("check", "scratch", "8")
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_a_where_that_names_a_file_over_5_mb_is_refused_by_name(self):
        line = "The save button keeps the desk's own focus ring.\n"
        self.report(good_report(findings=[finding(severity="HIGH", ruling="ruling-7")]))
        self.rulings([dict(page_ruling("ruling-7"), where="notes/choice.md", quote="the desk's own focus ring")])
        self.write("notes/choice.md", line + "x" * (5 * MB + 1 - len(line)))
        self.assertRefused(self.close(), REPORT, "ruling-7", "notes/choice.md", "over 5 MB", "not read")
        self.write("notes/choice.md", line + "x" * (5 * MB - len(line)))
        self.assertCloses(self.close())


class TestTheConfigHasTheSameCeiling(UiGateCase):
    """G8, security 6. `ui-gate/config.json` is read from git at the base, and held to the same 5 MB."""

    def config(self, size):
        text = json.dumps({"surfaces": ["*.tsx"]})
        self.branch_changing("app/page.tsx", on_main={"ui-gate/config.json": text + " " * (size - len(text))})
        self.phase_8_otherwise_ready()
        return self.close()

    def test_a_config_over_5_mb_is_refused_by_name(self):
        self.assertRefused(self.config(5 * MB + 1), "ui-gate/config.json", "over 5 MB")

    def test_control_a_config_of_5_mb_is_used(self):
        out = self.config(5 * MB)
        self.assertRefused(out, "app/page.tsx", "UI gate report")
        self.assertNotIn("over 5 MB", out.stderr)


class TestAWhereIsReadOnce(unittest.TestCase):
    """G8, security 6. Within one check a file is read once, however many findings cite it."""

    def test_the_lines_of_a_file_are_read_once_in_one_check(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            here = os.getcwd()
            self.addCleanup(os.chdir, here)
            os.chdir(tmp)
            os.makedirs("notes")
            with open("notes/choice.md", "w", encoding="utf-8") as f:
                f.write("one\ntwo\n")
            run = {"root": os.path.join(tmp, "docs"), "slug": "scratch"}
            lines = pipeline.ui_gate_context(run)[0]["file_lines"]
            first = lines("notes/choice.md")
            self.assertEqual(first, ["one", "two"])
            with open("notes/choice.md", "w", encoding="utf-8") as f:
                f.write("three\n")
            self.assertIs(lines("notes/choice.md"), first)
            # Control: the next check reads the file as it is then.
            self.assertEqual(pipeline.ui_gate_context(run)[0]["file_lines"]("notes/choice.md"), ["three"])
            self.assertIsNone(lines("notes/gone.md"))


ESCAPE = "\x1b"


class TestWhatTheGatePrintsIsSafeForATerminal(UiGateCase):
    """G8, security 7. A report's own text reaches the terminal through `printable`."""

    def setUp(self):
        super().setUp()
        self.branch_changing("app/page.tsx", "ui-gate/pa\x1b[2Jge.css")
        self.phase_8_otherwise_ready()

    def test_an_escape_sequence_in_a_verdict_and_a_location_is_not_printed(self):
        self.report(good_report(verdict="\x1b[2J\x1b[31mApprove", findings=[finding(location="app/\x1b]0;owned\x07page.tsx")]))
        self.rulings([dict(ruling(), id="stop\x1b[2J-is-plain")])
        check = self.run_pipeline("ui-gate", "check", "scratch")
        self.assertEqual(check.returncode, 2, check.stdout + check.stderr)
        # What is said is still said: the verdict, the location and the ruling's id, with the bytes that act taken out.
        self.assertIn("[31mApprove", check.stdout)
        self.assertIn("0;owned page.tsx", check.stdout)
        self.assertRegex(check.stdout, r"note: this run added or changed 1 of the rulings .*stop \[2J-is-plain")
        closing = self.close()
        self.assertRefused(closing, "[31mApprove", "0;owned page.tsx")
        self.assertIn("note: this run added or changed 1 of the rulings", closing.stderr)
        record = self.run_pipeline("ui-gate", "record", "scratch")
        checks = self.run_pipeline("check", "scratch", "8")
        self.assertEqual(checks.returncode, 2, checks.stdout + checks.stderr)
        self.assertIn("[31mApprove", checks.stdout)
        for out in (check, closing, record, checks):
            for unsafe in (ESCAPE, "\x07"):
                self.assertNotIn(unsafe, out.stdout)
                self.assertNotIn(unsafe, out.stderr)

    def test_a_changed_file_s_name_is_printed_safely_when_phase_8_names_it(self):
        closing = self.close()
        self.assertRefused(closing, "app/page.tsx", "ui-gate/pa [2Jge.css")
        skipping = self.run_pipeline("phase", "scratch", "8", "skipped", "--note", "nothing to review")
        self.assertEqual(skipping.returncode, 2, skipping.stdout + skipping.stderr)
        self.assertIn("ui-gate/pa [2Jge.css", skipping.stderr)
        for out in (closing, skipping):
            self.assertNotIn(ESCAPE, out.stdout + out.stderr)


class TestAFindingIsAtAFileThatIsThere(CheckReportCase):
    """G8, correctness 11. A finding's location names a file in the repository."""

    def there(self, *paths):
        return {"file_there": lambda path: path in paths}

    def test_a_location_naming_a_file_that_is_not_there_is_refused_by_name(self):
        self.refused(good_report(findings=[finding(location="app/gone.tsx:3")]), "finding 1 (A11Y-STRUCTURE)",
                     "app/gone.tsx", "not a file in the repository", **self.there("app/page.tsx"))

    def test_control_a_location_naming_a_file_that_is_there_passes(self):
        self.passes(good_report(), **self.there("app/page.tsx"))

    def test_a_location_that_leads_out_is_refused_without_asking(self):
        for location in ("../outside/page.tsx:3", "/etc/hosts:1"):
            with self.subTest(location=location):
                self.refused(good_report(findings=[finding(location=location)]), "finding 1 (A11Y-STRUCTURE)",
                             location.rsplit(":", 1)[0], "not a file in the repository",
                             file_there=lambda path: self.fail(f"{path} was looked for"))

    def test_a_location_of_the_wrong_shape_is_one_problem_not_two(self):
        self.refused(good_report(findings=[finding(location="app/page.tsx")]), "not shaped path:line", **self.there())


class TestTheLocationThroughTheCommand(UiGateCase):
    """G8, correctness 11, with real files (the reviewer's probe: a file that does not exist)."""

    def setUp(self):
        super().setUp()
        self.branch_changing("app/page.tsx")
        self.phase_8_otherwise_ready()

    def test_a_location_naming_no_file_or_a_folder_or_a_link_out_is_refused(self):
        import tempfile
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        with open(os.path.join(outside.name, "page.tsx"), "w", encoding="utf-8") as f:
            f.write("outside\n")
        os.symlink(outside.name, os.path.join(self.tmp.name, "linked"))
        for location in ("app/gone.tsx:3", "app:1", "linked/page.tsx:1"):
            with self.subTest(location=location):
                self.report(good_report(findings=[finding(location=location)]), located=False)
                self.assertRefused(self.close(), REPORT, "finding 1 (A11Y-STRUCTURE)", location.rsplit(":", 1)[0],
                                   "not a file in the repository")

    def test_control_a_location_in_the_changed_page_closes(self):
        self.report(good_report())
        self.assertCloses(self.close())


class TestRecordCountsWhatItWrote(RecordCase):
    """G8, correctness 11. The reviewer's probe: 49 other rows, two findings, and none of them fits."""

    def others(self, count):
        rows = [f"LOW|Code|lib/a.ts:{at}|review {at}|defer" for at in range(1, count + 1)]
        out = self.run_pipeline("table", "scratch", "8", "--columns", ",".join(RECORD_COLUMNS),
                                *[arg for row in rows for arg in ("--row", row)])
        self.assertEqual(out.returncode, 0, out.stderr)

    def findings(self, count):
        self.init_scratch()
        self.write("app/page.tsx")
        self.report(report_of([finding(location=f"app/page.tsx:{at}") for at in range(1, count + 1)]))

    def test_with_no_room_for_a_finding_the_message_says_none_was_recorded(self):
        self.findings(2)
        self.others(49)
        out = self.record()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        mine = [row for row in self.table()["rows"] if row[1].startswith(OWNED)]
        self.assertEqual([row[1] for row in mine], [OWNED + "left out"])
        self.assertIn("recorded 0 of 2 findings from 1 report", out.stdout)
        self.assertIn("2 were left out", out.stdout)
        self.assertNotIn("recorded 2 findings", out.stdout)
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.events.jsonl"), encoding="utf-8") as f:
            self.assertIn("recorded 0 of 2 findings", f.read())

    def test_with_room_for_one_of_three_it_says_one(self):
        self.findings(3)
        self.others(48)
        out = self.record()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(len([row for row in self.table()["rows"] if row[1] == OWNED + "Accessibility"]), 1)
        self.assertIn("recorded 1 of 3 findings from 1 report", out.stdout)
        self.assertIn("2 were left out", out.stdout)

    def test_control_when_they_all_fit_it_says_how_many(self):
        self.findings(3)
        self.others(47)
        out = self.record()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(len([row for row in self.table()["rows"] if row[1] == OWNED + "Accessibility"]), 3)
        self.assertIn("recorded 3 findings from 1 report", out.stdout)
        self.assertNotIn("left out", out.stdout)


class TestABlockThatDoesNotFitIsAPlacementFinding(CheckReportCase):
    """G8 ruling. When any block does not fit, Placement's coverage is `findings`, never `clear`."""

    def test_left_out_beside_placement_clear_is_refused_and_beside_findings_passes(self):
        report = holding(pipeline.UI_GATE_MOST_FINDINGS, placement=[placed(**LEFT_OUT)])
        self.refused(report, "Placement", "clear", "placement entry 1 (state and controls)", "does not fit")
        self.passes(cut_placement(report))

    def test_placement_not_reviewed_beside_a_block_that_does_not_fit_is_refused(self):
        report = covered(holding(pipeline.UI_GATE_MOST_FINDINGS, placement=[placed(**LEFT_OUT)]), "Placement", "not-reviewed")
        problems = pipeline.check_report(report, **context())
        self.assertTrue(any("does not fit" in line and "not-reviewed" in line for line in problems), problems)

    def test_control_a_block_that_fits_leaves_placement_clear(self):
        self.passes(holding(pipeline.UI_GATE_MOST_FINDINGS))

    def test_a_block_pointing_at_its_finding_has_placement_findings(self):
        share = finding(domain="Placement", rule="PLACE-SHARE", location="app/page.tsx:9", before="too much room")
        self.passes(report_of([finding(), share]))


if __name__ == "__main__":
    unittest.main()
