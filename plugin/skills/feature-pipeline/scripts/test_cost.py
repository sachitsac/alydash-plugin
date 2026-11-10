#!/usr/bin/env python3
"""ALYDAS-135, S4. What a run cost, from its transcripts, when Claude Code has
not yet written the session's `cost-state` line.

Every fixture is written under a temporary directory standing in for
~/.claude/projects, so no test reads a real transcript. The figures each test
expects are worked out by hand in the test from the price table in the plan,
not read back off `pipeline.PRICES`, so a price moved in the code is a price
somebody had to move twice.

  python3 -m unittest test_cost -v

Python 3.9+, stdlib only.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.join(HERE, "pipeline.py")
sys.path.insert(0, HERE)
import pipeline  # noqa: E402

from test_heartbeat import Listener, PROXY_VARIABLES, TOKEN_VARIABLE, URL_VARIABLE, WORK_ITEM_VARIABLE  # noqa: E402

SESSION = "11111111-2222-3333-4444-555555555555"
# Inside the phase window every test opens at 2026-01-01T00:00:00Z.
AT = "2026-01-01T00:01:00.000Z"
PHASE_START = "2026-01-01T00:00:00.000Z"
PHASE_END = "2026-01-01T01:00:00.000Z"


def message(model, usage, mid, at=AT):
    """One assistant line as Claude Code writes it."""
    return {"type": "assistant", "timestamp": at, "requestId": "req-" + mid,
            "message": {"id": mid, "model": model, "usage": usage}}


def usage(input=0, write_5m=None, write_1h=None, flat_write=None, read=0, output=0):
    """A usage object. With a split, the flat count is the split's sum, the way
    the transcripts carry both; `flat_write` alone is a transcript with no split."""
    u = {"input_tokens": input, "cache_read_input_tokens": read, "output_tokens": output}
    if write_5m is not None or write_1h is not None:
        u["cache_creation"] = {"ephemeral_5m_input_tokens": write_5m or 0,
                               "ephemeral_1h_input_tokens": write_1h or 0}
        u["cache_creation_input_tokens"] = (write_5m or 0) + (write_1h or 0)
    else:
        u["cache_creation_input_tokens"] = flat_write or 0
    return u


def cost_state(model_usage):
    return {"type": "cost-state", "sessionId": SESSION, "totalCostUSD": sum(m["costUSD"] for m in model_usage.values()),
            "totalAPIDuration": 1000, "modelUsage": model_usage}


class CostCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.projects = os.path.join(self.tmp.name, "home", ".claude", "projects")
        self.project = os.path.join(self.projects, "-scratch-project")
        os.makedirs(self.project)
        saved = pipeline.PROJECTS
        pipeline.PROJECTS = self.projects
        self.addCleanup(setattr, pipeline, "PROJECTS", saved)

    def write_transcript(self, lines):
        with open(os.path.join(self.project, SESSION + ".jsonl"), "w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")

    def write_subagent(self, agent_id, lines):
        folder = os.path.join(self.project, SESSION, "subagents")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"agent-{agent_id}.jsonl"), "w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")

    def usage_of_one_phase(self):
        """compute_usage over one closed phase that spans every fixture message."""
        run = {"phases": [{"n": 0, "name": "Frame"}]}
        events = [{"ts": PHASE_START, "session": SESSION, "phase": 0, "event": "start"},
                  {"ts": PHASE_END, "session": SESSION, "phase": 0, "event": "done"}]
        pipeline.compute_usage(run, events)
        return run

    # ---------- the table, with no cost-state line ----------

    def test_a_transcript_with_no_cost_state_is_priced_from_the_table(self):
        """A live session has no cost-state line yet, so every message is priced
        per million tokens from the table: opus 4 in, 5 and 8 for the two cache
        writes, 0.20 read, 20 out."""
        self.write_transcript([
            message("claude-opus-5-5", usage(input=100_000, write_5m=300_000, write_1h=200_000,
                                             read=1_000_000, output=50_000), "m1"),
            # The same response written once per content block: counted once.
            message("claude-opus-5-5", usage(input=100_000, write_5m=300_000, write_1h=200_000,
                                             read=1_000_000, output=50_000), "m1"),
            # No split: the flat write is priced at the 5-minute rate.
            message("claude-opus-5-5", usage(flat_write=400_000), "m2"),
        ])
        # m1: 0.1 * 4 + 0.3 * 5 + 0.2 * 8 + 1.0 * 0.20 + 0.05 * 20
        #   = 0.40 + 1.50 + 1.60 + 0.20 + 1.00 = 4.70
        # m2: 0.4 * 5 = 2.00
        run = self.usage_of_one_phase()
        t = run["telemetry"]
        self.assertTrue(t["cost_known"], t.get("cost_note"))
        self.assertAlmostEqual(t["cost_usd"], 6.70, places=2)
        self.assertTrue(run["phases"][0]["telemetry"]["cost_known"])
        self.assertAlmostEqual(run["phases"][0]["telemetry"]["cost_usd"], 6.70, places=2)

    # ---------- the calibrated rate, matched by base id ----------

    def test_a_cost_state_keyed_with_a_suffix_prices_messages_without_one(self):
        """cost-state names the model `claude-opus-5-5[1m]` and every message says
        `claude-opus-5-5`. The calibrated rate is used, and the table is not added
        on top of it."""
        self.write_transcript([
            message("claude-opus-5-5", usage(input=500_000, output=100_000), "m1"),
            # $10 for 1,000,000 weighted tokens (input 1, output 5): $0.00001 each.
            cost_state({"claude-opus-5-5[1m]": {"inputTokens": 1_000_000, "outputTokens": 0,
                                                 "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                                                 "costUSD": 10.0}}),
        ])
        # Weighted: 500,000 + 5 * 100,000 = 1,000,000, so $10.00 calibrated. The
        # table would say 0.5 * 4 + 0.1 * 20 = $4.00, and both at once $14.00.
        t = self.usage_of_one_phase()["telemetry"]
        self.assertTrue(t["cost_known"])
        self.assertAlmostEqual(t["cost_usd"], 10.00, places=2)

    # ---------- subagents ----------

    def test_a_subagents_cache_writes_are_counted_and_priced_per_message(self):
        """A subagent's transcript carries the same 5m/1h split, and each of its
        messages is priced by its own model, not dropped and not by the first."""
        self.write_transcript([message("claude-opus-5-5", usage(output=10_000), "main-1")])
        self.write_subagent("a1", [
            message("claude-opus-5-5", usage(write_1h=1_000_000), "sub-1"),
            message("claude-opus-5-5", usage(write_1h=1_000_000), "sub-1"),
            message("claude-sonnet-5", usage(output=100_000), "sub-2"),
        ])
        # main: 0.01 * 20 = 0.20. sub-1: 1.0 * 8 = 8.00, once. sub-2: 0.1 * 10 = 1.00.
        run = self.usage_of_one_phase()
        t = run["telemetry"]
        self.assertTrue(t["cost_known"])
        self.assertAlmostEqual(t["cost_usd"], 9.20, places=2)
        self.assertEqual(t["tokens"]["cache_write"], 1_000_000)
        self.assertEqual(t["agents"], 1)
        [row] = t["agent_rows"]
        self.assertAlmostEqual(row["cost_usd"], 9.00, places=2)

    # ---------- a model in neither source ----------

    def test_one_unpriced_message_makes_the_whole_cost_unknown_not_a_lower_bound(self):
        """The declared choice: a partial is unknown. A run that spent on a model
        with no price shows no cost, rather than a smaller number shown as the cost."""
        self.write_transcript([
            message("claude-opus-5-5", usage(output=100_000), "m1"),
            message("claude-mystery-9", usage(output=100_000), "m2"),
        ])
        run = self.usage_of_one_phase()
        for where, t in (("run", run["telemetry"]), ("phase", run["phases"][0]["telemetry"])):
            with self.subTest(where):
                self.assertFalse(t["cost_known"])
                self.assertIsNone(t["cost_usd"])
        self.assertEqual(pipeline.beat_totals(run)[1], None)

    def test_an_unknown_model_sends_no_cost_to_the_desk(self):
        """The beat after a phase that spent only on an unpriced model carries no
        `costUsd` key at all: not 0.0, which the desk prints as "$0.00", and not null."""
        home = os.path.join(self.tmp.name, "home")
        root = os.path.join(self.tmp.name, "docs")
        env = dict(os.environ)
        for name in (URL_VARIABLE, TOKEN_VARIABLE, WORK_ITEM_VARIABLE) + PROXY_VARIABLES:
            env.pop(name, None)
        env.update({"HOME": home, "CLAUDE_CODE_SESSION_ID": SESSION})

        def run_pipeline(*args, extra=None):
            out = subprocess.run([sys.executable, PIPELINE, "--root", root] + list(args),
                                 capture_output=True, text=True, cwd=self.tmp.name, env={**env, **(extra or {})})
            self.assertEqual(out.returncode, 0, out.stderr)
            return out

        run_pipeline("init", "scratch", "--title", "Scratch", "--goal", "A scratch run.",
                      "--branch", "s4", "--date", "2026-01-01")
        # Phase 0 opened before the fixture's message, by hand, so the window
        # holds it whatever the clock says now.
        with open(os.path.join(root, "boards", "2026-01-01-scratch.events.jsonl"), "a") as f:
            f.write(json.dumps({"ts": PHASE_START, "session": SESSION, "phase": 0, "event": "start"}) + "\n")
        self.write_transcript([message("claude-mystery-9", usage(input=1_000, output=100_000), "m1")])

        with Listener() as desk:
            run_pipeline("phase", "scratch", "1", "start", extra={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            [body] = desk.bodies()
        # The tokens were counted, so the fixture was read; only the cost is unknown.
        self.assertEqual(body["tokens"], 101_000)
        self.assertNotIn("costUsd", body)

    # ---------- S3: at least, then exact ----------
    #
    # Sachit's ruling of 2026-09-26. The transcripts lack part of what Claude
    # Code bills (S4 measured 16 to 33% under), and Claude Code writes its own
    # total, `cost-state` -> `totalCostUSD`, only near a session's end. So the
    # beat sends that total as exact when every session of the run has one, and
    # the computed figure marked not exact otherwise.

    def beat_across(self, sessions):
        """The first beat of a run whose phase 0 opened in each of `sessions`
        in turn (one hand-written start per session, the way a resumed run
        logs), with `phase 1 start` run from the last, against a listener."""
        home = os.path.join(self.tmp.name, "home")
        root = os.path.join(self.tmp.name, "docs")
        env = dict(os.environ)
        for name in (URL_VARIABLE, TOKEN_VARIABLE, WORK_ITEM_VARIABLE) + PROXY_VARIABLES:
            env.pop(name, None)
        env.update({"HOME": home, "CLAUDE_CODE_SESSION_ID": sessions[-1]})

        def run_pipeline(*args, extra=None):
            out = subprocess.run([sys.executable, PIPELINE, "--root", root] + list(args),
                                 capture_output=True, text=True, cwd=self.tmp.name, env={**env, **(extra or {})})
            self.assertEqual(out.returncode, 0, out.stderr)
            return out

        # --force, so a test may beat twice with different transcripts.
        run_pipeline("init", "scratch", "--title", "Scratch", "--goal", "A scratch run.",
                     "--branch", "s3", "--date", "2026-01-01", "--force")
        with open(os.path.join(root, "boards", "2026-01-01-scratch.events.jsonl"), "a") as f:
            for session in sessions:
                f.write(json.dumps({"ts": PHASE_START, "session": session, "phase": 0, "event": "start"}) + "\n")
        with Listener() as desk:
            run_pipeline("phase", "scratch", "1", "start", extra={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            [body] = desk.bodies()
        return body

    def write_session(self, session, lines):
        with open(os.path.join(self.project, session + ".jsonl"), "w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")

    def test_a_session_with_its_own_total_sends_that_total_as_the_exact_cost(self):
        """Claude Code's `totalCostUSD` is the bill, so it is the cost, and exact.
        The computed figure here would be $4.00 from the table; the total says 270.84."""
        self.write_transcript([
            message("claude-opus-5-5", usage(input=500_000, output=100_000), "m1"),
            dict(cost_state({"claude-opus-5-5[1m]": {"inputTokens": 1_000_000, "outputTokens": 0,
                                                      "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                                                      "costUSD": 10.0}}), totalCostUSD=270.84),
        ])
        body = self.beat_across([SESSION])
        self.assertEqual(body["costUsd"], 270.84)
        self.assertIs(body["costExact"], True)

    def test_a_live_session_sends_the_computed_cost_marked_not_exact(self):
        """No cost-state yet: the table's figure, which is a floor, and says so."""
        self.write_transcript([message("claude-opus-5-5", usage(input=500_000, output=100_000), "m1")])
        body = self.beat_across([SESSION])
        # 0.5 * 4 + 0.1 * 20 = 4.00
        self.assertAlmostEqual(body["costUsd"], 4.00, places=2)
        self.assertIs(body["costExact"], False)

    def test_a_run_across_two_sessions_is_exact_only_when_both_have_their_total(self):
        """The declared choice. `session_cost_usd` sums the totals of the sessions
        that have one, so with one session still live it is a part, not the run's
        bill: the beat sends the computed figure, not exact. Once both have a
        total, the cost is their sum, exact."""
        other = "99999999-8888-7777-6666-555555555555"
        finished = cost_state({"claude-opus-5-5": {"inputTokens": 1, "outputTokens": 0, "cacheReadInputTokens": 0,
                                                   "cacheCreationInputTokens": 0, "costUSD": 100.0}})
        self.write_session(other, [message("claude-opus-5-5", usage(output=100_000), "o1"),
                                   dict(finished, sessionId=other)])
        self.write_transcript([message("claude-opus-5-5", usage(output=50_000), "m1")])
        body = self.beat_across([other, SESSION])
        self.assertIs(body["costExact"], False)
        self.assertNotAlmostEqual(body["costUsd"], 100.0, places=2)

        self.write_transcript([message("claude-opus-5-5", usage(output=50_000), "m1"),
                               dict(finished, totalCostUSD=25.5)])
        body = self.beat_across([other, SESSION])
        self.assertIs(body["costExact"], True)
        self.assertAlmostEqual(body["costUsd"], 125.5, places=2)

    def test_a_total_that_is_not_the_whole_bill_is_not_exact(self):
        """Two ways a `cost-state` line is not the bill: Claude Code says a model's
        cost was unknown to it, or the session carried on past the line (a total
        written, then more messages), so the total is from before them."""
        state = cost_state({"claude-opus-5-5": {"inputTokens": 1, "outputTokens": 0, "cacheReadInputTokens": 0,
                                                "cacheCreationInputTokens": 0, "costUSD": 50.0}})
        for label, lines in (
            ("unknown model cost", [message("claude-opus-5-5", usage(output=100_000), "m1"),
                                    dict(state, hasUnknownModelCost=True)]),
            ("messages after the total", [message("claude-opus-5-5", usage(output=100_000), "m1"), state,
                                          message("claude-opus-5-5", usage(output=100_000), "m2",
                                                  at="2026-01-01T00:02:00.000Z")]),
        ):
            with self.subTest(label):
                self.write_transcript(lines)
                body = self.beat_across([SESSION])
                self.assertIs(body["costExact"], False)
                self.assertNotEqual(body["costUsd"], 50.0)

    # Gate 8, F1. `claude --resume` writes a second `cost-state` line under the
    # same session id with a new `startTime`, and that line totals only the
    # resumed segment: in c77da73c (2026-09-24) the line at 2167 has
    # totalCostUSD 4.7517 and a modelUsage of 12 in, 6,032 out, 2,681,985 read,
    # 511,828 written, which are the resumed segment's own message tokens to
    # the token, while the two lines at 2057 and 2060 repeat $184.3217 under
    # the first startTime. So a session's bill is the last line of each
    # startTime, summed across startTimes: not the last line (4.75), and not
    # every line (373.39, which counts the repeat twice).

    def resumed(self, first_total=184.3217, resumed_total=4.751709, first_start=1790001328774,
                resumed_start=1790228401549):
        """c77da73c's shape: a segment, its total written twice, a resume, the resumed segment's own total."""
        first = cost_state({"claude-opus-5[1m]": {"inputTokens": 531_465, "outputTokens": 759_717,
                                                   "cacheReadInputTokens": 270_742_949,
                                                   "cacheCreationInputTokens": 3_301_786, "costUSD": first_total}})
        first["startTime"] = first_start
        second = cost_state({"claude-opus-5-5[1m]": {"inputTokens": 12, "outputTokens": 6_032,
                                                      "cacheReadInputTokens": 2_681_985,
                                                      "cacheCreationInputTokens": 511_828, "costUSD": resumed_total}})
        second["startTime"] = resumed_start
        return [
            message("claude-opus-5", usage(input=2_028, flat_write=1_041_092, read=94_912_510, output=204_467), "a1"),
            first, dict(first),
            message("claude-opus-5-5", usage(input=12, flat_write=511_828, read=2_681_985, output=6_032), "b1",
                    at="2026-01-01T00:30:00.000Z"),
            second,
        ]

    def test_a_resumed_session_s_cost_is_its_segments_added_up_and_exact(self):
        self.write_transcript(self.resumed())
        body = self.beat_across([SESSION])
        # 184.3217 + 4.751709 = 189.07, the bill; not 4.75, not 373.39.
        self.assertAlmostEqual(body["costUsd"], 189.07, places=2)
        self.assertIs(body["costExact"], True)

    def test_a_resumed_segment_with_no_total_yet_is_not_exact(self):
        """Resumed and still working: the first segment's total is not the bill."""
        lines = self.resumed()[:-1]
        self.write_transcript(lines)
        body = self.beat_across([SESSION])
        self.assertIs(body["costExact"], False)
        self.assertNotAlmostEqual(body["costUsd"], 184.32, places=2)

    def test_a_run_across_a_resumed_session_and_another_is_exact_as_their_sum(self):
        """costExact across sessions: every session has its bill, one of them in two segments."""
        other = "99999999-8888-7777-6666-555555555555"
        finished = cost_state({"claude-opus-5-5": {"inputTokens": 1, "outputTokens": 0, "cacheReadInputTokens": 0,
                                                   "cacheCreationInputTokens": 0, "costUSD": 10.0}})
        self.write_session(other, [message("claude-opus-5-5", usage(output=100_000), "o1"),
                                   dict(finished, sessionId=other, startTime=1)])
        self.write_transcript(self.resumed())
        body = self.beat_across([other, SESSION])
        self.assertIs(body["costExact"], True)
        self.assertAlmostEqual(body["costUsd"], 199.07, places=2)

    def test_an_unknown_cost_sends_neither_a_cost_nor_whether_it_is_exact(self):
        self.write_transcript([message("claude-mystery-9", usage(output=100_000), "m1")])
        body = self.beat_across([SESSION])
        self.assertNotIn("costUsd", body)
        self.assertNotIn("costExact", body)

    # ---------- the two cache writes ----------

    def test_a_one_hour_write_is_twice_input_and_a_five_minute_write_is_one_and_a_quarter(self):
        """For every model in the table, a million 1-hour write tokens cost twice a
        million input tokens, and a million 5-minute ones 1.25 times."""
        for model, input_price in (("claude-opus-5-5", 4.00), ("claude-sonnet-5", 2.00),
                                   ("claude-fable-5-1", 10.00), ("claude-haiku-4-5", 1.00),
                                   ("claude-haiku-4-5-20251001", 1.00)):
            with self.subTest(model):
                self.assertAlmostEqual(pipeline.price_message(model, usage(input=1_000_000)), input_price)
                self.assertAlmostEqual(pipeline.price_message(model, usage(write_1h=1_000_000)), 2 * input_price)
                self.assertAlmostEqual(pipeline.price_message(model, usage(write_5m=1_000_000)), 1.25 * input_price)
                self.assertAlmostEqual(pipeline.price_message(model, usage(flat_write=1_000_000)), 1.25 * input_price)

    def test_every_price_in_the_table_is_the_plans_price(self):
        """Gate 6, C13 and C15. Each of the five prices of every model, one token
        kind at a time, against the plan's table written out here. The loop is
        over `pipeline.PRICES` itself, so a model added to the code with no row
        here fails by name rather than going unpinned."""
        # Dollars per million: input, 5-minute write, 1-hour write, cache read, output.
        plan = {
            "claude-opus-5-5": (4.00, 5.00, 8.00, 0.20, 20.00),
            "claude-sonnet-5": (2.00, 2.50, 4.00, 0.20, 10.00),
            "claude-fable-5-1": (10.00, 12.50, 20.00, 0.25, 50.00),
            "claude-haiku-4-5": (1.00, 1.25, 2.00, 0.10, 5.00),
            "claude-haiku-4-5-20251001": (1.00, 1.25, 2.00, 0.10, 5.00),
        }
        kinds = (("input", usage(input=1_000_000)), ("5-minute write", usage(write_5m=1_000_000)),
                 ("1-hour write", usage(write_1h=1_000_000)), ("cache read", usage(read=1_000_000)),
                 ("output", usage(output=1_000_000)))
        self.assertGreaterEqual(len(pipeline.PRICES), 5, "the price table is nearly empty")
        for model in pipeline.PRICES:
            self.assertIn(model, plan, f"{model} is priced in pipeline.py and pinned by nothing here")
            for (kind, one_million), price in zip(kinds, plan[model]):
                with self.subTest(model=model, kind=kind):
                    self.assertAlmostEqual(pipeline.price_message(model, one_million), price)

    def test_two_cost_state_keys_with_one_base_are_pooled_into_one_rate(self):
        """Gate 6, C12. A session that switched context size writes both
        `claude-opus-5-5` and `claude-opus-5-5[1m]` in one `modelUsage`. The
        messages say `claude-opus-5-5`, so they are priced at the pooled rate:
        both keys' dollars over both keys' weighted tokens, not whichever key
        was read last and not either one alone."""
        self.write_transcript([
            message("claude-opus-5-5", usage(input=1_000_000), "m1"),
            cost_state({
                # $10 for 1,000,000 weighted: $0.00001 each.
                "claude-opus-5-5": {"inputTokens": 1_000_000, "outputTokens": 0, "cacheReadInputTokens": 0,
                                    "cacheCreationInputTokens": 0, "costUSD": 10.0},
                # $30 for 1,000,000 weighted: $0.00003 each.
                "claude-opus-5-5[1m]": {"inputTokens": 1_000_000, "outputTokens": 0, "cacheReadInputTokens": 0,
                                        "cacheCreationInputTokens": 0, "costUSD": 30.0},
            }),
        ])
        # Pooled: $40 over 2,000,000 weighted = $0.00002 each, so the message's
        # 1,000,000 weighted is $20.00. Either key alone says $10 or $30, and
        # the table $4.
        t = self.usage_of_one_phase()["telemetry"]
        self.assertTrue(t["cost_known"], t.get("cost_note"))
        self.assertAlmostEqual(t["cost_usd"], 20.00, places=2)

    # ---------- Gate 6: a <synthetic> line costs nothing ----------
    #
    # Claude Code writes an assistant line with `model: "<synthetic>"` and an
    # all-zero usage for "API Error: 500", "went to sleep mid-response" and
    # "Login expired". A survey of this machine's transcripts on 2026-09-26
    # found 62 such lines in 32 files, every one of them all zero, shaped as
    # SYNTHETIC_USAGE below (copied from a real line, content left out).

    SYNTHETIC_USAGE = {"cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 0},
                       "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "inference_geo": None,
                       "input_tokens": 0, "iterations": None, "output_tokens": 0, "output_tokens_details": None,
                       "server_tool_use": {"web_fetch_requests": 0, "web_search_requests": 0},
                       "service_tier": None, "speed": None}

    def synthetic(self, mid, at="2026-01-01T00:02:00.000Z", usage_=None):
        line = message("<synthetic>", usage_ or self.SYNTHETIC_USAGE, mid, at=at)
        line["message"]["content"] = [{"type": "text", "text": "API Error: 500"}]
        return line

    def test_a_synthetic_line_with_no_usage_costs_nothing_and_leaves_the_cost_known(self):
        """One API error in a session used to make the whole run's cost unknown.
        It is skipped: the run, the phase and a subagent keep their price."""
        self.write_transcript([message("claude-opus-5-5", usage(input=500_000, output=100_000), "m1"),
                               self.synthetic("syn-1")])
        self.write_subagent("a1", [message("claude-sonnet-5", usage(output=100_000), "sub-1"),
                                   self.synthetic("syn-2")])
        # m1: 0.5 * 4 + 0.1 * 20 = 4.00. sub-1: 0.1 * 10 = 1.00.
        run = self.usage_of_one_phase()
        for where, t in (("run", run["telemetry"]), ("phase", run["phases"][0]["telemetry"])):
            with self.subTest(where):
                self.assertTrue(t["cost_known"], t.get("cost_note"))
                self.assertAlmostEqual(t["cost_usd"], 5.00, places=2)
        [row] = run["telemetry"]["agent_rows"]
        self.assertAlmostEqual(row["cost_usd"], 1.00, places=2)
        self.assertEqual(row["model"], "claude-sonnet-5")
        self.assertAlmostEqual(pipeline.beat_totals(run)[1], 5.00, places=2)

    def test_a_synthetic_line_after_the_total_leaves_the_total_exact(self):
        """A zero-usage line spent nothing, so a total written before it is still the bill."""
        self.write_transcript([
            message("claude-opus-5-5", usage(input=500_000, output=100_000), "m1"),
            dict(cost_state({"claude-opus-5-5": {"inputTokens": 1_000_000, "outputTokens": 0,
                                                  "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                                                  "costUSD": 10.0}}), totalCostUSD=270.84),
            self.synthetic("syn-1", at="2026-01-01T00:03:00.000Z"),
        ])
        body = self.beat_across([SESSION])
        self.assertIs(body["costExact"], True)
        self.assertEqual(body["costUsd"], 270.84)

    def test_a_synthetic_line_that_did_spend_is_still_unpriced(self):
        """Only an all-zero synthetic line is nothing. One that carries tokens
        (none has been seen, but nothing says it cannot) has no price, so the
        cost is unknown, as for any unpriced model."""
        self.write_transcript([message("claude-opus-5-5", usage(output=100_000), "m1"),
                               self.synthetic("syn-1", usage_=dict(self.SYNTHETIC_USAGE, output_tokens=7))])
        t = self.usage_of_one_phase()["telemetry"]
        self.assertFalse(t["cost_known"])
        self.assertIsNone(t["cost_usd"])
        self.assertIn("<synthetic>", t["cost_note"])

    def test_a_suffixed_or_dated_model_is_priced_as_its_base(self):
        """`[1m]` is dropped, and a date the table does not list is its base."""
        self.assertAlmostEqual(pipeline.price_message("claude-opus-5-5[1m]", usage(output=1_000_000)), 20.00)
        self.assertAlmostEqual(pipeline.price_message("claude-sonnet-5-20260101", usage(output=1_000_000)), 10.00)
        self.assertIsNone(pipeline.price_message("claude-mystery-9", usage(output=1_000_000)))


if __name__ == "__main__":
    unittest.main()
