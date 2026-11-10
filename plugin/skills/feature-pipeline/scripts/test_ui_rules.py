#!/usr/bin/env python3
"""ALYDAS-223, S6. The UI gate's rule book, `references/ui-rules.md`, against
the licence it is adapted under, the measuring module that emits its ids, and
the rule that the gate runs with nothing else installed.

  python3 -m unittest test_ui_rules -v

Python 3.9+, stdlib only. Nothing here runs node: `ui_measure.mjs` is read as
text.
"""
import ast
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
RULE_BOOK = os.path.join("references", "ui-rules.md")
ATTRIBUTION = "Attribution"

# The source's LICENSE, word for word.
COPYRIGHT = "Copyright (c) 2026 Jakub Krehel"
MIT = """MIT License

Copyright (c) 2026 Jakub Krehel

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

YES, NO = "yes", "no"
# Each rule the gate has: its owner domain, and whether it is HIGH on sight.
# None is a rule that is HIGH only in the case its row names.
RULES = {
    "A11Y-FOCUS": ("Accessibility", YES), "A11Y-NAME": ("Accessibility", YES),
    "A11Y-KEYBOARD": ("Accessibility", YES), "A11Y-TARGET": ("Accessibility", NO),
    "A11Y-MOTION": ("Accessibility", YES), "A11Y-COLOR-ONLY": ("Accessibility", YES),
    "A11Y-STRUCTURE": ("Accessibility", NO), "A11Y-ANNOUNCE": ("Accessibility", NO),
    "LAYOUT-CLIP": ("Layout", YES), "LAYOUT-HIDDEN": ("Layout", YES),
    "LAYOUT-GROUP": ("Layout", NO), "LAYOUT-CONTROL": ("Layout", NO),
    "WRITE-DESTRUCTIVE": ("Writing", YES), "WRITE-ERROR": ("Writing", None), "WRITE-LABEL": ("Writing", NO),
    "WRITE-CONSISTENT": ("Writing", NO), "WRITE-EMPTY": ("Writing", NO),
    "TYPE-TRUNCATE": ("Typography", YES), "TYPE-HEADING-ORDER": ("Typography", NO), "TYPE-SCALE": ("Typography", NO),
    "TYPE-INPUT-ZOOM": ("Typography", NO), "TYPE-MEASURE": ("Typography", NO),
    "COLOR-CONTRAST": ("Colors", YES), "COLOR-MEANING": ("Colors", YES), "COLOR-TREATMENT": ("Colors", NO),
    "COLOR-TOKEN": ("Colors", NO),
    "UI-TRANSITION-ALL": ("UI", NO), "UI-MOTION": ("UI", None), "UI-RADIUS": ("UI", NO), "UI-STATE": ("UI", NO),
    "PLACE-MISSING": ("Placement", YES), "PLACE-OFFSCREEN": ("Placement", NO),
    "PLACE-SHARE": ("Placement", NO), "PLACE-ORDER": ("Placement", NO),
}
DOMAINS = ("Accessibility", "Layout", "Writing", "Typography", "Colors", "UI", "Placement")
SEEN_BY = ("script", "reviewer", "both")

# What the gate must not lean on: an installed plugin's path, or another skill
# by name. Spelled in halves, so this file is not its own hit.
FORBIDDEN = ("plugins" + "/cache", "interfaces" + ":", "better-" + "interface", "impec" + "cable", "~/.claude/" + "plugins")


def read(*parts):
    with open(os.path.join(SKILL, *parts), encoding="utf-8") as f:
        return f.read()


def sections(text):
    """Each `## ` section of a document: its heading and its body."""
    return {m.group(1).strip(): m.group(2) for m in re.finditer(r"^## ([^\n]+)\n(.*?)(?=^## |\Z)", text, re.S | re.M)}


def rule_rows(text):
    """Every row of every rule table: (id, owner section, rule, automatic HIGH, who sees it)."""
    rows = []
    for heading, body in sections(text).items():
        for line in body.splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            found = re.fullmatch(r"`([A-Z0-9]+(?:-[A-Z0-9]+)+)`", cells[0]) if line.startswith("|") else None
            if found:
                rows.append((found.group(1), heading, cells))
    return rows


def measured_ids():
    """The ids `ui_measure.mjs` exports as RULES, read from its text."""
    found = re.search(r"export const RULES = \[(.*?)\];", read("scripts", "ui_measure.mjs"), re.S)
    return re.findall(r"'([A-Z0-9-]+)'", found.group(1)) if found else []


def without_attribution(text):
    """The rule book with its attribution section cut out."""
    return re.sub(rf"^## {ATTRIBUTION}\n.*?(?=^## |\Z)", "", text, flags=re.S | re.M)


GATE_FILES = ("references/ui-gate.md", "references/ui-rules.md", "scripts/ui_measure.mjs")
GATE_SECTION = "The UI gate"
# The gate's code path in pipeline.py. S6 listed these by hand; Gate 6 (D09)
# derives the path (`gate_functions`), and this list is now only a floor: each
# of these has to be on the derived path.
GATE_FUNCTIONS = ("check_report", "check_rulings", "changed_files", "is_rendered_surface", "cmd_ui_gate",
                  "_screen_problems", "_ruling_problems")
# What the mutation pass found off the hand list. A floor too.
ONCE_UNSWEPT = ("refuse_skipped_review", "surface_names", "_inside", "_read_json", "_glob_match", "_glob_regex",
                "run_checks")
GATE_PREFIX = "ui_gate"


def gate_functions(text=None):
    """The source of each function on the gate's code path, by name. Derived
    from pipeline.py's own source, never listed:

    1. the gate's own functions: every one with `ui_gate` in its name;
    2. every function that calls one of those (Phase 8's check, the refusal
       to skip Phase 8, the commands that say the gate's notices);
    3. every function any of those names, however deep.

    A function that only hands a gate function on without calling it (`main`,
    which gives `cmd_ui_gate` to the parser) is not on the path: through it
    the path would be the whole script."""
    text = read("scripts", "pipeline.py") if text is None else text
    lines = text.splitlines()
    funcs = {node.name: node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef)}
    named = {name: {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and n.id in funcs and n.id != name}
             for name, fn in funcs.items()}
    called = {name: {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                     and n.func.id in funcs} for name, fn in funcs.items()}
    own = {name for name in funcs if GATE_PREFIX in name}
    path, queue = set(), sorted(own | {name for name in funcs if called[name] & own})
    while queue:
        name = queue.pop()
        if name not in path:
            path.add(name)
            queue.extend(named[name])
    return {name: "\n".join(lines[funcs[name].lineno - 1:funcs[name].end_lineno]) for name in sorted(path)}


def swept_texts():
    """What the sweep reads, by a name for each: the gate's own files, the UI
    gate section of SKILL.md, and the gate's functions in pipeline.py. The
    rest of the skill may name an optional skill; the gate may not lean on one."""
    texts = {name: read(*name.split("/")) for name in GATE_FILES}
    texts["SKILL.md § " + GATE_SECTION] = sections(read("SKILL.md")).get(GATE_SECTION, "")
    texts.update({f"scripts/pipeline.py {name}()": source for name, source in gate_functions().items()})
    return texts


def forbidden_hits(texts):
    """(file, line number, word) for each forbidden word in `texts`, a map of
    path to text. The rule book's attribution section is not read."""
    hits = []
    for path, text in sorted(texts.items()):
        if path == RULE_BOOK:
            # Blank the section rather than cut it, so line numbers hold.
            text = re.sub(rf"^## {ATTRIBUTION}\n.*?(?=^## |\Z)", lambda m: "\n" * m.group(0).count("\n"), text,
                          flags=re.S | re.M)
        for at, line in enumerate(text.splitlines(), 1):
            hits += [(path, at, word) for word in FORBIDDEN if word.casefold() in line.casefold()]
    return hits


class TestTheLicence(unittest.TestCase):
    def attribution(self):
        body = sections(read(RULE_BOOK)).get(ATTRIBUTION)
        self.assertIsNotNone(body, f"ui-rules.md has no '## {ATTRIBUTION}' section")
        return body

    def test_the_attribution_opens_the_rule_book(self):
        self.assertEqual(next(iter(sections(read(RULE_BOOK)))), ATTRIBUTION)

    def test_the_whole_mit_licence_is_there_word_for_word(self):
        self.assertIn(MIT.strip(), self.attribution())

    def test_the_copyright_line_and_the_permission_notice_are_there(self):
        """Each on its own, so a failure says which went."""
        body = " ".join(self.attribution().split())
        self.assertIn(COPYRIGHT, body)
        notice = " ".join(MIT.split("\n\n")[2].split())
        self.assertTrue(notice.startswith("Permission is hereby granted"))
        self.assertIn(notice, body)
        self.assertIn("The above copyright notice and this permission notice shall be included", body)

    def test_the_source_is_named_with_its_author_and_version(self):
        body = self.attribution()
        for words in ("Jakub Krehel", "github.com/jakubkrehel/skills", "1.6.3"):
            with self.subTest(words=words):
                self.assertIn(words, body)


class TestTheRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read(RULE_BOOK)
        cls.rows = rule_rows(cls.text)

    def test_the_brief_s_ids_are_thirty_three_in_seven_domains(self):
        """A floor, so the cases below cannot pass on an empty table."""
        self.assertEqual(len(RULES), 34)
        self.assertEqual({domain for domain, _ in RULES.values()}, set(DOMAINS))

    def test_the_measuring_module_s_ids_are_read(self):
        ids = measured_ids()
        self.assertEqual(len(ids), 9, ids)
        self.assertIn("COLOR-CONTRAST", ids)

    def test_every_id_the_measuring_module_emits_is_defined(self):
        defined = {rule for rule, _, _ in self.rows}
        for rule in measured_ids():
            with self.subTest(rule=rule):
                self.assertIn(rule, defined, f"ui_measure.mjs emits {rule} and ui-rules.md does not define it")

    def test_every_rule_is_defined_exactly_once_and_no_other_is(self):
        named = [rule for rule, _, _ in self.rows]
        for rule in RULES:
            with self.subTest(rule=rule):
                self.assertEqual(named.count(rule), 1, f"{rule} is defined {named.count(rule)} times")
        self.assertEqual(sorted(set(named) - set(RULES)), [], "a rule the gate was not given")

    def test_every_rule_sits_under_its_owner_domain(self):
        owner = {rule: heading for rule, heading, _ in self.rows}
        for rule, (domain, _) in RULES.items():
            with self.subTest(rule=rule):
                self.assertEqual(owner.get(rule), domain)

    def test_every_row_has_its_four_cells_and_a_rule_in_words(self):
        for rule, _, cells in self.rows:
            with self.subTest(rule=rule):
                self.assertEqual(len(cells), 4, cells)
                self.assertGreater(len(cells[1].split()), 4, "the rule is one sentence, in words")
                self.assertIn(cells[3].split(",")[0], SEEN_BY)

    def test_automatic_high_is_yes_no_or_the_case_it_names(self):
        high = {rule: cells[2] for rule, _, cells in self.rows if len(cells) == 4}
        for rule, (_, automatic) in RULES.items():
            with self.subTest(rule=rule):
                if automatic is None:
                    self.assertNotIn(high.get(rule), (YES, NO, "", None), "HIGH in one case, which the cell names")
                else:
                    self.assertEqual(high.get(rule), automatic)

    def test_write_destructive_is_high_on_sight_and_writing_owns_it(self):
        mine = [(heading, cells) for rule, heading, cells in self.rows if rule == "WRITE-DESTRUCTIVE"]
        self.assertEqual(len(mine), 1, mine)
        heading, cells = mine[0]
        self.assertEqual(heading, "Writing")
        self.assertEqual(cells[2], YES)
        writing = " ".join(sections(self.text)["Writing"].split())
        self.assertIn("COLOR-TREATMENT", writing)
        self.assertNotIn("COLOR-MEANING", writing, "the plain look is COLOR-TREATMENT's (G7)")
        self.assertRegex(writing, r"ruling on the look does not answer")

    def test_what_the_script_emits_is_marked_as_seen_by_the_script(self):
        seen = {rule: cells[3].split(",")[0] for rule, _, cells in self.rows if len(cells) == 4}
        emitted = set(measured_ids())
        for rule in RULES:
            with self.subTest(rule=rule):
                if rule in emitted:
                    self.assertIn(seen.get(rule), ("script", "both"))
                else:
                    self.assertEqual(seen.get(rule), "reviewer")

    def test_the_shared_rules_are_stated(self):
        body = " ".join(self.text.split())
        for words in ("Evidence, not taste", "Prefer the cheaper fix", "One root cause is one finding",
                      "HIGH", "MEDIUM", "LOW", "not from the interfaces skills"):
            with self.subTest(words=words):
                self.assertIn(words, body)
        self.assertRegex(body, r"fill under 40% while taking over 20% of the first screen")
        self.assertRegex(sections(self.text)["Accessibility"], r"(?s)global.*reduced-motion.*satisfies")


def pipeline_module():
    import sys
    sys.path.insert(0, HERE)
    import pipeline
    return pipeline


class TestTheScriptKnowsTheRuleBook(unittest.TestCase):
    """G6, 5. `pipeline.py` carries the rule ids and the ones that are HIGH on
    sight as constants, so `check_report` reads no file. Both are held here to
    the tables in ui-rules.md, parsed, so neither can drift from the book."""

    @classmethod
    def setUpClass(cls):
        cls.pipeline = pipeline_module()
        cls.rows = rule_rows(read(RULE_BOOK))

    def test_the_tables_are_read(self):
        """A floor, so the comparisons below cannot pass on two empty sets."""
        self.assertEqual(len(self.rows), 34)
        self.assertEqual(len([1 for _, _, cells in self.rows if cells[2] == YES]), 12)

    def test_the_script_s_rule_ids_are_exactly_the_rule_book_s(self):
        self.assertEqual(sorted(self.pipeline.UI_GATE_RULES), sorted(rule for rule, _, _ in self.rows))

    def test_the_script_s_automatic_high_rules_are_exactly_the_rows_marked_yes(self):
        marked = sorted(rule for rule, _, cells in self.rows if cells[2] == YES)
        self.assertEqual(sorted(self.pipeline.UI_GATE_AUTOMATIC_HIGH), marked)
        # HIGH in one case only is not HIGH on sight.
        for rule in ("WRITE-ERROR", "UI-MOTION"):
            self.assertNotIn(rule, self.pipeline.UI_GATE_AUTOMATIC_HIGH)

    def test_the_script_s_contexts_are_the_measuring_module_s(self):
        found = re.search(r"export const CONTEXTS = \[(.*?)\n\];", read("scripts", "ui_measure.mjs"), re.S)
        names = re.findall(r"name: '([a-z-]+)'", found.group(1))
        self.assertEqual(len(names), 4, names)
        self.assertEqual(list(self.pipeline.UI_GATE_CONTEXTS), names)


def module_number(name):
    """A number `ui_measure.mjs` exports by name, as the module writes it."""
    found = re.search(rf"^export const {name} = ([0-9.]+);$", read("scripts", "ui_measure.mjs"), re.M)
    return found.group(1) if found else None


class TestTheNumbersAreTheModules(unittest.TestCase):
    """G6, 17 (D06, D07). The thresholds the rule book states are the ones the
    measuring module measures by: both are read, and compared."""

    @classmethod
    def setUpClass(cls):
        cls.text = read(RULE_BOOK)
        cls.rule = {rule: cells[1] for rule, _, cells in rule_rows(cls.text)}

    def test_the_module_s_numbers_are_read(self):
        """A floor: the comparisons below are with these, not with None."""
        self.assertEqual([module_number(n) for n in ("CONTRAST_MIN", "TARGET_MIN", "TOUCH_TARGET_MIN")],
                         ["4.5", "24", "44"])

    def test_the_module_measures_by_the_numbers_it_exports(self):
        """Each is handed to the page, and no comparison carries a number of its own."""
        module = read("scripts", "ui_measure.mjs")
        self.assertIn("limits: { contrast: CONTRAST_MIN, target: TARGET_MIN, touchTarget: TOUCH_TARGET_MIN }", module)
        for use in ("value >= limits.contrast", "least < limits.target", "least < limits.touchTarget"):
            with self.subTest(use=use):
                self.assertIn(use, module)
        self.assertEqual(re.findall(r"(?:value|least) *[<>]=? *[0-9.]+", module), [])

    def test_the_contrast_ratio_the_rule_book_states_is_the_module_s(self):
        self.assertEqual(re.findall(r"([0-9.]+) to 1\b", self.rule["COLOR-CONTRAST"]), [module_number("CONTRAST_MIN")])

    def test_the_target_sizes_the_rule_book_states_are_the_module_s(self):
        least, touch = module_number("TARGET_MIN"), module_number("TOUCH_TARGET_MIN")
        row = self.rule["A11Y-TARGET"]
        self.assertEqual(re.findall(r"at least ([0-9.]+)px", row), [least])
        self.assertEqual(re.findall(r"([0-9.]+) to ([0-9.]+)", row), [(least, touch)])
        # And the paragraph under the table that says what the script reports.
        said = " ".join(next(p for p in sections(self.text)["Accessibility"].split("\n\n")
                             if p.startswith("`A11Y-TARGET`")).split())
        self.assertEqual(re.findall(r"under ([0-9.]+)px", said), [least])
        self.assertEqual(re.findall(r"([0-9.]+) to ([0-9.]+)", said), [(least, touch)])


class TestTheTwoNarrowedRules(unittest.TestCase):
    """G7, 4. A11Y-COLOR-ONLY and COLOR-MEANING say what they are not, each
    with an example of both sides."""

    def about(self, domain, rule):
        return " ".join(next(p for p in sections(read(RULE_BOOK))[domain].split("\n\n")
                             if p.startswith(f"`{rule}`")).split())

    def test_color_only_says_what_it_is_not(self):
        said = self.about("Accessibility", "A11Y-COLOR-ONLY")
        self.assertRegex(said, r"only when .* declared jobs or to recover from an error")
        self.assertRegex(said, r"category mark that the same row or its detail also says in words is not this rule")
        self.assertRegex(said, r"MEDIUM under `A11Y-STRUCTURE`")
        self.assertRegex(said, r"It is: .*It is not: ")

    def test_color_meaning_says_what_it_is_not(self):
        said = self.about("Colors", "COLOR-MEANING")
        self.assertRegex(said, r"only when a semantic colour signals the opposite of the state it is on")
        self.assertRegex(said, r"no distinct treatment, or one colour stretched over two near meanings, is not this rule")
        self.assertRegex(said, r"It is: .*It is not: ")
        self.assertIn("`COLOR-TREATMENT`", said)

    def test_color_treatment_is_the_plain_look_and_a_ruling_on_the_look_answers_it(self):
        row = {rule: cells for rule, _, cells in rule_rows(read(RULE_BOOK))}["COLOR-TREATMENT"]
        self.assertEqual(row[2:], [NO, "reviewer"])
        self.assertRegex(row[1], r"no distinct treatment")
        self.assertRegex(row[1], r"ruling on a plain look answers this rule")
        said = self.about("Colors", "COLOR-TREATMENT")
        self.assertRegex(said, r"MEDIUM by default")
        # The examples and the rulings in ui-gate.md use it for the plain look.
        gate = read("references", "ui-gate.md")
        self.assertIn('"rules": ["COLOR-TREATMENT"]', gate)
        self.assertRegex(" ".join(gate.split()), r"stays plain \(COLOR-TREATMENT\)")
        self.assertNotIn("COLOR-MEANING", gate)


class TestTheGateStandsAlone(unittest.TestCase):
    """Nothing in the skill sends the reader to an installed plugin or to
    another skill for the gate's rules."""

    def test_the_sweep_reads_the_gate_s_files_its_section_and_its_code_path(self):
        texts = swept_texts()
        for name in GATE_FILES + ("SKILL.md § " + GATE_SECTION,) + tuple(
                f"scripts/pipeline.py {fn}()" for fn in GATE_FUNCTIONS
                + ("ui_gate_check", "ui_gate_record", "ui_gate_table", "ui_gate_context", "ui_gate_problems")):
            with self.subTest(swept=name):
                self.assertGreater(len(texts.get(name, "")), 100, f"{name} is not swept, or is empty")
        self.assertGreaterEqual(len(gate_functions()), 17, sorted(gate_functions()))

    def test_the_code_path_is_derived_and_reaches_what_the_hand_list_missed(self):
        """G6, 19 (D09). Each function the mutation pass found unswept is on the path."""
        path = gate_functions()
        for name in GATE_FUNCTIONS + ONCE_UNSWEPT:
            with self.subTest(function=name):
                self.assertIn(name, path)
        self.assertNotIn("main", path, "main only hands cmd_ui_gate to the parser")

    def test_the_derivation_on_made_up_source(self):
        """The three rules, and what stays off the path."""
        source = "\n".join((
            "def ui_gate_check(run):\n    return helper(run)",
            "def helper(run):\n    return deeper(run)",
            "def deeper(run):\n    return 1",
            "def close_phase(run):\n    return ui_gate_check(run) and other(run)",
            "def other(run):\n    return 2",
            "def passes_it_on():\n    return register(fn=ui_gate_check)",
            "def register(fn):\n    return fn",
            "def unrelated():\n    return other(3)",
            "def cmd_ui_gate(a):\n    return 0",
        ))
        self.assertEqual(sorted(gate_functions(source)),
                         ["close_phase", "cmd_ui_gate", "deeper", "helper", "other", "ui_gate_check"])

    def test_a_forbidden_word_anywhere_on_the_path_is_a_hit(self):
        """D09 as the breaker ran it: the word in a function the hand list did not hold."""
        word = FORBIDDEN[3]
        text = read("scripts", "pipeline.py")
        marked = text.replace('    """ALYDAS-223, S2. Exit 2 when Phase 8 would be skipped',
                              f'    """ALYDAS-223, S2 (see the {word} skill). Exit 2 when Phase 8 would be skipped')
        self.assertNotEqual(marked, text, "the docstring this case marks is no longer there")
        texts = {f"scripts/pipeline.py {name}()": source for name, source in gate_functions(marked).items()}
        self.assertEqual([(path, found) for path, _, found in forbidden_hits(texts)],
                         [("scripts/pipeline.py refuse_skipped_review()", word)])

    def test_nothing_the_gate_runs_on_names_a_plugin_path_or_another_skill(self):
        self.assertEqual(forbidden_hits(swept_texts()), [])

    def test_the_rest_of_the_skill_may_still_name_an_optional_skill(self):
        """The run's goal: the optional enrichments stay as they are. Naming a
        skill to invoke when present is not depending on it."""
        word = FORBIDDEN[3]
        for parts in (("SKILL.md",), ("references", "build-standards.md")):
            with self.subTest(file=parts[-1]):
                self.assertIn(word, read(*parts))
        self.assertNotIn(word, sections(read("SKILL.md"))[GATE_SECTION])

    def test_a_word_in_the_gate_s_section_or_code_path_is_a_hit(self):
        """The narrowed sweep on made-up text, by the names it gives its subjects."""
        word = FORBIDDEN[0]
        for name in ("SKILL.md § " + GATE_SECTION, "scripts/pipeline.py check_report()"):
            with self.subTest(swept=name):
                self.assertEqual(forbidden_hits({name: f"fine\nsee {word}\n"}), [(name, 2, word)])

    def test_the_sweep_bites_and_only_the_attribution_is_exempt(self):
        """The sweep on made-up text: each word is a hit in any file and in the
        rule book outside its attribution, in any case; inside it, none is."""
        book = f"# Rules\n\n## {ATTRIBUTION}\n\nFrom {{word}}.\n\n## Accessibility\n\n{{after}}\n"
        for word in FORBIDDEN:
            with self.subTest(word=word):
                self.assertEqual(forbidden_hits({"SKILL.md": f"one\nsee {word.upper()} here\n"}),
                                 [("SKILL.md", 2, word)])
                self.assertEqual(forbidden_hits({RULE_BOOK: book.format(word=word, after="nothing")}), [])
                self.assertEqual(forbidden_hits({RULE_BOOK: book.format(word="the source", after=word)}),
                                 [(RULE_BOOK, 9, word)])
                self.assertEqual(forbidden_hits({"references/ui-gate.md": book.format(word=word, after="nothing")}),
                                 [("references/ui-gate.md", 5, word)])
        self.assertEqual(len(FORBIDDEN), 5)


if __name__ == "__main__":
    unittest.main()
