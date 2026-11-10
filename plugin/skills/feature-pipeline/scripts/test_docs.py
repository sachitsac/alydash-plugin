#!/usr/bin/env python3
"""ALYDAS-173, S7 (criterion 7, second half). The skill's docs against the
script they describe.

The flags and subcommands are read from `pipeline.py`'s own parser, never a
hand list: `main()` builds it and hands it to `parse_args`, which this file
intercepts, so a flag added to the script and not to the docs (or the other
way round) goes red here.

  python3 -m unittest test_docs -v

Python 3.9+, stdlib only.
"""
import argparse
import ast
import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import pipeline  # noqa: E402


class _Built(Exception):
    def __init__(self, parser):
        self.parser = parser


def build_parser():
    """The parser `pipeline.main()` builds, caught at `parse_args`."""
    def caught(self, *args, **kwargs):
        raise _Built(self)
    with mock.patch.object(argparse.ArgumentParser, "parse_args", caught):
        try:
            pipeline.main()
        except _Built as built:
            return built.parser
    raise AssertionError("pipeline.main() never reached parse_args")


def subcommands(parser):
    """Each subcommand's name and its parser."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    raise AssertionError("pipeline.py has no subcommands")


def options(parser):
    """A parser's long option strings, `--help` left out."""
    return {o for o in parser._option_string_actions if o.startswith("--") and o != "--help"}


def read(*parts):
    with open(os.path.join(SKILL, *parts), encoding="utf-8") as f:
        return f.read()


FENCE = re.compile(r"^```.*?^```", re.S | re.M)
FLAG = re.compile(r"(?<![\w-])--([a-z][a-z-]*)")


def inline_spans(text):
    """Backticked spans outside fenced blocks, a table's `\\|` read as `|`."""
    return [s.replace("\\|", "|") for s in re.findall(r"`([^`\n]+)`", FENCE.sub("", text))]


def pipe_lines(text):
    """Every script call in a fenced block (backslash continuations joined) or
    in a backticked span: the text after `"$PIPE"`."""
    joined = re.sub(r"\\\n\s*", " ", text)
    return [m.group(1) for m in re.finditer(r'"\$PIPE"\s+([^\n`]+)', joined)]


class TestDocsMatchTheParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parser = build_parser()
        cls.commands = subcommands(cls.parser)
        cls.top = options(cls.parser)
        cls.every = set(cls.top).union(*(options(p) for p in cls.commands.values()))

    def test_the_parser_is_the_one_the_skill_ships(self):
        """A floor, so a parser caught empty cannot pass everything below."""
        for name in ("init", "phase", "note", "step", "story", "table", "log", "check", "hold", "wait"):
            self.assertIn(name, self.commands)
        self.assertIn("--plain", options(self.commands["note"]))
        self.assertIn("--budget", options(self.commands["init"]))
        self.assertIn("--needs-you", options(self.commands["story"]))

    def check_call(self, where, call):
        """A call written `<subcommand> ...`: the subcommand exists and each
        flag is its own (or the top-level `--root`)."""
        words = call.split()
        name = words[0]
        self.assertIn(name, self.commands, f"{where}: `{call}` names no subcommand pipeline.py has")
        known = options(self.commands[name]) | self.top
        for flag in FLAG.findall(call):
            self.assertIn("--" + flag, known, f"{where}: `{call}`: {name} has no --{flag}")

    def test_every_flag_and_subcommand_named_in_skill_md_and_board_md_exists(self):
        checked = 0
        for parts in (("SKILL.md",), ("references", "board.md")):
            where = os.path.join(*parts)
            text = read(*parts)
            for span in inline_spans(text):
                # A span that opens with a word then `<slug>` or `...` is a call.
                if re.match(r"^[a-z]+ (<slug>|\.\.\.)", span):
                    self.check_call(where, span)
                    checked += 1
                    continue
                for flag in FLAG.findall(span):
                    self.assertIn("--" + flag, self.every, f"{where}: `{span}`: pipeline.py has no --{flag}")
                    checked += 1
            for call in pipe_lines(text):
                self.check_call(where, call)
                checked += 1
        self.assertGreater(checked, 25, "the docs name almost no calls; the scan is reading nothing")

    def test_every_script_call_in_the_references_exists(self):
        for name in sorted(os.listdir(os.path.join(SKILL, "references"))):
            if name.endswith(".md"):
                for call in pipe_lines(read("references", name)):
                    self.check_call(os.path.join("references", name), call)

    def documented_flags(self):
        spans = inline_spans(read("references", "board.md"))
        return spans, {"--" + f for s in spans for f in FLAG.findall(s)}

    def test_every_note_step_and_story_flag_is_documented_in_board_md(self):
        spans, documented = self.documented_flags()
        for name in ("note", "step", "story"):
            for flag in sorted(options(self.commands[name])):
                with self.subTest(command=name, flag=flag):
                    self.assertIn(flag, documented, f"board.md never names `{name} ... {flag}`")

    def test_every_clear_key_is_documented_in_board_md(self):
        spans, _ = self.documented_flags()
        named = {w for s in spans if "--clear" in s for w in re.split(r"[\s|,\[\]]+", s)}
        for name in ("note", "story"):
            choices = self.commands[name]._option_string_actions["--clear"].choices
            for key in choices:
                with self.subTest(command=name, key=key):
                    self.assertIn(key, named, f"board.md never names `{name} ... --clear {key}`")


    # Gate 6 (ALYDAS-173). The two scans above read `"$PIPE"` calls and
    # backticked spans, and the documented-flag case reads note, step and story.
    def doc_texts(self):
        names = [("SKILL.md",)] + [("references", n) for n in sorted(os.listdir(os.path.join(SKILL, "references")))
                                   if n.endswith(".md")]
        return {os.path.join(*parts): read(*parts) for parts in names}

    def test_every_pipe_in_the_docs_is_a_call_the_scan_reads(self):
        """A call written `$PIPE` without its quotes (or `${PIPE}`) was read by
        neither scan, so a flag it named that the script lacks passed. Every
        `PIPE` a doc names followed by a word is one `pipe_lines` reads."""
        for where, text in self.doc_texts().items():
            with self.subTest(file=where):
                joined = re.sub(r"\\\n\s*", " ", text)
                named = re.findall(r"\$\{?PIPE\}?\"?\s+[a-z]", joined)
                self.assertEqual(len(pipe_lines(text)), len(named),
                                 f"{where} names {len(named)} PIPE calls and the scan reads {len(pipe_lines(text))}")

    def test_every_flag_of_every_subcommand_is_named_in_the_docs(self):
        """The documented-flag case reads note, step and story, so a flag added
        to `phase` or `table` and to no doc passed. Every subcommand's every
        flag is named somewhere in SKILL.md or a reference, in a span or a call."""
        named = set()
        for text in self.doc_texts().values():
            for piece in inline_spans(text) + pipe_lines(text):
                named |= {"--" + f for f in FLAG.findall(piece)}
        self.assertGreater(len(named), 25, "the docs name almost no flags; the scan is reading nothing")
        for name, parser in sorted(self.commands.items()):
            for flag in sorted(options(parser)):
                with self.subTest(command=name, flag=flag):
                    self.assertIn(flag, named, f"no doc names `{name} ... {flag}`")

    # Gate 6 (ALYDAS-175). The case above reads flag names, 43, not the 45
    # (subcommand, flag) pairs: `--force` and `--clear` belong to two
    # subcommands each, so either one's line vouched for the other's.
    # `init --force` predates this and is named in no doc: a follow-up.
    UNDOCUMENTED_PAIRS = {("init", "--force")}

    def test_every_flag_is_named_on_a_line_of_its_own_subcommand(self):
        pieces = []
        for text in self.doc_texts().values():
            pieces += [p.strip() for p in inline_spans(text) + pipe_lines(text)]
        pairs = {(name, flag) for name, parser in self.commands.items() for flag in options(parser)}
        self.assertGreaterEqual(len(pairs), 46, "the parser is read")
        self.assertLessEqual(self.UNDOCUMENTED_PAIRS, pairs, "an excused pair the parser no longer has")
        for name, flag in sorted(pairs - self.UNDOCUMENTED_PAIRS):
            with self.subTest(command=name, flag=flag):
                self.assertTrue(any(re.match(rf"{name}\b", piece) and flag in {"--" + f for f in FLAG.findall(piece)}
                                    for piece in pieces), f"no doc names `{name} ... {flag}` on a {name} line")

    # ALYDAS-17, S6. `set` takes a few keys of its own that are no flag (`holds`,
    # and now `budget`), so the pair check above cannot see them. The keys are
    # read off `cmd_set`'s own comparisons; each one (or one of its aliases) is
    # named on a `set <slug> <key>` line.
    def test_every_key_set_parses_itself_is_named_on_a_set_line(self):
        pieces = []
        for text in self.doc_texts().values():
            pieces += [p.strip() for p in inline_spans(text) + pipe_lines(text)]
        groups = set_key_groups()
        self.assertIn(frozenset({"holds"}), groups, "the scan reads cmd_set")
        self.assertTrue(any("budget" in g for g in groups), "cmd_set parses a budget")
        for group in sorted(groups, key=sorted):
            with self.subTest(keys=sorted(group)):
                self.assertTrue(any(re.match(rf"set\s+<slug>\s+{re.escape(k)}\b", piece) for piece in pieces for k in group),
                                f"no doc names `set <slug> {sorted(group)[0]} ...`")


class TestDocsTeachTheUiGate(unittest.TestCase):
    """ALYDAS-223, S6. The UI gate is named where the phases are named, and
    `references/ui-gate.md` describes the report `check_report` reads. The
    actions, the fields and the columns are read off the script."""

    GATE = "The UI gate"

    @classmethod
    def setUpClass(cls):
        gate = subcommands(build_parser())["ui-gate"]
        cls.actions = list(next(a for a in gate._actions if a.dest == "action").choices)

    def pieces(self, *parts):
        text = read(*parts)
        return [p.strip() for p in inline_spans(text) + pipe_lines(text)]

    def test_the_actions_are_read_off_the_parser(self):
        """A floor, so the cases below cannot pass on an empty list."""
        self.assertEqual(sorted(self.actions), ["check", "record", "skip"])

    def test_every_ui_gate_action_is_named_on_a_ui_gate_line_in_both_places(self):
        for parts in (("SKILL.md",), ("references", "ui-gate.md")):
            pieces = self.pieces(*parts)
            for action in self.actions:
                with self.subTest(file=parts[-1], action=action):
                    self.assertTrue(any(re.match(rf"ui-gate {action} <slug>", piece) for piece in pieces),
                                    f"{parts[-1]} never names `ui-gate {action} <slug>`")

    def test_skill_md_s_section_lists_each_action_on_its_own_line_and_points_to_the_reference(self):
        body = section(read("SKILL.md"), self.GATE)
        self.assertIsNotNone(body, f"SKILL.md has no '## {self.GATE}' section")
        self.assertIn("`references/ui-gate.md`", body)
        for action in self.actions:
            with self.subTest(action=action):
                lines = [line for line in body.splitlines() if f"`ui-gate {action} <slug>" in line]
                self.assertEqual(len(lines), 1, f"`ui-gate {action} <slug>` is on {len(lines)} lines")
                others = [a for a in self.actions if a != action and f"ui-gate {a} " in lines[0]]
                self.assertEqual(others, [], "two actions share a line")

    def test_ui_gate_md_names_every_field_the_check_reads(self):
        text = read("references", "ui-gate.md")
        spans = set(inline_spans(text)) | {w for block in FENCE.findall(text) for w in re.findall(r'"([A-Za-z-]+)"', block)}
        fields = set(pipeline.UI_GATE_FIELDS) | {"answers", "ruling", "screen", "screenFile", "coverage", "inspected",
                                                 "result", "findings", "notVerified", "verdict",
                                                 "placement", "block", "serves", "fits", "finding", "note"}
        self.assertGreaterEqual(len(fields), 24)
        for name in sorted(fields | set(pipeline.UI_GATE_RESULTS) | set(pipeline.UI_GATE_SOURCES)
                           | set(pipeline.UI_GATE_DOMAINS) | set(pipeline.UI_GATE_SEVERITIES)):
            with self.subTest(name=name):
                self.assertIn(name, spans, f"ui-gate.md never names `{name}`")

    def examples(self):
        """The JSON examples in ui-gate.md, by what each one is."""
        import json
        held = [json.loads(block.split("\n", 1)[1].rsplit("```", 1)[0])
                for block in FENCE.findall(read("references", "ui-gate.md")) if block.startswith("```json")]
        return {"screen": next(h for h in held if "jobs" in h), "rulings": next(h for h in held if "rulings" in h),
                "report": next(h for h in held if "verdict" in h)}

    def checked(self, report, where="a.run-page-item"):
        examples = self.examples()
        rulings, wrong = pipeline.check_rulings(examples["rulings"])
        self.assertEqual(wrong, [])
        # Gate 6: a measurement is the four contexts, and every state the
        # example screen file lists is measured. The failure is the phone's, in
        # the first state.
        def measured(state, failures):
            contexts = {name: {"innerWidth": 390, "failures": [], "notes": []} for name in pipeline.UI_GATE_CONTEXTS}
            contexts["phone"]["failures"] = failures
            return {"screen": examples["screen"]["screen"], "state": state, "contexts": contexts}
        states = examples["screen"]["states"]
        self.assertGreater(len(states), 1)
        files = {f"run-page-{state}.json": measured(state, [{"rule": "A11Y-FOCUS", "where": where, "detail": "none"}]
                                                    if state == states[0] else []) for state in states}
        return pipeline.check_report(report, rulings=rulings, measurements=files,
                                     screen_file={report.get("screenFile"): examples["screen"]}.get,
                                     # ALYDAS-455: a file holding each example ruling's quote once.
                                     file_lines=lambda path: ["a line of code"] * 200
                                     + [f"// {r['quote']}" for r in examples["rulings"]["rulings"]])

    def test_the_example_report_in_ui_gate_md_passes_the_check_it_describes(self):
        """The doc's own report, against the doc's own screen file and rulings,
        and a measurement holding the failure its measured finding answers."""
        report = self.examples()["report"]
        self.assertEqual(self.checked(report), [])
        self.assertEqual({f["source"] for f in report["findings"]}, set(pipeline.UI_GATE_SOURCES))
        self.assertTrue(any(f["ruling"] for f in report["findings"]), "the example cites a ruling")
        # Control: the same report when the page failed somewhere it does not answer.
        self.assertEqual(len(self.checked(report, where="button.gone")), 2)

    def test_ui_gate_md_names_the_columns_record_writes_and_the_most_findings(self):
        text = " ".join(read("references", "ui-gate.md").split())
        self.assertIn("`" + ", ".join(pipeline.UI_GATE_RECORD_COLUMNS) + "`", text)
        self.assertRegex(text, rf"at most {pipeline.UI_GATE_MOST_FINDINGS} findings")

    def test_the_gate_is_named_where_the_phases_are_named(self):
        skill = read("SKILL.md")
        rows = {int(n): row for n, row in re.findall(r"^\| (\d) \|(.*)$", section(skill, "The arc") or "", re.M)}
        self.assertEqual(sorted(rows), list(range(10)), "the arc table is read")
        gates, phases = read("references", "gates.md"), read("references", "phases.md")
        places = {
            "SKILL.md arc, Phase 7": rows[7], "SKILL.md arc, Phase 8": rows[8],
            "gates.md Gate 3": re.search(r"^## Gate 3 .*?(?=^## )", gates, re.S | re.M).group(0),
            "gates.md Gate 4": re.search(r"^## Gate 4 .*", gates, re.S | re.M).group(0),
            "phases.md §1": section(phases, "§1 Brainstorm"), "phases.md §7": section(phases, "§7 Gate: goal achieved"),
            "phases.md §8": section(phases, "§8 Gate: review the diff"),
        }
        for place, body in places.items():
            with self.subTest(place=place):
                self.assertRegex(body or "", r"ui-gate\.md|`ui-gate/", f"{place} does not name the UI gate")
        for place in ("SKILL.md arc, Phase 7", "gates.md Gate 3", "phases.md §7"):
            with self.subTest(place=place, says="measure"):
                self.assertRegex(places[place], r"(?i)\bmeasur")
        for place in ("gates.md Gate 4", "phases.md §8"):
            with self.subTest(place=place, says="the close rule"):
                self.assertIn("Approve", places[place])

    def test_the_shared_table_is_described_where_phase_8_s_table_is(self):
        """Ruling B. A UI run's review table has the record's columns, and the
        review's own rows sit in it under a Domain outside the seven."""
        columns = "`" + ", ".join(pipeline.UI_GATE_RECORD_COLUMNS) + "`"
        gates = re.search(r"^## Gate 4 .*", read("references", "gates.md"), re.S | re.M).group(0)
        places = {"phases.md §8": section(read("references", "phases.md"), "§8 Gate: review the diff"),
                  "gates.md Gate 4": gates, "ui-gate.md": read("references", "ui-gate.md")}
        self.assertNotIn("code", {d.casefold() for d in pipeline.UI_GATE_DOMAINS})
        for place, body in places.items():
            with self.subTest(place=place):
                body = " ".join((body or "").split())
                self.assertIn(columns, body)
                self.assertIn("`Code`", body)

    # ---------- Gate 6 (D08, D10, D11): what ui-gate.md states is the script's ----------

    def gate_section(self, heading):
        body = section(read("references", "ui-gate.md"), heading)
        self.assertIsNotNone(body, f"ui-gate.md has no '## {heading}' section")
        return body

    def test_the_default_surfaces_and_what_is_left_out_are_the_script_s(self):
        """D08. The first paragraph of 'Which runs need it' lists the default
        patterns, then what is left out: both lists are the script's, whole
        and in its order."""
        first = paragraphs(self.gate_section("Which runs need it"))[0]
        self.assertIn("Test files", first)
        drawn, left_out = first.split("Test files", 1)
        globs = lambda words: [span for span in re.findall(r"`([^`]+)`", words) if "*" in span]
        self.assertEqual(globs(drawn), list(pipeline.DEFAULT_SURFACES))
        self.assertEqual(globs(left_out), list(pipeline.DEFAULT_IGNORE))
        self.assertGreaterEqual(len(pipeline.DEFAULT_SURFACES), 7)

    def test_the_cell_and_row_bounds_in_ui_gate_md_are_the_ledger_s(self):
        """D10."""
        body = " ".join(self.gate_section("Putting the findings on the board").split())
        self.assertEqual(re.findall(r"cut to ([0-9]+) characters", body), [str(pipeline.LEDGER_BOUNDS["cell"])])
        self.assertEqual(re.findall(r"table holds ([0-9]+) rows", body), [str(pipeline.LEDGER_BOUNDS["rows"])])

    def test_the_review_record_s_bounds_in_the_docs_are_the_script_s(self):
        """ALYDAS-174, Gate 6, M44 and M45. The 48 KB cap where SKILL.md,
        board.md and gates.md name it, and gates.md's table of the review's
        parts, read off `REVIEW_MOST_BYTES` and `LEDGER_BOUNDS`."""
        b = pipeline.LEDGER_BOUNDS
        for parts in (("SKILL.md",), ("references", "board.md"), ("references", "gates.md")):
            with self.subTest(doc="/".join(parts)):
                body = " ".join(read(*parts).split())
                self.assertEqual(re.findall(r"record (?:is )?over ([0-9]+) KB", body),
                                 [str(pipeline.REVIEW_MOST_BYTES // 1024)])
        rows = {}
        for line in read("references", "gates.md").splitlines():
            row = re.fullmatch(r"\| `(areas\[\]|findings\[\]|proofs\[\]|tests|verdict)` \| (.*) \|", line)
            if row:
                rows[row.group(1)] = row.group(2)
        self.assertEqual(sorted(rows), sorted(["areas[]", "findings[]", "proofs[]", "tests", "verdict"]))

        def num(text):
            return int(text.replace(",", ""))

        # Each part's count, "Up to N" at the end of its row.
        for part, bound in (("areas[]", "areas"), ("findings[]", "findings"), ("proofs[]", "proofs")):
            self.assertEqual([num(n) for n in re.findall(r"Up to ([0-9,]+)", rows[part])], [b[bound]], part)
        # Each field's bound, "`field` (N" or "`field` (..., N" as written.
        fields = (("areas[]", "name", "areaName"), ("areas[]", "about", "areaAbout"),
                  ("findings[]", "id", "findingId"), ("findings[]", "title", "findingTitle"),
                  ("findings[]", "by", "findingBy"), ("findings[]", "file", "filePath"),
                  ("tests", "command", "testCommand"), ("tests", "excerpt", "testExcerpt"))
        for part, field, bound in fields:
            found = re.search(rf"`{field}` \((?:[^)]*?, )?([0-9][0-9,]*)", rows[part])
            self.assertIsNotNone(found, f"{part} names no bound for {field}")
            self.assertEqual(num(found.group(1)), b[bound], f"{part} {field}")
        self.assertEqual([num(n) for n in re.findall(r"\(up to ([0-9,]+) path globs, ([0-9,]+) each", rows["areas[]"])[0]],
                         [b["patterns"], b["pattern"]])
        self.assertIn(f"each {b['findingText']:,};", rows["findings[]"])
        self.assertIn(f"1 to {b['criterion']}", rows["proofs[]"])
        self.assertIn(f"{b['proofText']:,} each", rows["proofs[]"])
        self.assertIn(f"({b['verdict']})", rows["verdict"])
        # Gate 8 ruling (Ask fc55dcae). The fold, where gates.md describes the
        # files `review record` adds: the 40 an area keeps, and its `more`.
        body = " ".join(read("references", "gates.md").split())
        self.assertEqual(re.findall(r"lists at most ([0-9]+) files", body), [str(pipeline.REVIEW_AREA_FILES)])
        self.assertEqual(pipeline.REVIEW_AREA_FILES, b["areaFiles"])
        self.assertIn("`more{files, added, removed, testAdded, testRemoved}`", body)
        self.assertIn("ties by path", body)

    def test_skill_md_names_init_s_default_holds(self):
        """ALYDAS-174, Gate 6, M43."""
        found = re.search(r"--hold [0-9,]+ .*# .*?default ([0-9,]+),", read("SKILL.md"))
        self.assertIsNotNone(found, "SKILL.md's init line names no default holds")
        self.assertEqual(found.group(1), ",".join(map(str, pipeline.INIT_HOLDS)))

    def test_the_record_s_mark_in_the_docs_is_the_script_s(self):
        """G6, 9. Where Phase 8's table is described, the docs give the mark a
        row of the record's carries; ui-gate.md also names its two rows that
        are not findings, and an example of one that is."""
        mark = "`" + pipeline.UI_GATE_OWNED + "`"
        self.assertEqual(mark, "`UI gate: `")
        gates = re.search(r"^## Gate 4 .*", read("references", "gates.md"), re.S | re.M).group(0)
        for place, body in (("ui-gate.md", self.gate_section("Putting the findings on the board")),
                            ("gates.md Gate 4", gates),
                            ("phases.md §8", section(read("references", "phases.md"), "§8 Gate: review the diff"))):
            with self.subTest(place=place):
                self.assertIn(mark, " ".join((body or "").split()))
        board = " ".join(self.gate_section("Putting the findings on the board").split())
        for row in (pipeline.UI_GATE_LEFT_OUT, pipeline.UI_GATE_RULED, pipeline.UI_GATE_OWNED + "Layout"):
            with self.subTest(row=row):
                self.assertIn("`" + row + "`", board)

    def test_hold_refuses_what_phase_done_refuses_in_the_docs_and_in_the_code(self):
        """D11. ui-gate.md says, in two places, that `hold <slug> 8` runs the
        gate's check and refuses on a Block; and in the script both `phase
        done` and `hold` reach the gate's check through the same refusal."""
        block = " ".join(self.gate_section("What Block means for the run").split())
        self.assertRegex(block, r"`phase <slug> 8 done` and `hold <slug> 8` both refuse")
        checking = " ".join(self.gate_section("Checking it").split())
        self.assertRegex(checking, r"Phase 8, and `hold <slug> 8`, run the same check")
        for command in ("cmd_phase", "cmd_hold"):
            with self.subTest(command=command):
                self.assertIn("refuse_unclosable", calls_of(command))
        self.assertIn("ui_gate_problems", reached_from("refuse_unclosable"))

    # ---------- Gate 8: what the review's fixes made the script hold ----------

    def test_the_base_a_run_is_judged_by_is_the_one_init_records(self):
        """Correctness 1. The doc says `init` records the commit the run
        started from as `base`, and that a run with nothing ahead of it is
        refused, in the script's own words; `init` and the changed files both
        reach the functions that do it."""
        body = " ".join(self.gate_section("Which runs need it").split())
        self.assertRegex(body, r"`init` records on the ledger, as `base`, the merge-base at that moment")
        self.assertRegex(body, r"as it is now, so a branch that was rebased lists only its own files")
        self.assertRegex(body, r"When that merge-base is HEAD itself, .* the script falls back on the commit the run "
                               r"started from")
        checking = " ".join(self.gate_section("Checking it").split())
        self.assertRegex(checking, r"has `fits` false, pointing at a finding or `leftOut`, the Placement coverage "
                                   r"row's result is `findings`")
        self.assertIn("nothing ahead of its base and no uncommitted or untracked file", body)
        self.assertIn("nothing ahead of its base and no uncommitted or untracked file", pipeline.NOTHING_AHEAD)
        self.assertIn("run_base", calls_of("cmd_init"))
        for asked in ("recorded_base", "default_base"):
            with self.subTest(asked=asked):
                self.assertIn(asked, calls_of("changed_files"))

    def test_what_the_gate_does_not_establish_is_said_plainly(self):
        """Correctness 6. Stated as limits, not as things the check holds."""
        body = " ".join(self.gate_section("What the gate does not establish").split())
        self.assertRegex(body, r"Nothing maps a changed file to a screen")
        self.assertRegex(body, r"lists, in its report's `notVerified`, any changed screen it did not review")
        self.assertRegex(body, r"A measurement file is not tied to a commit")
        self.assertRegex(body, r"measure again after a fix pass")
        self.assertRegex(self.brief(), r"any screen the diff changed that this review did not cover")
        self.assertNotRegex(" ".join(self.gate_section("Which runs need it").split()),
                            r"needs a report for each screen it touched")

    def test_the_cap_cuts_only_a_full_report_and_never_an_automatic_high_in_the_docs(self):
        """Correctness 5. The numbers and the rule named are the script's."""
        most = pipeline.UI_GATE_MOST_FINDINGS
        checking = " ".join(self.gate_section("Checking it").split())
        self.assertRegex(checking, rf"A `leftOut` entry is refused unless the report holds {most} findings")
        self.assertRegex(checking, r"so is one whose note names an automatic HIGH rule")
        self.assertRegex(checking, rf"refused unless the report holds {most} findings or the verdict is `Block`")
        shape = " ".join(self.gate_section("The report").split())
        self.assertRegex(shape, rf"only in a report that holds {most} findings")
        named = re.search(r"automatic HIGH rule \(`([A-Z-]+)` among them\)", shape)
        self.assertIsNotNone(named, "the report's shape names no automatic HIGH placement rule")
        self.assertIn(named.group(1), pipeline.UI_GATE_AUTOMATIC_HIGH)
        self.assertRegex(self.brief(), rf"only in a report that holds {most} findings, and never for {named.group(1)}")
        placing = [rule for rule in pipeline.UI_GATE_AUTOMATIC_HIGH if rule.startswith("PLACE-")]
        self.assertEqual(placing, [named.group(1)])
        rules = " ".join((section(read("references", "ui-rules.md"), "The cap") or "").split())
        self.assertRegex(rules, rf"leaves nothing out of a report that holds fewer than {most} findings")

    def test_what_the_gate_may_read_in_the_docs_is_what_the_script_reads(self):
        """Security 2 and 6, correctness 11. The ceiling is the script's, and
        every reader the doc names goes through the one check."""
        checking = " ".join(self.gate_section("Checking it").split())
        self.assertEqual(re.findall(r"not over ([0-9]+) MB", checking), [str(pipeline.UI_GATE_MOST_BYTES // (1024 * 1024))])
        self.assertEqual(pipeline.UI_GATE_MOST_BYTES % (1024 * 1024), 0)
        self.assertRegex(checking, r"the real path, with every symbolic link resolved, is inside the repository")
        self.assertRegex(checking, r"A link that leads out, a device, a pipe and a file over the ceiling are each "
                                   r"named and not read")
        self.assertRegex(checking, r"A `location` names a regular file inside the repository")
        for reader in ("ui_gate_reports", "ui_gate_context", "ui_gate_changed_rulings"):
            with self.subTest(reader=reader):
                self.assertIn("not_read", reached_from(reader))
                self.assertNotIn("_read_json", calls_of(reader), f"{reader} opens a file it has not looked at")

    def test_the_record_s_count_in_the_docs_is_shaped_as_the_script_says_it(self):
        """Correctness 11."""
        board = " ".join(self.gate_section("Putting the findings on the board").split())
        self.assertIn("`recorded 1 of 3 findings`", board)
        import inspect
        self.assertIn('f"recorded {wrote} of {findings} findings"', inspect.getsource(pipeline.ui_gate_record))

    # ---------- Gate 7: what the blind reviewers needed said ----------

    def brief(self):
        """The reviewer's brief: the fenced block under 'Reviewing, at Phase 8'."""
        blocks = FENCE.findall(self.gate_section("Reviewing, at Phase 8"))
        self.assertEqual(len(blocks), 1)
        return " ".join(blocks[0].split())

    RULED = (r"only when the code under review does what the ruling's \S*choice\S* says and the finding objects to that "
             r"choice")
    DEPARTS = r"finding that the code departs from a recorded choice is never ruled, and its \S*why\S* names the ruling"

    def test_a_ruling_the_code_contradicts_is_not_cited_in_the_brief_and_in_the_rule_book(self):
        rules = " ".join((section(read("references", "ui-rules.md"), "Rulings") or "").split())
        for place, body in (("ui-gate.md brief", self.brief()), ("ui-rules.md § Rulings", rules)):
            with self.subTest(place=place):
                self.assertRegex(body, self.RULED)
                self.assertRegex(body, self.DEPARTS)

    def test_the_cap_and_what_it_never_cuts_are_stated_in_both_docs(self):
        most = pipeline.UI_GATE_MOST_FINDINGS
        rules = " ".join(read("references", "ui-rules.md").split())
        for place, body in (("ui-gate.md brief", self.brief()), ("ui-rules.md", rules)):
            with self.subTest(place=place):
                self.assertRegex(body, r"never cut by the cap")
                self.assertRegex(body, r"lowest severity first")
                self.assertRegex(body, rf"first {most} by reach")
                self.assertRegex(body, r"verdict is \S*Block\S*")
                self.assertIn("left out by the cap", body)
        said = " ".join(self.gate_section("Checking it").split())
        self.assertIn("left out by the cap", said)

    def test_the_brief_and_the_shape_say_every_block_is_stated(self):
        """G7, criterion 8."""
        self.assertRegex(self.brief(), r"State every block, not only the ones that fail")
        report = self.examples()["report"]
        self.assertEqual([entry["block"] for entry in report["placement"]],
                         [block["name"] for block in self.examples()["screen"]["blocks"]])
        shape = " ".join(self.gate_section("The report").split())
        self.assertRegex(shape, r"`placement`: one entry for each block of the screen file")
        self.assertRegex(" ".join(self.gate_section("Checking it").split()), r"block .* not stated")
        for words in (r"Several blocks may point at one finding",
                      r"breaks two placement rules points at the one with the higher severity",
                      r"judged across every measured state and size",
                      r"whose only objection is ruled fits, and the note names the ruling",
                      r"serving no job fits unless it takes more of the first screen than a block serving job 1",
                      r"\Sfits\S false, \Sfinding\S null, \SleftOut\S true"):
            with self.subTest(words=words):
                self.assertRegex(self.brief(), words)
        self.assertIn("leftOut", inline_spans(self.gate_section("The report")))

    def test_the_brief_says_the_smaller_things(self):
        brief = self.brief()
        for words in (r"one cause breaks two rules.*higher severity.*names the other",
                      r"touch-size note raised as a finding is \S*source\S*, not \S*measured",
                      r"keyboard walk is done at desktop only",
                      r"WRITE-DESTRUCTIVE is about ending, deleting or overwriting.*ordinary Send is not it",
                      r"WRITE-ERROR on a network failure counts as the main path",
                      r"files in scope are the changed rendered surfaces and what they import that draws"):
            with self.subTest(words=words):
                self.assertRegex(brief, words)

    def test_measuring_says_what_has_to_be_installed(self):
        said = " ".join(self.gate_section("Measuring, at Phase 7").split())
        self.assertRegex(said, r"Playwright's browsers must be installed.*report check needs nothing but Python")

    def test_record_is_named_among_the_calls_that_beat_without_pushing(self):
        skill = " ".join(read("SKILL.md").split())
        self.assertRegex(skill, r"`step`, `story`, `log`, `ui-gate record` and a note with no file report without pushing")
        self.assertNotRegex(" ".join(self.gate_section("Putting the findings on the board").split()), r"with the next beat")

    def test_board_md_s_phase_8_check_names_the_gate(self):
        row = re.search(r"^\| 8 \|(.*)$", read("references", "board.md"), re.M)
        self.assertIsNotNone(row, "board.md's checks table has no row for Phase 8")
        self.assertIn("review doc", row.group(1))
        self.assertRegex(row.group(1), r"UI gate report")
        self.assertIn("Approve", row.group(1))
        self.assertIn("`ui-gate skip", row.group(1))

    def test_the_failure_modes_name_a_gate_passed_by_a_report_nobody_measured(self):
        body = section(read("SKILL.md"), "Failure modes") or ""
        self.assertTrue(any(re.search(r"report", b) and re.search(r"nobody measured", b) for b in paragraphs(body)), body)


def set_key_groups():
    """ALYDAS-17, S6. The keys `cmd_set` handles itself, one set per
    comparison: `a.key == "holds"` is {"holds"}, `a.key in ("budget",
    "budget_usd")` is both."""
    tree = ast.parse(read("scripts", "pipeline.py"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "cmd_set")
    groups = set()
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute) and node.left.attr == "key"):
            continue
        op, right = node.ops[0], node.comparators[0]
        if isinstance(op, ast.Eq) and isinstance(right, ast.Constant):
            groups.add(frozenset({right.value}))
        elif isinstance(op, ast.In) and isinstance(right, (ast.Tuple, ast.List, ast.Set)):
            groups.add(frozenset(e.value for e in right.elts if isinstance(e, ast.Constant)))
    return groups


def hold_exit_codes():
    return exit_codes("cmd_hold")


def wait_exit_codes():
    """ALYDAS-17, S6. Every exit code `wait` can end with, read the way hold's are."""
    return exit_codes("cmd_wait")


def exit_codes(command):
    """ALYDAS-175, S7. Every exit code `hold` can end with, read off
    `pipeline.py`'s own source rather than listed by hand: each `sys.exit` in
    `cmd_hold` and in every module function it calls, however deep, with a
    named constant resolved through the module. A helper that exits with its own
    parameter (`die(msg, code=1)`) counts what each call passes, else its
    default. 0, the answer, is a return and not an exit, so it is added."""
    tree = ast.parse(read("scripts", "pipeline.py"))
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}

    def is_exit(call):
        f = call.func
        return isinstance(f, ast.Attribute) and f.attr == "exit" and isinstance(f.value, ast.Name) and f.value.id == "sys"

    # Gate 6: `raise SystemExit(n)` exits as `sys.exit(n)` does, and the scan
    # read only the latter, so a new exit written the other way passed.
    raised = {id(n.exc) for n in ast.walk(tree) if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)}

    def is_raised_exit(call):
        return id(call) in raised and isinstance(call.func, ast.Name) and call.func.id == "SystemExit"

    def value(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            return node.value
        if isinstance(node, ast.Name) and isinstance(getattr(pipeline, node.id, None), int):
            return getattr(pipeline, node.id)
        return None

    def exit_parameter(fn):
        """(position, name, default) of the parameter `fn` exits with, or None."""
        args = fn.args.args
        defaults = dict(zip([a.arg for a in args][len(args) - len(fn.args.defaults):], fn.args.defaults))
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and is_exit(node) and node.args and isinstance(node.args[0], ast.Name):
                names = [a.arg for a in args]
                if node.args[0].id in names:
                    return names.index(node.args[0].id), node.args[0].id, defaults.get(node.args[0].id)
        return None

    codes, seen = {0}, set()

    def walk(name):
        if name in seen:
            return
        seen.add(name)
        fn = funcs[name]
        params = {a.arg for a in fn.args.args}
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            if is_exit(node) or is_raised_exit(node):
                arg = node.args[0] if node.args else ast.Constant(0)
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    arg = ast.Constant(1)  # SystemExit("words") exits 1
                if isinstance(arg, ast.Name) and arg.id in params:
                    continue  # counted at each call site, below
                code = value(arg)
                if code is None:
                    raise AssertionError(f"{name}: sys.exit({ast.unparse(arg)}) is not a code this scan can read")
                codes.add(code)
            elif isinstance(node.func, ast.Name) and node.func.id in funcs:
                param = exit_parameter(funcs[node.func.id])
                if param is not None:
                    at, key, default = param
                    given = next((k.value for k in node.keywords if k.arg == key), None)
                    given = given if given is not None else (node.args[at] if len(node.args) > at else default)
                    code = value(given)
                    if code is None:
                        raise AssertionError(f"{name}: {node.func.id}(...) exits with a code this scan cannot read")
                    codes.add(code)
                walk(node.func.id)

    walk(command)
    return codes


def calls_of(name):
    """The module functions `name` calls directly, read off pipeline.py's source."""
    tree = ast.parse(read("scripts", "pipeline.py"))
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    return {n.func.id for n in ast.walk(funcs[name])
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in funcs}


def reached_from(name):
    """Every module function `name` calls, however deep."""
    seen, queue = set(), [name]
    while queue:
        for called in calls_of(queue.pop()):
            if called not in seen:
                seen.add(called)
                queue.append(called)
    return seen


def section(text, heading):
    found = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    return found.group(1) if found else None


def paragraphs(text):
    """Paragraphs outside fenced blocks, each list item its own, so one bullet
    naming a code cannot vouch for the bullet beside it."""
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n|\n(?=\s*(?:[-*]|\d+\.) )", FENCE.sub("", text)) if p.strip()]


class TestDocsTeachTheHold(unittest.TestCase):
    """ALYDAS-175, S7. The skill tells the agent how a run waits for a person,
    and nothing in it still says to carry on without one when a desk can ask."""

    WAITS = "How a run waits"

    def test_the_scan_reads_the_codes_hold_exits_with(self):
        """A floor, so a scan that read nothing cannot pass the case below."""
        codes = hold_exit_codes()
        for named in ("STOP_EXIT", "NO_DESK_EXIT", "READ_REFUSED_EXIT"):
            self.assertIn(getattr(pipeline, named), codes, named)

    def test_skill_md_says_what_every_hold_exit_code_means(self):
        """Each code `hold` can exit with opens a bullet of SKILL.md's section
        on waiting, `- **<code>**`, and no bullet names a code it cannot."""
        body = section(read("SKILL.md"), self.WAITS)
        self.assertIsNotNone(body, f"SKILL.md has no '## {self.WAITS}' section")
        documented = {int(c) for c in re.findall(r"^- \*\*(\d+)\*\*", body, re.M)}
        self.assertEqual(documented, hold_exit_codes())

    def test_each_exit_bullet_opens_with_what_its_code_means(self):
        """Gate 6: the set of codes matched while two bullets swapped meanings.
        Each named code's bullet opens with its own words."""
        body = section(read("SKILL.md"), self.WAITS) or ""
        bullets = {int(c): " ".join(words.split()) for c, words in re.findall(r"^- \*\*(\d+)\*\*,([^\n]*)", body, re.M)}
        for code, opening in ((0, "answered"), (pipeline.STOP_EXIT, "stopped"), (pipeline.NO_DESK_EXIT, "no desk"),
                              (pipeline.READ_REFUSED_EXIT, "the desk refused to read the answer back")):
            with self.subTest(code=code):
                self.assertTrue(bullets.get(code, "").startswith(opening), f"- **{code}** opens {bullets.get(code)!r}")

    def test_skill_md_has_the_agent_hold_in_the_background(self):
        body = section(read("SKILL.md"), self.WAITS) or ""
        self.assertIn("run_in_background", body)
        self.assertRegex(" ".join(body.split()), r'"\$PIPE" hold <slug> <n>')

    def test_no_doc_carries_on_provisionally_unless_there_is_no_desk(self):
        """A provisional pick is only for a run nobody can answer on a desk,
        which is `hold` exiting NO_DESK_EXIT. Any paragraph in the skill that
        says provisional, or tells the run to go on on its own pick or
        recommendation, names that exit."""
        no_desk = pipeline.NO_DESK_EXIT
        names_it = re.compile(rf"(exits? {no_desk}\b|\*\*{no_desk}\*\*)")
        # Gate 6: "go ahead", "keep going" and "move on" were not read.
        carries_on = re.compile(r"provisional|\b(continue|carry on|go on|go ahead|keep going|move on|proceed)s?\b"
                                r"[^.]*\b(recommend\w*|pick|chose\w*|own choice)\b", re.I)
        read_any = False
        for where, text in TestDocsMatchTheParser.doc_texts(self).items():
            for para in paragraphs(text):
                if carries_on.search(para):
                    read_any = True
                    with self.subTest(file=where, paragraph=para[:80]):
                        self.assertRegex(para, names_it, f"{where} carries on without the person, with a desk set")
        self.assertTrue(read_any, "no doc mentions a provisional pick; the scan is reading nothing")

    def test_prototype_md_holds_phase_2(self):
        """The run holds after its verdict: prototype.md names the call."""
        calls = pipe_lines(read("references", "prototype.md"))
        self.assertTrue(any(re.match(r"hold <slug> 2\b", c) for c in calls), calls)


def own_exit_codes(name):
    """ALYDAS-206, S4. The codes `name` itself passes to `sys.exit`, not those
    of what it calls, each resolved through the module."""
    tree = ast.parse(read("scripts", "pipeline.py"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    codes = set()
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "exit"
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "sys" and node.args):
            arg = node.args[0]
            codes.add(arg.value if isinstance(arg, ast.Constant) else getattr(pipeline, arg.id))
    return codes


def printed_strings(name):
    """Every literal piece of text in `name`'s body, f-strings split at their fields."""
    tree = ast.parse(read("scripts", "pipeline.py"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    return [n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def sentences(text):
    """Sentences outside fenced blocks, one paragraph at a time, so a sentence
    about pushing cannot vouch for a sentence about hold beside it."""
    return [s for para in paragraphs(text) for s in re.split(r"(?<=[.!?])\s+", para) if s]


class TestDocsTeachPushBeforeHold(unittest.TestCase):
    """ALYDAS-206, S4 (criterion 5). A held phase's recorded files must be on
    origin before hold asks about them: hold pushes, then refuses (raising
    nothing) when the desk still could not open one. The docs say so where the
    agent reads about holding, and the words are held to the script: the exit
    code is `refuse_unopenable`'s own, the flag is hold's parser's, and the
    reason words are ones hold prints."""

    NOT_PUSHED = "not pushed"

    @classmethod
    def setUpClass(cls):
        codes = own_exit_codes("refuse_unopenable")
        cls.code = next(iter(codes)) if len(codes) == 1 else None
        cls.hold_flags = options(subcommands(build_parser())["hold"])

    def test_the_words_are_the_script_s(self):
        """Floors, so the cases below cannot pass on words hold never uses."""
        self.assertEqual(own_exit_codes("refuse_unopenable"), {self.code}, "refuse_unopenable exits with one code")
        self.assertIn(self.code, hold_exit_codes())
        self.assertIn("--force", self.hold_flags)
        self.assertTrue(any(s.startswith(self.NOT_PUSHED) for s in printed_strings("why_not_on_github")),
                        f"hold no longer prints {self.NOT_PUSHED!r} as a reason")

    def states_the_rule(self, sentence):
        """One sentence naming hold, its refusal, and the files being on origin."""
        return (re.search(r"\b[Hh]old\b", sentence)
                and re.search(rf"\b(refuses?|exits? {self.code}|not held)\b", sentence)
                and re.search(r"\b(on origin|pushed)\b", sentence))

    def test_skill_md_s_refusal_bullet_names_files_not_on_origin_and_the_way_past(self):
        body = section(read("SKILL.md"), TestDocsTeachTheHold.WAITS) or ""
        bullet = next((" ".join(p.split()) for p in paragraphs(body) if p.startswith(f"- **{self.code}**")), None)
        self.assertIsNotNone(bullet, f"SKILL.md's hold exits have no - **{self.code}** bullet")
        self.assertIn(self.NOT_PUSHED, bullet)
        self.assertRegex(bullet, r"\borigin\b")
        self.assertRegex(bullet, r"hold <slug> <n> --force\b")

    def test_skill_md_s_section_states_the_rule(self):
        body = section(read("SKILL.md"), TestDocsTeachTheHold.WAITS) or ""
        prose = [s for s in sentences(body) if not s.startswith("- **")]
        self.assertTrue(any(self.states_the_rule(s) for s in prose),
                        "no sentence of § How a run waits, outside the exit bullets, says hold refuses files not on origin")

    def test_prototype_md_states_the_rule(self):
        self.assertTrue(any(self.states_the_rule(s) for s in sentences(read("references", "prototype.md"))),
                        "no sentence of prototype.md says hold refuses a file not on origin")

    def test_phases_md_s_prototype_section_states_the_rule(self):
        body = section(read("references", "phases.md"), "§2 Prototype")
        self.assertIsNotNone(body, "phases.md has no '## §2 Prototype'")
        self.assertTrue(any(self.states_the_rule(s) for s in sentences(body)),
                        "no sentence of phases.md §2 says hold refuses a file not on origin")

    def test_the_exit_code_each_rule_sentence_names_is_the_script_s(self):
        """Gate 6. The rule's prose names the exit code in all three places;
        each number it names is `refuse_unopenable`'s own, so the prose cannot
        keep saying 2 after the code and the exit bullet have moved."""
        places = {
            "SKILL.md": [s for s in sentences(section(read("SKILL.md"), TestDocsTeachTheHold.WAITS) or "")
                         if not s.startswith("- **")],
            "prototype.md": sentences(read("references", "prototype.md")),
            "phases.md §2": sentences(section(read("references", "phases.md"), "§2 Prototype") or ""),
        }
        for place, said in places.items():
            with self.subTest(place):
                named = [int(c) for s in said if self.states_the_rule(s)
                         for c in re.findall(r"\bexits? (\d)\b", s)]
                self.assertTrue(named, f"no rule sentence in {place} names the exit code")
                self.assertEqual(set(named), {self.code}, f"{place} names exit {named}, the script exits {self.code}")


class TestCaseIsSaidAsCapitalLetters(unittest.TestCase):
    """ALYDAS-220, S2 (c2, the wording Sachit approved at Phase 2). A path that
    differs from a committed file only in capitals is "in other capital
    letters", and its fix says "the capital letters the commit has". The old
    words appear nowhere: not in the script's text, not in any doc, however a
    line wraps them."""

    OLD = ("in another case", "the case the commit has")
    SHA = "abc1234" + "0" * 33

    @staticmethod
    def flat(text):
        return " ".join(text.split()).lower()

    def test_hold_says_other_capital_letters(self):
        """Floors, built as hold builds them, so the sweep below cannot pass on words hold never prints."""
        one = pipeline.explain_miss("Docs/x.html", "Docs/x.html", self.SHA, ["docs/x.html"])
        two = pipeline.explain_miss("X.html", "X.html", self.SHA, ["x.html", "x.HTML"])
        self.assertEqual(one, "in other capital letters, which GitHub does not find; docs/x.html is in abc1234")
        self.assertEqual(pipeline.fixes_for([("X.html", two)]), ["record it in the capital letters the commit has"])

    def test_the_old_words_are_nowhere(self):
        texts = {"scripts/pipeline.py": read("scripts", "pipeline.py"), **TestDocsMatchTheParser.doc_texts(self)}
        self.assertIn(os.path.join("references", "prototype.md"), texts)
        for place, text in texts.items():
            for old in self.OLD:
                with self.subTest(place=place, old=old):
                    self.assertNotIn(old, self.flat(text))

    def test_skill_md_names_other_capital_letters(self):
        self.assertIn("other capital letters", self.flat(read("SKILL.md")))


class TestDocsTeachTheBudget(unittest.TestCase):
    """ALYDAS-17, S6. The skill tells the agent what a budget stop is, what
    exit 6 asks of it, and what `wait` exits with."""

    OVER = "When a run is over its budget"
    WRITERS = ("cmd_phase", "cmd_note", "cmd_step", "cmd_story", "cmd_log")

    def test_the_scan_reads_the_codes_wait_exits_with_and_exit_6_is_the_writers(self):
        """A floor, so a scan that read nothing cannot pass the cases below.
        Exit 6 is a writing command's; `hold` and `wait` wait through a budget stop."""
        codes = wait_exit_codes()
        for named in ("STOP_EXIT", "NO_DESK_EXIT"):
            self.assertIn(getattr(pipeline, named), codes, named)
        self.assertNotIn(pipeline.BUDGET_EXIT, codes, "wait waits through a budget stop")
        self.assertNotIn(pipeline.BUDGET_EXIT, hold_exit_codes(), "hold waits through a budget stop (D9)")
        for writer in self.WRITERS:
            with self.subTest(command=writer):
                self.assertIn(pipeline.BUDGET_EXIT, exit_codes(writer))

    def test_skill_md_says_what_every_wait_exit_code_means(self):
        body = section(read("SKILL.md"), self.OVER)
        self.assertIsNotNone(body, f"SKILL.md has no '## {self.OVER}' section")
        documented = {int(c) for c in re.findall(r"^- \*\*(\d+)\*\*", body, re.M)}
        self.assertEqual(documented, wait_exit_codes())

    def test_each_wait_exit_bullet_opens_with_what_its_code_means(self):
        body = section(read("SKILL.md"), self.OVER) or ""
        bullets = {int(c): " ".join(words.split()) for c, words in re.findall(r"^- \*\*(\d+)\*\*,([^\n]*)", body, re.M)}
        for code, opening in ((0, "extended"), (1, "no run"), (pipeline.STOP_EXIT, "stopped"),
                              (pipeline.NO_DESK_EXIT, "no desk")):
            with self.subTest(code=code):
                self.assertTrue(bullets.get(code, "").startswith(opening), f"- **{code}** opens {bullets.get(code)!r}")

    def test_the_section_says_exit_6_means_wait_in_the_background_and_start_nothing(self):
        body = " ".join((section(read("SKILL.md"), self.OVER) or "").split())
        self.assertRegex(body, rf"\*\*exit code {pipeline.BUDGET_EXIT}\*\*")
        self.assertIn("run_in_background", body)
        self.assertRegex(body, r'"\$PIPE" wait <slug>')
        self.assertRegex(body, r"(?i)do not start the next phase")
        self.assertIn("over budget", body)

    def test_the_desk_can_answer_back_paragraph_names_the_budget_stop(self):
        para = next((p for p in paragraphs(read("SKILL.md")) if p.startswith("The desk can answer back")), None)
        self.assertIsNotNone(para)
        self.assertRegex(para, rf"\*\*exit code {pipeline.BUDGET_EXIT}\*\*")
        self.assertIn("wait", para)

    def test_the_ledger_block_names_init_budget_set_budget_and_wait(self):
        calls = pipe_lines(read("SKILL.md"))
        self.assertTrue(any(re.match(r"init <slug> .*--budget\b", c) for c in calls), calls)
        self.assertTrue(any(re.match(r"set\s+<slug> budget\b", c) for c in calls), calls)
        self.assertTrue(any(re.match(r"wait\s+<slug>", c) for c in calls), calls)

    def test_board_md_says_wait_beats_on_hold_s_clock(self):
        found = re.search(r"^### Which calls reach the desk\n(.*?)(?=^##)", read("references", "board.md"), re.S | re.M)
        self.assertIsNotNone(found)
        self.assertRegex(found.group(1), rf"(?m)^- `wait`[^\n]*every {pipeline.HOLD_BEAT_S:g} s")

    def test_the_section_says_wait_beats_on_hold_s_clock_and_hold_waits_through(self):
        """G6, D10 and D11. SKILL.md's own clock for `wait`, and D9: `hold`
        waits through a budget stop rather than ending on it."""
        body = " ".join((section(read("SKILL.md"), self.OVER) or "").split())
        self.assertIn(f"It beats every {pipeline.HOLD_BEAT_S:g} s", body)
        self.assertRegex(body, r"`hold` waits through a budget stop")
        self.assertNotIn(pipeline.BUDGET_EXIT, hold_exit_codes())


class TestWritingForTheReader(unittest.TestCase):
    """The rules, each pinned by the phrase that states it, inside the section."""

    RULES = {
        "write for a newcomer": r"someone who has not read the code",
        "no bare codes": r"no bare codes",
        "a code carries its plain name": r"plain name beside it",
        "every code goes in terms": r"every code .{0,60}`terms`",
        "numbers say what they mean": r"numbers say what they mean",
        "a decision names the alternatives": r"what else was on the table",
        "a step is ticked when it finishes": r"tick a step when it finishes",
        "shorthand lives in Technical": r"technical.{0,200}shorthand",
    }

    def section(self):
        text = read("references", "board.md")
        found = re.search(r"^## Writing for the reader\n(.*?)(?=^## |\Z)", text, re.S | re.M)
        self.assertIsNotNone(found, "board.md has no '## Writing for the reader' section")
        return " ".join(found.group(1).split())

    def test_each_rule_is_stated(self):
        body = self.section()
        for rule, phrase in self.RULES.items():
            with self.subTest(rule=rule):
                self.assertRegex(body, re.compile(phrase, re.I))

    def test_a_summary_no_longer_invites_numbers(self):
        for parts in (("SKILL.md",), ("references", "board.md")):
            with self.subTest(file=os.path.join(*parts)):
                self.assertNotIn("numbers allowed", read(*parts))


    HEADLINES = ("Write for someone who has not read the code.", "No bare codes in the story.",
                 "Numbers say what they mean.", "Every decision names what else was on the table.",
                 "Tick a step when it finishes")

    def test_each_rule_opens_with_its_own_headline(self):
        """Each rule is pinned by a phrase anywhere in the section, so a rule
        turned round ("Bare codes are fine in the story", with "no bare codes"
        kept in a clause after it) passed. Each numbered rule opens with its
        headline in bold, word for word."""
        text = read("references", "board.md")
        found = re.search(r"^## Writing for the reader\n(.*?)(?=^## |\Z)", text, re.S | re.M)
        self.assertIsNotNone(found)
        opened = re.findall(r"^\d+\. \*\*(.+?)\*\*", found.group(1), re.M)
        self.assertEqual(opened, list(self.HEADLINES))


class TestTheDocsNameTheNewRefusals(unittest.TestCase):
    """ALYDAS-218, S7. SKILL.md's refusal list names the link and submodule
    cases S3 added, and board.md says `note` refuses a byte that is not UTF-8
    (S5), so a reader meets each before hold or note prints it."""

    flat = staticmethod(TestCaseIsSaidAsCapitalLetters.flat)

    def test_skill_md_names_a_link_and_a_submodule(self):
        text = self.flat(read("SKILL.md"))
        for words in ("a link", "a submodule"):
            with self.subTest(words=words):
                self.assertIn(words, text)
        # Gate 6. In the refusal list, and the link named as one GitHub does
        # not follow: a link to a file opens, so "a link" alone says too much.
        self.assertIn("a directory, a link github does not follow, a submodule,", text)

    def test_board_md_says_note_refuses_a_byte_that_is_not_utf8(self):
        self.assertIn("not utf-8", self.flat(read("references", "board.md")))
        # Gate 6. Its row says the note is refused and nothing written, not only that the words appear.
        row = [line for line in read("references", "board.md").splitlines() if "not UTF-8 |" in line]
        self.assertEqual(len(row), 1, row)
        self.assertIn("is refused before anything is written, exit 1", row[0])


class TestTheDocsSayHowStepsAreKept(unittest.TestCase):
    """ALYDAS-225, K4. What K1 to K3 made the script do, said where an agent
    reads it: `SKILL.md`'s ledger block and its desk paragraph, and
    `references/board.md`'s command table, fields table, checks table and
    rule 5. Each sentence is pinned inside its own line or section, and to the
    script where the script can say it."""

    flat = staticmethod(lambda text: " ".join(text.split()))

    def row(self, start):
        """board.md's one table row that opens with this cell."""
        rows = [line for line in read("references", "board.md").splitlines() if line.startswith(start)]
        self.assertEqual(len(rows), 1, f"{len(rows)} rows open with {start!r}")
        return rows[0]

    def ledger_line(self, start):
        """SKILL.md's one ledger-block line that opens with this command."""
        lines = [line for line in read("SKILL.md").splitlines() if line.startswith('python3 "$PIPE" ' + start)]
        self.assertEqual(len(lines), 1, f"{len(lines)} lines open with {start!r}")
        return lines[0]

    def refusal(self):
        """What the script prints for a step still to-do, with the run's own
        slug, phase and step written as the docs write them."""
        run = {"root": os.path.join(SKILL, "no-such-root"), "slug": "<slug>",
               "phases": [{"n": 9, "plain": "Said.", "steps": [{"text": "One", "state": "done", "sub": ""},
                                                             {"text": "Two", "state": "dropped", "sub": "why"},
                                                             {"text": "Three", "state": "doing", "sub": ""},
                                                             {"text": "Four", "state": "todo", "sub": ""}]}]}
        named = [p for p in pipeline.run_checks(run, 9) if "still to do" in p]
        self.assertEqual(len(named), 1, named)
        self.assertIn("step 4 is still to do", named[0])
        return named[0].replace("<slug> 9 4", "<slug> <n> <i>")

    def test_the_states_in_the_docs_are_the_script_s(self):
        """The step row lists the states the parser takes, in its order, and
        the fields row names the same four."""
        step = subcommands(build_parser())["step"]
        choices = next(a.choices for a in step._actions if a.dest == "state")
        self.assertEqual(tuple(choices), pipeline.STEP_STATES)
        self.assertEqual(pipeline.STEP_STATES[-1], "dropped")
        row = self.row("| `step <slug> <n> <number or exact text>")
        self.assertIn("[" + "\\|".join(pipeline.STEP_STATES) + "]", row)
        self.assertIn("each todo, doing, done or dropped", self.row("| `steps` |"))

    def test_board_md_says_start_marks_the_first_step_doing_and_a_tick_moves_on(self):
        self.assertIn("marks the first todo step doing when none is", self.row("| `phase <slug> <n> start` |"))
        row = self.row("| `step <slug> <n> <number or exact text>")
        # Gate 8, 9: after any change that leaves none doing, not only after done or dropped.
        self.assertIn("on a running phase, any change that leaves no step doing moves the next todo step to doing", row)
        self.assertNotIn("done or dropped moves the next todo step", row)

    def test_board_md_says_an_added_step_is_the_one_doing_when_none_is(self):
        """Gate 6. `--add` on a running phase with no step doing adds the step
        doing, not todo: the rule that keeps one doing covers every `step` call."""
        row = self.row('| `step <slug> <n> "text" --add` |')
        self.assertIn("| adds a step of the agent's own, todo; on a running phase with no step doing, it is added doing |", row)

    def test_board_md_says_dropped_needs_its_reason(self):
        row = self.row("| `step <slug> <n> <number or exact text>")
        self.assertIn('`dropped` is a step that was not done and needs `--sub "<why>"`', row)
        self.assertIn("refused without one, exit 1, nothing written", row)

    def test_the_docs_say_a_phase_with_a_step_still_todo_does_not_close(self):
        """The command row, the checks table and SKILL.md's ledger block, each
        with the two ways out as the script itself prints them."""
        printed = self.refusal()
        fixes = ("step <slug> <n> <i> done", 'step <slug> <n> <i> dropped --sub "<why>"')
        for fix in fixes:
            self.assertIn(fix, printed, "the script no longer prints this fix")
        self.assertIn("refused while a step is still todo", self.row("| `phase <slug> <n> done` |"))
        checks = section(read("references", "board.md"), "Checks that gate `phase done`")
        rows = [line for line in checks.splitlines() if line.startswith("| every |")]
        self.assertEqual(len(rows), 2, rows)
        self.assertIn("no step is still todo", rows[1])
        for fix in fixes:
            self.assertIn("`" + fix + "`", rows[1])
        self.assertIn("refused while a step is still todo: tick it or drop it", self.ledger_line("phase <slug> <n> done"))

    def test_the_docs_say_hold_force_does_not_go_past_a_step_still_todo(self):
        """`phase done --force` goes past it and `hold --force` does not: the
        hold row and the paragraph under the checks table both say so, and in
        the script the hold's refusal offers no force."""
        self.assertIn("`--force` does not go past a step still todo", self.row("| `hold <slug> <n> "))
        checks = self.flat(section(read("references", "board.md"), "Checks that gate `phase done`"))
        self.assertIn("`hold --force` goes past none of them", checks)
        self.assertIn("a step still todo included", checks)
        source = read("scripts", "pipeline.py")
        self.assertIn("refuse_unclosable(path, run, a.n, held=True)", source)

    def test_skill_md_s_ledger_block_shows_the_start_the_drop_and_the_move(self):
        self.assertIn("the first step marked doing", self.ledger_line("phase <slug> <n> start"))
        self.assertIn("the next todo step moves to doing", self.ledger_line("step  <slug> <n> 1 done"))
        dropped = self.ledger_line("step  <slug> <n> 2 dropped")
        self.assertIn('dropped --sub "<why it was not done>"', dropped)
        self.assertIn("the reason is required", dropped)

    def test_rule_5_says_to_drop_a_step_that_was_not_done(self):
        text = read("references", "board.md")
        found = re.search(r"^## Writing for the reader\n(.*?)(?=^## |\Z)", text, re.S | re.M)
        rule = re.search(r"^5\. \*\*Tick a step when it finishes\*\*(.*?)(?=^\S|\Z)", found.group(1), re.S | re.M)
        self.assertIsNotNone(rule, "rule 5 no longer opens with its headline")
        body = self.flat(rule.group(1))
        self.assertIn("A step that was not done is dropped with its reason, never ticked", body)
        self.assertIn("a phase does not close while one is still todo", body)

    def test_each_sentence_about_steps_is_there_whole(self):
        """Gate 6. The tests above pin a few words of each sentence, so a "not"
        put before them, or the half they do not name deleted or turned round,
        went through (20 of 50 such changes did). Here each sentence is pinned
        whole, from the mark that opens it to the mark that closes it."""
        board = read("references", "board.md")
        cells = {
            "| `phase <slug> <n> start` |": (
                ", all todo; then marks the first todo step doing when none is, so a running phase always shows a step "
                "in hand (a restart does the same, and leaves a phase with nothing todo alone) |",),
            "| `phase <slug> <n> done` |": (
                "prints the boundary block; refused while a step is still todo, naming each one (tick it or drop it); "
                "refused on a phase the run holds",),
            "| `hold <slug> <n> ": (
                "a send-back reopens it; `--force` does not go past a step still todo, or any other check; `SKILL.md`",),
            "| `step <slug> <n> <number or exact text>": (
                "marks the one before it done; on a running phase, any change that leaves no step doing moves the next "
                "todo step to doing, not only done or dropped. "
                '`dropped` is a step that was not done and needs `--sub "<why>"`: it is refused without one, exit 1, '
                "nothing written |",),
            "| `steps` |": (
                "| the phase's to-do list, each todo, doing, done or dropped, with a line under it (a dropped step's "
                "line is its reason) |",),
        }
        for start, wholes in cells.items():
            for whole in wholes:
                self.assertIn(whole, self.row(start))
        checks = section(board, "Checks that gate `phase done`")
        step_row = [line for line in checks.splitlines() if line.startswith("| every | no step is still todo")]
        self.assertEqual(step_row, [
            "| every | no step is still todo: each one is named with its number and text, to tick with "
            '`step <slug> <n> <i> done` or drop with `step <slug> <n> <i> dropped --sub "<why>"`. '
            "A step being done is not held to it: the close finishes it |"])
        under = self.flat(checks)
        for whole in (
            "the phase was closed by hand. `hold` runs the same checks before it asks, and `hold --force` goes past "
            "none of them, a step still todo included: a held phase gets past one only by ticking or dropping it. "
            "`failed`, `blocked` and `skipped` run no check, so a phase that did not finish its steps shows them. "
            "The checks find missing evidence.",
        ):
            self.assertIn(whole, under)
        rules = self.flat(re.search(r"^## Writing for the reader\n(.*?)(?=^## |\Z)", board, re.S | re.M).group(1))
        self.assertIn(
            "5. **Tick a step when it finishes**, not at the end of the phase: the desk shows the phase moving only if "
            "the steps do. The start marks the first step doing, and each tick moves the next one to doing, so the page "
            "always says what is in hand. A step that was not done is dropped with its reason, never ticked, and a "
            "phase does not close while one is still todo. Technical (summary,", rules)
        # SKILL.md: each ledger line's comment whole, to the end of its line.
        for start, comment in (
            ("phase <slug> <n> start", "# entering: purpose stamped, usual steps seeded, the first step marked doing"),
            ("step  <slug> <n> 1 done", "# as it finishes, not at the end; the next todo step moves to doing"),
            ("step  <slug> <n> 2 dropped", "# a step that will not be done; the reason is required"),
            ("phase <slug> <n> done", "# done (and hold) is refused while a step is still todo: tick it or drop it"),
        ):
            line = self.ledger_line(start)
            self.assertTrue(line.endswith(comment), line)
            self.assertEqual(line.count("#"), 1, line)
        self.assertIn(
            "A desk from before ALYDAS-225, October 2026, refuses a beat that carries a dropped step, and every beat "
            "after it while the step stays dropped, so drop a step only on a desk that already knows the state. "
            "At **Gate 7**",
            self.flat(read("SKILL.md")))
        # Gate 8, 9. What a close refuses, and what each `--force` goes past, said once.
        skill = self.flat(read("SKILL.md"))
        self.assertIn(
            "`phase ... done` runs the artifact checks for that phase (`check` shows them) and refuses to close on a "
            "missing plain line, a step still todo, a missing artifact, a task report with no red output, a gate doc "
            "with no pasted failure, a NOT CONFIRMED verdict that was not escalated. Fix the artifact. "
            '`--force "<reason>"` is for a decision the user made, and it prints on the board in amber. On '
            "`phase ... done` it goes past every one of these, a step still todo included; on `hold` it goes past "
            "none of them, only the check that the desk can open each recorded file. Every mutating call", skill)
        self.assertEqual(skill.count("it goes past none of them"), 1)
        self.assertEqual(skill.count("a step still todo included"), 1)
        # Gate 8, 9. Resuming a run an older script started.
        resume = [p for p in paragraphs(read("SKILL.md")) if p.startswith("- Resume = ")]
        self.assertEqual(len(resume), 1, resume)
        self.assertTrue(resume[0].endswith(
            "Do not re-read the run from the top. A run started under an older script and resumed under this one is "
            "refused at its first close until the open phase's steps are ticked or dropped."), resume[0])
        self.assertEqual(skill.count("A run started under an older script"), 1)

    def test_phases_md_says_phase_5_s_steps_are_the_wave_s(self):
        """Gate 8, 4. Phase 5's usual steps are the wave's, not one slice's:
        §5 says so, whole, once, and names the way to add a step for a slice."""
        dispatch = self.flat(section(read("references", "phases.md"), "§5 Dispatch"))
        whole = ("The phase's usual steps are the wave's, not one slice's: tick each as the wave goes, and add a step "
                 "for each slice with `step --add` when the page should show the slices. Board table:")
        self.assertIn(whole, dispatch)
        self.assertEqual(self.flat(read("references", "phases.md")).count("The phase's usual steps are the wave's"), 1)
        self.assertIn("--add", options(subcommands(build_parser())["step"]))

    def test_the_cli_s_own_help_names_every_state(self):
        """Gate 6. `step --help` is a doc too: both of its lines name each state in STEP_STATES.
        Gate 8, 8: so is the top-level help, `pipeline.py --help`, which prints
        the module's own usage lines: its `step` line lists the states too, in
        the script's order."""
        parser = build_parser()
        listed = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        line = next(c.help for c in listed._choices_actions if c.dest == "step")
        state = next(a.help for a in subcommands(parser)["step"]._actions if a.dest == "state")
        for word in pipeline.STEP_STATES:
            self.assertIn(word, line)
            self.assertIn(word, state)
        top = [text for text in parser.format_help().splitlines() if text.strip().startswith("pipeline.py step ")]
        self.assertEqual(len(top), 1, top)
        self.assertIn("[" + "|".join(pipeline.STEP_STATES) + "]", top[0])

    def test_skill_md_says_an_older_desk_refuses_a_dropped_step(self):
        """Once, in the paragraph that describes reporting to the desk."""
        text = self.flat(read("SKILL.md"))
        sentence = "A desk from before ALYDAS-225, October 2026, refuses a beat that carries a dropped step"
        self.assertEqual(text.count(sentence), 1)
        # Gate 8, 9. "This change" names nothing to a reader who comes later.
        self.assertNotIn("before this change", text)
        reporting = [p for p in paragraphs(read("SKILL.md")) if "is also reported to the alydash desk" in self.flat(p)]
        self.assertEqual(len(reporting), 1)
        self.assertIn(sentence, self.flat(reporting[0]))


class TestTheDocsTeachTheGate8Refusals(unittest.TestCase):
    """ALYDAS-174, Gate 8 (CR-1 / W-1, W-3, W-4, CR-4). What the script now
    refuses, and how it counts a rename, said where the run reads it."""

    @staticmethod
    def flat(text):
        return " ".join(text.split())

    def test_gates_md_quotes_the_stale_record_refusal_as_the_script_says_it(self):
        from test_heartbeat import A_RECORD
        run = {"slug": "<slug>", "phases": [{"n": 8, "review": dict(A_RECORD, head="a" * 40)}]}
        with mock.patch.object(pipeline, "head_commit", return_value="b" * 40):
            [problem] = pipeline.review_close_problems(run)
        said = re.sub(r"\b[ab]{12}\b", "<sha>", str(problem))
        self.assertIn(said, self.flat(read("references", "gates.md")))
        self.assertIn("made at HEAD", self.flat(read("SKILL.md")))

    def test_the_docs_say_a_holding_phase_is_skipped_only_with_force(self):
        waits = self.flat(section(read("SKILL.md"), TestDocsTeachTheHold.WAITS) or "")
        self.assertIn("`phase <n> skipped --note` refuses it too", waits)
        self.assertIn("A held Gate 8 is not skipped by hand", self.flat(read("references", "gates.md")))
        skips = [c for c in pipe_lines(read("references", "prototype.md")) if re.match(r"phase <slug> 2 skipped", c)] \
            + re.findall(r"`(phase <slug> 2 skipped[^`]*)`", read("references", "prototype.md"))
        self.assertTrue(skips, "prototype.md names no Phase 2 skip")
        for call in skips:
            self.assertIn("--force", call, call)

    def test_the_docs_say_review_record_waits_for_phase_8_and_counts_renames_once(self):
        gates = self.flat(read("references", "gates.md"))
        self.assertIn("`git diff --numstat -M <base>...HEAD`", gates)
        self.assertIn("a pure rename is 0 added and 0 removed", gates)
        self.assertIn("`review record` runs only while Phase 8 is running or held", gates)
        self.assertIn("runs only while Phase 8 is running or held", self.flat(read("SKILL.md")))


if __name__ == "__main__":
    unittest.main()
