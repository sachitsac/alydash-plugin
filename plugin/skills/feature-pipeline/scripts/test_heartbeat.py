#!/usr/bin/env python3
"""The desk heartbeat, tested against a local listener.

Every test runs pipeline.py as a subprocess against a scratch run under a
temporary --root, so no test can reach a real board. The listener is an
http.server on an ephemeral port that records what arrived and answers the way
the desk's route answers, which is the only way to tell "made no request" from
"made a request nobody was listening for".

  python3 -m unittest test_heartbeat -v

Python 3.9+, stdlib only.
"""
import argparse
import glob
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = os.path.join(HERE, "pipeline.py")

URL_VARIABLE = "ALYDASH_URL"
TOKEN_VARIABLE = "ALYDASH_SESSION_TOKEN"
WORK_ITEM_VARIABLE = "ALYDASH_WORK_ITEM"
# ALYDAS-127, S8. A runner that starts a session from the desk exports these;
# taken out of every run like the others, so a suite run inside such a session
# cannot carry its start or budget into a case.
RUN_START_VARIABLE = "ALYDASH_RUN_START"
RUN_BUDGET_VARIABLE = "ALYDASH_RUN_BUDGET"
RUN_VARIABLES = (RUN_START_VARIABLE, RUN_BUDGET_VARIABLE)

# urllib reads these from the environment. They are taken out of every run so
# a proxy on the machine running the suite cannot decide where a request goes,
# and handed back by the one case that uses a local listener as the proxy.
PROXY_VARIABLES = ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY",
                   "all_proxy", "ALL_PROXY", "no_proxy", "NO_PROXY")


def subject_key(body):
    """ALYDAS-303, S9. The key of the work item a beat names, or None: the beat
    names it as `subject: {kind: 'work_item', key}`, never as `workItem`."""
    subject = body.get("subject")
    return subject.get("key") if isinstance(subject, dict) else None


class Recorder(BaseHTTPRequestHandler):
    """Records every request and answers the way the route does, or with a redirect.

    Every method is recorded, not only POST. urllib follows a 302 or a 303 on a
    POST by sending a GET, so a listener that only heard POST would record
    nothing for a redirect that was followed, and a test of "nothing arrived"
    would pass on a request that did.
    """

    def record(self):
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        self.server.requests.append({
            "method": self.command,
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "raw": raw.decode("utf-8", "replace"),
            # ALYDAS-175, Gate 8 (S-1): when it arrived, for a test of a wait.
            "at": time.monotonic(),
        })
        # Gate 6 residual R13. Whatever the test wants read off disk at the
        # moment the request arrives, read here rather than after the process
        # exits, because the order the ledger does its three things in, save,
        # render, then report, is only visible from inside the request.
        if self.server.watch is not None:
            self.server.observed.append(self.server.watch())
        # ALYDAS-175, S2. A route the test named answers for its method and
        # path (the query left off); every other request gets the one canned
        # reply, as before. A test can change a route while a run waits.
        route = (getattr(self.server, "routes", None) or {}).get((self.command, self.path.split("?", 1)[0]))
        status, reply, *extra = route if route is not None else (self.server.status, self.server.reply)
        # ALYDAS-299, Gate 8 (code #3). A route's reply may be a function of
        # the request's path, query included, for a read that names an item.
        if callable(reply):
            reply = reply(self.path)
        # A desk that cannot link an item refuses the whole beat naming it.
        unlinked = getattr(self.server, "unlinked", None)
        if unlinked:
            try:
                named = subject_key(json.loads(raw.decode("utf-8")))
            except Exception:
                named = None
            if named in unlinked:
                status, reply, extra = 404, {"error": "There is no such item."}, []
        body = json.dumps(reply).encode("utf-8")
        self.send_response(status)
        # Gate 8 (S-1): a route may name headers too, a 429's Retry-After.
        for name, value in (extra[0] if extra else {}).items():
            self.send_header(name, value)
        if self.server.location is not None:
            self.send_header("location", self.server.location)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = record
    do_GET = record
    do_PUT = record
    do_HEAD = record
    do_CONNECT = record

    def log_message(self, *args):
        pass


class SixServer(ThreadingHTTPServer):
    """The same server on the IPv6 loopback."""
    address_family = socket.AF_INET6


class Listener:
    """A recording desk on an ephemeral port."""

    def __init__(self, status=200, reply=None, watch=None, location=None, routes=None):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
        self.server.location = location
        self.server.routes = dict(routes or {})
        self.server.requests = []
        self.server.status = status
        self.server.reply = reply if reply is not None else {"session": {"id": "s1"}}
        self.server.watch = watch
        self.server.observed = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def port(self):
        return self.server.server_address[1]

    @property
    def requests(self):
        return self.server.requests

    def answer(self, method, path, status, reply, headers=None):
        """ALYDAS-175, S2. From now on, `method path` gets this status and reply
        (and, Gate 8, these headers)."""
        self.server.routes[(method, path)] = (status, reply, dict(headers or {}))

    def clear(self):
        self.server.requests = []
        self.server.observed = []

    @property
    def observed(self):
        return self.server.observed

    def bodies(self):
        return [json.loads(r["raw"]) for r in self.requests]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


# The desk this ledger reports to, for the guards that read its source. The
# bounds tables in pipeline.py are hand copies of the desk's (`HEARTBEAT_BOUNDS`
# in `packages/modules/runs/src/heartbeat-body.ts`, `LEDGER_BOUNDS` and
# `BUDGET_FLOOR_USD` in `packages/modules/runs/src/ledger-bounds.ts`, and the
# body ceiling `HEARTBEAT_BODY_MAX` in
# `packages/module-kit/src/http/request-bounds.ts`), and a hand copy with
# nothing pinning it is a second declaration of one number.
#
# ALYDAS-303, S9. They used to be read from `packages/core/src/heartbeat.ts`
# and `apps/desk/lib/request-bounds.ts`. The heartbeat's body and the ledger's
# wall are the runs module's now (ALYDAS-303, S2), and the body ceiling is the
# module kit's (the desk's file only re-exports it, which a pin cannot read).
#
# ALYDASH_REPO names the checkout. Without it the pins read the checkout this
# desk lives in on the machine that wrote the skill, which may be on a branch
# that predates a constant.
#
# Gate 6 (ALYDAS-135, P01b, P02 and P03). What each pin does when it cannot
# read the desk is now one rule, in `desk_constant`:
#   - the constant is not declared in the checkout read: a skip when
#     ALYDASH_REPO is unset, whose reason names the checkout and is printed to
#     stderr so the plain invocation shows it, and a failure when ALYDASH_REPO
#     names that checkout, because then it is the desk under test;
#   - the constant is declared but not in a shape the pin can read (a type
#     annotation or `as const satisfies ...` it now reads; a named constant as
#     a value, a spread, a computed key it does not): always a failure. A pin
#     that skips because the desk changed its formatting is a pin a drift ships
#     past, which is how the P03 compound shipped green.
DESK_REPO_NAMED = bool(os.environ.get("ALYDASH_REPO"))
DESK_REPO = os.environ.get("ALYDASH_REPO") or os.path.expanduser("~/workspace/alydash")
DESK_HEARTBEAT_BODY = os.path.join("packages", "modules", "runs", "src", "heartbeat-body.ts")
DESK_LEDGER_BOUNDS = os.path.join("packages", "modules", "runs", "src", "ledger-bounds.ts")
DESK_REQUEST_BOUNDS = os.path.join("packages", "module-kit", "src", "http", "request-bounds.ts")
# ALYDAS-303, S9. The kind of subject a beat names its work item as, which
# runs' Work integration declares (`WORK_ITEM`).
DESK_RUNS_WORK = os.path.join("packages", "modules", "runs", "src", "integrations", "work.ts")
# ALYDAS-174, Gate 6, M02. Where the desk declares a review finding's groups.
DESK_RUNS_TYPES = os.path.join("packages", "modules", "runs", "src", "types.ts")


class DeskAbsent(Exception):
    """The checkout read does not declare the constant."""


class DeskUnreadable(Exception):
    """The checkout declares the constant, in a shape this reader cannot read."""


def desk_checkout_words(repo, named):
    return (f"the checkout ALYDASH_REPO names, {repo}" if named
            else f"the default checkout {repo}, because ALYDASH_REPO is unset")


def _declaration(repo, named, relative, name):
    """(path, the source from `export const <name>` on). Raises DeskAbsent."""
    path = os.path.join(repo, relative)
    where = desk_checkout_words(repo, named)
    if not os.path.exists(path):
        raise DeskAbsent(f"{name} was not read: {relative} is not in {where}")
    with open(path, encoding="utf-8") as f:
        source = f.read()
    found = re.search(rf"\bexport\s+(?:const|(?:async\s+)?function)\s+{name}\b", source)
    if found is None:
        raise DeskAbsent(f"{name} is not declared in {path}, in {where}")
    return path, source[found.start():]


def _no_comments(text):
    return re.sub(r"//[^\n]*", "", re.sub(r"/\*.*?\*/", "", text, flags=re.S))


def read_desk_table(repo, named, relative, name):
    """`export const <name> = { key: 1_000, ... }` as a dict, whatever follows the
    closing brace. Raises DeskAbsent, or DeskUnreadable naming what it could not read."""
    path, source = _declaration(repo, named, relative, name)
    block = re.match(rf"export\s+const\s+{name}\b[^=]*=\s*\{{(.*?)\}}", source, re.S)
    if block is None:
        raise DeskUnreadable(f"{name} is declared in {path} but not as an object literal this pin can read")
    table = {}
    for entry in (e.strip() for e in _no_comments(block.group(1)).split(",")):
        if not entry:
            continue
        pair = re.fullmatch(r"(\w+)\s*:\s*([0-9][0-9_]*)", entry)
        if pair is None:
            raise DeskUnreadable(f"{name} in {path} has an entry this pin cannot read as a number: {entry!r}")
        table[pair.group(1)] = int(pair.group(2).replace("_", ""))
    if not table:
        raise DeskUnreadable(f"{name} in {path} was read as empty")
    return table


def read_desk_number(repo, named, relative, name):
    """`export const <name> = 262_144;`, with or without a type or `as const`."""
    path, source = _declaration(repo, named, relative, name)
    found = re.match(rf"export\s+const\s+{name}\b[^=]*=\s*([0-9][0-9_]*)\s*(?:as\s+const\s*)?;", source)
    if found is None:
        raise DeskUnreadable(f"{name} is declared in {path} but not as a number this pin can read")
    return int(found.group(1).replace("_", ""))


def read_desk_decimal(repo, named, relative, name):
    """`export const <name> = 0.01;`, a number that may have a fraction (ALYDAS-17 G8)."""
    path, source = _declaration(repo, named, relative, name)
    found = re.match(rf"export\s+const\s+{name}\b[^=]*=\s*([0-9][0-9_]*(?:\.[0-9_]+)?)\s*(?:as\s+const\s*)?;", source)
    if found is None:
        raise DeskUnreadable(f"{name} is declared in {path} but not as a number this pin can read")
    return float(found.group(1).replace("_", ""))


def read_desk_string(repo, named, relative, name):
    """`export const <name> = 'work_item';`, a string in single or double quotes (ALYDAS-303, S9)."""
    path, source = _declaration(repo, named, relative, name)
    found = re.match(rf"export\s+const\s+{name}\b[^=]*=\s*(['\"])([^'\"\\\n]*)\1\s*(?:as\s+const\s*)?;", source)
    if found is None:
        raise DeskUnreadable(f"{name} is declared in {path} but not as a string this pin can read")
    return found.group(2)


def read_desk_strings(repo, named, relative, name):
    """`export const <name> = ['a', 'b'] as const;`, a list of plain strings (ALYDAS-174, Gate 6, M02)."""
    path, source = _declaration(repo, named, relative, name)
    found = re.match(rf"export\s+const\s+{name}\b[^=]*=\s*\[([^\]]*)\]\s*(?:as\s+const\s*)?;", source)
    if found is None:
        raise DeskUnreadable(f"{name} is declared in {path} but not as a list this pin can read")
    entries = [e.strip() for e in _no_comments(found.group(1)).split(",") if e.strip()]
    strings = [re.fullmatch(r"(['\"])([^'\"\\\n]*)\1", e) for e in entries]
    if not entries or None in strings:
        raise DeskUnreadable(f"{name} in {path} is not a list of plain strings this pin can read")
    return [m.group(2) for m in strings]


def desk_constant(case, reader, relative, name, repo=None, named=None):
    """`reader` against DESK_REPO, with the rule above: absent skips (loudly) or
    fails, unreadable fails. `repo` and `named` are for the reader's own cases."""
    real = repo is None
    repo = DESK_REPO if real else repo
    named = DESK_REPO_NAMED if named is None else named
    try:
        return reader(repo, named, relative, name)
    except DeskUnreadable as e:
        case.fail(str(e))
    except DeskAbsent as e:
        if named:
            case.fail(str(e))
        reason = f"{e}. Set ALYDASH_REPO to a desk checkout that has it to run this pin."
        if real:
            print(f"\n{case.id()}: skipped: {reason}", file=sys.stderr)
        case.skipTest(reason)


def desk_bounds(case):
    """HEARTBEAT_BOUNDS as the desk declares it, read out of the TypeScript."""
    return desk_constant(case, read_desk_table, DESK_HEARTBEAT_BODY, "HEARTBEAT_BOUNDS")


# What JavaScript's `String.prototype.trim` takes off, which is what the desk
# trims before it counts or asks whether a string is empty. Python's `strip`
# takes all of these but U+FEFF.
JS_SPACE = ("\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008"
            "\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff")


def js_blank(text):
    """Whether the desk reads `text` as empty once it has trimmed it."""
    return not isinstance(text, str) or text.strip(JS_SPACE) == ""


DESK_REFUSAL_SCRIPT = """
const { pathToFileURL } = require('node:url');
const { readFileSync } = require('node:fs');
import(pathToFileURL(process.argv[1]).href).then((core) => {
  const bodies = JSON.parse(readFileSync(process.argv[2], 'utf8'));
  process.stdout.write(JSON.stringify(bodies.map((body) => core.heartbeatRefusal(body))));
});
"""


def desk_refusals(case, bodies):
    """The desk's own `heartbeatRefusal` in `packages/modules/runs/src/heartbeat-body.ts`, run
    by node over `bodies`: one refusal sentence or None each. Found by the rule
    `desk_constant` keeps, so an absent desk skips (named) or fails."""
    def reader(repo, named, relative, name):
        path, _ = _declaration(repo, named, relative, name)
        return path
    source = desk_constant(case, reader, DESK_HEARTBEAT_BODY, "heartbeatRefusal")
    folder = tempfile.mkdtemp()
    case.addCleanup(__import__("shutil").rmtree, folder, True)
    with open(os.path.join(folder, "bodies.json"), "w", encoding="utf-8") as f:
        json.dump(bodies, f, ensure_ascii=False)
    try:
        out = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", "-e", DESK_REFUSAL_SCRIPT,
                              source, os.path.join(folder, "bodies.json")],
                             capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        case.fail("node is not on the PATH, so the desk's heartbeatRefusal could not be run")
    case.assertEqual(out.returncode, 0, f"node could not run the desk's heartbeatRefusal: {out.stderr[-2000:]}")
    return json.loads(out.stdout)


# Gate 6 (ALYDAS-173). The local board as its own script draws it. The page
# is the template with the run's JSON in it, so a case that looks for strings
# in the page finds the template's headings and the run's words whatever the
# script does with them. This runs the page's script in node over the
# smallest DOM it touches, and hands back what it drew: the short version
# (None when the script removed the block), each rail tab's markup, and each
# panel's markup by its id, and (ALYDAS-206, S3) the run log's markup.
# `parse_html` reads the markup back.
BOARD_DOM_SCRIPT = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const data = fs.readFileSync(process.argv[1], 'utf8');
const code = fs.readFileSync(process.argv[2], 'utf8');
const byId = {};
function element(tag, id) {
  const e = { tag, id: id || '', innerHTML: '', textContent: '', className: '', hidden: false, tabIndex: 0,
    attrs: {}, children: [], removed: false,
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; },
    appendChild(c) { this.children.push(c); if (c.id) byId[c.id] = c; return c; },
    remove() { this.removed = true; }, addEventListener() {}, focus() {}, scrollIntoView() {} };
  if (id) byId[id] = e;
  return e;
}
element('script', 'run-data').textContent = data;
for (const id of ['head', 'short', 'strip', 'rail', 'timeline', 'panels', 'log']) element('section', id);
const summary = element('summary');
const document = { getElementById: (id) => byId[id] || null, createElement: (tag) => element(tag),
  querySelector: (selector) => (selector === 'details > summary' ? summary : null) };
vm.runInNewContext(code, { document, location: { hash: '' }, history: { replaceState() {} } });
const drawn = { short: byId.short.removed ? null : byId.short.innerHTML,
  rail: byId.rail.children.map((tab) => tab.innerHTML), panels: {}, log: byId.log.innerHTML };
for (const panel of byId.panels.children) drawn.panels[panel.id] = panel.innerHTML;
process.stdout.write(JSON.stringify(drawn));
"""


def render_board_in_node(case, page):
    """What the board page's own script draws, run by node."""
    data = re.search(r'<script id="run-data" type="application/json">(.*?)</script>', page, re.S)
    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    case.assertIsNotNone(data, "the board has no run-data script")
    case.assertTrue(scripts, "the board has no script")
    folder = tempfile.mkdtemp()
    case.addCleanup(__import__("shutil").rmtree, folder, True)
    for name, text in (("data.json", data.group(1)), ("board.js", scripts[-1])):
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            f.write(text)
    try:
        out = subprocess.run(["node", "-e", BOARD_DOM_SCRIPT, os.path.join(folder, "data.json"),
                              os.path.join(folder, "board.js")], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        case.fail("node is not on the PATH, so the board's script could not be run")
    case.assertEqual(out.returncode, 0, f"the board's script failed in node: {out.stderr[-2000:]}")
    return json.loads(out.stdout)


class DrawnNode:
    """One element of drawn markup: its tag, attributes and children (text or elements)."""

    def __init__(self, tag, attrs):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def elements(self):
        return [c for c in self.children if isinstance(c, DrawnNode)]

    def text(self):
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def walk(self):
        for c in self.elements():
            yield c
            yield from c.walk()

    def find(self, tag):
        return [n for n in self.walk() if n.tag == tag]

    def by_class(self, name):
        return [n for n in self.walk() if name in (n.attrs.get("class") or "").split()]

    def first(self, name):
        """The text of the first element of class `name` inside, or None."""
        found = self.by_class(name)
        return found[0].text() if found else None

    def children_named(self, tag):
        return [c for c in self.elements() if c.tag == tag]

    def heading(self, name):
        found = [h for h in self.find("h3") if h.children and h.children[0] == name]
        assert len(found) == 1, f"{len(found)} headings named {name!r}"
        return found[0]

    def under_headings(self):
        """Each top-level h3's own text, and the element drawn after it."""
        out, tops = {}, self.elements()
        for i, node in enumerate(tops):
            if node.tag == "h3" and node.children and isinstance(node.children[0], str):
                out[node.children[0]] = tops[i + 1] if i + 1 < len(tops) else None
        return out


def parse_html(markup):
    """Drawn markup as a DrawnNode tree under a root."""
    from html.parser import HTMLParser

    class Reader(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack = [DrawnNode("root", {})]

        def handle_starttag(self, tag, attrs):
            node = DrawnNode(tag, attrs)
            self.stack[-1].children.append(node)
            if tag not in ("br", "img", "input", "hr", "meta", "link"):
                self.stack.append(node)

        def handle_endtag(self, tag):
            while len(self.stack) > 1:
                if self.stack.pop().tag == tag:
                    break

        def handle_data(self, data):
            self.stack[-1].children.append(data)

    reader = Reader()
    reader.feed(markup)
    reader.close()
    return reader.stack[0]


def items(ul):
    """Each list item's text."""
    return [li.text() for li in ul.children_named("li")]


def pipeline_module():
    """pipeline.py, imported, so its own tables can be read rather than retyped."""
    sys.path.insert(0, HERE)
    import pipeline
    return pipeline


def closed_port():
    """A port nothing is listening on: bound, read, and given back."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ALYDAS-174, S1. A review record as `review record` writes it on Phase 8.
A_RECORD = {
    "base": "a" * 40, "head": "b" * 40,
    "areas": [{"name": "Scratch", "patterns": ["**"],
               "files": [{"path": "tool.py", "added": 1, "removed": 0, "test": False}],
               "added": 1, "removed": 0, "testAdded": 0, "testRemoved": 0}],
    "findings": [{"id": "F1", "title": "One thing to do later", "plain": "A scratch finding.", "why": "None.",
                  "how": "Read it.", "by": "a reviewer", "group": "later", "blocks": False}],
    "proofs": [], "tests": {"command": "true", "passed": 1, "failed": 0, "skipped": 0},
    "verdict": "Nothing blocks the merge.",
}


class ScratchRunCase(unittest.TestCase):
    """A scratch run under a temporary root, and pipeline.py run against it.
    ALYDAS-175, S2: `HeartbeatCase`'s own helpers, shared with `HoldCase`
    without inheriting (and so running again) every heartbeat test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "docs")
        self.addCleanup(self.tmp.cleanup)

    def run_pipeline(self, *args, env=None):
        """pipeline.py under the temp root, in the temp directory, never in a repo."""
        environment = dict(os.environ)
        # ALYDASH_CREDENTIALS too: a plugin session exports it, and a store
        # found through it would make a case with no token configured.
        for name in (URL_VARIABLE, TOKEN_VARIABLE, "ALYDASH_CREDENTIALS", WORK_ITEM_VARIABLE,
                     "CLAUDE_CODE_SESSION_ID") + PROXY_VARIABLES + RUN_VARIABLES:
            environment.pop(name, None)
        environment.update(env or {})
        return subprocess.run(
            [sys.executable, PIPELINE, "--root", self.root] + list(args),
            capture_output=True, text=True, cwd=self.tmp.name, env=environment,
        )

    def init_scratch(self, *extra):
        """ALYDAS-175, S5: `extra` goes on the `init` line, for `--hold none`
        where a test closes Phase 2 or starts a later phase directly."""
        out = self.run_pipeline(
            "init", "scratch",
            "--title", "Scratch",
            "--goal", "A scratch run the heartbeat tests write to.",
            "--branch", "work-desk-S8",
            "--date", "2026-09-13",
            *extra,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        return out

    # ALYDAS-206, S1. The repository helpers, moved here from `HeartbeatCase`
    # so the hold cases run on a real feature branch with a bare origin too.
    # Nothing here can reach a real remote: the only remote any case names is
    # a path under the case's own temp directories.

    def git(self, *args, cwd=None):
        environment = dict(os.environ)
        environment.update({
            "GIT_AUTHOR_NAME": "Scratch", "GIT_AUTHOR_EMAIL": "scratch@example.invalid",
            "GIT_COMMITTER_NAME": "Scratch", "GIT_COMMITTER_EMAIL": "scratch@example.invalid",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        })
        out = subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"] + list(args),
            cwd=cwd or self.tmp.name, capture_output=True, text=True, env=environment,
        )
        self.assertEqual(out.returncode, 0, f"git {' '.join(args)}: {out.stderr}")
        return out.stdout.strip()

    def record_a_review(self, slug="scratch"):
        """ALYDAS-174, S1. A well-formed Gate 8 review on Phase 8 of the
        ledger, written there directly, for the cases about something else that
        need Phase 8 otherwise ready (Phase 8 does not close or hold without
        one). `test_review` drives `review record` itself."""
        path = sorted(glob.glob(os.path.join(self.root, "boards", f"*-{slug}.json")))[-1]
        with open(path, encoding="utf-8") as f:
            run = json.load(f)
        self.assertEqual(pipeline_module().review_problems(A_RECORD), [])
        record = json.loads(json.dumps(A_RECORD))
        # Gate 8, CR-1: Phase 8 takes a record made at HEAD only, so in a case
        # with a repository the record names its HEAD, as `review record` would.
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.tmp.name, capture_output=True, text=True)
        if head.returncode == 0:
            record["head"] = head.stdout.strip()
        next(p for p in run["phases"] if p["n"] == 8)["review"] = record
        with open(path, "w", encoding="utf-8") as f:
            json.dump(run, f, indent=1, ensure_ascii=False)

    def commit(self, name):
        with open(os.path.join(self.tmp.name, name), "w", encoding="utf-8") as f:
            f.write(name + "\n")
        self.git("add", name)
        self.git("commit", "-q", "-m", name)
        return self.git("rev-parse", "HEAD")

    def repo_with_origin(self, default="main"):
        """The temp directory as a repository on `default`, and a bare clone of it as origin.

        Answers the bare origin's path. `refs/remotes/origin/HEAD` names the
        default, the way a clone from GitHub has it.
        """
        self.git("init", "-q", "-b", default)
        self.commit("first.txt")
        bare = tempfile.TemporaryDirectory()
        self.addCleanup(bare.cleanup)
        origin = os.path.join(bare.name, "origin.git")
        self.git("clone", "-q", "--bare", self.tmp.name, origin, cwd=bare.name)
        self.git("remote", "add", "origin", origin)
        self.git("fetch", "-q", "origin")
        self.git("remote", "set-head", "origin", default)
        return origin

    def origin_refs(self, origin):
        """Every branch on the bare origin, by name, with the commit it points at."""
        listed = self.git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", cwd=origin)
        return dict(line.split(" ", 1) for line in listed.splitlines())

    def on_a_feature_branch(self, origin, branch="feature/alydas-135-s6"):
        """A feature branch that tracks origin/main, as this repository's do, with
        `push.default=upstream`, so a bare `git push` from it would move main."""
        self.git("checkout", "-q", "-b", branch)
        self.git("branch", "-q", "--set-upstream-to", "origin/main")
        self.git("config", "push.default", "upstream")
        return self.commit("second.txt")


class HeartbeatCase(ScratchRunCase):
    # ---------- test 1 ----------

    def test_unset_variables_make_no_request(self):
        """With the variables unset, a phase boundary makes no request."""
        self.init_scratch()
        with Listener() as desk:
            halves = {
                "both unset": {},
                "url only": {URL_VARIABLE: desk.url},
                "token only": {TOKEN_VARIABLE: "t0k3n"},
                "both empty": {URL_VARIABLE: "", TOKEN_VARIABLE: ""},
                "url set, token empty": {URL_VARIABLE: desk.url, TOKEN_VARIABLE: ""},
                "url empty, token set": {URL_VARIABLE: "", TOKEN_VARIABLE: "t0k3n"},
            }
            for name, env in halves.items():
                with self.subTest(name):
                    desk.clear()
                    out = self.run_pipeline("phase", "scratch", "0", "start", env=env)
                    self.assertEqual(out.returncode, 0, out.stderr)
                    self.assertEqual(desk.requests, [], f"{name} reached the desk")

    # ---------- test 2 ----------

    def test_both_set_posts_the_boundary(self):
        """With both set, phase start posts once with run, phase and statusLine,
        and phase 9 done posts ended true. ALYDAS-135, S8: never a boardUrl."""
        self.init_scratch()
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}

            out = self.run_pipeline("phase", "scratch", "0", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(len(desk.requests), 1, "phase start did not post exactly once")

            sent = desk.requests[0]
            self.assertEqual(sent["path"], "/api/sessions/heartbeat")
            self.assertEqual(sent["headers"]["authorization"], "bearer t0k3n")
            self.assertEqual(sent["headers"]["content-type"], "application/json")

            body = desk.bodies()[0]
            self.assertEqual(body["run"], "scratch")
            self.assertEqual(body["agent"], "feature-pipeline")
            self.assertEqual(body["statusLine"], "Phase 0 · Frame · running")
            self.assertEqual(body["phase"], "0 · Frame")
            self.assertEqual(body["branch"], "work-desk-S8")
            # ALYDAS-154, S2. The run's title and goal, off the ledger `init` wrote.
            self.assertEqual(body["title"], "Scratch")
            self.assertEqual(body["goal"], "A scratch run the heartbeat tests write to.")
            self.assertNotIn("boardUrl", body)
            self.assertEqual(body["ended"], False)
            self.assertNotIn("workItem", body)
            self.assertNotIn("subject", body)
            # ALYDAS-303, S9. The phases the run's arc has, on every beat.
            self.assertEqual(body["phaseRange"], [0, 9])
            self.assertIsInstance(body["tokens"], int)
            self.assertGreaterEqual(body["tokens"], 0)
            # ALYDAS-135, S4. A scratch run has no transcript, so its cost is
            # unknown, and unknown is no key rather than the 0.0 this used to
            # accept, which the desk printed as "$0.00".
            self.assertNotIn("costUsd", body)

            desk.clear()
            out = self.run_pipeline("phase", "scratch", "9", "done", "--force", "a scratch run", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(len(desk.requests), 1, "phase 9 done did not post exactly once")
            ended = desk.bodies()[0]
            self.assertEqual(ended["statusLine"], "Phase 9 · Finish · done")
            self.assertEqual(ended["ended"], True)
            self.assertEqual(ended["phaseRange"], [0, 9])

            desk.clear()
            out = self.run_pipeline("phase", "scratch", "8", "done", "--force", "a scratch run", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(desk.bodies()[0]["ended"], False, "only phase 9 done ends a run")

    def test_the_work_item_variable_is_carried(self):
        """ALYDASH_WORK_ITEM rides along when it is set, and nothing does when it is not."""
        self.init_scratch()
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n", WORK_ITEM_VARIABLE: "ALY-7"}
            out = self.run_pipeline("phase", "scratch", "0", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            body = desk.bodies()[0]
            # ALYDAS-303, S9. The item is the beat's subject, of the kind Work links.
            self.assertEqual(body["subject"], {"kind": "work_item", "key": "ALY-7"})
            self.assertNotIn("workItem", body)

    def init_named(self, slug, branch):
        out = self.run_pipeline("init", slug, "--title", "Named", "--goal", "A run whose name carries a key.",
                                "--branch", branch, "--date", "2026-09-13")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_with_no_variable_the_item_is_read_off_the_slug_then_the_branch(self):
        """`joone-2-confirm` names JOONE-2 with nothing set; the slug is read before the branch."""
        cases = {
            ("joone-2-confirm", "feature/other"): "JOONE-2",
            ("confirm-to-file", "feature/joone-2-confirm-to-file"): "JOONE-2",
            ("alydas-301-wave", "feature/joone-2"): "ALYDAS-301",
            ("release-2026", "feature/v2-10"): None,
            ("fix-utf8", "feature/item-007"): None,
        }
        for (slug, branch), expected in cases.items():
            with self.subTest(slug=slug, branch=branch), Listener() as desk:
                self.init_named(slug, branch)
                env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
                out = self.run_pipeline("phase", slug, "0", "start", env=env)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(subject_key(desk.bodies()[0]), expected)
                self.assertNotIn("workItem", desk.bodies()[0])

    def test_the_variable_outranks_the_name_and_is_never_dropped_as_a_guess(self):
        """A person's word is sent as it is: a refusal of it is not retried without it."""
        self.init_named("joone-2-confirm", "feature/joone-2")
        with Listener() as desk:
            desk.server.unlinked = {"ALY-7"}
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n", WORK_ITEM_VARIABLE: "ALY-7"}
            out = self.run_pipeline("phase", "joone-2-confirm", "0", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual([subject_key(body) for body in desk.bodies()], ["ALY-7"])
            self.assertIn("the desk did not take the heartbeat", out.stderr)

    def test_a_guess_the_desk_refuses_is_dropped_kept_and_never_sent_again(self):
        """A branch that only looks like a key costs one refused beat, then the run reports without it."""
        self.init_named("sha-256-fix", "feature/joone-2-sha-256")
        board = os.path.join(self.root, "boards", "2026-09-13-sha-256-fix.json")
        with Listener() as desk:
            desk.server.unlinked = {"SHA-256"}
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("phase", "sha-256-fix", "0", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            # Refused with the slug's key, taken with the branch's.
            self.assertEqual([subject_key(body) for body in desk.bodies()], ["SHA-256", "JOONE-2"])
            self.assertIn("could not link this run to SHA-256", out.stderr)
            self.assertNotIn("did not take the heartbeat", out.stderr)
            with open(board) as f:
                self.assertEqual(json.load(f)["item_guess_refused"], ["SHA-256"])
            self.assertIn("/runs/s1", out.stdout)

            desk.clear()
            out = self.run_pipeline("note", "sha-256-fix", "0", "--plain", "Framed.", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual([subject_key(body) for body in desk.bodies()], ["JOONE-2"])

            # Every key refused: the beat still lands, naming none.
            desk.clear()
            desk.server.unlinked = {"SHA-256", "JOONE-2"}
            out = self.run_pipeline("note", "sha-256-fix", "0", "--plain", "Framed again.", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual([subject_key(body) for body in desk.bodies()], ["JOONE-2", None])
            desk.clear()
            out = self.run_pipeline("note", "sha-256-fix", "0", "--plain", "Framed a third time.", env=env)
            self.assertEqual([subject_key(body) for body in desk.bodies()], [None])

    def test_every_beat_names_the_session_it_is_sent_from_and_is_blank_outside_one(self):
        """ALYDAS-264. CLAUDE_CODE_SESSION_ID rides every beat as `inSession`, the
        ended one too, so the desk can tell this run and the plugin's session run
        are one. Unset, or not the desk's shape for a session id, it is sent
        blank, never left out: blank is what moves a run out of a session."""
        self.init_scratch("--hold", "none")
        session = "3f9a2b1c-0d4e-4f5a-8b6c-7d8e9f0a1b2c"
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n", "CLAUDE_CODE_SESSION_ID": session}
            for args in (("phase", "scratch", "0", "start"), ("note", "scratch", "0", "--summary", "s"),
                         ("phase", "scratch", "9", "done", "--force", "a scratch run")):
                out = self.run_pipeline(*args, env=env)
                self.assertEqual(out.returncode, 0, out.stderr)
            bodies = desk.bodies()
            self.assertEqual(len(bodies), 3)
            self.assertEqual([body["inSession"] for body in bodies], [session] * 3)
            self.assertEqual(bodies[2]["ended"], True)

            for name, value in {"unset": None, "blank": "  ", "two words": "two words", "a path": "../x",
                                "too long": "x" * 129}.items():
                with self.subTest(name):
                    desk.clear()
                    env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
                    if value is not None:
                        env["CLAUDE_CODE_SESSION_ID"] = value
                    out = self.run_pipeline("note", "scratch", "0", "--summary", name, env=env)
                    self.assertEqual(out.returncode, 0, out.stderr)
                    self.assertEqual(desk.bodies()[0]["inSession"], "")

    def test_the_desk_takes_the_session_a_beat_names_and_refuses_what_is_sent_blank(self):
        """ALYDAS-264. The desk's own rule, wherever a desk checkout is to hand:
        a beat naming a session, and one sent blank, are both taken, and the ids
        this script blanks are ones the desk would refuse the whole beat for."""
        self.init_scratch("--hold", "none")
        session = "3f9a2b1c-0d4e-4f5a-8b6c-7d8e9f0a1b2c"
        with Listener() as desk:
            for value in (session, "x" * 128, None):
                env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
                if value is not None:
                    env["CLAUDE_CODE_SESSION_ID"] = value
                out = self.run_pipeline("note", "scratch", "0", "--summary", "s", env=env)
                self.assertEqual(out.returncode, 0, out.stderr)
            bodies = desk.bodies()
        self.assertEqual([body["inSession"] for body in bodies], [session, "x" * 128, ""])
        blanked = [dict(bodies[0], inSession=bad) for bad in ("two words", "../x", "x" * 129)]
        refusals = desk_refusals(self, bodies + blanked)
        self.assertEqual(refusals[:3], [None, None, None])
        self.assertTrue(all(refusals[3:]), f"the desk took an id this script blanks: {refusals[3:]}")

    # ---------- ALYDAS-135, S8: no artifact, and the Board line names the desk ----------

    def board_line(self, stdout):
        """The boundary block's Board line, or None when it has none."""
        lines = [line for line in stdout.splitlines() if line.startswith("Board ")]
        self.assertLessEqual(len(lines), 1, stdout)
        return lines[0][len("Board"):].strip() if lines else None

    def ledger_json(self):
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_no_beat_carries_a_board_url_even_when_an_old_ledger_has_one(self):
        """`set artifact_url` still stores the field, so an old ledger keeps working,
        but it sends nothing and no later beat carries `boardUrl`."""
        self.init_scratch()
        # Gate 8: `init` no longer writes the key, so it is put back the way a
        # ledger made before S8 carries it.
        ledger = self.ledger_json()
        ledger["artifact_url"] = ""
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.json"), "w", encoding="utf-8") as f:
            json.dump(ledger, f)
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("set", "scratch", "artifact_url", "https://claude.ai/code/artifact/old", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(self.ledger_json()["artifact_url"], "https://claude.ai/code/artifact/old")
            self.assertEqual(desk.requests, [], "set artifact_url still beats")
            for args in (("phase", "scratch", "0", "start"), ("phase", "scratch", "0", "done", "--force", "x"),
                         ("phase", "scratch", "9", "done", "--force", "x")):
                out = self.run_pipeline(*args, env=env)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertNotIn("claude.ai", out.stdout, "the old board URL is still printed")
            self.assertEqual(len(desk.bodies()), 3)
            for body in desk.bodies():
                self.assertNotIn("boardUrl", body)

    def test_a_new_ledger_has_no_artifact_url(self):
        """Gate 8 (S9's follow-up): the board is no longer published, so `init`
        writes no empty `artifact_url`, and `set` refuses it on a new ledger."""
        self.init_scratch()
        self.assertNotIn("artifact_url", self.ledger_json())
        out = self.run_pipeline("set", "scratch", "artifact_url", "https://claude.ai/code/artifact/new")
        self.assertNotEqual(out.returncode, 0)
        self.assertNotIn("artifact_url", self.ledger_json())

    def test_with_a_desk_the_board_line_is_the_run_s_own_page_with_or_without_a_work_item(self):
        """The run id comes back in the reply's `session.id`, is kept in the ledger,
        and the Board line is `<ALYDASH_URL>/runs/<id>`, on the block the beat
        prints and on a later `status`. ALYDAS-154, S2: a run has its own page,
        so a work item is no longer needed for the link, and naming one does not
        change it."""
        for name, extra in {"no work item": {}, "a work item": {WORK_ITEM_VARIABLE: "ALY-7"}}.items():
            with self.subTest(name):
                self.init_scratch_again()
                with Listener(reply={"session": {"id": "r_7Qx2"}}) as desk:
                    env = {URL_VARIABLE: desk.url + "/", TOKEN_VARIABLE: "t0k3n", **extra}
                    out = self.run_pipeline("phase", "scratch", "0", "start", env=env)
                    self.assertEqual(out.returncode, 0, out.stderr)
                    self.assertEqual(self.board_line(out.stdout), f"{desk.url}/runs/r_7Qx2")
                    self.assertEqual(self.ledger_json()["desk_run_id"], "r_7Qx2")
                    out = self.run_pipeline("status", "scratch", "0", env=env)
                    self.assertEqual(out.returncode, 0, out.stderr)
                    self.assertEqual(self.board_line(out.stdout), f"{desk.url}/runs/r_7Qx2")

    def test_without_a_desk_the_board_line_is_the_local_board(self):
        """No desk, or a desk that never said which run this is: the local HTML
        board's path."""
        local = os.path.join(self.root, "boards", "2026-09-13-scratch.html")
        with Listener(reply={"session": {"id": "r_1"}}) as desk:
            cases = {
                "no desk": {},
                "a work item and no desk": {WORK_ITEM_VARIABLE: "ALY-7"},
                "a desk that is down": {URL_VARIABLE: f"http://127.0.0.1:{closed_port()}", TOKEN_VARIABLE: "t0k3n",
                                        WORK_ITEM_VARIABLE: "ALY-7"},
            }
            for name, env in cases.items():
                with self.subTest(name):
                    self.init_scratch_again()
                    out = self.run_pipeline("phase", "scratch", "0", "start", env=env)
                    self.assertEqual(out.returncode, 0, out.stderr)
                    self.assertEqual(self.board_line(out.stdout), local)
                    self.assertTrue(os.path.exists(local))

    def test_a_run_id_that_is_not_a_plain_id_is_not_kept_or_printed(self):
        """The id goes into a printed link, so anything but letters, digits, `-` and
        `_` is refused and the Board line stays local."""
        local = os.path.join(self.root, "boards", "2026-09-13-scratch.html")
        for bad in ("a b", "x\u001b[31m", "../../evil", "", 7, None, "r" * 200):
            with self.subTest(bad=bad):
                self.init_scratch_again()
                with Listener(reply={"session": {"id": bad}}) as desk:
                    env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n", WORK_ITEM_VARIABLE: "ALY-7"}
                    out = self.run_pipeline("phase", "scratch", "0", "start", env=env)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(self.board_line(out.stdout), local)
                self.assertNotIn("desk_run_id", self.ledger_json())

    def test_the_board_link_never_prints_what_the_url_carries_besides_its_address(self):
        """A token pasted into ALYDASH_URL as a user, a query or a fragment stays out
        of the link, and so does its path. Gate 8, D3 (changed): the path used to
        be kept as a prefix the desk is served under, and a path can carry a
        secret, so the link is the desk's origin and `/runs/<id>` only."""
        from unittest import mock
        pipeline = pipeline_module()
        cases = {
            "http://t0k3n:x@127.0.0.1:3000/?key=t0k3n#t0k3n": "http://127.0.0.1:3000/runs/r_9",
            "https://desk.example/": "https://desk.example/runs/r_9",
            "https://desk.example/desk": "https://desk.example/runs/r_9",
            "https://desk.example:8443/s3cr3t/": "https://desk.example:8443/runs/r_9",
        }
        for url, link in cases.items():
            with self.subTest(url=url):
                env = {URL_VARIABLE: url, TOKEN_VARIABLE: "t0k3n"}
                with mock.patch.dict(os.environ, env):
                    os.environ.pop(WORK_ITEM_VARIABLE, None)
                    self.assertEqual(pipeline.desk_run_link("r_9"), link)
        with mock.patch.dict(os.environ, {URL_VARIABLE: "ftp://desk.example", TOKEN_VARIABLE: "t0k3n",
                                          WORK_ITEM_VARIABLE: "ALY-7"}):
            self.assertIsNone(pipeline.desk_run_link("r_9"), "a URL the token may not go to is no link")

    def init_scratch_again(self):
        """A fresh scratch run in the same root, the last one's files gone."""
        boards = os.path.join(self.root, "boards")
        if os.path.isdir(boards):
            for name in os.listdir(boards):
                os.remove(os.path.join(boards, name))
        self.init_scratch()

    def test_the_totals_on_the_card_are_the_ledger_totals(self):
        """tokens and costUsd are the telemetry totals `usage` computes, not a second count."""
        self.init_scratch()
        board = os.path.join(self.root, "boards", "2026-09-13-scratch.json")
        with open(board) as f:
            run = json.load(f)
        # A telemetry block of the shape compute_usage writes. The numbers are a
        # fixture; what is asserted is that the body carries these and not a
        # count of its own.
        run["telemetry"] = {
            "tokens": {"input": 746, "cache_write": 638_964, "cache_read": 29_589_801, "output": 98_341},
            "cost_usd": 6.31,
        }
        with open(board, "w") as f:
            json.dump(run, f)

        with Listener() as desk:
            out = self.run_pipeline(
                "status", "scratch", "0",
                env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"},
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            # `status` reports nothing: it is not a boundary and it writes nothing.
            self.assertEqual(desk.requests, [])

        # The totals reach the body through the one function that reads them, so
        # the assertion is on that function rather than on a phase boundary,
        # which would recompute the telemetry from the transcripts and overwrite
        # the fixture before the body was built.
        sys.path.insert(0, HERE)
        import pipeline
        self.assertEqual(pipeline.beat_totals(run), (30_327_852, 6.31))
        body = pipeline.beat_body(run, 5, "start", False)
        self.assertEqual(body["tokens"], 30_327_852)
        self.assertEqual(body["costUsd"], 6.31)

    # ---------- Gate 6 residual R11: the token never reaches stderr ----------

    def beat_stderr(self, url, token):
        """One phase boundary against a desk that cannot be reached, and its stderr."""
        out = self.run_pipeline(
            "phase", "scratch", "0", "start",
            env={URL_VARIABLE: url, TOKEN_VARIABLE: token},
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("the desk did not take the heartbeat", out.stderr)
        return out.stderr

    def test_a_url_that_carries_the_token_is_printed_with_the_token_taken_out(self):
        """A failure that quotes the URL back prints <token> where the token was.

        R11. The three cases this file already had, a refused connection, a 401
        and a URL that is not a URL, carry no URL in their messages at all, so
        the assertion that the token is absent was true whether or not the
        redaction was there. Measured rather than assumed: a refused connection
        says only "[Errno 61] Connection refused". These three do quote the URL,
        which are the three shapes urllib spells with the URL in them.

        The second assertion is what keeps the case from going vacuous the same
        way: the line must show the redaction, so the day a Python release stops
        quoting the URL this says so rather than passing on an absence it could
        no longer have found.
        """
        self.init_scratch()
        token = "t0k3n"
        urls = {
            "the token is the scheme": token + "://127.0.0.1/x",
            "the token is the port": "http://127.0.0.1:" + token,
            "the token is in the path, beside a control character":
                f"http://127.0.0.1:{closed_port()}/{token}\x7f",
        }
        for name, url in urls.items():
            with self.subTest(name):
                stderr = self.beat_stderr(url, token)
                self.assertNotIn(token, stderr, f"{name}: the token reached stderr")
                self.assertIn(
                    "<token>", stderr,
                    f"{name}: the message did not quote the URL, so this case proves nothing: {stderr!r}",
                )

    def test_a_token_a_message_escapes_is_printed_with_the_token_taken_out(self):
        """A token holding a control character is redacted in its escaped form too.

        R11's other half, and the one that was red when it was written. A token
        is whatever the environment holds: `.strip()` takes the outer whitespace
        off and nothing looks at the middle. urllib prints a URL and a header
        value through repr, so a token holding a newline reaches the message as
        the two characters backslash and n, and a replacement of the raw string
        does not match it. Both halves are exercised: the URL path raises
        InvalidURL quoting the path, and the header path raises ValueError
        quoting the header value before any socket is opened.
        """
        self.init_scratch()
        token = "t0k\n3n"
        escaped = token.encode("unicode_escape").decode("ascii")
        port = closed_port()
        urls = {
            "escaped inside the url": f"http://127.0.0.1:{port}/{token}",
            "escaped inside the authorization header": f"http://127.0.0.1:{port}",
        }
        for name, url in urls.items():
            with self.subTest(name):
                stderr = self.beat_stderr(url, token)
                for rendering in (token, escaped):
                    self.assertNotIn(
                        rendering, stderr,
                        f"{name}: {rendering!r} reached stderr: {stderr!r}",
                    )
                self.assertIn(
                    "<token>", stderr,
                    f"{name}: the message did not quote the token at all, so this case proves nothing: {stderr!r}",
                )

    # ---------- Gate 6 residual R12: the bound, and the table it comes from ----------

    def test_a_branch_past_the_bound_is_cut_to_the_bound(self):
        """A 300 character branch reaches the desk cut to the branch bound.

        R12. Nothing in this file sent a run slug, branch or status line
        anywhere near 200 characters, so the cut in `plain()` never fired and
        removing it changed nothing anybody could see. The bound exists so a
        long branch name is cut on this side rather than earning a 400 at the
        desk, which is a refusal nobody is looking at.

        The expected length is read off pipeline.py's own table rather than
        typed here, so this case says the cut happens at the bound and the case
        below says the table is the desk's. Two claims, two assertions.
        """
        self.init_scratch()
        bound = pipeline_module().HEARTBEAT_BOUNDS["branch"]
        long_branch = "b" * 300
        self.assertGreater(len(long_branch), bound, "the branch is not past the bound, so nothing is cut")

        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("set", "scratch", "branch", long_branch, env=env)
            self.assertEqual(out.returncode, 0, out.stderr)

            desk.clear()
            out = self.run_pipeline("phase", "scratch", "0", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(len(desk.requests), 1, "phase start did not post exactly once")

            sent = desk.bodies()[0]["branch"]
            self.assertEqual(len(sent), bound, f"the branch reached the desk {len(sent)} long")
            self.assertEqual(sent, long_branch[:bound], "the branch was cut somewhere other than the end")

    def test_a_title_and_goal_past_their_bounds_are_cut_and_blank_ones_are_not_sent(self):
        """ALYDAS-154, S2. The ledger's title and goal ride on every beat, cut to
        pipeline.py's hand copy of the desk's bounds at the end, and a ledger with
        none sends no key, which the desk reads as "keep what the run has"."""
        self.init_scratch()
        bounds = pipeline_module().HEARTBEAT_BOUNDS
        run = self.board()
        run["title"], run["goal"] = "t" * (bounds["title"] + 50), "  " + "g" * (bounds["goal"] + 50) + "  "
        self.write_board(run)
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("phase", "scratch", "0", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            body = desk.bodies()[0]
        self.assertEqual(body["title"], "t" * bounds["title"])
        self.assertEqual(body["goal"], "g" * bounds["goal"])

        # Not None: the board renderer cannot draw a null title (html_escape),
        # and `init` always writes text.
        for blank in ("", "   "):
            with self.subTest(blank=blank):
                run = self.board()
                run["title"], run["goal"] = blank, blank
                self.write_board(run)
                with Listener() as desk:
                    out = self.run_pipeline("phase", "scratch", "0", "start",
                                            env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
                    self.assertEqual(out.returncode, 0, out.stderr)
                    body = desk.bodies()[0]
                self.assertNotIn("title", body)
                self.assertNotIn("goal", body)

    def test_the_bounds_table_is_the_desks_bounds_table(self):
        """Every bound pipeline.py holds is the desk's number for that field.

        R12's other half, and R1's shape on this side of the wire. The table
        here is a hand copy, so the desk raising a bound and the skill keeping
        the old one is two declarations of one number parting company with
        nothing to say so. The desk may bound a field the skill never sends,
        `prUrl` today, so the rule is that the skill's table is contained in the
        desk's and agrees with it field by field.
        """
        theirs = desk_bounds(self)
        self.assertGreaterEqual(len(theirs), 6, f"the desk's table was read as {theirs}, so this case proves nothing")

        ours = pipeline_module().HEARTBEAT_BOUNDS
        self.assertGreaterEqual(len(ours), 6, "pipeline.py's table is empty")

        self.assertEqual(
            {field: ours[field] for field in sorted(ours)},
            {field: theirs.get(field) for field in sorted(ours)},
            "pipeline.py's hand copy of HEARTBEAT_BOUNDS disagrees with the desk's",
        )

    # ---------- Gate 6 residual R13: save, then render, then report ----------

    def test_the_board_on_disk_already_holds_the_phase_when_the_desk_hears_of_it(self):
        """The desk is told a boundary only after the board on disk holds it.

        R13. The comment beside the call says what the order is for, that what
        the desk is told is what the board already says and that a desk that is
        down cannot lose the boundary, and nothing checked it. With a desk that
        is up both orders post the same body, so the difference is only visible
        from inside the request: this listener reads the board off disk at the
        moment the POST arrives.

        Two readings, one per thing that has to have happened: the JSON is the
        save and the HTML is the render. The `--force` reason is what makes the
        HTML readable as before or after, because it is on the board only once
        the boundary is stamped.
        """
        self.init_scratch()
        self.run_pipeline("phase", "scratch", "0", "start")

        boards = os.path.join(self.root, "boards")
        board = os.path.join(boards, "2026-09-13-scratch.json")
        page = os.path.join(boards, "2026-09-13-scratch.html")
        reason = "a scratch run"

        def snapshot():
            with open(board) as f:
                run = json.load(f)
            with open(page, encoding="utf-8") as f:
                rendered = f.read()
            return {
                "status": run["phases"][0]["status"],
                "rendered": reason in rendered,
            }

        # The same two readings before the boundary, so a snapshot that could
        # not tell the two apart would say so here rather than passing below.
        self.assertEqual(
            snapshot(), {"status": "active", "rendered": False},
            "the board already looked closed before the boundary, so this case proves nothing",
        )

        with Listener(watch=snapshot) as desk:
            out = self.run_pipeline(
                "phase", "scratch", "0", "done", "--force", reason,
                env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"},
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(len(desk.requests), 1, "phase done did not post exactly once")
            self.assertEqual(
                desk.observed,
                [{"status": "done", "rendered": True}],
                "the desk was told the boundary before the board on disk held it",
            )


    # ---------- G8W3, S2: where the bearer is allowed to go ----------

    def test_a_redirect_is_not_followed_and_the_second_host_hears_nothing(self):
        """A desk that answers 30x gets one request, the Location gets none, and stderr gets one line.

        S2. urllib's redirect handler rebuilds the request from the original
        headers minus the two content headers, so `authorization` is replayed to
        whatever host the Location names. The run's only acting credential would
        be handed to a third party by one answer from whatever is listening at
        ALYDASH_URL. Every redirect code urllib follows is tried, because 302 and
        303 on a POST become a GET and 307 and 308 keep the POST.
        """
        self.init_scratch()
        token = "t0k3n"
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code), Listener() as elsewhere:
                with Listener(status=code, location=elsewhere.url + "/x") as desk:
                    out = self.run_pipeline(
                        "phase", "scratch", "0", "start",
                        env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: token},
                    )
                    self.assertEqual(out.returncode, 0, out.stderr)
                    self.assertEqual(len(desk.requests), 1, f"{code}: the desk did not hear exactly one request")
                    self.assertEqual(
                        elsewhere.requests, [],
                        f"{code}: the redirect was followed to a second host: {elsewhere.requests!r}",
                    )
                    self.assertEqual(len(out.stderr.strip().splitlines()), 1, out.stderr)
                    self.assertIn("the desk did not take the heartbeat", out.stderr)
                    self.assertNotIn(token, out.stderr)

    def test_a_url_that_is_neither_https_nor_local_http_sends_nothing(self):
        """http to a host that is not this machine sends nothing and says so in one line.

        S2's other half, and the one that needs no redirect: a bearer over
        cleartext to a host on the network is a bearer for anybody on the path.
        The listener stands in as the proxy urllib reads from the environment,
        so a request for http://example.com arrives there if it is made at all,
        and "sent nothing" is a request count rather than an absence nobody
        could have observed.
        """
        self.init_scratch()
        token = "t0k3n"
        with Listener() as proxy:
            proxied = {"http_proxy": proxy.url, "HTTP_PROXY": proxy.url, "no_proxy": "", "NO_PROXY": ""}

            # The premise, so the refusals below are not a proxy nobody uses: an
            # https URL is sent, and through this proxy it arrives as a CONNECT.
            out = self.run_pipeline(
                "phase", "scratch", "0", "start",
                env=dict(proxied, **{URL_VARIABLE: "https://desk.example", TOKEN_VARIABLE: token,
                                     "https_proxy": proxy.url, "HTTPS_PROXY": proxy.url}),
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(
                [r["method"] for r in proxy.requests], ["CONNECT"],
                f"an https desk was not sent through the proxy, so this case proves nothing: {out.stderr!r}",
            )

            refused = [
                "http://example.com",
                "http://desk.example:3000/",
                "http://localhost.example.com",
                "http://127.0.0.1.example",
                "HTTP://example.com",
                "ftp://example.com",
            ]
            for url in refused:
                with self.subTest(url):
                    proxy.clear()
                    out = self.run_pipeline(
                        "phase", "scratch", "0", "start",
                        env=dict(proxied, **{URL_VARIABLE: url, TOKEN_VARIABLE: token}),
                    )
                    self.assertEqual(out.returncode, 0, out.stderr)
                    self.assertEqual(proxy.requests, [], f"{url} was sent: {proxy.requests!r}")
                    self.assertEqual(len(out.stderr.strip().splitlines()), 1, out.stderr)
                    self.assertIn("the desk did not take the heartbeat", out.stderr)
                    self.assertIn(URL_VARIABLE, out.stderr, f"{url}: the line does not name the variable")
                    self.assertNotIn(token, out.stderr)

    def test_http_on_this_machine_still_sends_once(self):
        """http on localhost and 127.0.0.1 is the desk on a laptop, and it still reports."""
        self.init_scratch()
        with Listener() as desk:
            for host in ("localhost", "127.0.0.1", "LOCALHOST"):
                with self.subTest(host):
                    desk.clear()
                    out = self.run_pipeline(
                        "phase", "scratch", "0", "start",
                        env={URL_VARIABLE: f"http://{host}:{desk.port}", TOKEN_VARIABLE: "t0k3n"},
                    )
                    self.assertEqual(out.returncode, 0, out.stderr)
                    self.assertEqual(len(desk.requests), 1, f"{host}: {out.stderr!r}")
                    self.assertEqual(desk.requests[0]["headers"]["authorization"], "bearer t0k3n")
                    self.assertEqual(out.stderr, "")

    def test_http_on_the_ipv6_loopback_still_sends_once(self):
        """http on [::1] is this machine too.

        A listener on the IPv6 loopback is needed, and a machine without one
        skips and says so rather than passing on a refusal.
        """
        self.init_scratch()
        try:
            six = SixServer(("::1", 0), Recorder)
        except OSError as e:
            self.skipTest(f"no IPv6 loopback to listen on ({e}), so ::1 was not exercised")
        six.requests, six.status, six.reply = [], 200, {"session": {"id": "s1"}}
        six.watch, six.observed, six.location = None, [], None
        thread = threading.Thread(target=six.serve_forever, daemon=True)
        thread.start()
        try:
            out = self.run_pipeline(
                "phase", "scratch", "0", "start",
                env={URL_VARIABLE: f"http://[::1]:{six.server_address[1]}", TOKEN_VARIABLE: "t0k3n"},
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(len(six.requests), 1, out.stderr)
            self.assertEqual(out.stderr, "")
        finally:
            six.shutdown()
            six.server_close()
            thread.join(timeout=5)

    # ---------- test 3 ----------

    def board_files(self):
        """Every board file the ledger writes, by path, with its bytes."""
        boards = os.path.join(self.root, "boards")
        files = {}
        for name in sorted(os.listdir(boards)):
            with open(os.path.join(boards, name), "rb") as f:
                files[name] = f.read()
        return files

    def test_an_unreachable_desk_changes_nothing(self):
        """A refused or unreachable URL leaves the exit code and the board unchanged."""
        self.init_scratch()
        # The board as the ledger leaves it when the desk is not in the picture.
        self.run_pipeline("phase", "scratch", "0", "start")
        before = self.board_files()

        refused = f"http://127.0.0.1:{closed_port()}"
        out = self.run_pipeline(
            "phase", "scratch", "0", "done", "--force", "a scratch run",
            env={URL_VARIABLE: refused, TOKEN_VARIABLE: "t0k3n"},
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Phase 0 · Frame", out.stdout)
        self.assertNotIn("Traceback", out.stderr)
        self.assertNotIn("t0k3n", out.stderr)
        self.assertEqual(len(out.stderr.strip().splitlines()), 1, out.stderr)
        self.assertIn("the desk did not take the heartbeat", out.stderr)

        # The same command with no desk in sight, against a fresh copy of the
        # same ledger: the two boards are the same bytes but for the timestamps,
        # so the comparison is which files exist and which phase they closed.
        after = self.board_files()
        self.assertEqual(sorted(before), sorted(after))
        self.assertEqual(json.loads(after["2026-09-13-scratch.json"])["phases"][0]["status"], "done")

        # A desk that answers 401, which is what an expired token looks like.
        with Listener(status=401, reply={"error": "That token is not known here."}) as desk:
            out = self.run_pipeline(
                "phase", "scratch", "1", "start",
                env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"},
            )
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(len(desk.requests), 1)
            self.assertIn("the desk did not take the heartbeat", out.stderr)
            self.assertNotIn("t0k3n", out.stderr)

        # And a URL that is not a URL at all.
        out = self.run_pipeline(
            "phase", "scratch", "1", "done", "--force", "a scratch run",
            env={URL_VARIABLE: "not a url", TOKEN_VARIABLE: "t0k3n"},
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("Traceback", out.stderr)

    def test_a_refused_close_still_refuses(self):
        """The exit code the ledger already had is the exit code, desk or no desk."""
        self.init_scratch()
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            # Phase 9 cannot close without a resume doc: exit 2, and no beat,
            # because the boundary was never stamped.
            out = self.run_pipeline("phase", "scratch", "9", "done", env=env)
            self.assertEqual(out.returncode, 2, out.stdout)
            self.assertEqual(desk.requests, [])
            # A phase that does not exist still dies the way it died before.
            out = self.run_pipeline("phase", "scratch", "11", "start", env=env)
            self.assertEqual(out.returncode, 1, out.stdout)

    # ---------- ALYDAS-124: the directive on the reply ----------

    def events_text(self):
        boards = os.path.join(self.root, "boards")
        text = ""
        for name in sorted(os.listdir(boards)):
            if name.endswith(".events.jsonl"):
                with open(os.path.join(boards, name), encoding="utf-8") as f:
                    text += f.read()
        return text

    def test_a_stop_directive_ends_the_run_on_the_desk_and_exits_3(self):
        """Told to stop, the ledger records it, sends one ended beat and exits 3."""
        self.init_scratch("--hold", "none")  # S5: starts a phase past Phase 2
        with Listener(reply={"session": {"id": "s1"}, "directive": {"kind": "stop"}}) as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("phase", "scratch", "3", "start", env=env)
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertIn("Phase 3 · Plan", out.stdout, "the boundary block is still printed")
            self.assertIn("pipeline: the desk says stop", out.stdout)
            bodies = desk.bodies()
            self.assertEqual(len(bodies), 2, "a stop sends exactly one more beat")
            self.assertEqual(bodies[0]["ended"], False)
            self.assertEqual(bodies[1]["ended"], True, "the second beat ends the run")
            self.assertIn("stopped", bodies[1]["statusLine"])
            self.assertIn("stopped from the desk", self.events_text())
            self.assertNotIn("t0k3n", out.stdout + out.stderr)

    def test_a_stop_on_the_beat_that_already_ended_the_run_sends_nothing_more(self):
        """Phase 9 done already ended the run: the stop is printed, nothing else changes."""
        self.init_scratch()
        with Listener(reply={"session": {"id": "s1"}, "directive": {"kind": "stop"}}) as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("phase", "scratch", "9", "done", "--force", "a scratch run", env=env)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(len(desk.requests), 1)
            self.assertIn("pipeline: the desk says stop", out.stdout)

    def test_a_message_directive_is_printed_where_the_agent_reads(self):
        """A message is printed on stdout, once, in plain text, and the exit code is untouched."""
        self.init_scratch("--hold", "none")  # S5: starts a phase past Phase 2
        reply = {"session": {"id": "s1"},
                 "directive": {"kind": "message", "id": "m1", "text": "Use port 5444\u001b[31m for tests",
                               "sentAt": "2026-09-25T03:00:00.000Z"}}
        with Listener(reply=reply) as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("phase", "scratch", "3", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(len(desk.requests), 1)
            lines = [line for line in out.stdout.splitlines() if "message from the desk" in line]
            self.assertEqual(lines, ["pipeline: message from the desk: Use port 5444 [31m for tests"])
            self.assertNotIn("\u001b", out.stdout, "a control character reached the terminal")
            self.assertIn("message from the desk", self.events_text())

    def test_a_message_with_c1_or_bidi_characters_is_blanked_before_the_terminal(self):
        """Gate 8, L1: C1 controls and bidi or zero-width marks never reach the terminal."""
        self.init_scratch("--hold", "none")  # S5: starts a phase past Phase 2
        text = "ok\u009b2J \u202eevil\u202c \u200bz\u2066w\u2069"
        reply = {"session": {"id": "s1"}, "directive": {"kind": "message", "id": "m1", "text": text,
                                                         "sentAt": "2026-09-25T03:00:00.000Z"}}
        with Listener(reply=reply) as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("phase", "scratch", "3", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            line = [l for l in out.stdout.splitlines() if "message from the desk" in l][0]
            for c in ("\u009b", "\u202e", "\u202c", "\u200b", "\u2066", "\u2069"):
                self.assertNotIn(c, line, repr(c))
            self.assertIn("ok", line)

    def test_continue_and_an_old_desk_with_no_directive_change_nothing(self):
        """`continue`, no directive at all, an unknown kind and a body that is not JSON all leave the run alone."""
        self.init_scratch("--hold", "none")  # S5: starts a phase past Phase 2
        for name, reply in {
            "continue": {"session": {"id": "s1"}, "directive": {"kind": "continue"}},
            "an old desk": {"session": {"id": "s1"}},
            "an unknown kind": {"session": {"id": "s1"}, "directive": {"kind": "dance"}},
            "a directive that is not an object": {"session": {"id": "s1"}, "directive": "stop"},
        }.items():
            with self.subTest(name), Listener(reply=reply) as desk:
                env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
                out = self.run_pipeline("phase", "scratch", "3", "start", env=env)
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                self.assertEqual(len(desk.requests), 1)
                self.assertNotIn("the desk says stop", out.stdout)
                self.assertNotIn("message from the desk", out.stdout)
                self.assertEqual(out.stderr, "")

    # ---------- ALYDAS-135, S1: the beat carries the ledger's phases ----------

    def test_the_beat_after_phase_1_start_carries_all_ten_phases_with_phase_1_running(self):
        """Every phase in the ledger rides on the beat, in `n` order, with its status and summary.

        ALYDAS-135 design section 1: the desk keeps a snapshot of the whole
        ledger, so every beat sends every phase, not only the one that moved.
        Phase 0 is closed with a summary first, so the body has to carry a
        done phase with its words, the running one, and eight not started,
        which is three different states a wrong mapping cannot all get right.
        The status is the status line's word (`running`), the one the desk's
        step strip reads, not the ledger's internal `active`.
        """
        self.init_scratch()
        out = self.run_pipeline("note", "scratch", "0", "--summary", "Framed from the item's criteria.")
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_pipeline("phase", "scratch", "0", "done", "--force", "a scratch run")
        self.assertEqual(out.returncode, 0, out.stderr)

        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("phase", "scratch", "1", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(len(desk.requests), 1, "phase start did not post exactly once")
            ledger = desk.bodies()[0].get("ledger")

        self.assertIsInstance(ledger, dict, "the beat carried no ledger")
        phases = ledger["phases"]
        self.assertEqual([p["n"] for p in phases], list(range(10)))
        self.assertEqual(
            [p["name"] for p in phases],
            ["Frame", "Brainstorm", "Prototype", "Plan", "Verify the brief", "Dispatch",
             "Break the guards", "Goal achieved", "Review the diff", "Finish"],
        )
        self.assertEqual([p["status"] for p in phases], ["done", "running"] + ["pending"] * 8)
        # ALYDAS-154, S2. Each phase's kind, off the ledger: phase or gate.
        self.assertEqual([p["kind"] for p in phases], [kind for _, _, _, kind in pipeline_module().PHASES])
        self.assertEqual({p["kind"] for p in phases}, {"phase", "gate"})
        self.assertEqual(phases[0]["summary"], "Framed from the item's criteria.")
        self.assertEqual([p["summary"] for p in phases[1:]], [""] * 9)
        # Exactly these keys, so a slice adding a field is a field somebody
        # chose to add. S1 sent the first four; S2 sends the rest.
        self.assertEqual({key for p in phases for key in p}, set(LEDGER_PHASE_KEYS))

    def test_a_summary_past_the_ledger_bound_is_cut_to_the_bound(self):
        """A phase summary longer than the desk takes is cut here, so the beat is not a 400.

        The desk refuses the whole beat when one phase is over a bound, and a
        refused beat is a run the Now strip stops hearing. The bound is read off
        pipeline.py's own table, which mirrors `LEDGER_BOUNDS` in
        `packages/modules/runs/src/ledger-bounds.ts`.
        """
        self.init_scratch()
        bound = pipeline_module().LEDGER_BOUNDS["summary"]
        long_summary = "s" * (bound + 50)
        out = self.run_pipeline("note", "scratch", "0", "--summary", long_summary)
        self.assertEqual(out.returncode, 0, out.stderr)
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("phase", "scratch", "1", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            sent = desk.bodies()[0]["ledger"]["phases"][0]["summary"]
        self.assertEqual(sent, long_summary[:bound])

    # ---------- ALYDAS-173, S1: the plain line ----------
    #
    # One sentence per phase a newcomer understands, shown under the phase's
    # name on the desk's run page and on the local board. A phase cannot close
    # without one: the story is written as the run goes, not reconstructed.

    def board_bytes(self):
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.json"), "rb") as f:
            return f.read()

    def test_a_phase_with_no_plain_line_cannot_close(self):
        """Every phase refuses `done` without a plain line: exit 2, the refusal
        names the command that fixes it, and the ledger JSON is unchanged (the
        `check` event is still logged, as for every refused close). A plain line
        of only spaces is no plain line. `--force` still closes it.

        The phases are PHASES, read off pipeline.py, so a phase added later is
        walked here the day it is added."""
        self.init_scratch()
        numbers = [n for n, _, _, _ in pipeline_module().PHASES]
        self.assertEqual(numbers, list(range(10)))
        for n in numbers:
            for said in (None, "   \t "):
                if said is not None:
                    out = self.run_pipeline("note", "scratch", str(n), "--plain", said)
                    self.assertEqual(out.returncode, 0, out.stderr)
                before = self.board_bytes()
                checks = self.events_text().count('"event": "check"')
                out = self.run_pipeline("phase", "scratch", str(n), "done")
                self.assertEqual(out.returncode, 2, f"phase {n} closed with plain {said!r}: {out.stdout}")
                self.assertIn(f'no plain line: write one with note scratch {n} --plain "..."', out.stderr)
                self.assertEqual(self.board_bytes(), before, f"a refused close of phase {n} changed the ledger")
                self.assertEqual(self.events_text().count('"event": "check"'), checks + 1)
                self.assertEqual(self.board()["phases"][n]["status"], "pending")
        # Forced, it closes, and says why on the board.
        out = self.run_pipeline("phase", "scratch", "3", "done", "--force", "no plain line in a scratch run")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.board()["phases"][3]["status"], "done")
        self.assertEqual(self.board()["phases"][3]["forced"], "no plain line in a scratch run")

    def test_a_plain_line_is_what_lets_a_phase_whose_artifacts_are_there_close(self):
        """The other direction: phase 0 with its goal doc in place closes on a
        plain line alone, and without one is refused for that alone."""
        self.init_scratch()
        os.makedirs(os.path.join(self.root, "specs"), exist_ok=True)
        with open(os.path.join(self.root, "specs", "2026-09-13-scratch-goal.md"), "w") as f:
            f.write("# Goal\n\nE1. Observed by: a scratch test.\n")
        out = self.run_pipeline("phase", "scratch", "0", "done")
        self.assertEqual(out.returncode, 2, out.stdout)
        self.assertIn("phase 0 cannot close: no plain line", out.stderr)
        self.assertNotIn("goal doc", out.stderr)
        out = self.run_pipeline("note", "scratch", "0", "--plain", "Wrote down what done looks like.")
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_pipeline("phase", "scratch", "0", "done")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.board()["phases"][0]["status"], "done")

    def test_note_plain_sets_the_line_logs_it_and_clear_takes_it_away(self):
        """`init` gives every phase a blank plain line; `note --plain` sets it and
        logs a `plain` event, as `--summary` logs `result`; `--clear plain` blanks it."""
        self.init_scratch()
        self.assertEqual([p["plain"] for p in self.board()["phases"]], [""] * 10)
        out = self.run_pipeline("note", "scratch", "2", "--plain", "Measured the leak on today's code.")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.board()["phases"][2]["plain"], "Measured the leak on today's code.")
        logged = [json.loads(line) for line in self.events_text().splitlines() if '"plain"' in line]
        self.assertEqual([(e["phase"], e["event"], e["note"]) for e in logged],
                         [(2, "plain", "Measured the leak on today's code.")])
        out = self.run_pipeline("note", "scratch", "2", "--clear", "plain")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.board()["phases"][2]["plain"], "")

    def test_s1_no_bidi_override_reaches_the_desk_and_the_words_around_it_do(self):
        """ALYDAS-173 Gate 8, S-1, Sachit's ruling: strip in the pipeline. A bidi
        embedding, override or isolate (U+202A to U+202E, U+2066 to U+2069, the
        desk's own `BIDI_CONTROL`) makes a line draw other than it is stored, so
        "gpj.hs" reads "sh.jpg". None leaves on a beat, in a phase text, a list
        entry, a waiting item, a document title or the log; the letters stay.
        A zero-width joiner is kept: emoji and Persian need it."""
        self.init_scratch()
        bidi = "".join(chr(c) for c in [*range(0x202A, 0x202F), *range(0x2066, 0x206A)])
        sneaky = "Run ‮gpj.hs‬ now"
        for args in (("note", "scratch", "0", "--plain", sneaky, "--scene", "a" + bidi + "b"),
                     ("note", "scratch", "0", "--left-out", sneaky, "--doc", "docs/a.md|" + sneaky + "|about|short"),
                     ("story", "scratch", "--needs-you", sneaky + "|why⁧x⁩", "--happened", "fam‍ily")):
            self.assertEqual(self.run_pipeline(*args).returncode, 0)
        self.assertEqual(self.run_pipeline("log", "scratch", sneaky).returncode, 0)
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("phase", "scratch", "1", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            body = json.dumps(desk.bodies()[-1], ensure_ascii=False)
        self.assertEqual([c for c in body if c in bidi], [])
        for kept in ("Run gpj.hs now", "ab", "whyx", "fam‍ily"):
            self.assertIn(kept, body)

    def test_the_beat_carries_each_phases_plain_line_and_an_older_ledger_sends_it_blank(self):
        """The plain line rides on every beat, per phase, cut to the desk's bound.
        A ledger written before the field existed has no `plain` key at all, and
        is read as blank rather than failing."""
        self.init_scratch()
        bound = pipeline_module().LEDGER_BOUNDS["plain"]
        self.assertEqual(bound, 300)
        out = self.run_pipeline("note", "scratch", "0", "--plain", "Wrote down what done looks like.")
        self.assertEqual(out.returncode, 0, out.stderr)
        # Gate 8, ST-2: `note` now refuses a plain line past its bound, so the
        # long one is written into the ledger, as a hand edit or an older
        # ledger would carry it; the beat still cuts it.
        run = self.board()
        run["phases"][1]["plain"] = "p" * (bound + 20)
        self.write_board(run)
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("phase", "scratch", "2", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            phases = desk.bodies()[0]["ledger"]["phases"]
        self.assertEqual([p["plain"] for p in phases],
                         ["Wrote down what done looks like.", "p" * bound] + [""] * 8)

        run = self.board()
        for p in run["phases"]:
            del p["plain"]
        self.write_board(run)
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("phase", "scratch", "2", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            phases = desk.bodies()[0]["ledger"]["phases"]
        self.assertEqual([p["plain"] for p in phases], [""] * 10)

    def test_the_trim_cuts_a_plain_line_like_a_summary(self):
        """A ledger over its budget has its plain lines cut to the trim's width,
        with the rest of the texts; a plain line the trim did not count would
        leave the ledger over budget."""
        P = pipeline_module()
        blank = {"summary": "", "next": "", "decided": [], "measured": [], "wrong": [], "overruled": [],
                 "produced": [], "table": None, "skipReason": "", "forced": ""}
        ledger = {"phases": [{**blank, "n": n, "name": f"Phase {n}", "status": "done", "plain": "p" * 300}
                             for n in range(10)], "deferred": []}
        budget = P.ledger_bytes(ledger) - 1_000
        out, report = P.trim_ledger(ledger, budget)
        self.assertIsNotNone(report)
        self.assertLessEqual(P.ledger_bytes(out), budget)
        self.assertLess(report["width"], 300)
        self.assertEqual({len(p["plain"]) for p in out["phases"]}, {report["width"]})
        self.assertTrue(all(p["plain"].endswith("…") for p in out["phases"]))

    # ---------- ALYDAS-173, S2: setting the scene ----------
    #
    # Every phase opens with what the phase is for (fixed text that ships with
    # the skill) and where the story is (written by the agent at the start).
    # Frame also carries the backstory, what "fixed" means and what won't change.

    def test_every_phase_has_a_purpose_of_one_or_two_plain_sentences_within_the_bound(self):
        """PHASE_PURPOSE names every phase in PHASES and no other, each one or two
        sentences, inside the desk's purpose bound, and no two alike."""
        P = pipeline_module()
        self.assertEqual(sorted(P.PHASE_PURPOSE), [n for n, _, _, _ in P.PHASES])
        for n, text in P.PHASE_PURPOSE.items():
            self.assertEqual(text, text.strip(), n)
            self.assertTrue(text, n)
            self.assertLessEqual(len(text), P.LEDGER_BOUNDS["purpose"], n)
            self.assertIn(len(re.findall(r"[.!?](?:\s|$)", text)), (1, 2), f"phase {n}: {text}")
            self.assertNotIn("ALYDAS", text, f"phase {n} names an item: {text}")
        self.assertEqual(len(set(P.PHASE_PURPOSE.values())), len(P.PHASE_PURPOSE))

    def test_phase_start_stamps_the_purpose_and_a_reworded_skill_restamps_it(self):
        """`init` writes no purpose; `phase <n> start` stamps PHASE_PURPOSE[n] on
        that phase alone, and a start after the text changed stamps the new text."""
        P = pipeline_module()
        self.init_scratch("--hold", "none")  # S5: starts a phase past Phase 2
        self.assertEqual([p.get("purpose") for p in self.board()["phases"]], [None] * 10)
        for n, _, _, _ in P.PHASES:
            out = self.run_pipeline("phase", "scratch", str(n), "start")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(self.board()["phases"][n]["purpose"], P.PHASE_PURPOSE[n])
            self.assertEqual([p.get("purpose") for p in self.board()["phases"][n + 1:]], [None] * (9 - n))
        run = self.board()
        run["phases"][3]["purpose"] = "What an older skill said Plan was for."
        self.write_board(run)
        out = self.run_pipeline("phase", "scratch", "3", "start")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.board()["phases"][3]["purpose"], P.PHASE_PURPOSE[3])
        # Closing does not stamp it: a phase closed without a start keeps what it had.
        run = self.board()
        del run["phases"][4]["purpose"]
        self.write_board(run)
        out = self.run_pipeline("phase", "scratch", "4", "done", "--force", "a scratch run")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("purpose", self.board()["phases"][4])

    def test_note_scene_on_any_phase_and_the_frame_three_on_phase_0_only(self):
        """`--scene` sets where the story is on any phase; `--backstory` sets Frame's
        backstory; `--fixed-means` and `--wont-change` append, a repeat ignored.
        Each is logged. The three Frame flags on any other phase exit 1, name the
        flag and phase 0, and leave the ledger as it was. `--clear` takes each away."""
        self.init_scratch()
        out = self.run_pipeline("note", "scratch", "3", "--scene", "The approach is settled; now the order.")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.board()["phases"][3]["scene"], "The approach is settled; now the order.")
        out = self.run_pipeline("note", "scratch", "0",
                                "--scene", "This is where the story starts.",
                                "--backstory", "Tokens can be limited, and a limited one saw too much.",
                                "--fixed-means", "A refused token gets one answer.",
                                "--fixed-means", "A test fails if the old order returns.",
                                "--wont-change", "People see what they see today.")
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_pipeline("note", "scratch", "0", "--fixed-means", "A refused token gets one answer.",
                                "--fixed-means", "One shared check does it.")
        self.assertEqual(out.returncode, 0, out.stderr)
        zero = self.board()["phases"][0]
        self.assertEqual(zero["scene"], "This is where the story starts.")
        self.assertEqual(zero["backstory"], "Tokens can be limited, and a limited one saw too much.")
        self.assertEqual(zero["fixed_means"], ["A refused token gets one answer.", "A test fails if the old order returns.",
                                               "One shared check does it."])
        self.assertEqual(zero["wont_change"], ["People see what they see today."])
        logged = [json.loads(line) for line in self.events_text().splitlines()]
        self.assertEqual([(e["phase"], e["event"]) for e in logged if e["event"] in ("scene", "backstory", "fixed-means", "wont-change")],
                         [(3, "scene"), (0, "scene"), (0, "backstory"), (0, "fixed-means"), (0, "fixed-means"),
                          (0, "wont-change"), (0, "fixed-means")])

        for flag in ("--backstory", "--fixed-means", "--wont-change"):
            for n in ("1", "9"):
                before = self.board_bytes()
                out = self.run_pipeline("note", "scratch", n, flag, "Belongs on Frame.")
                self.assertEqual(out.returncode, 1, f"{flag} on phase {n}: {out.stdout}")
                self.assertIn(f"{flag} is Frame's: write it on phase 0", out.stderr)
                self.assertEqual(self.board_bytes(), before, f"{flag} on phase {n} changed the ledger")

        for key, blank in (("scene", ""), ("backstory", ""), ("fixed-means", []), ("wont-change", [])):
            out = self.run_pipeline("note", "scratch", "0", "--clear", key)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(self.board()["phases"][0][key.replace("-", "_")], blank, key)

    def test_the_beat_nests_the_story_with_every_phases_purpose_and_cuts_it_to_the_bounds(self):
        """The beat sends `story` per phase: the purpose on every phase, pending ones
        from PHASE_PURPOSE, the scene where one was written, and Frame's three.
        Empty keys are left out. Each text and list is cut to the desk's bound.
        An older ledger with none of the keys sends the purpose alone."""
        P = pipeline_module()
        b = P.LEDGER_BOUNDS
        self.assertEqual((b["purpose"], b["scene"], b["backstory"], b["fixedMeans"], b["wontChange"], b["storyEntry"]),
                         (600, 1_100, 2_000, 12, 12, 500))
        self.init_scratch()
        steps = [
            ("phase", "scratch", "0", "start"),
            ("note", "scratch", "0", "--wont-change", "w")
            + tuple(x for i in range(b["fixedMeans"] + 2) for x in ("--fixed-means", f"means {i}")),
            ("note", "scratch", "1", "--scene", "We know the problem; which fix?"),
        ]
        for step in steps:
            out = self.run_pipeline(*step)
            self.assertEqual(out.returncode, 0, out.stderr)
        # Gate 8, ST-2: `note` now refuses these past their bounds, so the long
        # ones are written into the ledger, as a hand edit or an older ledger
        # would carry them; the beat still cuts each.
        run = self.board()
        run["phases"][0].update(scene="s" * (b["scene"] + 9), backstory="b" * (b["backstory"] + 9),
                                wont_change=["w" * (b["storyEntry"] + 9)])
        self.write_board(run)
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("phase", "scratch", "1", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            phases = desk.bodies()[0]["ledger"]["phases"]
        self.assertEqual(phases[0]["story"], {
            "purpose": P.PHASE_PURPOSE[0],
            "scene": "s" * b["scene"],
            "backstory": "b" * b["backstory"],
            "fixedMeans": [f"means {i}" for i in range(b["fixedMeans"])],
            "wontChange": ["w" * b["storyEntry"]],
            # ALYDAS-173, S4. Each phase started carries its seeded steps.
            # ALYDAS-225, K3: the first of them doing, from the start beat on.
            "steps": [{"text": t, "state": "todo" if i else "doing"} for i, t in enumerate(P.PHASE_STEPS[0])],
        })
        self.assertEqual(phases[1]["story"], {"purpose": P.PHASE_PURPOSE[1], "scene": "We know the problem; which fix?",
                                              "steps": [{"text": t, "state": "todo" if i else "doing"}
                                                        for i, t in enumerate(P.PHASE_STEPS[1])]})
        self.assertEqual([p["story"] for p in phases[2:]], [{"purpose": P.PHASE_PURPOSE[n]} for n in range(2, 10)])

        run = self.board()
        for p in run["phases"]:
            # ALYDAS-173, S4: and the steps, which a ledger from before S2 has none of.
            for key in ("purpose", "scene", "backstory", "fixed_means", "wont_change", "steps"):
                p.pop(key, None)
        self.assertEqual([p["story"] for p in P.beat_ledger(run)["phases"]],
                         [{"purpose": P.PHASE_PURPOSE[n]} for n in range(10)])
        # A phase the skill has no purpose for, with nothing written, sends no story.
        run["phases"].append(dict(run["phases"][0], n=12, key="extra", name="Extra"))
        self.assertIsNone(P.beat_ledger(run)["phases"][-1]["story"])

    def test_the_trim_cuts_the_story_texts_and_drops_a_story_cut_to_nothing(self):
        """A ledger over its budget has its story texts and list entries cut to the
        trim's width with every other text; a story the trim did not count would
        leave the ledger over budget. At width 0 the story says nothing and is None."""
        P = pipeline_module()
        blank = {"summary": "", "next": "", "decided": [], "measured": [], "wrong": [], "overruled": [],
                 "produced": [], "table": None, "skipReason": "", "forced": "", "plain": ""}
        story = {"purpose": "p" * 600, "scene": "s" * 1_000, "backstory": "b" * 2_000,
                 "fixedMeans": ["f" * 500] * 12, "wontChange": ["w" * 500] * 12}
        ledger = {"phases": [{**blank, "n": n, "name": f"Phase {n}", "status": "done", "story": story}
                             for n in range(10)], "deferred": []}
        budget = P.ledger_bytes(ledger) - 10_000
        out, report = P.trim_ledger(ledger, budget)
        self.assertLessEqual(P.ledger_bytes(out), budget)
        self.assertLess(report["width"], 2_000)
        width = report["width"]
        for p in out["phases"]:
            s = p["story"]
            self.assertEqual(len(s["backstory"]), width)
            self.assertTrue(s["backstory"].endswith("…"))
            self.assertEqual(s["purpose"], "p" * 600 if width >= 600 else "p" * (width - 1) + "…")
            self.assertEqual({len(e) for e in s["fixedMeans"] + s["wontChange"]}, {min(500, width)})
        tiny = P.shaped(ledger, 0, 0, 0)
        self.assertEqual({p["story"] is None for p in tiny["phases"]}, {True})

    def test_the_local_board_shows_every_phases_purpose_and_frames_three(self):
        """The rendered board carries each phase's purpose, pending ones from
        PHASE_PURPOSE, and Frame's backstory and lists, and its template draws
        the scene block's headings."""
        P = pipeline_module()
        self.init_scratch()
        out = self.run_pipeline("note", "scratch", "0", "--scene", "Where it starts.", "--backstory", "Why this run exists.",
                                "--fixed-means", "One answer.", "--wont-change", "People see the same.")
        self.assertEqual(out.returncode, 0, out.stderr)
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.html"), encoding="utf-8") as f:
            html = f.read()
        for n in range(10):
            self.assertIn(json.dumps(P.PHASE_PURPOSE[n], ensure_ascii=False)[1:-1], html, n)
        for text in ("Where it starts.", "Why this run exists.", "One answer.", "People see the same.",
                     "What this phase is for", "Where the story is", "The backstory", 'What "fixed" means',
                     "What won't change"):
            self.assertIn(text, html)
        self.assertNotIn("purpose", self.board()["phases"][5], "rendering wrote a purpose into the ledger")

    # ---------- ALYDAS-173, S3: the Story view's packets, and the run's terms ----------

    def test_note_writes_each_story_packet_into_its_own_key_and_clear_takes_each_away(self):
        """`--lede` sets the lede; `--number`, `--option`, `--assumed` and `--left-out`
        append, a repeat ignored; `--wrong "what => did"` keeps the whole line in
        `wrong` and appends the two halves to `problems`, and a `--wrong` with no
        ` => ` goes to `wrong` only. Each is logged. `--clear` takes each away."""
        self.init_scratch()
        out = self.run_pipeline(
            "note", "scratch", "1",
            "--lede", "Five ways to fix it were weighed.",
            "--number", "18 of 18 leaked|Every route told a real item from a made-up one.",
            "--number", "6 routes|",
            "--option", "One shared check|chosen|Every route gets it the same way.",
            "--option", "Six hand-written checks|rejected|A seventh route would forget it.",
            "--option", "Say nothing|rejected|",
            "--assumed", "Tokens are only minted by people|Review found an agent can mint one.",
            "--assumed", "The desk runs on Postgres 16",
            "--left-out", "Rate limits: another item.",
            "--wrong", "11 run-link tests failed => Gave the test setup read permission.",
            "--wrong", "A flaky screenshot.",
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_pipeline("note", "scratch", "1", "--number", "6 routes|", "--left-out", "Rate limits: another item.",
                                "--option", "Say nothing|rejected|",
                                "--wrong", "11 run-link tests failed => Gave the test setup read permission.")
        self.assertEqual(out.returncode, 0, out.stderr)
        one = self.board()["phases"][1]
        self.assertEqual(one["lede"], "Five ways to fix it were weighed.")
        self.assertEqual(one["numbers"], [
            {"figure": "18 of 18 leaked", "means": "Every route told a real item from a made-up one."},
            {"figure": "6 routes", "means": ""},
        ])
        self.assertEqual(one["options"], [
            {"name": "One shared check", "chosen": True, "why": "Every route gets it the same way."},
            {"name": "Six hand-written checks", "chosen": False, "why": "A seventh route would forget it."},
            {"name": "Say nothing", "chosen": False, "why": ""},
        ])
        self.assertEqual(one["assumed"], [
            {"text": "Tokens are only minted by people", "later": "Review found an agent can mint one."},
            {"text": "The desk runs on Postgres 16", "later": ""},
        ])
        self.assertEqual(one["left_out"], ["Rate limits: another item."])
        self.assertEqual(one["wrong"], ["11 run-link tests failed => Gave the test setup read permission.", "A flaky screenshot."])
        self.assertEqual(one["problems"], [{"what": "11 run-link tests failed", "did": "Gave the test setup read permission."}])
        logged = [json.loads(line) for line in self.events_text().splitlines()]
        self.assertEqual([e["event"] for e in logged if e["phase"] == 1],
                         ["lede", "number", "number", "option", "option", "option", "assumed", "assumed", "left-out",
                          "wrong", "wrong"])

        for key, blank in (("lede", ""), ("numbers", []), ("options", []), ("assumed", []), ("left-out", []),
                           ("problems", [])):
            out = self.run_pipeline("note", "scratch", "1", "--clear", key)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(self.board()["phases"][1][key.replace("-", "_")], blank, key)
        # Clearing the problems leaves `wrong`, which Technical draws, as it was.
        self.assertEqual(len(self.board()["phases"][1]["wrong"]), 2)

    def test_an_option_whose_middle_word_is_not_chosen_or_rejected_exits_1_and_writes_nothing(self):
        """The middle of `--option` is `chosen` or `rejected` and nothing else; any
        other word, or an option with no middle, exits 1 naming the flag, and the
        ledger is left as it was, the lede on the same call included. A number or
        an assumption with nothing before its bar exits 1 the same way."""
        self.init_scratch()
        for flags in (("--option", "Shared check|yes|Because."), ("--option", "Shared check|Chosen|Because."),
                      ("--option", "Shared check"), ("--option", "|chosen|Because."),
                      ("--number", "|what it means"), ("--assumed", " |later")):
            before = self.board_bytes()
            out = self.run_pipeline("note", "scratch", "1", "--lede", "Not written.", *flags)
            self.assertEqual(out.returncode, 1, f"{flags}: {out.stdout}")
            self.assertIn(flags[0], out.stderr)
            self.assertEqual(self.board_bytes(), before, f"{flags} changed the ledger")

    def test_story_term_sets_a_codes_plain_name_a_later_one_replaces_it_and_clear_takes_them(self):
        """`story <slug> --term "CODE=plain name"` keeps the run's terms by code; a
        later term for the same code replaces it; a term with no `=` or no code
        exits 1 and writes nothing; `--clear terms` empties them."""
        self.init_scratch()
        out = self.run_pipeline("story", "scratch", "--term", "D probe=Prototype shape D: a script that measures",
                                "--term", "c1=Criterion 1")
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_pipeline("story", "scratch", "--term", "c1=Criterion 1: one answer for every key",
                                "--term", "CARRIERS = The routes that pass an item along")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.board()["terms"], {
            "D probe": "Prototype shape D: a script that measures",
            "c1": "Criterion 1: one answer for every key",
            "CARRIERS": "The routes that pass an item along",
        })
        for term in ("no equals sign", "=a plain name with no code", "  =x"):
            before = self.board_bytes()
            out = self.run_pipeline("story", "scratch", "--term", term)
            self.assertEqual(out.returncode, 1, f"{term}: {out.stdout}")
            self.assertIn("--term", out.stderr)
            self.assertEqual(self.board_bytes(), before, term)
        out = self.run_pipeline("story", "scratch", "--clear", "terms")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.board()["terms"], {})

    def test_the_beat_nests_the_story_packets_and_carries_the_terms_each_cut_to_its_bound(self):
        """The beat's story carries lede, numbers, options, assumed, leftOut and
        problems by the wire's names, each text and list cut to the desk's bound,
        an empty optional half left out, and the ledger carries `terms` beside the
        phases, cut to its count, each code and plain name to theirs. An older
        ledger with none of them sends no packet and empty terms."""
        P = pipeline_module()
        b = P.LEDGER_BOUNDS
        self.assertEqual((b["lede"], b["numbers"], b["figure"], b["options"], b["optionName"], b["assumed"], b["leftOut"],
                          b["problems"], b["terms"], b["termCode"], b["termPlain"], b["storyEntry"]),
                         (1_000, 12, 80, 12, 200, 12, 12, 12, 80, 40, 200, 500))
        self.init_scratch()
        long = lambda field, extra=9: "x" * (b[field] + extra)
        run = self.board()
        run["phases"][1].update({
            "lede": long("lede"),
            "numbers": [{"figure": long("figure"), "means": long("storyEntry")}] + [{"figure": f"{i}", "means": ""} for i in range(b["numbers"] + 2)],
            "options": [{"name": long("optionName"), "chosen": True, "why": long("storyEntry")},
                        {"name": "Not this", "chosen": False, "why": ""}, {"name": " ", "chosen": True, "why": "dropped"}],
            "assumed": [{"text": long("storyEntry"), "later": long("storyEntry")}, {"text": "Alone", "later": ""}],
            "left_out": [long("storyEntry")] + ["  "] + [f"left {i}" for i in range(b["leftOut"] + 2)],
            "problems": [{"what": long("storyEntry"), "did": long("storyEntry")}, {"what": "What alone", "did": ""}],
        })
        run["terms"] = {**{"c" * (b["termCode"] + 3): long("termPlain"), " ": "no code"},
                        **{f"T{i}": f"Term {i}" for i in range(b["terms"] + 5)}}
        self.write_board(run)
        with Listener() as desk:
            out = self.run_pipeline("phase", "scratch", "1", "start", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stderr)
            ledger = desk.bodies()[0]["ledger"]
        story = ledger["phases"][1]["story"]
        self.assertEqual(story["lede"], "x" * b["lede"])
        self.assertEqual(len(story["numbers"]), b["numbers"])
        self.assertEqual(story["numbers"][:2], [{"figure": "x" * b["figure"], "means": "x" * b["storyEntry"]}, {"figure": "0"}])
        self.assertEqual(story["options"], [{"name": "x" * b["optionName"], "chosen": True, "why": "x" * b["storyEntry"]},
                                            {"name": "Not this", "chosen": False}])
        self.assertEqual(story["assumed"], [{"text": "x" * b["storyEntry"], "later": "x" * b["storyEntry"]}, {"text": "Alone"}])
        self.assertEqual(len(story["leftOut"]), b["leftOut"] - 1)
        self.assertEqual(story["leftOut"][0], "x" * b["storyEntry"])
        self.assertEqual(story["problems"], [{"what": "x" * b["storyEntry"], "did": "x" * b["storyEntry"]}, {"what": "What alone"}])
        terms = ledger["terms"]
        self.assertEqual(len(terms), b["terms"])
        self.assertEqual(terms["c" * b["termCode"]], "x" * b["termPlain"])
        self.assertNotIn("", terms)
        # An older ledger: no packet, no terms.
        run = self.board()
        for p in run["phases"]:
            for key in ("lede", "numbers", "options", "assumed", "left_out", "problems"):
                p.pop(key, None)
        run.pop("terms", None)
        sent = P.beat_ledger(run)
        self.assertEqual(sent["terms"], {})
        for p in sent["phases"]:
            self.assertEqual(set(p["story"] or {}) & {"lede", "numbers", "options", "assumed", "leftOut", "problems"}, set())

    def test_the_trim_cuts_the_story_packets_and_the_terms_plain_names(self):
        """A ledger over its budget has the packets' texts and the terms' plain
        names cut to the trim's width with every other text; a code is not cut. At
        width 0 an entry whose required half is gone is dropped, and a term with
        no plain name left is dropped."""
        P = pipeline_module()
        blank = {"summary": "", "next": "", "decided": [], "measured": [], "wrong": [], "overruled": [],
                 "produced": [], "table": None, "skipReason": "", "forced": "", "plain": ""}
        story = {"lede": "l" * 1_000, "numbers": [{"figure": "f" * 80, "means": "m" * 500}] * 12,
                 "options": [{"name": "o" * 200, "chosen": True, "why": "w" * 500}] * 12,
                 "assumed": [{"text": "a" * 500, "later": "t" * 500}] * 12, "leftOut": ["x" * 500] * 12,
                 "problems": [{"what": "p" * 500, "did": "d" * 500}] * 12}
        ledger = {"phases": [{**blank, "n": n, "name": f"Phase {n}", "status": "done", "story": story} for n in range(10)],
                  "deferred": [], "terms": {f"CODE{i}": "n" * 200 for i in range(80)}}
        budget = P.ledger_bytes(ledger) - 20_000
        out, report = P.trim_ledger(ledger, budget)
        self.assertLessEqual(P.ledger_bytes(out), budget)
        width = report["width"]
        self.assertLess(width, 1_000)
        for p in out["phases"]:
            s = p["story"]
            self.assertEqual(len(s["lede"]), width)
            self.assertEqual({len(e["means"]) for e in s["numbers"]}, {min(500, width)})
            self.assertEqual({len(e["why"]) for e in s["options"]}, {min(500, width)})
            self.assertEqual({len(e["later"]) for e in s["assumed"]}, {min(500, width)})
            self.assertEqual({len(e) for e in s["leftOut"]}, {min(500, width)})
            self.assertEqual({len(e["did"]) for e in s["problems"]}, {min(500, width)})
            self.assertEqual({e["chosen"] for e in s["options"]}, {True})
        self.assertEqual(sorted(out["terms"]), sorted(ledger["terms"]))
        self.assertEqual({len(v) for v in out["terms"].values()}, {min(200, width)})
        tiny = P.shaped(ledger, 0, 0, 0)
        self.assertEqual({p["story"] is None for p in tiny["phases"]}, {True})
        self.assertEqual(tiny["terms"], {})

    def test_the_local_board_draws_the_story_packets_under_the_scene(self):
        """The rendered board carries each packet's words, and its template draws
        their headings."""
        self.init_scratch()
        out = self.run_pipeline("note", "scratch", "1", "--lede", "Five ways were weighed.",
                                "--number", "18 of 18|Every route leaked.", "--option", "Shared check|chosen|One place.",
                                "--assumed", "People mint tokens|An agent can.", "--left-out", "Rate limits.",
                                "--wrong", "Tests failed => Gave read.")
        self.assertEqual(out.returncode, 0, out.stderr)
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.html"), encoding="utf-8") as f:
            html = f.read()
        for text in ("Five ways were weighed.", "18 of 18", "Every route leaked.", "Shared check", "One place.",
                     "People mint tokens", "An agent can.", "Rate limits.", "Gave read.",
                     "What the numbers mean", "What was weighed", "Assumed", "Later:", "Left out on purpose",
                     "What went wrong, and what we did", "Not chosen"):
            self.assertIn(text, html)

    # ---------- ALYDAS-173, S4: live steps ----------
    #
    # Each phase's usual steps ship with the skill (PHASE_STEPS) and are seeded,
    # all todo, when a phase with none starts. `step` ticks them: one doing at a
    # time, a sub-line under a step, a new step added, and every call beats, so
    # a run page open on the desk moves with each tick.

    def steps_of(self, n):
        return self.board()["phases"][n].get("steps")

    def test_every_phase_has_three_to_six_usual_steps_in_plain_words(self):
        """PHASE_STEPS names every phase in PHASES and no other, each with three
        to six short lines inside the desk's step bound, none naming a code, and
        no phase's list the same as another's."""
        P = pipeline_module()
        self.assertEqual(sorted(P.PHASE_STEPS), [n for n, _, _, _ in P.PHASES])
        for n, steps in P.PHASE_STEPS.items():
            self.assertTrue(3 <= len(steps) <= 6, f"phase {n} has {len(steps)} steps")
            for text in steps:
                self.assertEqual(text, text.strip(), n)
                self.assertTrue(text, n)
                self.assertLessEqual(len(text), 80, f"phase {n}: a usual step is a short line: {text}")
                self.assertLessEqual(len(text), P.LEDGER_BOUNDS["stepText"], n)
                self.assertIsNone(re.search(r"ALYDAS|\b[A-Z]+\d+\b|[`_]|\bc\d\b", text), f"phase {n} names a code: {text}")
            self.assertEqual(len(set(steps)), len(steps), f"phase {n} repeats a step")
        self.assertEqual(len({tuple(s) for s in P.PHASE_STEPS.values()}), len(P.PHASE_STEPS))

    def test_phase_5_s_usual_steps_are_a_wave_s_not_one_slice_s(self):
        """Gate 8, 4. Dispatch builds a wave of slices, each by its own
        builder. Its usual steps described one slice's loop ("Write the failing
        test", "Make it pass"), which the agent running the wave does not do
        itself, so with the close held to the steps it ticked or dropped them
        every run. They are the wave's now, in the order the wave goes."""
        steps = pipeline_module().PHASE_STEPS[5]
        self.assertEqual(steps, ["Hand each slice to a builder",
                                 "See each slice's failing test before its fix",
                                 "See each builder break its own change",
                                 "Run the whole suite",
                                 "Collect the reports"])
        for old in ("Write the failing test", "Make it pass", "Break it on purpose and watch the test catch it",
                    "Write the report"):
            self.assertNotIn(old, steps)

    def test_phase_start_seeds_the_usual_steps_the_first_doing_and_never_reseeds(self):
        """`init` writes no steps; `phase <n> start` seeds PHASE_STEPS[n], each todo
        with no sub-line, on that phase alone. A restart keeps the ticks, and a
        phase that already has steps of its own is not seeded over. ALYDAS-225,
        K3: the start then marks the first of them doing."""
        P = pipeline_module()
        self.init_scratch("--hold", "none")  # S5: starts a phase past Phase 2
        self.assertEqual({len(p.get("steps") or []) for p in self.board()["phases"]}, {0})
        for n, _, _, _ in P.PHASES:
            out = self.run_pipeline("phase", "scratch", str(n), "start")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(self.steps_of(n), [{"text": t, "state": "todo" if i else "doing", "sub": ""}
                                                for i, t in enumerate(P.PHASE_STEPS[n])])
            self.assertEqual({len(p.get("steps") or []) for p in self.board()["phases"][n + 1:]}, {0} if n < 9 else set())
        # Ticked, then restarted: the ticks stay.
        out = self.run_pipeline("step", "scratch", "5", "1", "done", "--sub", "3 red first")
        self.assertEqual(out.returncode, 0, out.stderr)
        ticked = self.steps_of(5)
        out = self.run_pipeline("phase", "scratch", "5", "start")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.steps_of(5), ticked)
        # A phase whose steps the agent wrote is not seeded over either.
        run = self.board()
        run["phases"][3]["status"] = "pending"
        run["phases"][3]["steps"] = [{"text": "Only this", "state": "todo", "sub": ""}]
        self.write_board(run)
        out = self.run_pipeline("phase", "scratch", "3", "start")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.steps_of(3), [{"text": "Only this", "state": "doing", "sub": ""}])

    def test_step_moves_a_step_by_number_or_text_one_doing_at_a_time_and_keeps_its_sub_line(self):
        """`step <n> <which> <state>` sets the state of the step named by its
        1-based number or its exact text. Moving one to doing marks the phase's
        previous doing step done; todo steps stay todo. `--sub` sets the line
        under it and a call without one keeps it. `--add` appends a step. Each
        call is logged as a `step` event."""
        P = pipeline_module()
        self.init_scratch("--hold", "none")  # S5: starts a phase past Phase 2
        self.assertEqual(self.run_pipeline("phase", "scratch", "5", "start").returncode, 0)
        usual = P.PHASE_STEPS[5]
        state = lambda: [(s["state"], s["sub"]) for s in self.steps_of(5)]
        out = self.run_pipeline("step", "scratch", "5", "1", "doing", "--sub", "writing the failing test")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(state()[:3], [("doing", "writing the failing test"), ("todo", ""), ("todo", "")])
        # By its text, to doing: the one doing before it is done, its sub kept.
        out = self.run_pipeline("step", "scratch", "5", usual[2], "doing")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(state()[:3], [("done", "writing the failing test"), ("todo", ""), ("doing", "")])
        # A sub-line alone changes nothing else; a state alone keeps the sub.
        out = self.run_pipeline("step", "scratch", "5", "3", "--sub", "6 red on main")
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_pipeline("step", "scratch", "5", "3", "doing")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(state()[:3], [("done", "writing the failing test"), ("todo", ""), ("doing", "6 red on main")])
        # Back to todo, and done by hand.
        out = self.run_pipeline("step", "scratch", "5", "1", "todo")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(state()[0], ("todo", "writing the failing test"))
        # Added: todo by default, or the state given, and a doing one takes over.
        out = self.run_pipeline("step", "scratch", "5", "Fix the flaky screenshot", "--add")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.steps_of(5)[-1], {"text": "Fix the flaky screenshot", "state": "todo", "sub": ""})
        out = self.run_pipeline("step", "scratch", "5", "Ask about criterion 3", "doing", "--add", "--sub", "needs production")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.steps_of(5)[-1], {"text": "Ask about criterion 3", "state": "doing", "sub": "needs production"})
        self.assertEqual([s["state"] for s in self.steps_of(5)].count("doing"), 1)
        self.assertEqual(self.steps_of(5)[2]["state"], "done")
        self.assertEqual(len(self.steps_of(5)), len(usual) + 2)
        # A step on a phase never started, added, is its first.
        out = self.run_pipeline("step", "scratch", "7", "Start a local desk", "--add")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.steps_of(7), [{"text": "Start a local desk", "state": "todo", "sub": ""}])
        logged = [json.loads(line) for line in self.events_text().splitlines()]
        steps = [e["note"] for e in logged if e["event"] == "step" and e["phase"] == 5]
        self.assertEqual(steps[0], f"step 1 doing: {usual[0]}")
        self.assertEqual(steps[1], f"step 3 doing: {usual[2]}")
        self.assertEqual(len(steps), 7)

    def test_a_step_nobody_wrote_exits_1_listing_the_steps_and_writes_nothing(self):
        """A number out of range, text no step has, a sub-line past its bound, or
        a state word that is not one of the three: exit non-zero, name the
        steps, and leave the ledger as it was."""
        P = pipeline_module()
        self.init_scratch("--hold", "none")  # S5: starts a phase past Phase 2
        self.assertEqual(self.run_pipeline("phase", "scratch", "5", "start").returncode, 0)
        for which in ("0", str(len(P.PHASE_STEPS[5]) + 1), "No such step", P.PHASE_STEPS[5][0].upper()):
            before = self.board_bytes()
            out = self.run_pipeline("step", "scratch", "5", which, "done")
            self.assertEqual(out.returncode, 1, f"{which}: {out.stdout}")
            self.assertIn(f"1. {P.PHASE_STEPS[5][0]}", out.stderr)
            self.assertIn(f"{len(P.PHASE_STEPS[5])}. {P.PHASE_STEPS[5][-1]}", out.stderr)
            self.assertEqual(self.board_bytes(), before, which)
        before = self.board_bytes()
        out = self.run_pipeline("step", "scratch", "5", "1", "finished")
        self.assertEqual(out.returncode, 2, out.stdout)
        self.assertEqual(self.board_bytes(), before)
        out = self.run_pipeline("step", "scratch", "5", "1", "--sub", "x" * (P.LEDGER_BOUNDS["stepSub"] + 1))
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("--sub", out.stderr)
        self.assertEqual(self.board_bytes(), before)
        # A phase with no steps says so.
        out = self.run_pipeline("step", "scratch", "8", "1", "done")
        self.assertEqual(out.returncode, 1, out.stdout)
        self.assertIn("no steps", out.stderr)

    def test_every_step_call_sends_one_beat_naming_the_phase_the_run_is_in(self):
        """Each `step` call is one beat, carrying the step as it now is, whatever
        the call changed: a state, a sub-line, or an added step. The beat names
        the phase the run is in, as a note's does, even for a step on another."""
        self.init_scratch("--hold", "none")  # S5: starts a phase past Phase 2
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            self.assertEqual(self.run_pipeline("phase", "scratch", "5", "start", env=env).returncode, 0)
            desk.clear()
            calls = (("5", "2", "doing", "--sub", "fixing the setup"), ("5", "2", "--sub", "fixing it"),
                     ("5", "2", "done"), ("5", "One more", "--add"), ("0", "Late frame step", "done", "--add"))
            for i, call in enumerate(calls, start=1):
                out = self.run_pipeline("step", "scratch", *call, env=env)
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                self.assertEqual(len(desk.requests), i, f"{call} sent {len(desk.requests) - i + 1} beats")
            bodies = desk.bodies()
        self.assertEqual({b["statusLine"] for b in bodies}, {"Phase 5 · Dispatch · running"})
        self.assertEqual({b["ended"] for b in bodies}, {False})
        steps = lambda body, n: [p for p in body["ledger"]["phases"] if p["n"] == n][0]["story"]["steps"]
        self.assertEqual(steps(bodies[0], 5)[1]["state"], "doing")
        self.assertEqual(steps(bodies[0], 5)[1]["sub"], "fixing the setup")
        self.assertEqual(steps(bodies[1], 5)[1]["sub"], "fixing it")
        self.assertEqual(steps(bodies[2], 5)[1], {"text": pipeline_module().PHASE_STEPS[5][1], "state": "done", "sub": "fixing it"})
        self.assertEqual(steps(bodies[3], 5)[-1], {"text": "One more", "state": "todo"})
        self.assertEqual(steps(bodies[4], 0), [{"text": "Late frame step", "state": "done"}])
        # Without a desk, a step sends nothing and still lands.
        out = self.run_pipeline("step", "scratch", "5", "3", "doing")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.steps_of(5)[2]["state"], "doing")

    def test_a_stop_on_a_step_beat_ends_the_run_and_exits_3(self):
        """The same as a stop on a note's beat: the step lands, one ended beat, exit 3."""
        self.init_scratch("--hold", "none")  # S5: starts a phase past Phase 2
        self.assertEqual(self.run_pipeline("phase", "scratch", "5", "start").returncode, 0)
        with Listener(reply={"session": {"id": "s1"}, "directive": {"kind": "stop"}}) as desk:
            out = self.run_pipeline("step", "scratch", "5", "1", "doing", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertIn("pipeline: the desk says stop", out.stdout)
            self.assertEqual([b["ended"] for b in desk.bodies()], [False, True])
        self.assertEqual(self.steps_of(5)[0]["state"], "doing")
        self.assertIn("stopped from the desk", self.events_text())
        with Listener(reply={"session": {"id": "s1"}, "directive": {"kind": "message", "text": "B please"}}) as desk:
            out = self.run_pipeline("step", "scratch", "5", "1", "done", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("pipeline: message from the desk: B please", out.stdout)
            self.assertEqual(len(desk.requests), 1)

    def test_phase_done_closes_the_doing_step_and_leaves_todo_steps_todo(self):
        self.init_scratch("--hold", "none")  # S5: starts a phase past Phase 2
        self.assertEqual(self.run_pipeline("phase", "scratch", "5", "start").returncode, 0)
        self.assertEqual(self.run_pipeline("step", "scratch", "5", "1", "done").returncode, 0)
        self.assertEqual(self.run_pipeline("step", "scratch", "5", "2", "doing", "--sub", "nearly").returncode, 0)
        before = [s["state"] for s in self.steps_of(5)]
        out = self.run_pipeline("phase", "scratch", "5", "done", "--force", "a scratch run")
        self.assertEqual(out.returncode, 0, out.stderr)
        after = self.steps_of(5)
        self.assertEqual([s["state"] for s in after], ["done", "done"] + before[2:])
        self.assertEqual(set(before[2:]), {"todo"})
        self.assertEqual(after[1]["sub"], "nearly")
        # Any other boundary leaves a doing step doing.
        self.assertEqual(self.run_pipeline("phase", "scratch", "6", "start").returncode, 0)
        self.assertEqual(self.run_pipeline("step", "scratch", "6", "1", "doing").returncode, 0)
        for event in ("blocked", "failed"):
            out = self.run_pipeline("phase", "scratch", "6", event)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(self.steps_of(6)[0]["state"], "doing", event)

    # ---------- ALYDAS-225, K1: a phase does not close with a step still to-do ----------

    def frame_ready(self):
        """Phase 0 running with its goal doc and its plain line, so the only
        thing between it and a close is its steps."""
        self.init_scratch("--hold", "none")
        os.makedirs(os.path.join(self.root, "specs"), exist_ok=True)
        with open(os.path.join(self.root, "specs", "2026-09-13-scratch-goal.md"), "w") as f:
            f.write("# Goal\n\nE1. Observed by: a scratch test.\n")
        for args in (("phase", "scratch", "0", "start"),
                     ("note", "scratch", "0", "--plain", "Wrote down what done looks like.")):
            out = self.run_pipeline(*args)
            self.assertEqual(out.returncode, 0, out.stderr)

    def test_phase_done_refuses_a_step_still_todo(self):
        """`phase <n> done` exits 2 while a step is to-do, naming each one by its
        number and its text with both ways out, and the phase stays running.
        `check` says the same. A step being done is not named: the close
        finishes it. With every step ticked the phase closes."""
        usual = pipeline_module().PHASE_STEPS[0]
        self.frame_ready()
        out = self.run_pipeline("step", "scratch", "0", "1", "doing")
        self.assertEqual(out.returncode, 0, out.stderr)
        before = self.board_bytes()
        out = self.run_pipeline("phase", "scratch", "0", "done")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("phase 0 cannot close", out.stderr)
        checked = self.run_pipeline("check", "scratch", "0")
        self.assertEqual(checked.returncode, 2, checked.stdout)
        for i, text in enumerate(usual[1:], start=2):
            for said in (out.stderr, checked.stdout):
                self.assertIn(f'step {i} is still to do ("{text}")', said)
                self.assertIn(f"step scratch 0 {i} done", said)
                self.assertIn(f'step scratch 0 {i} dropped --sub "<why>"', said)
        self.assertNotIn("step 1 is still to do", out.stderr)
        self.assertNotIn("goal doc", out.stderr)
        self.assertEqual(self.board_bytes(), before, "a refused close changed the ledger")
        self.assertEqual(self.board()["phases"][0]["status"], "active")
        # The run log says it in words, with no command line in it.
        check = [json.loads(line) for line in self.events_text().splitlines() if '"event": "check"' in line][-1]
        self.assertIn(f"step 2 is still to do ({usual[1]})", check["note"])
        self.assertNotIn("step scratch", check["note"])
        # One step left to-do still refuses, and names only that one.
        for i in range(2, len(usual)):
            self.assertEqual(self.run_pipeline("step", "scratch", "0", str(i), "done").returncode, 0)
        out = self.run_pipeline("phase", "scratch", "0", "done")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertEqual(out.stderr.count("is still to do"), 1, out.stderr)
        self.assertIn(f"step {len(usual)} is still to do", out.stderr)
        # Ticked, it closes, and the step it was on is finished by the close.
        self.assertEqual(self.run_pipeline("step", "scratch", "0", str(len(usual)), "done").returncode, 0)
        out = self.run_pipeline("phase", "scratch", "0", "done")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.board()["phases"][0]["status"], "done")
        self.assertEqual({s["state"] for s in self.steps_of(0)}, {"done"})

    def test_phase_done_force_closes_past_a_step_still_todo_and_only_done_is_held_to_it(self):
        """`--force` closes a phase with steps to-do, as it does past every
        other check, and records why. `failed`, `blocked` and `skipped` run no
        check, so a phase that did not finish its steps says so."""
        self.frame_ready()
        out = self.run_pipeline("phase", "scratch", "0", "done", "--force", "the steps were not kept in a scratch run")
        self.assertEqual(out.returncode, 0, out.stderr)
        phase = self.board()["phases"][0]
        self.assertEqual((phase["status"], phase["forced"]), ("done", "the steps were not kept in a scratch run"))
        self.assertIn("todo", {s["state"] for s in phase["steps"]})
        for n, event in ((1, "failed"), (3, "blocked"), (4, "skipped")):
            self.assertEqual(self.run_pipeline("phase", "scratch", str(n), "start").returncode, 0)
            out = self.run_pipeline("phase", "scratch", str(n), event, "--note", "a scratch run")
            self.assertEqual(out.returncode, 0, f"{event}: {out.stderr}")
            self.assertEqual(self.board()["phases"][n]["status"], event)

    # ---------- ALYDAS-225, K2: dropping a step ----------

    def test_a_dropped_step_needs_a_reason_and_lets_the_phase_close(self):
        """`step <n> <i> dropped --sub "<why>"` is the fourth state. With no
        `--sub`, or a blank one, it is refused, exit 1, nothing written. With
        one, the step keeps its place, its state is dropped and its sub-line is
        the reason; the beat carries both; moving another step to doing leaves
        it dropped; its reason cannot be blanked afterwards; the phase closes
        over it; and the local board draws it with no box, the word "Dropped: ",
        its reason under it, and counts it apart in the heading."""
        usual = pipeline_module().PHASE_STEPS[0]
        why = "Nothing was known yet, so there was nothing to read."
        self.frame_ready()
        for sub in ((), ("--sub", ""), ("--sub", " \t "), ("--sub", "﻿")):
            before = self.board_bytes()
            out = self.run_pipeline("step", "scratch", "0", "2", "dropped", *sub)
            self.assertEqual(out.returncode, 1, f"{sub}: {out.stdout}{out.stderr}")
            self.assertIn('a dropped step says why: step scratch 0 2 dropped --sub "<why>"', out.stderr)
            self.assertEqual(self.board_bytes(), before, f"a refused drop wrote the ledger: {sub}")
        with Listener() as desk:
            out = self.run_pipeline("step", "scratch", "0", "2", "dropped", "--sub", f"  {why} ",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            [body] = desk.bodies()
        self.assertIn("phase 0 step 2 dropped", out.stdout)
        self.assertEqual(self.steps_of(0)[1], {"text": usual[1], "state": "dropped", "sub": why})
        self.assertEqual(len(self.steps_of(0)), len(usual))
        self.assertEqual(body["ledger"]["phases"][0]["story"]["steps"][1], {"text": usual[1], "state": "dropped", "sub": why})
        # Its reason is not blanked afterwards: the desk refuses a dropped step with none.
        before = self.board_bytes()
        out = self.run_pipeline("step", "scratch", "0", "2", "--sub", " ")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("a dropped step says why", out.stderr)
        self.assertEqual(self.board_bytes(), before)
        # Another step named doing leaves the dropped one dropped.
        self.assertEqual(self.run_pipeline("step", "scratch", "0", "3", "doing").returncode, 0)
        self.assertEqual(self.steps_of(0)[1]["state"], "dropped")
        # The phase still does not close over a step to-do, and names only those.
        # Gate 6: said outright (it was `if todo:`, which would have skipped the check in silence).
        self.assertEqual([s["state"] for s in self.steps_of(0)], ["done", "dropped", "doing", "todo"])
        out = self.run_pipeline("phase", "scratch", "0", "done")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertNotIn("step 2 is still to do", out.stderr)
        self.assertEqual(out.stderr.count("is still to do"), 1, out.stderr)
        for i in (1, 4):
            self.assertEqual(self.run_pipeline("step", "scratch", "0", str(i), "done").returncode, 0)
        out = self.run_pipeline("phase", "scratch", "0", "done")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.board()["phases"][0]["status"], "done")
        self.assertEqual([s["state"] for s in self.steps_of(0)], ["done", "dropped", "done", "done"])
        # Drawn: no box, the word, the reason under it, and counted apart.
        frame = parse_html(self.drawn_board()["panels"]["p0"])
        self.assertEqual(frame.heading("Steps").text(), "Steps3 of 4 done, 1 dropped")
        rows = frame.under_headings()["Steps"].children_named("li")
        self.assertEqual([(li.attrs.get("data-s"), len(li.by_class("tick")), li.first("vh"), li.first("st-text"), li.first("sub"))
                          for li in rows],
                         [("done", 1, "Done: ", usual[0], None), ("dropped", 0, "Dropped: ", usual[1], why),
                          ("done", 1, "Done: ", usual[2], None), ("done", 1, "Done: ", usual[3], None)])
        # A board with nothing dropped says nothing about dropping.
        self.assertEqual(self.run_pipeline("phase", "scratch", "1", "start").returncode, 0)
        other = parse_html(self.drawn_board()["panels"]["p1"])
        self.assertNotIn("dropped", other.heading("Steps").text())

    def test_a_dropped_step_can_be_moved_again_and_every_drop_asks_its_reason(self):
        """Gate 6. Dropping is not final: a dropped step moves to any state in
        STEP_STATES, as any step does. And every drop asks its reason, on the
        call that drops: a line already under the step (what it was doing)
        does not stand in for it. The run log says dropped, not done.
        Gate 8, 6: this pinned that a step moved out of dropped kept the
        drop's reason as its line "until it is rewritten", so a step being
        done, or done, read "Dropped before ...". The move now clears the
        line, unless the call gives one with `--sub`; a move between other
        states keeps the line, as before."""
        P = pipeline_module()
        self.init_scratch("--hold", "none")
        ok = lambda *args: self.assertEqual(self.run_pipeline(*args).returncode, 0, args)
        state = lambda: (self.steps_of(6)[0]["state"], self.steps_of(6)[0]["sub"])
        ok("step", "scratch", "6", "Read the page", "--add")
        ok("step", "scratch", "6", "1", "doing", "--sub", "reading it")

        def refused_with_no_reason():
            before = self.board_bytes()
            out = self.run_pipeline("step", "scratch", "6", "1", "dropped")
            self.assertEqual(out.returncode, 1, f"dropped with no --sub over {state()}: {out.stdout}{out.stderr}")
            self.assertIn('a dropped step says why: step scratch 6 1 dropped --sub "<why>"', out.stderr)
            self.assertEqual(self.board_bytes(), before)

        refused_with_no_reason()
        self.assertEqual(len(P.STEP_STATES), 4)
        for to in P.STEP_STATES:
            ok("step", "scratch", "6", "1", "dropped", "--sub", f"Dropped before {to}.")
            self.assertEqual(state(), ("dropped", f"Dropped before {to}."))
            last = [json.loads(line) for line in self.events_text().splitlines() if '"event": "step"' in line][-1]
            self.assertEqual(last["note"], "step 1 dropped: Read the page")
            ok("step", "scratch", "6", "1", to, *(("--sub", "Dropped again.") if to == "dropped" else ()))
            self.assertEqual(state()[0], to)
            if to != "dropped":
                self.assertEqual(state()[1], "", f"moved to {to}, the step still says why it was dropped")
                refused_with_no_reason()
                # With a line of its own on the move, that is its line.
                ok("step", "scratch", "6", "1", "dropped", "--sub", "Dropped once more.")
                ok("step", "scratch", "6", "1", to, "--sub", f"Back to {to}.")
                self.assertEqual(state(), (to, f"Back to {to}."))
                # And a move that does not start from dropped keeps the line.
                for other in ("todo", "doing", "done"):
                    ok("step", "scratch", "6", "1", other)
                    self.assertEqual(state(), (other, f"Back to {to}."))
        # A line alone on a dropped step is its new reason, and it stays dropped.
        ok("step", "scratch", "6", "1", "dropped", "--sub", "Not needed.")
        ok("step", "scratch", "6", "1", "--sub", "Not needed after all.")
        self.assertEqual(state(), ("dropped", "Not needed after all."))

    def test_every_phase_is_held_to_its_steps(self):
        """Gate 6. "Every phase", walked: the command tests close Phases 0 and
        2. Here the check itself is asked about each phase in PHASES, with one
        step in each state in STEP_STATES: the to-do one is named, alone."""
        P = pipeline_module()
        self.assertEqual(len(P.PHASES), 10)
        self.init_scratch("--hold", "none")
        run = self.board()
        for n, _, _, _ in P.PHASES:
            run["phases"][n].update(plain="Said.", steps=[{"text": f"A step {state}", "state": state, "sub": "why"}
                                                          for state in P.STEP_STATES])
            named = [str(problem) for problem in P.run_checks(run, n) if "is still to do" in problem]
            self.assertEqual(len(named), 1, f"phase {n}: {named}")
            self.assertIn(f'step 1 is still to do ("A step todo"): tick it with step scratch {n} 1 done, '
                          f'or drop it with step scratch {n} 1 dropped --sub "<why>"', named[0])

    def test_a_refused_close_names_the_plain_line_then_the_steps_then_the_phase_s_own(self):
        """Gate 6. The order the plan gives the step check: after the plain
        line, before the phase's own artifacts, each step in its own order."""
        self.init_scratch("--hold", "none")
        self.assertEqual(self.run_pipeline("phase", "scratch", "0", "start").returncode, 0)
        out = self.run_pipeline("phase", "scratch", "0", "done")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        said = out.stderr
        places = [said.index(words) for words in ("no plain line", "step 2 is still to do", "step 3 is still to do",
                                                  "step 4 is still to do", "goal doc missing")]
        self.assertEqual(places, sorted(places), said)
        self.assertIn('fix them, or pass --force "reason" to close anyway (recorded on the board).', said)

    # ---------- ALYDAS-225, K3: a running phase always has a step doing ----------

    def test_a_running_phase_has_a_step_doing_from_its_first_beat(self):
        """Read off the beats at the recording desk. The start beat carries
        exactly one step doing, the first. `step <i> done` or `dropped` that
        leaves none doing moves the next to-do step to doing, so between ticks
        there is always one until none is left, and then the phase closes. No
        beat of a running phase with steps carries all of them to-do."""
        usual = pipeline_module().PHASE_STEPS[0]
        self.init_scratch("--hold", "none")
        os.makedirs(os.path.join(self.root, "specs"), exist_ok=True)
        with open(os.path.join(self.root, "specs", "2026-09-13-scratch-goal.md"), "w") as f:
            f.write("# Goal\n\nE1. Observed by: a scratch test.\n")
        states = lambda body, n=0: [s["state"] for s in body["ledger"]["phases"][n]["story"].get("steps", [])]
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            def beat(*args):
                desk.clear()
                out = self.run_pipeline(*args, env=env)
                self.assertEqual(out.returncode, 0, f"{args}: {out.stdout}{out.stderr}")
                bodies = desk.bodies()
                seen.extend(bodies)
                return bodies[-1]
            seen = []
            self.assertEqual(states(beat("phase", "scratch", "0", "start")), ["doing", "todo", "todo", "todo"])
            self.assertEqual(states(beat("note", "scratch", "0", "--plain", "Wrote down what done looks like.")),
                             ["doing", "todo", "todo", "todo"])
            # Done moves on to the next; so does dropped.
            self.assertEqual(states(beat("step", "scratch", "0", "1", "done")), ["done", "doing", "todo", "todo"])
            self.assertEqual(states(beat("step", "scratch", "0", "2", "dropped", "--sub", "Said already.")),
                             ["done", "dropped", "doing", "todo"])
            # A tick out of order, while one is doing, moves nothing.
            self.assertEqual(states(beat("step", "scratch", "0", "4", "done")), ["done", "dropped", "doing", "done"])
            # The last one ticked: none doing, none to-do, and the phase closes.
            self.assertEqual(states(beat("step", "scratch", "0", "3", "done")), ["done", "dropped", "done", "done"])
            self.assertEqual(states(beat("phase", "scratch", "0", "done")), ["done", "dropped", "done", "done"])
            self.assertEqual(self.board()["phases"][0]["status"], "done")

            # With none after it, the first to-do step anywhere is next.
            self.assertEqual(states(beat("phase", "scratch", "1", "start"), 1), ["doing", "todo", "todo", "todo"])
            self.assertEqual(states(beat("step", "scratch", "1", "3", "doing"), 1), ["done", "todo", "doing", "todo"])
            self.assertEqual(states(beat("step", "scratch", "1", "3", "done"), 1), ["done", "todo", "done", "doing"])
            self.assertEqual(states(beat("step", "scratch", "1", "4", "done"), 1), ["done", "doing", "done", "done"])

        # No beat of a running phase with steps carried all of them to-do, and
        # each carried exactly one doing while any was to-do.
        self.assertGreater(len(seen), 10)
        for body in seen:
            for phase in body["ledger"]["phases"]:
                steps = [s["state"] for s in phase["story"].get("steps", [])]
                if phase["status"] == "running" and steps:
                    self.assertNotEqual(set(steps), {"todo"}, f"phase {phase['n']} running with every step to-do")
                    if "todo" in steps:
                        self.assertEqual(steps.count("doing"), 1, f"phase {phase['n']}: {steps}")
        self.assertEqual(len(usual), 4)

    def test_nothing_advances_on_a_phase_that_is_not_running_and_start_picks_up_where_it_left(self):
        """ALYDAS-225, K3. A tick on a phase never started, blocked or done
        moves no other step. `phase <n> start` marks the first to-do step doing
        when none is: on steps the agent added before the start (nothing is
        seeded over them), and on a restart. A restarted phase whose steps are
        all done or dropped is left alone, and so is one with a step doing."""
        self.init_scratch("--hold", "none")
        states = lambda n: [s["state"] for s in self.steps_of(n)]
        ok = lambda *args: self.assertEqual(self.run_pipeline(*args).returncode, 0, args)
        # Never started: the agent's own steps, one ticked.
        ok("step", "scratch", "5", "First of mine", "--add")
        ok("step", "scratch", "5", "Second of mine", "--add")
        ok("step", "scratch", "5", "Third of mine", "--add")
        ok("step", "scratch", "5", "1", "done")
        self.assertEqual(states(5), ["done", "todo", "todo"])
        ok("phase", "scratch", "5", "start")
        self.assertEqual(states(5), ["done", "doing", "todo"])
        self.assertEqual([s["text"] for s in self.steps_of(5)], ["First of mine", "Second of mine", "Third of mine"])
        # Started again with a step doing: left as it is.
        ok("phase", "scratch", "5", "start")
        self.assertEqual(states(5), ["done", "doing", "todo"])
        # Blocked: a tick moves nothing; the restart picks the next up.
        ok("phase", "scratch", "5", "blocked")
        ok("step", "scratch", "5", "2", "done")
        self.assertEqual(states(5), ["done", "done", "todo"])
        ok("phase", "scratch", "5", "start")
        self.assertEqual(states(5), ["done", "done", "doing"])
        # Done (forced, with a step to-do): a tick on it moves nothing.
        ok("step", "scratch", "5", "A fourth", "--add")
        ok("step", "scratch", "5", "A fifth", "--add")
        ok("phase", "scratch", "5", "done", "--force", "a scratch run")
        self.assertEqual(states(5), ["done", "done", "done", "todo", "todo"])
        ok("step", "scratch", "5", "4", "dropped", "--sub", "Not needed.")
        self.assertEqual(states(5), ["done", "done", "done", "dropped", "todo"])
        # Every step done or dropped: a restart marks none.
        ok("step", "scratch", "5", "5", "done")
        ok("phase", "scratch", "5", "start")
        self.assertEqual(states(5), ["done", "done", "done", "dropped", "done"])

    # ---------- ALYDAS-225, Gate 6: one rule wherever the script changes a phase's steps ----------

    def test_a_running_phase_with_a_step_to_do_has_one_doing_whatever_the_command(self):
        """Gate 6. K3 moved on only after `done` or `dropped`, so three ways were
        left to a running phase with a step to-do and none doing: `step <i> todo`
        on the one doing, `--add` after the steps were cleared, and `--add` after
        the only step was done. It is one rule now, on every path through `step`
        and at `phase start`: on a running phase, when no step is doing and one
        is to-do, the first to-do step after the one touched, else the first
        anywhere, becomes doing, and the command says so. Read off the beats at
        the recording desk: the three by name; then every state in STEP_STATES
        on every step of three, from the same place; then a generated walk of
        those commands, `--add`, a line alone, `--clear steps` and a restart,
        shuffled into rounds. Each beat is checked."""
        import random
        P = pipeline_module()
        self.init_scratch("--hold", "none")
        states = lambda body, n: [s["state"] for s in body["ledger"]["phases"][n]["story"].get("steps", [])]
        held = {}  # by phase: the beats in which it was running with a step to-do, so the rule was asserted

        def one_doing(body, after):
            """The rule, on every phase of one beat; and the ledger says what the beat does."""
            for phase in body["ledger"]["phases"]:
                steps = states(body, phase["n"])
                self.assertLessEqual(steps.count("doing"), 1, f"after {after}: phase {phase['n']} has two doing: {steps}")
                self.assertEqual(steps, [s["state"] for s in self.steps_of(phase["n"]) or []], f"after {after}")
                if phase["status"] == "running" and "todo" in steps:
                    held[phase["n"]] = held.get(phase["n"], 0) + 1
                    self.assertEqual(steps.count("doing"), 1, f"after {after}: phase {phase['n']} is running with a step "
                                                              f"to-do and none doing: {steps}")

        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}

            def beat(*args, refused=False):
                """One command and the beat it sent; None when it was refused, having sent nothing."""
                desk.clear()
                out = self.run_pipeline(*args, env=env)
                self.assertIn(out.returncode, (0, 1) if refused else (0,), f"{args}: {out.stdout}{out.stderr}")
                bodies = desk.bodies()
                self.assertEqual(bool(bodies), out.returncode == 0, f"{args}: {len(bodies)} beats, exit {out.returncode}")
                for body in bodies:
                    one_doing(body, args)
                return (bodies[-1], out.stdout) if bodies else (None, out.stdout)

            # 1. `todo` on the one doing, with to-do steps after it: the next takes over.
            body, _ = beat("phase", "scratch", "0", "start")
            self.assertEqual(states(body, 0), ["doing", "todo", "todo", "todo"])
            body, said = beat("step", "scratch", "0", "1", "todo")
            self.assertEqual(states(body, 0), ["todo", "doing", "todo", "todo"])
            self.assertIn("phase 0 step 1 todo\n", said)
            self.assertIn("phase 0 step 2 is now doing: a running phase always has one step doing", said)
            # With none after it, the first to-do anywhere, which here is step 1.
            for i in ("2", "3", "4"):
                body, said = beat("step", "scratch", "0", i, "done")
            self.assertEqual(states(body, 0), ["doing", "done", "done", "done"])
            self.assertIn("phase 0 step 1 is now doing", said)
            # `todo` on the one doing when it is the only one left: it stays doing, and the command says why.
            body, said = beat("step", "scratch", "0", "1", "todo")
            self.assertEqual(states(body, 0), ["doing", "done", "done", "done"])
            self.assertIn("phase 0 step 1 stays doing: it is the only step still to do, and a running phase "
                          "always has one step doing", said)
            self.assertNotIn("phase 0 step 1 todo", said)
            # A command that leaves one doing says nothing more than it did.
            body, said = beat("step", "scratch", "0", "2", "todo")
            self.assertEqual(states(body, 0), ["doing", "todo", "done", "done"])
            self.assertEqual(said.count("doing"), 0, said)
            # 2. The steps cleared, then one added: it is the one doing. Gate 8, 3:
            # a running phase's steps are cleared only once each is done or dropped.
            for i in ("2", "1"):
                beat("step", "scratch", "0", i, "done")
            body, _ = beat("note", "scratch", "0", "--clear", "steps")
            self.assertEqual(states(body, 0), [])
            body, said = beat("step", "scratch", "0", "A step of my own", "--add")
            self.assertEqual(states(body, 0), ["doing"])
            self.assertIn("phase 0 step 1 is now doing", said)
            # 3. The only step done, then another added: it is the one doing.
            body, _ = beat("step", "scratch", "0", "1", "done")
            self.assertEqual(states(body, 0), ["done"])
            body, _ = beat("step", "scratch", "0", "Another of my own", "--add")
            self.assertEqual(states(body, 0), ["done", "doing"])
            # Added while one is doing: to-do, as before.
            body, said = beat("step", "scratch", "0", "A third", "--add")
            self.assertEqual(states(body, 0), ["done", "doing", "todo"])
            self.assertEqual(said.count("doing"), 0, said)
            # A line alone under a step, on a phase a hand left with none doing: the rule mends it.
            run = self.board()
            run["phases"][0]["steps"][1]["state"] = "todo"
            self.write_board(run)
            body, _ = beat("step", "scratch", "0", "3", "--sub", "a line")
            self.assertEqual(states(body, 0), ["done", "doing", "todo"])

            # Not running, nothing moves: never started, then blocked, then closed.
            body, _ = beat("step", "scratch", "6", "Mine", "--add")
            body, _ = beat("step", "scratch", "6", "Mine too", "--add")
            body, _ = beat("step", "scratch", "6", "1", "todo")
            self.assertEqual(states(body, 6), ["todo", "todo"])
            beat("phase", "scratch", "6", "start")
            body, _ = beat("phase", "scratch", "6", "blocked")
            self.assertEqual(states(body, 6), ["doing", "todo"])
            body, _ = beat("step", "scratch", "6", "1", "todo")
            self.assertEqual(states(body, 6), ["todo", "todo"])
            body, said = beat("step", "scratch", "6", "Mine as well", "--add")
            self.assertEqual(states(body, 6), ["todo", "todo", "todo"])
            self.assertNotIn("doing", said)
            body, _ = beat("phase", "scratch", "6", "done", "--force", "a scratch run")
            body, _ = beat("step", "scratch", "6", "2", "todo")
            self.assertEqual(states(body, 6), ["todo", "todo", "todo"])

            # Every state on every step, each from the same place: three steps, the
            # one named doing and the others to-do. Whatever it is moved to, one is
            # doing afterwards: itself if it was told doing, else the next round.
            beat("phase", "scratch", "5", "start")
            for i in (0, 1, 2):
                for state in P.STEP_STATES:
                    run = self.board()
                    run["phases"][5]["steps"] = [{"text": f"Step {j + 1}", "state": "doing" if j == i else "todo", "sub": ""}
                                                 for j in (0, 1, 2)]
                    self.write_board(run)
                    body, _ = beat("step", "scratch", "5", str(i + 1), state,
                                   *(("--sub", "Not needed.") if state == "dropped" else ()))
                    after = states(body, 5)
                    self.assertEqual(after.index("doing"), i if state == "doing" else (i + 1) % 3, f"step {i + 1} {state}: {after}")
                    self.assertEqual(after[i], "doing" if state == "doing" else state, f"step {i + 1} {state}: {after}")

            # The walk, on the same phase: every command, shuffled, four rounds over.
            commands = [("step", "scratch", "5", str(i), state) + (("--sub", "Not needed.") if state == "dropped" else ())
                        for i in (1, 2, 3) for state in P.STEP_STATES]
            commands += [("step", "scratch", "5", "2", "--sub", "a line"), ("note", "scratch", "5", "--clear", "steps"),
                         ("phase", "scratch", "5", "start")]
            self.assertEqual(len(commands), 3 * len(P.STEP_STATES) + 3)
            shuffle, sent, added, before_the_walk = random.Random(225), 0, 0, held.get(5, 0)
            for round_ in range(4):
                order = commands + [("step", "scratch", "5", None, "--add")] * 3
                shuffle.shuffle(order)
                for args in order:
                    if args[3] is None:
                        added += 1
                        args = args[:3] + (f"Added {added}",) + args[4:]
                    body, _ = beat(*args, refused=True)
                    sent += body is not None
        # Not vacuous. Measured again at Gate 8, where `--clear steps` is
        # refused on a running phase with a step to-do or doing, so the list
        # is seldom emptied: 68 of the walk's 84 commands beat (it was 59; the
        # rest are the refused clears and a step named before it was added),
        # and in 66 of those beats (it was 31) its phase had a to-do step to
        # hold the rule to.
        self.assertGreaterEqual(sent, 60, f"the walk's commands were mostly refused: {sent} sent, {held}")
        self.assertGreaterEqual(held.get(5, 0) - before_the_walk, 55, (held, before_the_walk))

    def test_no_step_is_taken_up_on_a_phase_in_any_status_but_running(self):
        """Gate 6. "Not running" walked from the script's own table rather than
        named by hand: a phase never started, and one in each status
        EVENT_TO_STATUS can leave it in other than active. Whatever `step` does
        there (done, dropped, todo, a line alone, `--add`), no step becomes
        doing unless the command named it."""
        P = pipeline_module()
        self.init_scratch("--hold", "none")
        ok = lambda *args: self.assertEqual(self.run_pipeline(*args).returncode, 0, args)
        events = [None] + [event for event, status in P.EVENT_TO_STATUS.items() if status != "active"]
        self.assertEqual(sorted(P.EVENT_TO_STATUS[e] for e in events[1:]), ["blocked", "done", "failed", "skipped"])
        for n, event in zip((3, 4, 5, 6, 7), events, strict=True):
            for text in ("One", "Two", "Three", "Four"):
                ok("step", "scratch", str(n), text, "--add")
            if event:
                ok("phase", "scratch", str(n), "start")
                self.assertEqual(self.steps_of(n)[0]["state"], "doing")
                ok("phase", "scratch", str(n), event, "--note", "a scratch run",
                   *(("--force", "a scratch run") if event == "done" else ()))
            status = self.board()["phases"][n]["status"]
            self.assertEqual(status, P.EVENT_TO_STATUS[event] if event else "pending")
            for args in (("1", "done"), ("2", "dropped", "--sub", "Not needed."), ("3", "todo"),
                         ("3", "--sub", "a line"), ("A fifth", "--add")):
                ok("step", "scratch", str(n), *args)
                self.assertNotIn("doing", [s["state"] for s in self.steps_of(n)], f"{status}, after step {args}")
            self.assertEqual([s["state"] for s in self.steps_of(n)], ["done", "dropped", "todo", "todo", "todo"], status)

    def test_the_trim_keeps_each_step_s_state_and_a_dropped_step_s_reason(self):
        """Gate 6. A ledger over the desk's budget is cut to a width. At every
        width the trim can choose, each step it still sends is in the state it
        was in, every state in STEP_STATES walked, and a dropped one still
        says why: cut, never blank, since the desk refuses a whole beat for a
        dropped step with no line under it. At width 0 the steps go whole."""
        P = pipeline_module()
        story = {"steps": [{"text": f"A step that is {state}", "state": state, "sub": "Why: " + "it was long, " * 20 + "and that is all."}
                           for state in P.STEP_STATES]}
        self.assertGreater(len(story["steps"][0]["sub"]), P.TRIM_TEXT_FLOOR)
        for width in (1, 2, 17, P.TRIM_TEXT_FLOOR, 10_000):
            cut = P.shaped_story(story, width)["steps"]
            self.assertEqual([s["state"] for s in cut], list(P.STEP_STATES), f"width {width}")
            for step in cut:
                self.assertTrue(P.said(step.get("sub")), f"width {width}: {step}")
                self.assertEqual(step["sub"], P.clip(story["steps"][0]["sub"], width), f"width {width}")
                self.assertLessEqual(len(step["sub"]), width)
        self.assertIsNone(P.shaped_story(story, 0))

    def test_a_step_in_a_state_the_script_does_not_know_is_to_do_for_the_check_as_for_the_beat(self):
        """Gate 6. A step a hand wrote with a state outside STEP_STATES, or with
        none, is sent to the desk as to-do. So it is to-do for the close as
        well: the check and the beat read a step's state the same way, and a
        phase does not close over a step the desk shows as still to do."""
        self.frame_ready()
        run = self.board()
        steps = run["phases"][0]["steps"]
        steps[0]["state"], steps[1]["state"], steps[3]["state"] = "done", "halfway", "done"
        del steps[2]["state"]
        self.write_board(run)
        out = self.run_pipeline("phase", "scratch", "0", "done")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertEqual(out.stderr.count("is still to do"), 2, out.stderr)
        for i in (2, 3):
            self.assertIn(f'step {i} is still to do ("{steps[i - 1]["text"]}")', out.stderr)
        with Listener() as desk:
            out = self.run_pipeline("note", "scratch", "0", "--scene", "We know the problem.",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            [body] = desk.bodies()
        self.assertEqual([s["state"] for s in body["ledger"]["phases"][0]["story"]["steps"]], ["done", "todo", "todo", "done"])
        # And for the rule that keeps one doing: the next command on its steps takes the first of them up.
        self.assertEqual(self.run_pipeline("step", "scratch", "0", "4", "--sub", "a line").returncode, 0)
        self.assertEqual([s.get("state") for s in self.steps_of(0)], ["done", "doing", None, "done"])
        self.assertEqual(self.run_pipeline("step", "scratch", "0", "2", "done").returncode, 0)
        self.assertEqual([s.get("state") for s in self.steps_of(0)], ["done", "done", "doing", "done"])
        self.assertEqual(self.run_pipeline("phase", "scratch", "0", "done").returncode, 0)

    # ---------- ALYDAS-225, Gate 8: what the review found ----------

    # Reasons that say nothing once the beat has cleaned them: an escape, a
    # direction override, a pair of isolates, and a mix with a tab and U+FEFF.
    UNSAID = ("\x1b", "‮", "⁦⁩", " \x07‪\t﻿\x7f")

    def test_a_drop_reason_that_cleans_to_nothing_is_no_reason(self):
        """Gate 8, 1. The reason is tested as the beat will send it. The beat
        takes control characters and direction marks out of a line, so a reason
        made only of them leaves the step dropped with no line, and the desk
        refuses that beat and every one after it. Such a drop is refused, exit
        1, nothing written and nothing sent; so is rewriting a reason to one.
        A reason with such characters in it goes cleaned, and the desk takes it."""
        usual = pipeline_module().PHASE_STEPS[0]
        self.frame_ready()
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            for sub in self.UNSAID:
                before = self.board_bytes()
                out = self.run_pipeline("step", "scratch", "0", "2", "dropped", "--sub", sub, env=env)
                self.assertEqual(out.returncode, 1, f"{sub!r} was taken as a reason: {out.stdout}{out.stderr}")
                self.assertIn('a dropped step says why: step scratch 0 2 dropped --sub "<why>"', out.stderr)
                self.assertEqual(self.board_bytes(), before, f"a refused drop wrote the ledger: {sub!r}")
                self.assertEqual(desk.requests, [], f"a refused drop was sent: {sub!r}")
            out = self.run_pipeline("step", "scratch", "0", "2", "dropped", "--sub", "\x1b[31mNot needed‮", env=env)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            [body] = desk.bodies()
            self.assertEqual(body["ledger"]["phases"][0]["story"]["steps"][1],
                             {"text": usual[1], "state": "dropped", "sub": "[31mNot needed"})
            self.assertEqual(desk_refusals(self, [body]), [None])
            # Rewritten afterwards to a line that cleans to nothing: refused the same.
            desk.clear()
            for sub in self.UNSAID:
                before = self.board_bytes()
                out = self.run_pipeline("step", "scratch", "0", "2", "--sub", sub, env=env)
                self.assertEqual(out.returncode, 1, f"{sub!r}: {out.stdout}{out.stderr}")
                self.assertIn("a dropped step says why", out.stderr)
                self.assertEqual(self.board_bytes(), before)
            self.assertEqual(desk.requests, [])

    def test_a_step_dropped_on_the_ledger_with_no_reason_is_to_do_for_the_check_the_rule_and_the_beat(self):
        """Gate 8, 1. A ledger a hand wrote can hold a dropped step whose line
        is missing, blank, or cleans to nothing. The beat never carries it as
        dropped (the desk would refuse the whole beat): it goes as to-do, with
        no line. And as for a state the script does not know, the three agree:
        the close names it as still to do, and the rule that keeps one step
        doing takes it up."""
        P = pipeline_module()
        usual = P.PHASE_STEPS[0]
        self.frame_ready()
        run = self.board()
        steps = run["phases"][0]["steps"]
        steps[0]["state"] = "done"
        steps[1].update(state="dropped", sub="\x1b")
        steps[2].update(state="dropped", sub="‮ ")
        steps[3]["state"] = "dropped"
        del steps[3]["sub"]
        self.write_board(run)
        with Listener() as desk:
            out = self.run_pipeline("note", "scratch", "0", "--scene", "We know the problem.",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            [body] = desk.bodies()
        sent = body["ledger"]["phases"][0]["story"]["steps"]
        for step in sent:
            self.assertFalse(step["state"] == "dropped" and not P.said(step.get("sub")), f"sent dropped with no reason: {step}")
        self.assertEqual(sent, [{"text": usual[0], "state": "done"}] + [{"text": text, "state": "todo"} for text in usual[1:]])
        self.assertEqual(desk_refusals(self, [body]), [None])
        # The check: each is still to do.
        out = self.run_pipeline("phase", "scratch", "0", "done")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertEqual(out.stderr.count("is still to do"), 3, out.stderr)
        for i in (2, 3, 4):
            self.assertIn(f'step {i} is still to do ("{usual[i - 1]}")', out.stderr)
        # The rule: the next command on the phase's steps takes the first of them up.
        out = self.run_pipeline("step", "scratch", "0", "1", "--sub", "a line")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("phase 0 step 2 is now doing", out.stdout)
        self.assertEqual([s["state"] for s in self.steps_of(0)], ["done", "doing", "dropped", "dropped"])
        # Given its reason, a step dropped by hand is dropped everywhere, and the rest tick as any step does.
        for args in (("3", "--sub", "Said in the request."), ("2", "done"), ("4", "done")):
            self.assertEqual(self.run_pipeline("step", "scratch", "0", *args).returncode, 0, args)
        self.assertEqual([s["state"] for s in self.steps_of(0)], ["done", "done", "dropped", "done"])
        self.assertEqual(self.run_pipeline("phase", "scratch", "0", "done").returncode, 0)

    def test_an_added_step_dropped_with_no_reason_is_told_the_call_that_adds_it(self):
        """Gate 8, 7. `step "<text>" dropped --add` with no reason is refused
        before the step is added, so the fix it printed named a step number
        that did not exist. It prints the same `--add` call with `--sub
        "<why>"`, the text quoted for the shell: typed with a reason, it adds
        the step dropped. A drop by number still names the number."""
        self.frame_ready()
        there = len(self.steps_of(0))
        for text, sub in (("Check the old page", ()), ("It's a step; with $HOME in it", ("--sub", " ")),
                          ("Ask about criterion 3", ("--sub", "\x1b"))):
            before = self.board_bytes()
            out = self.run_pipeline("step", "scratch", "0", text, "dropped", "--add", *sub)
            self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
            self.assertEqual(self.board_bytes(), before)
            self.assertNotIn(f"step scratch 0 {there + 1} dropped", out.stderr, "the fix names a step that was never added")
            said = out.stderr.strip().splitlines()
            self.assertEqual(len(said), 1, out.stderr)
            self.assertTrue(said[0].startswith("pipeline: a dropped step says why: step scratch 0 "), said[0])
            fix = shlex.split(said[0].split("a dropped step says why: ", 1)[1])
            self.assertEqual(fix, ["step", "scratch", "0", text, "dropped", "--add", "--sub", "<why>"])
            out = self.run_pipeline(*["Not needed." if word == "<why>" else word for word in fix])
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            there += 1
            self.assertEqual(len(self.steps_of(0)), there)
            self.assertEqual(self.steps_of(0)[-1], {"text": text, "state": "dropped", "sub": "Not needed."})
        out = self.run_pipeline("step", "scratch", "0", "2", "dropped")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertEqual(out.stderr.strip(), 'pipeline: a dropped step says why: step scratch 0 2 dropped --sub "<why>"')

    def test_the_run_log_says_which_step_the_rule_moved_in_the_words_the_command_prints(self):
        """Gate 8, 5. The run log is how the desk replays a run. A `step` call
        writes one event for the step it named, in the state that step ended
        in. When the rule that keeps one step doing moved a step, or kept the
        named one doing against `todo`, a second event says so, in the words
        the command prints after "phase <n> ". A call that moved nothing writes
        one event. Read from the events file."""
        P = pipeline_module()
        usual = P.PHASE_STEPS[0]
        self.frame_ready()
        steps = lambda: [json.loads(line)["note"] for line in self.events_text().splitlines()
                         if json.loads(line)["event"] == "step" and json.loads(line)["phase"] == 0]
        rule = "a running phase always has one step doing"

        def step(*args):
            before = steps()
            out = self.run_pipeline("step", "scratch", "0", *args)
            self.assertEqual(out.returncode, 0, f"{args}: {out.stdout}{out.stderr}")
            return steps()[len(before):], out.stdout

        # `todo` on the one doing: it is logged todo, and the step the rule took up is logged too.
        wrote, said = step("1", "todo")
        self.assertEqual(wrote, [f"step 1 todo: {usual[0]}", f"step 2 is now doing: {rule}"])
        self.assertIn(f"phase 0 {wrote[1]}\n", said)
        # A tick that hands on to the next.
        wrote, said = step("2", "done")
        self.assertEqual(wrote, [f"step 2 done: {usual[1]}", f"step 3 is now doing: {rule}"])
        self.assertIn(f"phase 0 {wrote[1]}\n", said)
        # A tick while another is doing moves nothing: one event.
        wrote, said = step("4", "done")
        self.assertEqual(wrote, [f"step 4 done: {usual[3]}"])
        self.assertNotIn("is now doing", said)
        # The rule goes back to the first one to-do.
        wrote, said = step("3", "dropped", "--sub", "Said already.")
        self.assertEqual(wrote, [f"step 3 dropped: {usual[2]}", f"step 1 is now doing: {rule}"])
        # `todo` on the only step left: the event says the state it ended in, and a second says why.
        wrote, said = step("1", "todo")
        self.assertEqual(wrote, [f"step 1 doing: {usual[0]}",
                                 f"step 1 stays doing: it is the only step still to do, and {rule}"])
        self.assertIn(f"phase 0 {wrote[1]}\n", said)
        # An added step the rule takes up at once.
        self.assertEqual(step("1", "done")[0], [f"step 1 done: {usual[0]}"])
        wrote, said = step("One more", "--add")
        self.assertEqual(wrote, [f"step 5 doing: One more", f"step 5 is now doing: {rule}"])
        self.assertIn(f"phase 0 {wrote[1]}\n", said)
        # As the desk's log reads it.
        last = json.loads(self.events_text().splitlines()[-1])
        self.assertEqual(P.log_words(self.board(), last), f"Step 5 is now doing: {rule}")

    def test_note_clear_steps_is_refused_while_a_started_phase_has_a_step_unaccounted_for_and_every_clear_is_logged(self):
        """Gate 8, 3. `note <n> --clear steps` emptied the list of a running
        phase, the close then found no step to-do, and nothing said the steps
        had been there. On a phase that has started (running, or blocked) it is
        now refused while any step is to-do or being done: exit 1, nothing
        written (not the note's other flags either), nothing sent, each such
        step named, with the two ways on. On a phase not yet started, or once
        every step is done or dropped, it clears as before, and every clear
        writes a line in the run log saying how many steps it cleared.
        (It was `test_note_clear_steps_empties_them`, which cleared a running
        phase's untouched steps and expected that to work.)"""
        usual = pipeline_module().PHASE_STEPS[0]
        self.frame_ready()
        ok = lambda *args: self.assertEqual(self.run_pipeline(*args).returncode, 0, args)
        last = lambda: json.loads(self.events_text().splitlines()[-1])

        def refused(n, named, *more):
            before, logged = self.board_bytes(), self.events_text()
            with Listener() as desk:
                out = self.run_pipeline("note", "scratch", str(n), *more, "--clear", "steps",
                                        env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
                self.assertEqual(len(desk.requests), 0, "a refused clear was sent")
            self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
            self.assertIn(f"phase {n} has started, and {named} still to do or being done, so its steps are not cleared", out.stderr)
            self.assertIn(f'drop each with its reason (step scratch {n} <i> dropped --sub "<why>")', out.stderr)
            self.assertIn(f'or add your own with step scratch {n} "<text>" --add', out.stderr)
            self.assertEqual(self.board_bytes(), before, "a refused clear wrote the ledger")
            self.assertEqual(self.events_text(), logged, "a refused clear wrote the run log")

        every = ", ".join(f'step {i} ("{text}")' for i, text in enumerate(usual, start=1))
        refused(0, every + " are")
        # With the note's other flags: none of them is written either.
        refused(0, every + " are", "--plain", "Another line.", "--scene", "Somewhere else.", "--decided", "Something.")
        self.assertEqual(self.board()["phases"][0]["plain"], "Wrote down what done looks like.")
        # One step left, the one being done: still refused, and it alone is named.
        for i in (2, 3, 4):
            ok("step", "scratch", "0", str(i), "done")
        self.assertEqual([s["state"] for s in self.steps_of(0)], ["doing", "done", "done", "done"])
        refused(0, f'step 1 ("{usual[0]}") is')
        # Blocked by hand: it has started, so the same.
        ok("phase", "scratch", "1", "start")
        ok("phase", "scratch", "1", "blocked", "--note", "a scratch run")
        refused(1, ", ".join(f'step {i} ("{text}")' for i, text in enumerate(pipeline_module().PHASE_STEPS[1], start=1)) + " are")
        # Every step done or dropped: it clears, and the run log says how many.
        ok("step", "scratch", "0", "1", "dropped", "--sub", "Nothing was known yet.")
        ok("note", "scratch", "0", "--clear", "steps")
        self.assertEqual(self.steps_of(0), [])
        self.assertEqual((last()["phase"], last()["event"], last()["note"]), (0, "step", "cleared 4 steps"))
        # A phase not yet started: cleared whatever state its steps are in, and logged the same.
        ok("step", "scratch", "7", "Mine", "--add")
        ok("step", "scratch", "7", "Mine too", "doing", "--add")
        ok("note", "scratch", "7", "--clear", "steps")
        self.assertEqual(self.steps_of(7), [])
        self.assertEqual((last()["phase"], last()["event"], last()["note"]), (7, "step", "cleared 2 steps"))
        ok("step", "scratch", "7", "Only one", "--add")
        ok("note", "scratch", "7", "--clear", "steps")
        self.assertEqual(last()["note"], "cleared 1 step")
        ok("note", "scratch", "7", "--clear", "steps", "--clear", "scene")
        self.assertEqual(last()["note"], "cleared 0 steps")
        # In the desk's log it reads as a sentence.
        self.assertEqual(pipeline_module().log_words(self.board(), last()), "Cleared 0 steps")

    def test_the_beat_nests_the_steps_each_cut_to_its_bound(self):
        """The beat's story carries `steps` as [{text, state, sub?}], at most
        `steps` of them, each text and sub-line cut to its bound, a blank sub left
        out, a step with no text left out, and a state the desk does not know sent
        as todo."""
        P = pipeline_module()
        b = P.LEDGER_BOUNDS
        self.assertEqual((b["steps"], b["stepText"], b["stepSub"]), (30, 200, 300))
        self.init_scratch()
        run = self.board()
        run["phases"][5]["steps"] = (
            [{"text": "t" * (b["stepText"] + 9), "state": "doing", "sub": "s" * (b["stepSub"] + 9)},
             {"text": "  ", "state": "done", "sub": "dropped"},
             {"text": "Odd state", "state": "finished", "sub": "  "}]
            + [{"text": f"step {i}", "state": "todo", "sub": ""} for i in range(b["steps"] + 3)])
        self.write_board(run)
        sent = P.beat_ledger(self.board())["phases"][5]["story"]["steps"]
        # The first `steps` are kept, then the blank one left out, as every packet does.
        self.assertEqual(len(sent), b["steps"] - 1)
        self.assertEqual(sent[0], {"text": "t" * b["stepText"], "state": "doing", "sub": "s" * b["stepSub"]})
        self.assertEqual(sent[1], {"text": "Odd state", "state": "todo"})
        self.assertEqual(sent[2], {"text": "step 0", "state": "todo"})
        # A phase not started sends none; an older ledger none.
        self.assertNotIn("steps", P.beat_ledger(self.board())["phases"][6]["story"])

    def test_the_trim_cuts_each_steps_text_and_sub_line_and_keeps_its_state(self):
        P = pipeline_module()
        blank = {"summary": "", "next": "", "decided": [], "measured": [], "wrong": [], "overruled": [],
                 "produced": [], "table": None, "skipReason": "", "forced": "", "plain": ""}
        story = {"steps": [{"text": "t" * 200, "state": "doing", "sub": "s" * 300}] * 30}
        ledger = {"phases": [{**blank, "n": n, "name": f"Phase {n}", "status": "done", "story": story} for n in range(10)],
                  "deferred": [], "terms": {}}
        budget = P.ledger_bytes(ledger) - 20_000
        out, report = P.trim_ledger(ledger, budget)
        self.assertLessEqual(P.ledger_bytes(out), budget)
        width = report["width"]
        self.assertLess(width, 300)
        for p in out["phases"]:
            self.assertEqual({len(s["text"]) for s in p["story"]["steps"]}, {min(200, width)})
            self.assertEqual({len(s["sub"]) for s in p["story"]["steps"]}, {width})
            self.assertEqual({s["state"] for s in p["story"]["steps"]}, {"doing"})
        tiny = P.shaped(ledger, 0, 0, 0)
        self.assertEqual({p["story"] is None for p in tiny["phases"]}, {True})

    def test_the_local_board_draws_the_steps_with_their_states(self):
        """The rendered board carries each step's words, and its template draws
        the list, its count and each of the three states in words. ALYDAS-225,
        K2: the fourth too, and the count now ends "done" before a dropped
        count rather than at the span's end."""
        self.init_scratch("--hold", "none")  # S5: starts a phase past Phase 2
        self.assertEqual(self.run_pipeline("phase", "scratch", "5", "start").returncode, 0)
        self.assertEqual(self.run_pipeline("step", "scratch", "5", "1", "done", "--sub", "three red first").returncode, 0)
        self.assertEqual(self.run_pipeline("step", "scratch", "5", "2", "doing", "--sub", "fixing the setup").returncode, 0)
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.html"), encoding="utf-8") as f:
            html = f.read()
        for text in ("three red first", "fixing the setup", pipeline_module().PHASE_STEPS[5][2],
                     "Steps", " done'", " dropped'", "data-s=", "Done:", "Doing now:", "To do:", "Dropped:",
                     # Gate 6. What makes a dropped step look dropped: the short rule in the box's place, and the strike.
                     ".norule { width: 10px;", 'ul.steps li[data-s="dropped"] .st-text { color: var(--muted); text-decoration: line-through; }'):
            self.assertIn(text, html)

    # ---------- ALYDAS-173, S5: documents ----------

    def produced_of(self, n):
        return self.board()["phases"][n]["produced"]

    def test_note_doc_upserts_onto_produced_by_path_keeping_the_note_and_logs_wrote(self):
        """`--doc "path|title|about|in short"` names a produced file: an existing
        path keeps its note and gains the three; a new path is added with no
        note. Again on the same path replaces what it names and keeps what it
        does not, and a pipe inside the in-short text stays in it."""
        self.init_scratch()
        out = self.run_pipeline("note", "scratch", "3", "--produced", "docs/plans/plan.md=four slices")
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_pipeline("note", "scratch", "3",
                                "--doc", "docs/plans/plan.md|Plan|Four slices, each test first.|Build the check | then the routes.",
                                "--doc", " docs/specs/goal.md | What done looks like ")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.produced_of(3), [
            {"path": "docs/plans/plan.md", "note": "four slices", "title": "Plan",
             "about": "Four slices, each test first.", "in_short": "Build the check | then the routes."},
            {"path": "docs/specs/goal.md", "note": "", "title": "What done looks like"},
        ])
        self.assertIn("docs/specs/goal.md: What done looks like", self.events_text())
        self.assertIn('"event": "wrote"', self.events_text())
        out = self.run_pipeline("note", "scratch", "3", "--doc", "docs/plans/plan.md|The plan")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.produced_of(3)[0], {
            "path": "docs/plans/plan.md", "note": "four slices", "title": "The plan",
            "about": "Four slices, each test first.", "in_short": "Build the check | then the routes."})
        self.assertEqual(len(self.produced_of(3)), 2)

    def test_note_doc_needs_a_path_and_a_title_within_their_bounds_and_writes_nothing_otherwise(self):
        b = pipeline_module().LEDGER_BOUNDS
        self.assertEqual((b["title"], b["about"], b["inShort"]), (120, 300, 600))
        self.init_scratch()
        before = self.board()
        for doc in ("docs/a.md", "docs/a.md|", "docs/a.md|  |about", "|Title", " |Title",
                    "docs/a.md|" + "t" * (b["title"] + 1),
                    "docs/a.md|T|" + "a" * (b["about"] + 1),
                    "docs/a.md|T|a|" + "s" * (b["inShort"] + 1)):
            with self.subTest(doc[:40]):
                out = self.run_pipeline("note", "scratch", "3", "--summary", "should not land", "--doc", doc)
                self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
                self.assertEqual(self.board(), before)
        # At each bound, taken.
        out = self.run_pipeline("note", "scratch", "3", "--doc",
                                "docs/a.md|" + "t" * b["title"] + "|" + "a" * b["about"] + "|" + "s" * b["inShort"])
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_the_beat_sends_a_documents_title_about_and_in_short_each_cut_and_only_when_said(self):
        P = pipeline_module()
        b = P.LEDGER_BOUNDS
        self.init_scratch()
        run = self.board()
        run["phases"][3]["produced"] = [
            {"path": "docs/a.md", "note": "n", "title": "t" * (b["title"] + 5), "about": "a" * (b["about"] + 5),
             "in_short": "s" * (b["inShort"] + 5)},
            {"path": "docs/b.md", "note": "", "title": "B", "about": "  ", "in_short": ""},
            {"path": "docs/c.md", "note": "plain file"},
        ]
        self.write_board(run)
        self.assertEqual(P.beat_ledger(self.board())["phases"][3]["produced"], [
            {"path": "docs/a.md", "note": "n", "title": "t" * b["title"], "about": "a" * b["about"],
             "inShort": "s" * b["inShort"]},
            {"path": "docs/b.md", "note": "", "title": "B"},
            {"path": "docs/c.md", "note": "plain file"},
        ])

    def test_the_trim_cuts_a_documents_title_about_and_in_short_and_never_its_path(self):
        P = pipeline_module()
        blank = {"summary": "", "next": "", "decided": [], "measured": [], "wrong": [], "overruled": [],
                 "table": None, "skipReason": "", "forced": "", "plain": ""}
        produced = [{"path": f"docs/{i:02d}.md", "note": "", "title": "t" * 120, "about": "a" * 300, "inShort": "s" * 600}
                    for i in range(30)]
        ledger = {"phases": [{**blank, "n": n, "name": f"Phase {n}", "status": "done", "produced": produced}
                             for n in range(10)], "deferred": [], "terms": {}}
        budget = P.ledger_bytes(ledger) - 50_000
        out, report = P.trim_ledger(ledger, budget)
        self.assertLessEqual(P.ledger_bytes(out), budget)
        width = report["width"]
        self.assertLess(width, 600)
        for p in out["phases"]:
            self.assertEqual({len(f["inShort"]) for f in p["produced"]}, {width})
            self.assertEqual({len(f["about"]) for f in p["produced"]}, {min(300, width)})
            self.assertEqual({len(f["title"]) for f in p["produced"]}, {min(120, width)})
            self.assertEqual([f["path"] for f in p["produced"]], [f["path"] for f in produced])
        tiny = P.shaped(ledger, 0, 0, 0)
        self.assertEqual({tuple(sorted(f)) for p in tiny["phases"] for f in p["produced"]}, {("note", "path")})

    def test_the_local_board_names_a_document_with_its_about_line_over_its_path(self):
        self.init_scratch()
        out = self.run_pipeline("note", "scratch", "3", "--doc", "docs/plans/plan.md|The plan|Four slices, each test first.")
        self.assertEqual(out.returncode, 0, out.stderr)
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.html"), encoding="utf-8") as f:
            html = f.read()
        for text in ("The plan", "Four slices, each test first.", "docs/plans/plan.md",
                     'class="dtitle"', 'class="dabout"', "e.title"):
            self.assertIn(text, html)

    # ---------- ALYDAS-173, S6: the short version and the run log ----------
    #
    # `story` also writes the run's short version: what happened, where it
    # stands, and what is waiting on Sachit, and every `story` call beats, so a
    # finished run's band appears without waiting for a phase event. The beat
    # carries that short version beside the terms, and the run log: the newest
    # 200 events, each in words a person reads, oldest first. A ledger over its
    # budget drops the oldest log entries before any phase text is cut.

    def short_of(self):
        return self.board().get("story")

    def test_story_writes_what_happened_where_it_stands_and_what_waits_and_clear_takes_each(self):
        """`--happened` and `--needs-you` append (a repeat ignored), `--stands`
        replaces, each logged as a `story` event; `--clear` takes each away."""
        self.init_scratch()
        out = self.run_pipeline("story", "scratch", "--happened", "Proved the leak.", "--happened", "Fixed it in one place.",
                                "--stands", "Built and committed, not pushed.",
                                "--needs-you", "Decide how tokens behave|They drop off Now when refused.")
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_pipeline("story", "scratch", "--happened", "Proved the leak.", "--happened", "Reviewed.",
                                "--stands", "Built, committed and pushed.", "--needs-you", "Check proof 6 on production",
                                "--needs-you", "A pipe | inside the why | stays")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.short_of(), {
            "happened": ["Proved the leak.", "Fixed it in one place.", "Reviewed."],
            "stands": "Built, committed and pushed.",
            "needs_you": [{"what": "Decide how tokens behave", "why": "They drop off Now when refused."},
                          {"what": "Check proof 6 on production", "why": ""},
                          {"what": "A pipe", "why": "inside the why | stays"}],
        })
        events = [json.loads(line) for line in self.events_text().splitlines() if '"story"' in line]
        self.assertEqual([e["note"] for e in events if e["event"] == "story"], [
            "What happened: Proved the leak.", "What happened: Fixed it in one place.",
            "Where it stands: Built and committed, not pushed.", "Waiting on you: Decide how tokens behave",
            "What happened: Reviewed.", "Where it stands: Built, committed and pushed.",
            "Waiting on you: Check proof 6 on production", "Waiting on you: A pipe",
        ])
        self.assertEqual({e["phase"] for e in events}, {None})
        for clear, key, empty in (("happened", "happened", []), ("stands", "stands", ""), ("needs-you", "needs_you", [])):
            out = self.run_pipeline("story", "scratch", "--clear", clear)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(self.short_of()[key], empty, clear)
        # A ledger written before S6 has no story; `--clear` on it still works.
        run = self.board(); run.pop("story", None); self.write_board(run)
        self.assertEqual(self.run_pipeline("story", "scratch", "--clear", "needs-you").returncode, 0)
        self.assertEqual(self.short_of()["needs_you"], [])

    def test_story_past_its_bounds_exits_1_and_writes_nothing(self):
        """Eight lines of what happened, eight things waiting; a line and where it
        stands 300 each, what is waiting 200 and why 500, and what cannot be
        blank. Refused whole, before anything is written."""
        P = pipeline_module()
        b = P.LEDGER_BOUNDS
        self.assertEqual((b["happened"], b["happenedLine"], b["stands"], b["needsYou"], b["needWhat"], b["needWhy"]),
                         (8, 300, 300, 8, 200, 500))
        self.init_scratch()
        self.assertEqual(self.run_pipeline("story", "scratch", *[x for i in range(8) for x in ("--happened", f"Line {i}")]).returncode, 0)
        self.assertEqual(self.run_pipeline("story", "scratch", *[x for i in range(8) for x in ("--needs-you", f"Need {i}|why")]).returncode, 0)
        # At the bounds, astral characters counted as one.
        smile = "\U0001F600"
        self.assertEqual(self.run_pipeline("story", "scratch", "--stands", smile * 300).returncode, 0)
        refused = (
            ("--happened", "Line 9"),
            ("--needs-you", "Need 9|why"),
            ("--happened", "x" * 301),
            ("--happened", "  "),
            ("--stands", "x" * 301),
            ("--needs-you", "|why with no what"),
            ("--needs-you", "w" * 201 + "|why"),
            ("--needs-you", "what|" + "y" * 501),
        )
        run = self.board()
        run["story"]["happened"] = run["story"]["happened"][:7]
        run["story"]["needs_you"] = run["story"]["needs_you"][:7]
        self.write_board(run)
        # With seven of each, one more fits, and two more do not.
        self.assertEqual(self.run_pipeline("story", "scratch", "--happened", "Line 7").returncode, 0)
        self.assertEqual(self.run_pipeline("story", "scratch", "--needs-you", "Need 7|why").returncode, 0)
        for args in refused:
            before = self.board_bytes()
            out = self.run_pipeline("story", "scratch", "--stands", "Changed", *args)
            self.assertEqual(out.returncode, 1, f"{args}: {out.stdout}{out.stderr}")
            self.assertIn(args[0], out.stderr)
            self.assertEqual(self.board_bytes(), before, args)

    def test_every_story_call_sends_one_beat_naming_the_phase_the_run_is_in_and_carrying_the_short_version(self):
        """Each `story` call is one beat, terms alone included; after the run
        ended, the beat keeps it ended; a stop on it exits 3."""
        self.init_scratch("--hold", "none")  # S5: starts a phase past Phase 2
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            self.assertEqual(self.run_pipeline("phase", "scratch", "5", "start", env=env).returncode, 0)
            desk.clear()
            calls = (("--happened", "Proved the leak."), ("--stands", "Built."), ("--needs-you", "Decide|Why."),
                     ("--term", "c1=Criterion 1"), ("--clear", "stands"))
            for i, call in enumerate(calls, start=1):
                out = self.run_pipeline("story", "scratch", *call, env=env)
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                self.assertEqual(len(desk.requests), i, f"{call} sent {len(desk.requests) - i + 1} beats")
            bodies = desk.bodies()
        self.assertEqual({b["statusLine"] for b in bodies}, {"Phase 5 · Dispatch · running"})
        self.assertEqual({b["ended"] for b in bodies}, {False})
        self.assertEqual(bodies[0]["ledger"]["story"], {"happened": ["Proved the leak."]})
        self.assertEqual(bodies[3]["ledger"]["story"], {"happened": ["Proved the leak."], "stands": "Built.",
                                                         "needsYou": [{"what": "Decide", "why": "Why."}]})
        self.assertEqual(bodies[4]["ledger"]["story"], {"happened": ["Proved the leak."], "needsYou": [{"what": "Decide", "why": "Why."}]})
        # The run ends; a story after it keeps it ended.
        run = self.board()
        run["phases"][9]["status"] = "done"
        self.write_board(run)
        with Listener() as desk:
            out = self.run_pipeline("story", "scratch", "--happened", "Shipped.", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stderr)
            [body] = desk.bodies()
        self.assertTrue(body["ended"])
        self.assertEqual(body["ledger"]["story"]["happened"][-1], "Shipped.")
        run["phases"][9]["status"] = "pending"
        self.write_board(dict(self.board(), phases=run["phases"]))
        with Listener(reply={"session": {"id": "s1"}, "directive": {"kind": "stop"}}) as desk:
            out = self.run_pipeline("story", "scratch", "--stands", "Stopping.", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertEqual([b["ended"] for b in desk.bodies()], [False, True])
        self.assertEqual(self.short_of()["stands"], "Stopping.")
        # Without a desk, a story sends nothing and still lands.
        self.assertEqual(self.run_pipeline("story", "scratch", "--stands", "Quiet.").returncode, 0)
        self.assertEqual(self.short_of()["stands"], "Quiet.")

    def test_the_beat_carries_the_short_version_each_cut_to_its_bound_and_none_when_nothing_is_said(self):
        P = pipeline_module()
        b = P.LEDGER_BOUNDS
        self.init_scratch()
        long = lambda field, extra=9: "x" * (b[field] + extra)
        run = self.board()
        self.assertEqual(P.beat_ledger(run)["story"], None)
        run["story"] = {"happened": [long("happenedLine"), "  "] + [f"Line {i}" for i in range(b["happened"] + 3)],
                        "stands": long("stands"),
                        "needs_you": [{"what": long("needWhat"), "why": long("needWhy")}, {"what": " ", "why": "dropped"},
                                      {"what": "Alone", "why": ""}] + [{"what": f"N{i}"} for i in range(b["needsYou"] + 2)]}
        story = P.beat_ledger(run)["story"]
        self.assertEqual(story["happened"][0], "x" * b["happenedLine"])
        self.assertEqual(len(story["happened"]), b["happened"] - 1)
        self.assertEqual(story["stands"], "x" * b["stands"])
        self.assertEqual(story["needsYou"][:2], [{"what": "x" * b["needWhat"], "why": "x" * b["needWhy"]}, {"what": "Alone"}])
        self.assertEqual(len(story["needsYou"]), b["needsYou"] - 1)
        run["story"] = {"happened": ["  "], "stands": " ", "needs_you": []}
        self.assertEqual(P.beat_ledger(run)["story"], None)

    def write_events(self, events):
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.events.jsonl"), "w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def test_the_beat_carries_the_newest_200_events_oldest_first_in_words_a_person_reads(self):
        """Each entry is its time with a zone, its phase (or null), its kind cut to
        20 and its text cut to 1,300 (ALYDAS-175, Gate 8) by code point; the newest 200 of the events and
        the agents the transcripts saw, oldest first."""
        P = pipeline_module()
        b = P.LEDGER_BOUNDS
        self.assertEqual((b["log"], b["logKind"], b["logText"]), (200, 20, 1_300))
        self.init_scratch()
        ts = lambda i: f"2026-09-13T10:{i // 60:02d}:{i % 60:02d}.000Z"
        filler = [{"ts": ts(i), "phase": 5, "event": "log", "note": f"filler {i}"} for i in range(240)]
        named = [
            {"ts": "2026-09-13T12:00:00.000Z", "phase": 3, "event": "start", "note": ""},
            {"ts": "2026-09-13T12:00:01.000Z", "phase": 3, "event": "done", "note": "Four slices."},
            {"ts": "2026-09-13T12:00:02.000Z", "phase": 4, "event": "done", "note": "", "forced": "Sachit ruled it."},
            {"ts": "2026-09-13T12:00:03.000Z", "phase": 5, "event": "check", "note": "cannot close: no red"},
            {"ts": "2026-09-13T12:00:04.000Z", "phase": 5, "event": "step", "note": "step 2 doing: See each slice's failing test before its fix"},
            {"ts": "2026-09-13T12:00:05.000Z", "phase": None, "event": "term", "note": "c1 = Criterion 1"},
            {"ts": "2026-09-13T12:00:06.000Z", "phase": 5, "event": "wrote", "note": "docs/plan.md: The plan"},
            {"ts": "2026-09-13T12:00:07.000Z", "phase": None, "event": "story", "note": "What happened: Proved it."},
            {"ts": "2026-09-13T12:00:08.000Z", "phase": 5, "event": "a-kind-nobody-knows-at-all", "note": "\U0001F600" * 1_400},
            {"ts": "not a time", "phase": 5, "event": "log", "note": "dropped: no time"},
            {"ts": "2026-09-13T12:00:09", "phase": 5, "event": "log", "note": "dropped: no zone"},
            {"ts": "2026-09-13T22:00:10+10:00", "phase": 150, "event": "log", "note": "a phase the desk has no number for"},
        ]
        run = self.board()
        run["events"] = filler + named
        run["telemetry"] = {"agent_rows": [{"phase": 5, "description": "Build slice S2", "first": "2026-09-13T12:00:04.500000+00:00",
                                            "last": "2026-09-13T12:00:07.500000+00:00"}]}
        log = P.beat_ledger(run)["log"]
        self.assertEqual(len(log), b["log"])
        self.assertEqual([e["at"] for e in log], sorted(e["at"] for e in log), "oldest first")
        self.assertTrue(all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", e["at"]) for e in log), log[:3])
        tail = [(e["phase"], e["kind"], e["text"]) for e in log if e["at"] >= "2026-09-13T12:00:00"]
        self.assertEqual(tail[:12], [
            (3, "start", "Phase 3 · Plan started"),
            (3, "done", "Phase 3 · Plan done: Four slices."),
            (4, "done", "Gate 4 · Verify the brief done (closed by hand: Sachit ruled it.)"),
            (5, "check", "Phase 5 · Dispatch could not close yet: no red"),
            (5, "step", "Step 2 doing: See each slice's failing test before its fix"),
            (5, "dispatched", "Agent dispatched: Build slice S2"),
            (None, "term", "Term: c1 = Criterion 1"),
            (5, "wrote", "Wrote docs/plan.md: The plan"),
            (None, "story", "What happened: Proved it."),
            (5, "landed", "Agent landed: Build slice S2"),
            (5, "a-kind-nobody-knows-", "A-kind-nobody-knows-at-all: " + "\U0001F600" * (1_300 - 28)),
            (None, "log", "a phase the desk has no number for"),
        ])
        self.assertNotIn("dropped", json.dumps(log))
        # The newest 200: the oldest fillers are the ones left out.
        fillers = [e["text"] for e in log if e["text"].startswith("filler")]
        self.assertEqual(fillers[-1], "filler 239")
        self.assertEqual(len(fillers), b["log"] - 12)
        self.assertEqual(fillers[0], f"filler {240 - len(fillers)}")
        self.assertEqual(log[0]["kind"], "log")
        # A ledger with no events sends an empty log.
        self.assertEqual(P.beat_ledger({"phases": [], "deferred": []})["log"], [])
        # On the wire, the log is read off the events file as it stands when the
        # beat goes: here, the step the beat is for is its newest entry.
        self.write_events(filler)
        run = self.board(); run["phases"][5]["steps"] = [{"text": "One", "state": "todo", "sub": ""}]; self.write_board(run)
        with Listener() as desk:
            out = self.run_pipeline("step", "scratch", "5", "1", "doing", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stderr)
            [body] = desk.bodies()
        sent = body["ledger"]["log"]
        self.assertEqual(len(sent), b["log"])
        self.assertEqual((sent[-1]["phase"], sent[-1]["kind"], sent[-1]["text"]), (5, "step", "Step 1 doing: One"))
        self.assertEqual(sent[-2]["text"], "filler 239")

    def test_the_log_reads_as_prose_for_every_kind_that_wrote_cli_syntax_or_a_python_value(self):
        """Gate 8, ST-1. The desk's log is read by a person, so no entry carries
        Python's True or False, the `a|b|c` a flag takes, a `what => did` arrow,
        a `CODE=plain` or `key = value`, or a command line to type. Each kind
        that used to is written here through the CLI, and the log built from
        the events it left is compared line for line. The command line stays
        on stderr, where the agent that has to type it reads it."""
        P = pipeline_module()
        self.init_scratch()
        writes = [
            ("phase", "scratch", "1", "start"),
            ("note", "scratch", "1", "--option", "Two columns|rejected|cut off on a phone",
             "--option", "One column|chosen|reads on a phone", "--option", "A drawer|chosen"),
            ("note", "scratch", "1", "--number", "37.9 KB|the largest real ledger", "--number", "12"),
            ("note", "scratch", "1", "--assumed", "The desk is up|It was, all run", "--assumed", "Nobody reads the board"),
            ("note", "scratch", "1", "--wrong", "Tests failed => Gave the reader the file"),
            ("story", "scratch", "--term", "c1=Criterion 1"),
            ("set", "scratch", "goal_path", "docs/specs/goal.md"),
        ]
        for args in writes:
            out = self.run_pipeline(*args)
            self.assertEqual(out.returncode, 0, f"{args}: {out.stderr}")
        # ALYDAS-225, K1. Its steps ticked, so the refusal below is for the two
        # things the log line names and no step is listed between them.
        for i in range(1, len(P.PHASE_STEPS[1]) + 1):
            self.assertEqual(self.run_pipeline("step", "scratch", "1", str(i), "done").returncode, 0)
        refused = self.run_pipeline("phase", "scratch", "1", "done")
        self.assertEqual(refused.returncode, 2, refused.stdout)
        # The command line is still on stderr, for the agent.
        self.assertIn('note scratch 1 --plain "..."', refused.stderr)
        run = self.board()
        run["events"] = P.read_events(os.path.join(self.root, "boards", "2026-09-13-scratch.json"))
        texts = [e["text"] for e in P.beat_ledger(run)["log"]]
        self.assertEqual(texts[1:], [
            "Weighed: Two columns (not chosen): cut off on a phone",
            "Weighed: One column (chosen): reads on a phone",
            "Weighed: A drawer (chosen)",
            "A number: 37.9 KB, the largest real ledger",
            "A number: 12",
            "Assumed: The desk is up (later: It was, all run)",
            "Assumed: Nobody reads the board",
            "Went wrong: Tests failed; done about it: Gave the reader the file",
            "Term: c1 stands for Criterion 1",
            "Set the goal path to docs/specs/goal.md",
            # ALYDAS-225, Gate 8, 5. Each tick but the last hands on to the next
            # step, and the log says that too, in words.
            *(line for i, text in enumerate(P.PHASE_STEPS[1], start=1)
              for line in [f"Step {i} done: {text}"] + [f"Step {i + 1} is now doing: a running phase always has one step doing"]
              * (i < len(P.PHASE_STEPS[1]))),
            "Phase 1 · Brainstorm could not close yet: it has no plain line; the design doc is missing",
        ])
        for text in texts:
            for mark in ("True", "False", "|", "=>", " = ", "--", "note scratch", "docs/superpowers", "*"):
                self.assertNotIn(mark, text)

    def test_an_assumption_s_later_is_stored_without_a_leading_later(self):
        """Gate 7 (Sachit's note): the desk prints "Later:" itself, so a `later`
        written as "Later: ..." read "Later: Later: ...". A leading "Later:",
        in any case and with space around it, is taken off; "Later" that begins
        a sentence is kept."""
        self.init_scratch()
        for value in ("A|Later: it held", "B|later:it held", "C|  LATER  :  it held ", "D|Later that day it held"):
            out = self.run_pipeline("note", "scratch", "1", "--assumed", value)
            self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual([a["later"] for a in self.board()["phases"][1]["assumed"]],
                         ["it held", "it held", "it held", "Later that day it held"])

    def test_note_refuses_any_story_text_past_its_bound_naming_the_flag_and_the_limit(self):
        """Gate 8, ST-2. Every Story flag's text, and each half of one that has
        halves, is held to the desk's bound when `note` is called, as `--doc`
        already was: one character over exits 1 with the flag and the limit
        named, writes nothing and sends nothing, where it used to be kept whole
        and cut silently when the beat went. At the bound, each is kept whole.
        Each flag is tried on its own, so a flag the check skips fails alone."""
        P = pipeline_module()
        b = P.LEDGER_BOUNDS
        x = lambda k: "x" * k
        # (flag, a value with one text one past its bound, the message's words, that bound's key)
        over = [
            ("--plain", x(b["plain"] + 1), "--plain is", "plain"),
            ("--scene", x(b["scene"] + 1), "--scene is", "scene"),
            ("--backstory", x(b["backstory"] + 1), "--backstory is", "backstory"),
            ("--lede", x(b["lede"] + 1), "--lede is", "lede"),
            ("--fixed-means", x(b["storyEntry"] + 1), "--fixed-means is", "storyEntry"),
            ("--wont-change", x(b["storyEntry"] + 1), "--wont-change is", "storyEntry"),
            ("--left-out", x(b["storyEntry"] + 1), "--left-out is", "storyEntry"),
            ("--number", x(b["figure"] + 1) + "|means", "--number: the figure is", "figure"),
            ("--number", "12|" + x(b["storyEntry"] + 1), "--number: the means is", "storyEntry"),
            ("--option", x(b["optionName"] + 1) + "|chosen|why", "--option: the name is", "optionName"),
            ("--option", "A|rejected|" + x(b["storyEntry"] + 1), "--option: the why is", "storyEntry"),
            ("--assumed", x(b["storyEntry"] + 1) + "|later", "--assumed: the text is", "storyEntry"),
            ("--assumed", "text|" + x(b["storyEntry"] + 1), "--assumed: the later is", "storyEntry"),
            ("--wrong", x(b["storyEntry"] + 1) + " => did", "--wrong: the what is", "storyEntry"),
            ("--wrong", "what => " + x(b["storyEntry"] + 1), "--wrong: the did is", "storyEntry"),
        ]
        self.init_scratch()
        ledger = os.path.join(self.root, "boards", "2026-09-13-scratch.json")
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            for flag, value, words, bound in over:
                with self.subTest(words):
                    with open(ledger, encoding="utf-8") as f:
                        before = f.read()
                    out = self.run_pipeline("note", "scratch", "0", "--decided", "kept?", flag, value, env=env)
                    self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
                    # The text over is always one past its bound.
                    self.assertIn(f"{words} {b[bound] + 1} characters; keep it to {b[bound]}", out.stderr)
                    with open(ledger, encoding="utf-8") as f:
                        self.assertEqual(f.read(), before, "a refused note wrote to the ledger")
            self.assertEqual(desk.requests, [], "a refused note reached the desk")
        at = ["--plain", x(b["plain"]), "--scene", x(b["scene"]), "--backstory", x(b["backstory"]), "--lede", x(b["lede"]),
              "--fixed-means", x(b["storyEntry"]), "--wont-change", x(b["storyEntry"]), "--left-out", x(b["storyEntry"]),
              "--number", x(b["figure"]) + "|" + x(b["storyEntry"]),
              "--option", x(b["optionName"]) + "|chosen|" + x(b["storyEntry"]),
              "--assumed", x(b["storyEntry"]) + "|" + x(b["storyEntry"]),
              "--wrong", x(b["storyEntry"]) + " => " + x(b["storyEntry"])]
        out = self.run_pipeline("note", "scratch", "0", *at)
        self.assertEqual(out.returncode, 0, out.stderr)
        zero = self.board()["phases"][0]
        self.assertEqual(len(zero["plain"]), b["plain"])
        self.assertEqual(zero["options"][0], {"name": x(b["optionName"]), "chosen": True, "why": x(b["storyEntry"])})
        self.assertEqual(zero["problems"][0], {"what": x(b["storyEntry"]), "did": x(b["storyEntry"])})

    def test_every_flag_note_takes_text_by_refuses_a_byte_that_is_not_utf8(self):
        """ALYDAS-219, Gate 6. The check reads every flag `note` was given; the
        S5 rows try six by hand. Here every flag that takes a free value is
        taken from note's own parser, each alone, so a flag the check skips
        fails by name: exit 1, the byte named, no traceback, nothing written."""
        from test_docs import build_parser, subcommands
        note = subcommands(build_parser())["note"]
        flags = sorted(a.option_strings[-1] for a in note._actions
                       if a.option_strings and a.nargs != 0 and not a.choices and a.dest not in ("help", "root"))
        self.assertEqual(len(flags), 19, flags)
        self.init_scratch()
        self.assertEqual(self.run_pipeline("note", "scratch", "2", "--summary", "seeded").returncode, 0)
        boards = os.path.join(self.root, "boards")
        def files():
            found = {}
            for name in sorted(os.listdir(boards)):
                with open(os.path.join(boards, name), "rb") as f:
                    found[name] = f.read()
            return found
        for flag in flags:
            with self.subTest(flag):
                before = files()
                out = self.run_pipeline("note", "scratch", "2", flag, os.fsdecode(b"docs/v\xff.html"))
                self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
                self.assertNotIn("Traceback", out.stderr)
                self.assertIn("a byte that is not UTF-8, which the desk cannot send", out.stderr)
                self.assertEqual(files(), before, "a refused note wrote to the boards folder")

    def test_note_refuses_a_path_or_text_that_is_not_utf8_and_writes_nothing(self):
        """ALYDAS-219, S5. A byte that is not UTF-8 reaches `note` through argv
        as the shell passes it (a surrogate, sent back as the raw byte). It used
        to raise `UnicodeEncodeError` in `log_event`, after the `--summary` of
        the same call had already been logged. Now it exits 1 with one plain
        line naming the path, or the flag of a text, and the ledger, its events
        and its page are byte for byte as before. A good UTF-8 path in the same
        call records normally."""
        bad = os.fsdecode(b"docs/bad-\xff.html")
        rename = "docs/bad-\\xff.html: a byte that is not UTF-8, which the desk cannot send; rename the file and record the new name"
        again = lambda flag: f"{flag}: a byte that is not UTF-8, which the desk cannot send; write it again in UTF-8"
        rows = [
            ("--produced", ["--produced", bad + "=x"], rename),
            ("--doc", ["--doc", bad + "|Title|about"], rename),
            # Gate 6. S5's deviation 3: named for its byte though the line has no title.
            ("--doc with no title", ["--doc", bad], rename),
            ("--drop-produced", ["--drop-produced", bad], rename),
            ("--produced, a note that is not UTF-8", ["--produced", "docs/ok.html=" + os.fsdecode(b"n\xff")], again("--produced")),
            ("--summary", ["--summary", os.fsdecode(b"s\xff")], again("--summary")),
            ("--decided", ["--decided", os.fsdecode(b"d\xff")], again("--decided")),
        ]
        self.init_scratch()
        # One note first, so the events file exists and is compared too.
        self.assertEqual(self.run_pipeline("note", "scratch", "2", "--summary", "seeded").returncode, 0)
        boards = os.path.join(self.root, "boards")

        def files():
            found = {}
            for name in sorted(os.listdir(boards)):
                with open(os.path.join(boards, name), "rb") as f:
                    found[name] = f.read()
            return found

        for name, flags, words in rows:
            with self.subTest(name):
                before = files()
                self.assertTrue(any(n.endswith(".events.jsonl") for n in before), sorted(before))
                out = self.run_pipeline("note", "scratch", "2", "--summary", "s", "--produced", "docs/café.html=good", *flags)
                self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
                self.assertNotIn("Traceback", out.stderr)
                self.assertEqual(out.stderr.strip(), f"pipeline: {words}")
                self.assertEqual(files(), before, "a refused note wrote to the boards folder")
        # The control: the same call with only good paths records them.
        out = self.run_pipeline("note", "scratch", "2", "--summary", "s", "--produced", "docs/café.html=good",
                                "--doc", "docs/naïve.html|Title")
        self.assertEqual(out.returncode, 0, out.stderr)
        two = self.board()["phases"][2]
        self.assertEqual(two["summary"], "s")
        self.assertEqual([e["path"] for e in two["produced"]], ["docs/café.html", "docs/naïve.html"])

    def test_the_trim_drops_the_oldest_log_entries_before_any_phase_text(self):
        """Over the budget, the oldest log entries go first and only as many as
        must; no phase text is cut while the log can give. When the whole log is
        not enough, it is all dropped and the texts are cut as before."""
        P = pipeline_module()
        blank = {"summary": "", "next": "", "decided": [], "measured": [], "wrong": [], "overruled": [],
                 "produced": [], "table": None, "skipReason": "", "forced": "", "plain": ""}
        log = [{"at": f"2026-09-13T10:00:{i % 60:02d}.000Z", "phase": 1, "kind": "log", "text": f"{i:03d} " + "l" * 296}
               for i in range(200)]
        ledger = {"phases": [{**blank, "n": n, "name": f"Phase {n}", "status": "done", "summary": "s" * 1_000,
                              "story": {"lede": "d" * 1_000}} for n in range(10)],
                  "deferred": [], "terms": {}, "story": {"happened": ["h" * 300], "stands": "t" * 300,
                                                          "needsYou": [{"what": "w" * 200, "why": "y" * 500}]},
                  "log": log}
        whole = P.ledger_bytes(ledger)
        budget = whole - 10_000
        out, report = P.trim_ledger(ledger, budget)
        self.assertLessEqual(P.ledger_bytes(out), budget)
        self.assertEqual(out["phases"], ledger["phases"], "a phase text was cut while the log could give")
        self.assertEqual(out["story"], ledger["story"])
        self.assertEqual(out["log"], log[report["log"]:], "the newest entries are kept, in order")
        self.assertGreater(report["log"], 0)
        # As few as fit: one fewer dropped does not.
        self.assertGreater(P.ledger_bytes({**ledger, "log": log[report["log"] - 1:]}), budget)
        self.assertIn(f"{report['log']} oldest log entries dropped", P.trim_words(report))
        # Past what the whole log gives, it is all gone and texts are cut, the
        # run's short version with them.
        without = P.ledger_bytes({**ledger, "log": []})
        out, report = P.trim_ledger(ledger, without - 5_000)
        self.assertEqual(out["log"], [])
        self.assertEqual(report["log"], 200)
        self.assertLess(report["width"], 1_000)
        self.assertLessEqual(P.ledger_bytes(out), without - 5_000)
        tiny = P.shaped({**ledger, "log": []}, 0, 0, 0)
        self.assertEqual(tiny["story"], None)

    def test_the_local_board_draws_the_short_version_at_the_top(self):
        self.init_scratch()
        out = self.run_pipeline("story", "scratch", "--happened", "Proved the leak.", "--stands", "Built, not pushed.",
                                "--needs-you", "Decide how tokens behave|They drop off Now.")
        self.assertEqual(out.returncode, 0, out.stderr)
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.html"), encoding="utf-8") as f:
            html = f.read()
        for text in ("Proved the leak.", "Built, not pushed.", "Decide how tokens behave", "They drop off Now.",
                     'id="short"', "What happened", "Waiting on you", "needs_you"):
            self.assertIn(text, html)
        self.assertLess(html.index('id="short"'), html.index('id="strip"'))

    # ---------- ALYDAS-135, S2: every ledger field, and the deferrals ----------

    def board(self):
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.json")) as f:
            return json.load(f)

    def write_board(self, run):
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.json"), "w") as f:
            json.dump(run, f)

    def test_a_note_with_every_field_a_table_and_a_defer_arrive_field_for_field_at_phase_done(self):
        """What `note`, `table` and `defer` wrote is what the beat at `phase done` carries.

        Every field is set to a value of its own, so a field sent from the
        wrong key (next from summary, measured from decided) is a mismatch,
        and each is compared twice: to the literal the command was given, and
        to the ledger on disk after the beat, which is what "field for field"
        means. Phase 1 is skipped with a reason and phase 0 is closed by hand,
        so both of those strings are real rather than empty.
        """
        self.init_scratch()
        steps = [
            ("phase", "scratch", "0", "start"),
            ("note", "scratch", "0",
             "--summary", "Framed from the item's criteria.",
             "--next", "Phase 1: brainstorm the pane.",
             "--decided", "Plain text only.", "--decided", "Amber for blocked.",
             "--measured", "18 KB for ten phases.",
             "--wrong", "The first fixture was vacuous.",
             "--overruled", "A over B; Sachit picked B.",
             "--produced", "docs/specs/goal.md=goal and eight evidence items",
             "--produced", "docs/screenshots/strip.png"),
            ("table", "scratch", "0", "--columns", "#,Evidence,State",
             "--row", "E1|every field in the pane|open", "--row", "E2|deferrals under it|open"),
            ("defer", "scratch", "--item", "The run log on the desk", "--needs", "a ruling"),
            ("defer", "scratch", "--item", "A view for a run tied to no item"),
            ("phase", "scratch", "1", "skipped", "--note", "Nothing to brainstorm in a scratch run."),
        ]
        for step in steps:
            out = self.run_pipeline(*step)
            self.assertEqual(out.returncode, 0, f"{step[0]}: {out.stderr}")

        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("phase", "scratch", "0", "done", "--force", "closed by hand in a scratch run", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(len(desk.requests), 1, "phase done did not post exactly once")
            ledger = desk.bodies()[0]["ledger"]

        zero, one = ledger["phases"][0], ledger["phases"][1]
        self.assertEqual(zero["status"], "done")
        self.assertEqual(zero["summary"], "Framed from the item's criteria.")
        self.assertEqual(zero["next"], "Phase 1: brainstorm the pane.")
        self.assertEqual(zero["decided"], ["Plain text only.", "Amber for blocked."])
        self.assertEqual(zero["measured"], ["18 KB for ten phases."])
        self.assertEqual(zero["wrong"], ["The first fixture was vacuous."])
        self.assertEqual(zero["overruled"], ["A over B; Sachit picked B."])
        self.assertEqual(zero["produced"], [
            {"path": "docs/specs/goal.md", "note": "goal and eight evidence items"},
            {"path": "docs/screenshots/strip.png", "note": ""},
        ])
        self.assertEqual(zero["table"], {
            "columns": ["#", "Evidence", "State"],
            "rows": [["E1", "every field in the pane", "open"], ["E2", "deferrals under it", "open"]],
        })
        self.assertEqual(zero["forced"], "closed by hand in a scratch run")
        self.assertEqual(zero["skipReason"], "")
        self.assertEqual(one["status"], "skipped")
        self.assertEqual(one["skipReason"], "Nothing to brainstorm in a scratch run.")
        self.assertEqual(one["forced"], "")
        # A phase with no table sends null, not an empty one.
        self.assertIsNone(one["table"])
        self.assertEqual(ledger["deferred"], [
            {"item": "The run log on the desk", "needs": "a ruling"},
            {"item": "A view for a run tied to no item", "needs": ""},
        ])

        # And field for field against the ledger the beat was built from.
        run = self.board()
        for sent, kept in zip(ledger["phases"], run["phases"]):
            for key in ("summary", "next", "decided", "measured", "wrong", "overruled", "produced", "table", "forced"):
                self.assertEqual(sent[key], kept[key], f"phase {kept['n']} {key}")
            self.assertEqual(sent["skipReason"], kept["skip_reason"], f"phase {kept['n']} skip reason")
            self.assertEqual(sent["startedAt"], kept["started"] or None, f"phase {kept['n']} started")
            self.assertEqual(sent["endedAt"], kept["ended"] or None, f"phase {kept['n']} ended")
        self.assertEqual(ledger["deferred"], run["deferred"])
        # Phase 0 started and ended, so both stamps are there, in that order.
        self.assertTrue(zero["startedAt"] and zero["endedAt"] and zero["startedAt"] <= zero["endedAt"])
        # Phase 2 never started: no stamps, and no telemetry at all.
        two = ledger["phases"][2]
        self.assertEqual((two["startedAt"], two["endedAt"]), (None, None))
        self.assertEqual({key: two[key] for key in TELEMETRY_KEYS}, {key: None for key in TELEMETRY_KEYS})
        # No transcript in a scratch run, so no cost is known: null, never 0.
        self.assertIsNone(zero["costUsd"])
        self.assertIsInstance(zero["wallS"], int)

    def test_a_phase_s_telemetry_rides_on_its_phase_and_an_unknown_cost_is_null_never_zero(self):
        """The telemetry `usage` computed for a phase is the phase's, by kind.

        Built from a run dict, as the totals case is, because a boundary
        recomputes telemetry from transcripts a scratch run does not have. Two
        phases: one whose cost is known and one whose `cost_known` is false with
        a `cost_usd` of 0.0, which is exactly what compute_usage writes for a
        live run with no cost-state line. The second must send null.
        """
        self.init_scratch()
        run = self.board()
        run["phases"][0]["telemetry"] = {
            "wall_s": 346, "active_s": 290, "agents": 2, "cost_usd": 4.23, "cost_known": True,
            "tokens": {"input": 11, "cache_write": 22_000, "cache_read": 3_300_000, "output": 44_444},
        }
        run["phases"][1]["telemetry"] = {
            "wall_s": 12, "active_s": 9, "agents": 0, "cost_usd": 0.0, "cost_known": False,
            "tokens": {"input": 5, "cache_write": 6, "cache_read": 7, "output": 8},
        }
        phases = pipeline_module().beat_ledger(run)["phases"]
        self.assertEqual({key: phases[0][key] for key in TELEMETRY_KEYS}, {
            "wallS": 346, "activeS": 290, "agents": 2, "costUsd": 4.23,
            "inputTokens": 11, "cacheWriteTokens": 22_000, "cacheReadTokens": 3_300_000, "outputTokens": 44_444,
        })
        self.assertEqual({key: phases[1][key] for key in TELEMETRY_KEYS}, {
            "wallS": 12, "activeS": 9, "agents": 0, "costUsd": None,
            "inputTokens": 5, "cacheWriteTokens": 6, "cacheReadTokens": 7, "outputTokens": 8,
        })

    def test_every_list_and_string_past_its_ledger_bound_is_cut_to_the_bound(self):
        """A ledger longer than the desk takes anywhere is cut here, so the beat is not a 400.

        Every list one past twice its bound and every string past its own, on
        one phase and in the deferrals, written straight into the board because
        sixty `defer` commands would be a minute of subprocesses. The bounds are
        read off pipeline.py's table, which mirrors the desk's `LEDGER_BOUNDS`.
        """
        self.init_scratch()
        bounds = pipeline_module().LEDGER_BOUNDS
        run = self.board()
        p = run["phases"][0]
        long = lambda field: field[0] * (bounds[field] + 7)
        p["summary"] = long("summary")
        p["next"] = long("next")
        p["skip_reason"] = long("skipReason")
        p["forced"] = long("forced")
        for key in ("decided", "measured", "wrong", "overruled"):
            p[key] = [long("entry")] * (bounds[key] * 2 + 1)
        p["produced"] = [{"path": long("path"), "note": long("note")}] * (bounds["produced"] * 2 + 1)
        p["table"] = {
            "columns": [long("column")] * (bounds["columns"] * 2 + 1),
            "rows": [[long("cell")] * (bounds["columns"] * 2 + 1)] * (bounds["rows"] * 2 + 1),
        }
        run["deferred"] = [{"item": long("item"), "needs": long("needs")}] * (bounds["deferred"] * 2 + 1)
        self.write_board(run)

        # S3: read off `beat_ledger`, not the wire. A ledger this size (about
        # 530 KB even at the bounds) is over the 192 KB budget, so what is sent
        # is also trimmed (`trim_ledger`, pinned by the budget case below);
        # cutting to the bounds is `beat_ledger`'s job and is what this pins.
        ledger = pipeline_module().beat_ledger(self.board())

        sent = ledger["phases"][0]
        for field in ("summary", "next", "skipReason", "forced"):
            self.assertEqual(len(sent[field]), bounds[field], field)
        for key in ("decided", "measured", "wrong", "overruled"):
            self.assertEqual(len(sent[key]), bounds[key], f"{key} count")
            self.assertEqual({len(x) for x in sent[key]}, {bounds["entry"]}, f"{key} entry length")
        self.assertEqual(len(sent["produced"]), bounds["produced"])
        self.assertEqual({(len(x["path"]), len(x["note"])) for x in sent["produced"]}, {(bounds["path"], bounds["note"])})
        table = sent["table"]
        self.assertEqual(len(table["columns"]), bounds["columns"])
        self.assertEqual({len(c) for c in table["columns"]}, {bounds["column"]})
        self.assertEqual(len(table["rows"]), bounds["rows"])
        self.assertEqual({len(row) for row in table["rows"]}, {bounds["columns"]})
        self.assertEqual({len(c) for row in table["rows"] for c in row}, {bounds["cell"]})
        self.assertEqual(len(ledger["deferred"]), bounds["deferred"])
        self.assertEqual({(len(d["item"]), len(d["needs"])) for d in ledger["deferred"]}, {(bounds["item"], bounds["needs"])})
        # The first entries are kept, not the last: the ledger's order is the order written.
        self.assertEqual(sent["decided"][0], long("entry")[:bounds["entry"]])

    def test_a_produced_entry_with_no_path_and_a_deferral_with_no_item_are_left_out(self):
        """The desk refuses an empty path or item, so one would cost the whole beat.

        `note --produced "=a note"` and a hand-edited board can both make one.
        """
        self.init_scratch()
        run = self.board()
        run["phases"][0]["produced"] = [{"path": "  ", "note": "no path"}, {"path": "docs/a.md", "note": ""}]
        run["deferred"] = [{"item": "", "needs": "x"}, {"item": "kept", "needs": ""}]
        ledger = pipeline_module().beat_ledger(run)
        self.assertEqual(ledger["phases"][0]["produced"], [{"path": "docs/a.md", "note": ""}])
        self.assertEqual(ledger["deferred"], [{"item": "kept", "needs": ""}])

    def test_a_ledger_over_the_budget_arrives_under_it_with_every_phase_s_number_name_and_status(self):
        """ALYDAS-135, S3 (Sachit, 2026-09-26). The desk refuses a body over 256 KB,
        and a refused beat takes the run off the Now card and breaks Stop. So the
        ledger is trimmed to a fixed 192 KB before it is sent: the longest text
        is shortened and the oldest table rows dropped first, and no phase ever
        loses its number, name or status. The ledger's own log says it happened.

        About 4 MB of ledger, every phase at its bounds, written into the board
        by hand. One short field stays whole, because only the longest are cut.
        """
        self.init_scratch()
        bounds = pipeline_module().LEDGER_BOUNDS
        run = self.board()
        for p in run["phases"]:
            n = p["n"]
            p["summary"] = f"Phase {n} summary. " + "s" * bounds["summary"]
            p["decided"] = [f"decided {n}-{i} " + "d" * bounds["entry"] for i in range(bounds["decided"])]
            p["table"] = {"columns": [f"col {c}" for c in range(bounds["columns"])],
                          "rows": [[f"row {n}-{r}"] + ["v" * bounds["cell"]] * (bounds["columns"] - 1)
                                   for r in range(bounds["rows"])]}
        run["phases"][3]["next"] = "Keep me whole."
        self.write_board(run)
        whole = pipeline_module().beat_ledger(self.board())
        self.assertGreater(len(json.dumps(whole, ensure_ascii=False).encode("utf-8")), 2_000_000)

        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("phase", "scratch", "1", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            [request] = desk.requests

        budget = 192 * 1024
        self.assertEqual(pipeline_module().LEDGER_BUDGET_BYTES, budget)
        body = json.loads(request["raw"])
        ledger = body["ledger"]
        self.assertLessEqual(len(json.dumps(ledger, ensure_ascii=False).encode("utf-8")), budget)
        self.assertLessEqual(len(request["raw"].encode("utf-8")), 256 * 1024)
        # Every phase still there, with its number, name and status.
        self.assertEqual([(p["n"], p["name"], p["status"]) for p in ledger["phases"]],
                         [(p["n"], p["name"], "running" if p["n"] == 1 else p["status"]) for p in whole["phases"]])
        # The short field was not touched; a long one was cut, and says so.
        self.assertEqual(ledger["phases"][3]["next"], "Keep me whole.")
        self.assertTrue(ledger["phases"][0]["summary"].startswith("Phase 0 summary."))
        self.assertLess(len(ledger["phases"][0]["summary"]), bounds["summary"])
        self.assertTrue(ledger["phases"][0]["summary"].endswith("…"))
        # Rows go oldest first: what is left of the tables is the newest rows,
        # and the last phase keeps its last row.
        kept = [row[0] for p in ledger["phases"] if p["table"] for row in p["table"]["rows"]]
        every = [row[0] for p in whole["phases"] if p["table"] for row in p["table"]["rows"]]
        self.assertLess(len(kept), len(every))
        self.assertEqual(kept, every[len(every) - len(kept):])
        self.assertIn(f"row 9-{bounds['rows'] - 1}", kept[-1])
        # And the ledger's own log says so, with the sizes.
        trimmed = [json.loads(line) for line in self.events_text().splitlines() if '"trimmed"' in line]
        self.assertEqual(len(trimmed), 1, self.events_text()[-2000:])
        self.assertIn("192 KB", trimmed[0]["note"])

    def trimmed(self, phases, deferred=None):
        """`trim_ledger` on a ledger built straight from wire-shaped phases."""
        P = pipeline_module()
        blank = {"summary": "", "next": "", "decided": [], "measured": [], "wrong": [], "overruled": [],
                 "produced": [], "table": None, "skipReason": "", "forced": ""}
        ledger = {"phases": [{**blank, "n": n, "name": f"Phase {n}", "status": "done", **extra}
                             for n, extra in enumerate(phases)], "deferred": deferred or []}
        out, report = P.trim_ledger(ledger)
        self.assertLessEqual(P.ledger_bytes(out), P.LEDGER_BUDGET_BYTES)
        self.assertEqual([(p["n"], p["name"], p["status"]) for p in out["phases"]],
                         [(p["n"], p["name"], p["status"]) for p in ledger["phases"]])
        self.assertEqual(P.trim_ledger(ledger)[0], out, "the trim is not deterministic")
        self.assert_nothing_the_desk_reads_as_empty(out)
        return ledger, out, report

    def assert_nothing_the_desk_reads_as_empty(self, ledger):
        """Gate 6. The desk refuses a whole beat for one required string it reads
        as empty: a phase's name, a produced path, a deferral's item. And a list
        entry the trim cut to nothing says nothing, so it is not sent either."""
        for p in ledger["phases"]:
            self.assertFalse(js_blank(p["name"]), f"phase {p['n']} has a name the desk reads as empty")
            for key in ("decided", "measured", "wrong", "overruled"):
                blank = [e for e in p.get(key) or [] if js_blank(e)]
                self.assertEqual(blank, [], f"phase {p['n']} sends {len(blank)} empty {key} entries")
            for f in p.get("produced") or []:
                self.assertFalse(js_blank(f["path"]), f"phase {p['n']} sends a produced file with no path")
        blank = [d for d in ledger.get("deferred") or [] if js_blank(d["item"])]
        self.assertEqual(blank, [], f"{len(blank)} deferrals are sent with an item the desk reads as empty")

    def test_text_is_cut_first_and_only_as_far_as_it_must_be(self):
        """Over the budget by long list entries alone: they are cut to the widest
        width that fits, which is well above the floor, so a 500-character field
        and a small table come through whole and no row is dropped."""
        P = pipeline_module()
        phases = [{"decided": [f"{n}-{i} " + "d" * 994 for i in range(30)]} for n in range(10)]
        phases[3]["next"] = "n" * 500
        phases[0]["table"] = {"columns": ["a", "b"], "rows": [["1", "2"], ["3", "4"], ["5", "6"]]}
        ledger, out, report = self.trimmed(phases)
        self.assertEqual(out["phases"][3]["next"], "n" * 500)
        self.assertEqual(out["phases"][0]["table"], ledger["phases"][0]["table"])
        self.assertEqual(report["rows"], 0)
        self.assertEqual(report["entries"], 0)
        widths = {len(e) for p in out["phases"] for e in p["decided"]}
        self.assertEqual(len(widths), 1)
        self.assertGreater(widths.pop(), 500)
        # The widest that fits: one character more and it would not.
        self.assertGreater(P.ledger_bytes(P.shaped(ledger, report["width"] + 1, 0, 0)), P.LEDGER_BUDGET_BYTES)

    def test_below_the_floor_short_fields_still_come_through_whole(self):
        """Many short-ish entries and no table: the third step cuts below the
        floor, and a field already shorter than the cut is left as it was."""
        phases = [{k: [f"{n}{k}{i} " + "e" * 290 for i in range(30)] for k in ("decided", "measured", "wrong", "overruled")}
                  for n in range(20)]
        phases[5]["next"] = "Keep me."
        _, out, report = self.trimmed(phases)
        self.assertLess(report["width"], pipeline_module().TRIM_TEXT_FLOOR)
        self.assertEqual(report["entries"], 0)
        self.assertEqual(out["phases"][5]["next"], "Keep me.")

    def test_a_ledger_at_every_bound_fits_and_no_produced_path_is_cut(self):
        """About 9 MB, the most the bounds allow: it still fits, every phase keeps
        its number, name and status, and a produced path that survives is whole,
        because a cut path names no file."""
        b = pipeline_module().LEDGER_BOUNDS
        phase = {"summary": "s" * b["summary"], "next": "x" * b["next"], "skipReason": "r" * b["skipReason"],
                 "forced": "f" * b["forced"],
                 **{k: ["e" * b["entry"]] * b[k] for k in ("decided", "measured", "wrong", "overruled")},
                 "produced": [{"path": f"{i:03d}".ljust(b["path"], "p"), "note": "o" * b["note"]}
                              for i in range(b["produced"])],
                 "table": {"columns": ["c" * b["column"]] * b["columns"],
                           "rows": [["v" * b["cell"]] * b["columns"]] * b["rows"]}}
        ledger, out, report = self.trimmed([phase] * b["phases"],
                                           [{"item": "i" * b["item"], "needs": "n" * b["needs"]}] * b["deferred"])
        self.assertGreater(report["before"], 9_000_000)
        paths = [f["path"] for p in out["phases"] for f in p["produced"]]
        self.assertTrue(paths, "every produced file was dropped")
        self.assertEqual({len(path) for path in paths}, {b["path"]})
        # Oldest first: the files left are the last phases' files, and in a
        # list cut part way, its last entries.
        self.assertEqual(len(out["phases"][-1]["produced"]), b["produced"])
        for sent, kept in zip(ledger["phases"], out["phases"]):
            left = [f["path"] for f in kept["produced"]]
            self.assertEqual(left, [f["path"] for f in sent["produced"]][len(sent["produced"]) - len(left):])
        self.assertTrue(any(0 < len(p["produced"]) < b["produced"] for p in out["phases"]),
                        "no list was cut part way, so the order is not being tested")

    def test_a_ledger_under_the_budget_is_sent_untouched_and_logs_nothing(self):
        """The trim is for a ledger that would be refused, and no other."""
        self.init_scratch()
        run = self.board()
        run["phases"][0]["summary"] = "x" * 1_000
        self.write_board(run)
        with Listener() as desk:
            out = self.run_pipeline("phase", "scratch", "1", "start", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stderr)
            [body] = desk.bodies()
        # The ledger as the board on disk has it, field for field.
        self.assertEqual(body["ledger"], pipeline_module().beat_ledger(self.board()))
        self.assertEqual(body["ledger"]["phases"][0]["summary"], "x" * 1_000)
        self.assertNotIn('"trimmed"', self.events_text())

    # ---------- Gate 6: the trim never makes a beat the desk refuses ----------

    def filled_past_every_bound(self, fill):
        """The scratch board with every phase, list and string past its bound in
        `fill`, thirteen phases more than the ten (one numbered 150, one with no
        number, one repeating 3), and the deferrals past theirs."""
        b = pipeline_module().LEDGER_BOUNDS
        run = self.board()
        template = run["phases"][0]
        run["phases"] += [dict(template, n=n, key=f"extra-{n}", name=f"Extra {n}") for n in range(10, 22)]
        run["phases"] += [dict(template, n=150, key="far", name="Far"), dict(template, n=None, key="none", name="None"),
                          dict(template, n=3, key="again", name="Three again")]
        over = lambda field, extra=7: fill * (b[field] + extra)
        for i, p in enumerate(run["phases"]):
            p["name"] = f"  {over('name')}  "
            p["summary"], p["next"] = over("summary"), over("next")
            p["skip_reason"], p["forced"] = over("skipReason"), over("forced")
            for key in ("decided", "measured", "wrong", "overruled"):
                p[key] = [f"{key} {i}-{e} " + over("entry") for e in range(b[key] + 3)]
            # ALYDAS-173, S5. Each file named as a document, its three past theirs.
            p["produced"] = [{"path": f"{i:02d}{e:02d}" + over("path"), "note": over("note"), "title": over("title"),
                              "about": over("about"), "in_short": over("inShort")}
                             for e in range(b["produced"] + 3)]
            p["table"] = {"columns": [over("column")] * (b["columns"] + 2),
                          "rows": [[over("cell")] * (b["columns"] + 2)] * (b["rows"] + 3)}
            # ALYDAS-173, S2. The story's texts and Frame's two lists, past theirs.
            p["plain"] = over("plain")
            p["purpose"], p["scene"], p["backstory"] = over("purpose"), over("scene"), over("backstory")
            for key, bound in (("fixed_means", "fixedMeans"), ("wont_change", "wontChange")):
                p[key] = [f"{key} {i}-{e} " + over("storyEntry") for e in range(b[bound] + 3)]
            # ALYDAS-173, S3. The Story view's packets, past theirs.
            p["lede"] = over("lede")
            p["numbers"] = [{"figure": over("figure"), "means": over("storyEntry")} for _ in range(b["numbers"] + 3)]
            p["options"] = [{"name": over("optionName"), "chosen": e % 2 == 0, "why": over("storyEntry")}
                            for e in range(b["options"] + 3)]
            p["assumed"] = [{"text": over("storyEntry"), "later": over("storyEntry")} for _ in range(b["assumed"] + 3)]
            p["left_out"] = [over("storyEntry") for _ in range(b["leftOut"] + 3)]
            p["problems"] = [{"what": over("storyEntry"), "did": over("storyEntry")} for _ in range(b["problems"] + 3)]
            # ALYDAS-173, S4. The steps, past theirs, one in a state the desk does not know.
            p["steps"] = [{"text": over("stepText"), "state": ("todo", "doing", "done", "later")[e % 4], "sub": over("stepSub")}
                          for e in range(b["steps"] + 3)]
        # ALYDAS-173, S3. And the run's terms, past their count and each bound.
        run["terms"] = {f"{e} " + over("termCode"): over("termPlain") for e in range(b["terms"] + 5)}
        run["deferred"] = [{"item": f"{e} " + over("item"), "needs": over("needs")} for e in range(b["deferred"] + 5)]
        # ALYDAS-173, S6. The run's short version past its bounds, and a log
        # past its count, each event's kind and words past theirs, one with no
        # zone, one naming a phase past the desk's numbers.
        run["story"] = {"happened": [f"{e} " + over("happenedLine") for e in range(b["happened"] + 3)],
                        "stands": over("stands"),
                        "needs_you": [{"what": f"{e} " + over("needWhat"), "why": over("needWhy")} for e in range(b["needsYou"] + 3)]}
        self.write_events([{"ts": f"2026-09-13T10:{e // 60:02d}:{e % 60:02d}.000Z", "phase": (e % 12) if e % 7 else 150,
                            "event": over("logKind"), "note": over("logText")} for e in range(b["log"] + 40)]
                          + [{"ts": "2026-09-13T11:00:00", "phase": 1, "event": "log", "note": "no zone"}])
        # ALYDAS-154, S2. And the run's own title and goal, past theirs.
        hb = pipeline_module().HEARTBEAT_BOUNDS
        run["title"], run["goal"] = fill * (hb["title"] + 7), fill * (hb["goal"] + 7)
        return run

    def test_a_beat_filled_past_every_bound_is_one_the_desk_takes(self):
        """Gate 6, the property. Whatever the ledger holds, the beat that reaches
        the desk is one its own `heartbeatRefusal` takes, and one under its body
        ceiling. Filled past every bound in four alphabets: ASCII, an astral
        emoji (two UTF-16 units, one code point), CJK with spaces, and nothing
        but the whitespace JavaScript trims and Python keeps (U+FEFF), which is
        a required string the desk reads as empty. Each beat is taken off the
        wire, as sent, and run through the desk's own function by node."""
        self.init_scratch()
        ceiling = desk_constant(self, read_desk_number, DESK_REQUEST_BOUNDS, "HEARTBEAT_BODY_MAX")
        fills = {"ascii": "x", "astral": "\U0001F600", "cjk": "界 面", "trimmed-away": "\ufeff "}
        sent = {}
        for label, fill in fills.items():
            self.write_board(self.filled_past_every_bound(fill))
            with Listener() as desk:
                out = self.run_pipeline("phase", "scratch", "1", "start", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
                self.assertEqual(out.returncode, 0, out.stderr)
                [request] = desk.requests
            self.assertLessEqual(len(request["raw"].encode("utf-8")), ceiling, f"{label}: the body is over the desk's ceiling")
            sent[label] = json.loads(request["raw"])
        refusals = desk_refusals(self, list(sent.values()))
        self.assertEqual(dict(zip(sent, refusals)), {label: None for label in sent},
                         "the desk refused a beat the pipeline trimmed to fit it")
        # And the desk read a ledger, not a beat that sent none.
        for label, body in sent.items():
            self.assertGreaterEqual(len(body["ledger"]["phases"]), 10, label)

    def test_the_trim_at_every_bound_never_sends_a_deferral_or_entry_the_desk_reads_as_empty(self):
        """Gate 6, production bug 1, on S3's own fixture. At the fourth step every
        text is cut to width 0, and a deferral kept with `item: ""` was a 400 for
        the whole beat. A deferral or list entry cut to nothing is dropped."""
        b = pipeline_module().LEDGER_BOUNDS
        phase = {"summary": "s" * b["summary"], "next": "x" * b["next"], "skipReason": "r" * b["skipReason"],
                 "forced": "f" * b["forced"],
                 **{k: ["e" * b["entry"]] * b[k] for k in ("decided", "measured", "wrong", "overruled")},
                 "produced": [{"path": f"{i:03d}".ljust(b["path"], "p"), "note": "o" * b["note"]}
                              for i in range(b["produced"])],
                 "table": {"columns": ["c" * b["column"]] * b["columns"],
                           "rows": [["v" * b["cell"]] * b["columns"]] * b["rows"]}}
        _, out, report = self.trimmed([phase] * b["phases"],
                                      [{"item": "i" * b["item"], "needs": "n" * b["needs"]}] * b["deferred"])
        self.assertEqual(report["width"], 0, "the fixture no longer reaches the fourth step, so this proves nothing")
        self.assertLessEqual(pipeline_module().ledger_bytes(out), pipeline_module().LEDGER_BUDGET_BYTES)

    def test_phases_past_the_desks_count_or_number_are_fitted_not_sent(self):
        """Gate 6, probe 11. The desk takes at most 20 phases, each numbered 0 to
        99 and each number once, and refuses the whole beat otherwise. The
        pipeline cut every other bound and not these. A phase with no whole
        number in range, or one repeating a number, is left out, and the first
        20 by number are sent, as a cut list keeps its first entries."""
        self.init_scratch()
        b = pipeline_module().LEDGER_BOUNDS
        run = self.board()
        template = run["phases"][0]
        run["phases"] += [dict(template, n=n, key=f"extra-{n}", name=f"Extra {n}") for n in range(10, 22)]
        run["phases"] += [dict(template, n=150, key="far", name="Far"), dict(template, n=None, key="none", name="None"),
                          dict(template, n=True, key="bool", name="A bool"), dict(template, n=2.5, key="half", name="Half"),
                          dict(template, n=3, key="again", name="Three again")]
        ledger = pipeline_module().beat_ledger(run)
        sent = [p["n"] for p in ledger["phases"]]
        self.assertEqual(sent, list(range(b["phases"])))
        self.assertEqual(len(ledger["phases"]), b["phases"])
        self.assertEqual(ledger["phases"][3]["name"], "Plan", "a repeated number replaced the first phase to carry it")
        # Under the count, a number past 99 is still left out, and so is one
        # below zero: the count is not what drops them.
        run["phases"] = [dict(template, n=n, name=f"P{n}") for n in (0, 1, b["n"] + 1, -1, 150)]
        self.assertEqual([p["n"] for p in pipeline_module().beat_ledger(run)["phases"]], [0, 1])
        # A board of exactly the bound, all in range, is sent whole.
        run["phases"] = [dict(template, n=b["n"] - i, name=f"P{i}") for i in range(b["phases"])]
        self.assertEqual(len(pipeline_module().beat_ledger(run)["phases"]), b["phases"])
        self.assertEqual(pipeline_module().beat_ledger(run)["phases"][-1]["n"], b["n"])

    def test_a_blank_name_is_sent_as_an_ellipsis_never_empty(self):
        """A phase's name is required, so a board whose name is blank, or only
        characters JavaScript trims, sends one the desk can take."""
        self.init_scratch()
        run = self.board()
        run["phases"][0]["name"] = "   "
        run["phases"][1]["name"] = "\ufeff \ufeff"
        run["phases"][2]["name"] = None
        ledger = pipeline_module().beat_ledger(run)
        for p in ledger["phases"][:3]:
            self.assertFalse(js_blank(p["name"]), p)
        self.assert_nothing_the_desk_reads_as_empty(ledger)

    def test_an_astral_string_is_cut_by_code_points_in_the_ledger_and_in_the_trim(self):
        """Gate 6, M10 on this side. The desk counts code points; an emoji is one
        code point and two UTF-16 units, so a pipeline counting units would send
        half as much and a desk counting units would refuse what the pipeline
        sends. The cut at the bound and the trim's width are both in code points."""
        self.init_scratch()
        b = pipeline_module().LEDGER_BOUNDS
        smile = "\U0001F600"
        run = self.board()
        run["phases"][0]["summary"] = smile * (b["summary"] + 7)
        run["phases"][0]["name"] = smile * (b["name"] + 7)
        run["phases"][0]["decided"] = [smile * (b["entry"] + 7)]
        run["deferred"] = [{"item": smile * (b["item"] + 7), "needs": smile * (b["needs"] + 7)}]
        ledger = pipeline_module().beat_ledger(run)
        self.assertEqual(ledger["phases"][0]["summary"], smile * b["summary"])
        self.assertEqual(ledger["phases"][0]["name"], smile * b["name"])
        self.assertEqual(ledger["phases"][0]["decided"], [smile * b["entry"]])
        self.assertEqual(ledger["deferred"], [{"item": smile * b["item"], "needs": smile * b["needs"]}])

        phases = [{"decided": [f"{n}-{i} " + smile * 994 for i in range(30)]} for n in range(10)]
        _, out, report = self.trimmed(phases)
        widths = {len(e) for p in out["phases"] for e in p["decided"]}
        self.assertEqual(widths, {report["width"]}, "an entry was cut to other than the trim's width in code points")
        self.assertTrue(all(e.endswith("\u2026") for p in out["phases"] for e in p["decided"]))

    def test_the_ledger_budget_fits_under_the_desks_body_ceiling(self):
        """The two numbers are one decision, pinned wherever `ALYDASH_REPO` names a
        desk: 192 KB of ledger inside the desk's 256 KB ceiling, with room for the
        rest of the body."""
        ceiling = desk_constant(self, read_desk_number, DESK_REQUEST_BOUNDS, "HEARTBEAT_BODY_MAX")
        self.assertEqual(ceiling, 256 * 1024)
        # The rest of a beat: every HEARTBEAT_BOUNDS field at its wall, escaped
        # at worst six bytes a character, and the keys. Well under the headroom.
        rest = sum(HEARTBEAT_BOUNDS_WORST.values())
        self.assertLess(pipeline_module().LEDGER_BUDGET_BYTES + rest, ceiling)

    def test_the_ledger_bounds_table_is_the_desks_ledger_bounds_table(self):
        """pipeline.py's LEDGER_BOUNDS is the desk's, number for number, where the desk has one.

        S1 left this a follow-up because the default desk checkout had no
        `LEDGER_BOUNDS` before ALYDAS-135 merges. It skips there, naming the
        checkout, and pins the two tables together wherever the desk has one.
        Gate 6: a table it cannot read fails, whatever the checkout.
        """
        theirs = desk_constant(self, read_desk_table, DESK_LEDGER_BOUNDS, "LEDGER_BOUNDS")
        ours = pipeline_module().LEDGER_BOUNDS
        self.assertGreater(len(ours), 10, "pipeline.py's ledger table is nearly empty")
        self.assertEqual(
            {field: ours[field] for field in sorted(ours)},
            {field: theirs.get(field) for field in sorted(ours)},
            "pipeline.py's hand copy of LEDGER_BOUNDS disagrees with the desk's",
        )
        # Gate 6. And no bound the desk has that the pipeline does not cut to:
        # a bound only the desk knows is a field the pipeline sends uncut.
        self.assertEqual(sorted(theirs), sorted(ours), "the desk bounds something pipeline.py does not")

    def test_the_review_groups_are_the_desks_review_groups(self):
        """ALYDAS-174, Gate 6, M02. A finding's groups, as the desk's
        `REVIEW_GROUPS` (`packages/modules/runs/src/types.ts`): a group only the
        pipeline has is recorded, sent, and refuses every beat after it."""
        theirs = desk_constant(self, read_desk_strings, DESK_RUNS_TYPES, "REVIEW_GROUPS")
        self.assertEqual(tuple(theirs), pipeline_module().REVIEW_GROUPS,
                         "pipeline.py's hand copy of REVIEW_GROUPS disagrees with the desk's")

    def test_the_budget_floor_is_the_desks_budget_floor(self):
        """ALYDAS-17 Gate 8, N1 / SEC-1. pipeline.py's BUDGET_FLOOR_USD is the
        desk's, so `init --budget` and `set budget` refuse what the desk refuses."""
        theirs = desk_constant(self, read_desk_decimal, DESK_LEDGER_BOUNDS, "BUDGET_FLOOR_USD")
        self.assertEqual(pipeline_module().BUDGET_FLOOR_USD, theirs,
                         "pipeline.py's budget floor disagrees with the desk's")

    def test_the_subject_kind_is_the_kind_the_desk_links(self):
        """ALYDAS-303, S9. A beat names its work item as a subject of the kind
        runs' Work integration links (`WORK_ITEM`); any other kind is refused
        whole (`SUBJECT_KIND_REFUSAL`), so the two are one string."""
        theirs = desk_constant(self, read_desk_string, DESK_RUNS_WORK, "WORK_ITEM")
        self.assertEqual(pipeline_module().SUBJECT_KIND, theirs, "pipeline.py's subject kind is not the desk's")

    # ---------- ALYDAS-135, S6: the beat names its commit, the branch is pushed by name ----------
    #
    # Every case below builds a real repository in the temp directory the ledger
    # runs in, with a bare repository beside it as `origin`. Nothing here can
    # reach a real remote: the only remote any case names is a path under the
    # case's own temp directories. The repository is set up without a push of
    # its own: the bare origin is a clone, and `refs/remotes/origin/*` come from
    # a fetch.

    def test_the_beat_carries_the_commit_head_names(self):
        """Every beat sends `commit`, what `git rev-parse HEAD` says in the repository."""
        origin = self.repo_with_origin()
        head = self.on_a_feature_branch(origin)
        self.init_scratch()
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("phase", "scratch", "0", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(desk.bodies()[0].get("commit"), head)
            later = self.commit("third.txt")
            out = self.run_pipeline("phase", "scratch", "0", "done", "--force", "a scratch run", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(desk.bodies()[1].get("commit"), later, "the second beat named the first beat's commit")

    def test_outside_a_repository_the_beat_names_no_commit(self):
        """No repository, no `commit` key: never an empty string, which the desk would refuse."""
        self.init_scratch()
        with Listener() as desk:
            out = self.run_pipeline("phase", "scratch", "0", "start",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertNotIn("commit", desk.bodies()[0])

    def test_a_feature_branch_is_pushed_by_name_and_main_on_origin_does_not_move(self):
        """At a boundary the branch lands at `refs/heads/<branch>` on origin, before the beat.

        The branch tracks origin/main with `push.default=upstream`, the setting
        under which a bare `git push` would have moved origin's main: main is
        asserted unmoved, so a push that is not by name is red here."""
        origin = self.repo_with_origin()
        main_before = self.origin_refs(origin)["refs/heads/main"]
        head = self.on_a_feature_branch(origin)
        self.init_scratch()

        # What origin holds at the moment the beat arrives: the push is before it.
        with Listener(watch=lambda: self.origin_refs(origin)) as desk:
            out = self.run_pipeline("phase", "scratch", "0", "start",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stderr, "")
            self.assertEqual(desk.observed[0].get("refs/heads/feature/alydas-135-s6"), head,
                             "the branch was not on origin when the beat arrived")
        refs = self.origin_refs(origin)
        self.assertEqual(refs["refs/heads/feature/alydas-135-s6"], head)
        self.assertEqual(refs["refs/heads/main"], main_before, "origin's main moved: a deploy")
        self.assertEqual(sorted(refs), ["refs/heads/feature/alydas-135-s6", "refs/heads/main"])

    # ALYDAS-135, S8 (ruled 2026-09-26). A beat names its commit only when origin
    # has it: the push just succeeded, or an origin branch already contains it.
    # Otherwise the desk would ask GitHub for a file at a commit GitHub does not
    # have, and say it cannot be read; with no commit it lists the paths.

    def test_a_repository_with_no_remote_names_no_commit(self):
        self.git("init", "-q", "-b", "feature/x")
        self.commit("first.txt")
        self.init_scratch()
        with Listener() as desk:
            out = self.run_pipeline("phase", "scratch", "0", "start",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stderr, "")
            self.assertNotIn("commit", desk.bodies()[0])

    def test_a_refused_push_from_main_of_a_commit_origin_lacks_names_no_commit(self):
        origin = self.repo_with_origin()
        head = self.commit("second.txt")
        self.init_scratch()
        with Listener() as desk:
            out = self.run_pipeline("phase", "scratch", "0", "start",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertNotIn("commit", desk.bodies()[0], f"{head} is only on this machine")
        self.assertNotIn(head, self.origin_refs(origin).values())

    def test_a_commit_origin_already_has_is_named_without_a_push(self):
        """On main at origin's main, nothing is pushed and the commit is named."""
        origin = self.repo_with_origin()
        head = self.git("rev-parse", "HEAD")
        before = self.origin_refs(origin)
        self.init_scratch()
        with Listener() as desk:
            out = self.run_pipeline("phase", "scratch", "0", "start",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(desk.bodies()[0].get("commit"), head)
        self.assertEqual(self.origin_refs(origin), before)

    def test_a_commit_only_another_remote_has_is_not_named(self):
        """`git branch -r` lists every remote's branches; only origin's count."""
        origin = self.repo_with_origin()
        head = self.on_a_feature_branch(origin)
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        fork = os.path.join(other.name, "fork.git")
        self.git("init", "-q", "--bare", fork, cwd=other.name)
        self.git("remote", "add", "fork", fork)
        self.git("push", "-q", "fork", "HEAD:refs/heads/elsewhere")
        self.git("checkout", "-q", "--detach")
        self.init_scratch()
        with Listener() as desk:
            out = self.run_pipeline("phase", "scratch", "0", "start",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertNotIn("commit", desk.bodies()[0], f"{head} is on fork/elsewhere only")

    def test_a_stale_origin_head_is_not_origin_having_the_commit(self):
        """Gate 6, G06. `on_origin` leaves `refs/remotes/origin/HEAD` out of the
        refs that count. As the symref a clone writes it can never change the
        answer, because the branch it names contains whatever it contains
        (measured: an equivalent mutant). It can when origin/HEAD is a plain
        ref, which `git fetch origin +HEAD:refs/remotes/origin/HEAD` writes and
        no later fetch moves. Here the default branch is `trunk`, which is never
        pushed; origin/HEAD is left at the commit trunk was on, and origin's
        trunk is then rewritten past it. No branch on origin has that commit,
        so it is not named, though origin/HEAD still points at it."""
        self.repo_with_origin(default="trunk")
        head = self.git("rev-parse", "HEAD")
        self.git("symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
        self.git("fetch", "-q", "origin", "+HEAD:refs/remotes/origin/HEAD")
        self.assertEqual(self.git("rev-parse", "refs/remotes/origin/HEAD"), head)
        # Origin's trunk rewritten to a commit that does not contain HEAD.
        self.git("checkout", "-q", "--orphan", "rewritten")
        self.commit("rewritten.txt")
        self.git("push", "-q", "-f", "origin", "HEAD:refs/heads/trunk")
        self.git("checkout", "-q", "trunk")
        self.git("fetch", "-q", "origin")
        self.assertNotEqual(self.git("rev-parse", "refs/remotes/origin/trunk"), head)
        self.assertEqual(self.git("rev-parse", "refs/remotes/origin/HEAD"), head, "the fetch moved origin/HEAD")
        self.assertEqual(self.git("branch", "-r", "--contains", head, "--format=%(refname)"), "refs/remotes/origin/HEAD")
        self.init_scratch()
        with Listener() as desk:
            out = self.run_pipeline("phase", "scratch", "0", "start",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertNotIn("commit", desk.bodies()[0], f"{head} is on no origin branch, only under origin/HEAD")

    def test_main_master_and_the_default_branch_are_never_pushed(self):
        """Main, master and whatever origin's HEAD names stay where origin has them."""
        for default, local in (("main", "main"), ("main", "master"), ("trunk", "trunk")):
            with self.subTest(default=default, branch=local):
                self.tmp.cleanup()
                os.makedirs(self.tmp.name)
                origin = self.repo_with_origin(default=default)
                before = self.origin_refs(origin)
                if local != default:
                    self.git("checkout", "-q", "-b", local)
                head = self.commit("second.txt")
                self.init_scratch()
                with Listener() as desk:
                    out = self.run_pipeline("phase", "scratch", "0", "start",
                                            env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
                    self.assertEqual(out.returncode, 0, out.stderr)
                    # S8 (ruled 2026-09-26): the commit is not on origin, so
                    # GitHub cannot serve a file at it, and the beat names none.
                    self.assertNotIn("commit", desk.bodies()[0], f"{head} is not on origin")
                self.assertEqual(self.origin_refs(origin), before, f"{local} was pushed")

    # Gate 8, security F5. With no `refs/remotes/origin/HEAD` the default branch
    # was taken to be none, so a deploy branch not called main or master was
    # pushed. Now the default is asked of origin (`git ls-remote --symref`), and
    # when origin cannot say either, nothing is pushed.

    def without_origin_head(self, default="trunk"):
        origin = self.repo_with_origin(default=default)
        self.git("remote", "set-head", "origin", "--delete")
        self.assertEqual(subprocess.run(["git", "-C", self.tmp.name, "symbolic-ref", "-q",
                                         "refs/remotes/origin/HEAD"]).returncode, 1)
        return origin

    def beat_once(self, *args):
        with Listener() as desk:
            out = self.run_pipeline(*(args or ("phase", "scratch", "0", "start")),
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stderr)
            return out, desk.bodies()[0]

    def test_with_no_origin_head_the_default_origin_names_is_never_pushed(self):
        origin = self.without_origin_head("trunk")
        before = self.origin_refs(origin)
        self.commit("second.txt")
        self.init_scratch()
        out, body = self.beat_once()
        self.assertEqual(self.origin_refs(origin), before, "trunk, origin's default, was pushed")
        self.assertNotIn("commit", body)

    def test_with_no_origin_head_a_feature_branch_is_still_pushed_when_origin_names_its_default(self):
        origin = self.without_origin_head("trunk")
        self.git("checkout", "-q", "-b", "feature/x")
        head = self.commit("second.txt")
        self.init_scratch()
        out, body = self.beat_once()
        self.assertEqual(out.stderr, "")
        self.assertEqual(self.origin_refs(origin).get("refs/heads/feature/x"), head)
        self.assertEqual(body.get("commit"), head)

    def test_when_the_default_branch_cannot_be_known_nothing_is_pushed(self):
        """No origin/HEAD, and origin's HEAD names a branch it does not have, so
        ls-remote answers no symref: one stderr line per boundary, no push of
        any branch, and a commit is named only when origin already has it."""
        origin = self.without_origin_head("trunk")
        self.git("symbolic-ref", "HEAD", "refs/heads/gone", cwd=origin)
        before = self.origin_refs(origin)
        on_origin = self.git("rev-parse", "HEAD")
        self.init_scratch()
        for branch in ("trunk", "feature/x"):
            with self.subTest(branch=branch):
                if branch != "trunk":
                    self.git("checkout", "-q", "-b", branch)
                out, body = self.beat_once("phase", "scratch", "0", "start")
                self.assertEqual(len(out.stderr.strip().splitlines()), 1, out.stderr)
                self.assertIn("not pushed", out.stderr)
                self.assertIn("default branch", out.stderr)
                self.assertEqual(body.get("commit"), on_origin, "a commit origin has is still named")
                head = self.commit(branch.replace("/", "-") + ".txt")
                out, body = self.beat_once("phase", "scratch", "0", "done", "--force", "x")
                self.assertEqual(len(out.stderr.strip().splitlines()), 1, out.stderr)
                self.assertNotIn("commit", body, f"{head} never reached origin")
                self.assertEqual(self.origin_refs(origin), before, f"{branch} was pushed")
                self.git("reset", "-q", "--hard", "HEAD~1")

    def test_a_detached_head_pushes_nothing_and_names_a_commit_only_if_origin_has_it(self):
        origin = self.repo_with_origin()
        on_origin = self.git("rev-parse", "HEAD")
        self.on_a_feature_branch(origin)
        before = self.origin_refs(origin)
        self.git("checkout", "-q", "--detach")
        self.commit("detached.txt")
        self.init_scratch()
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("phase", "scratch", "0", "start", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertNotIn("commit", desk.bodies()[0], "a detached commit origin never had")
            self.git("checkout", "-q", "--detach", on_origin)
            out = self.run_pipeline("phase", "scratch", "0", "done", "--force", "x", env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(desk.bodies()[1].get("commit"), on_origin, "a detached commit origin has")
        self.assertEqual(self.origin_refs(origin), before)

    def test_no_remote_named_origin_pushes_nothing_says_nothing_and_names_no_commit(self):
        origin = self.repo_with_origin()
        self.on_a_feature_branch(origin)
        self.git("remote", "rename", "origin", "upstream")
        # Even a commit `upstream` has is not one origin has.
        self.git("checkout", "-q", "main")
        self.init_scratch()
        with Listener() as desk:
            out = self.run_pipeline("phase", "scratch", "0", "start",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stderr, "")
            self.assertNotIn("commit", desk.bodies()[0])
        self.assertNotIn("refs/heads/feature/alydas-135-s6", self.origin_refs(origin))

    def test_with_no_desk_to_tell_nothing_is_pushed(self):
        """The push is for the desk's read. A run reporting to no desk pushes nothing."""
        origin = self.repo_with_origin()
        self.on_a_feature_branch(origin)
        before = self.origin_refs(origin)
        self.init_scratch()
        out = self.run_pipeline("phase", "scratch", "0", "start")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.origin_refs(origin), before)

    def test_a_push_that_fails_is_one_stderr_line_and_the_phase_still_closes(self):
        origin = self.repo_with_origin()
        head = self.on_a_feature_branch(origin)
        self.git("remote", "set-url", "origin", os.path.join(self.tmp.name, "no-such-origin.git"))
        self.init_scratch()
        with Listener() as desk:
            out = self.run_pipeline("phase", "scratch", "0", "done", "--force", "a scratch run",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertNotIn("Traceback", out.stderr)
            self.assertEqual(len(out.stderr.strip().splitlines()), 1, out.stderr)
            self.assertIn("not pushed", out.stderr)
            self.assertEqual(len(desk.requests), 1, "the beat did not go after the failed push")
            self.assertNotIn("commit", desk.bodies()[0], f"{head} never reached origin")
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["phases"][0]["status"], "done")

    # ALYDAS-135, S9 (Sachit, 2026-09-26: "no need to publish it anymore outside
    # of the desk. So keep everything within."). The Phase 2 variants page is
    # picked in the desk's run view while Phase 2 is still open, so the note that
    # records a produced file pushes and beats, the way a boundary does; a note
    # that records no file sends nothing, as before.

    VARIANTS = "docs/prototypes/2026-09-13-scratch.html"

    def phase_2_open_with_a_committed_page(self, origin):
        """Phase 2 started, then a variants page written and committed on the branch."""
        self.on_a_feature_branch(origin)
        self.init_scratch()
        out = self.run_pipeline("phase", "scratch", "2", "start")
        self.assertEqual(out.returncode, 0, out.stderr)
        os.makedirs(os.path.join(self.tmp.name, "docs", "prototypes"), exist_ok=True)
        return self.commit(self.VARIANTS)

    def test_a_note_that_records_a_produced_file_pushes_and_sends_one_beat_naming_it(self):
        origin = self.repo_with_origin()
        head = self.phase_2_open_with_a_committed_page(origin)
        with Listener(watch=lambda: self.origin_refs(origin)) as desk:
            out = self.run_pipeline("note", "scratch", "2", "--produced", self.VARIANTS + "=variants page",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(out.stderr, "")
            self.assertEqual(len(desk.requests), 1, "a note with a produced file sends exactly one beat")
            body = desk.bodies()[0]
            phase = [p for p in body["ledger"]["phases"] if p["n"] == 2][0]
            self.assertEqual(phase["status"], "running", "the phase is still open")
            self.assertIn({"path": self.VARIANTS, "note": "variants page"}, phase["produced"])
            self.assertEqual(body.get("commit"), head)
            self.assertFalse(body["ended"])
            self.assertEqual(desk.observed[0].get("refs/heads/feature/alydas-135-s6"), head,
                             "the branch was not on origin when the beat arrived")

    def test_a_note_that_records_no_produced_file_still_sends_one_beat(self):
        """ALYDAS-173 Gate 8: criterion 1's live activity log. This used to pin the
        opposite (a note with no file sent nothing), which left the plain line, the
        scene, the lede and every other story write, and the run log that shows
        them, waiting on the desk until the next step or phase beat. Now every note
        beats, and the beat carries what it wrote. It does not push: only a note
        that records a file does (see the push-count test below)."""
        origin = self.repo_with_origin()
        self.phase_2_open_with_a_committed_page(origin)
        with Listener() as desk:
            out = self.run_pipeline("note", "scratch", "2", "--plain", "Two ways to lay it out, one picked.",
                                    "--decided", "B", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(len(desk.requests), 1, "a note with no produced file did not send exactly one beat")
            body = desk.bodies()[0]
        phase = [p for p in body["ledger"]["phases"] if p["n"] == 2][0]
        self.assertEqual(phase["plain"], "Two ways to lay it out, one picked.")
        self.assertEqual(phase["decided"], ["B"])
        self.assertEqual(body["ledger"]["log"][-1]["text"], "Decided: B")
        self.assertFalse(body["ended"])

    def test_only_a_call_that_records_a_file_pushes_and_every_writing_call_beats_once(self):
        """ALYDAS-173 Gate 8 (coordinator's ruling): a push can start a Vercel
        preview build, and only a recorded file needs the branch on origin for
        the desk to open it. So `note --produced` and `note --doc` push once and
        beat once, and `step`, `story`, `log` and a note with no file beat once
        and push nothing. Pushes are counted by a post-receive hook on the bare
        origin, which runs once for each push that moves a ref; a fresh commit
        before each call means any push would move one."""
        origin = self.repo_with_origin()
        self.phase_2_open_with_a_committed_page(origin)
        pushes = os.path.join(os.path.dirname(origin), "pushes.log")
        hook = os.path.join(origin, "hooks", "post-receive")
        with open(hook, "w") as f:
            f.write(f"#!/bin/sh\necho pushed >> '{pushes}'\n")
        os.chmod(hook, 0o755)
        count = lambda: open(pushes).read().count("pushed") if os.path.exists(pushes) else 0
        calls = [
            (("note", "scratch", "2", "--plain", "Two ways, one picked."), 0),
            (("step", "scratch", "2", "1", "doing"), 0),
            (("story", "scratch", "--stands", "Picking a layout."), 0),
            (("log", "scratch", "A line."), 0),
            (("note", "scratch", "2", "--produced", self.VARIANTS + "=variants page"), 1),
            (("note", "scratch", "2", "--doc", "docs/specs/d.md|The design|what it says|in short"), 1),
        ]
        for i, (args, pushed) in enumerate(calls):
            with self.subTest(" ".join(args[:4])):
                self.commit(f"call-{i}.txt")
                before = count()
                with Listener() as desk:
                    out = self.run_pipeline(*args, env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
                    self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                    self.assertEqual(len(desk.requests), 1, "did not beat exactly once")
                self.assertEqual(count() - before, pushed, "pushed the wrong number of times")

    def test_a_log_line_sends_one_beat_carrying_it_and_a_stop_on_it_exits_3(self):
        """ALYDAS-173 Gate 8: criterion 1's live activity log. `log` writes only
        the run log, which the desk shows live, so it beats as a step does."""
        self.init_scratch("--hold", "none")  # S5: starts a phase past Phase 2
        self.assertEqual(self.run_pipeline("phase", "scratch", "5", "start").returncode, 0)
        with Listener() as desk:
            out = self.run_pipeline("log", "scratch", "Slice S2 landed green.", "--kind", "landed",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            [body] = desk.bodies()
        self.assertEqual(body["statusLine"], "Phase 5 · Dispatch · running")
        last = body["ledger"]["log"][-1]
        self.assertEqual((last["phase"], last["kind"], last["text"]), (5, "landed", "Landed: Slice S2 landed green."))
        with Listener(reply={"session": {"id": "s1"}, "directive": {"kind": "stop"}}) as desk:
            out = self.run_pipeline("log", "scratch", "Another line.", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertEqual([b["ended"] for b in desk.bodies()], [False, True])

    def test_a_refused_note_and_the_declaring_commands_send_nothing(self):
        """A note refused before it writes (exit 1, or argparse's exit 2) sends
        nothing; `table`, `defer` and `set` declare and stay silent."""
        self.init_scratch()
        with Listener() as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            for args, code in ((("note", "scratch", "2", "--backstory", "Frame's, not Brainstorm's."), 1),
                               (("note", "scratch", "2", "--clear", "nothing-by-that-name"), 2),
                               (("table", "scratch", "2", "--columns", "A,B", "--row", "1|2"), 0),
                               (("defer", "scratch", "--item", "Later."), 0),
                               (("set", "scratch", "title", "Scratch two"), 0)):
                with self.subTest(args[0]):
                    out = self.run_pipeline(*args, env=env)
                    self.assertEqual(out.returncode, code, out.stdout + out.stderr)
            self.assertEqual(desk.requests, [])

    def test_a_note_with_a_produced_file_and_no_desk_pushes_nothing_and_sends_nothing(self):
        origin = self.repo_with_origin()
        self.phase_2_open_with_a_committed_page(origin)
        before = self.origin_refs(origin)
        with Listener() as desk:
            for env in ({}, {URL_VARIABLE: desk.url}, {TOKEN_VARIABLE: "t0k3n"}):
                with self.subTest(env=sorted(env)):
                    out = self.run_pipeline("note", "scratch", "2", "--produced", self.VARIANTS + "=variants page",
                                            env=env)
                    self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(desk.requests, [])
        self.assertEqual(self.origin_refs(origin), before)

    def test_produced_for_a_path_a_document_named_updates_that_entry_s_note(self):
        """Gate 8, C-3. prototype.md names the page with `--doc`, then records it
        with `--produced PATH=note`: that is one file, so one entry carrying the
        title and the note, not a second entry with the note alone. A new path
        still appends, and a later note for the path replaces the note."""
        self.init_scratch()
        page = "docs/prototypes/2026-09-13-scratch.html"
        for args in (("--doc", page + "|Three layouts|What each looks like|B reads best"),
                     ("--produced", page + "=variants page"),
                     ("--produced", "docs/specs/design.md=the design"),
                     ("--produced", page + "=variants page, B picked")):
            out = self.run_pipeline("note", "scratch", "2", *args)
            self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.board()["phases"][2]["produced"], [
            {"path": page, "note": "variants page, B picked", "title": "Three layouts",
             "about": "What each looks like", "in_short": "B reads best"},
            {"path": "docs/specs/design.md", "note": "the design"},
        ])

    def test_a_stop_on_a_note_s_beat_ends_the_run_and_exits_3(self):
        """The same as a stop on a boundary's beat: logged, one ended beat, exit 3."""
        origin = self.repo_with_origin()
        self.phase_2_open_with_a_committed_page(origin)
        with Listener(reply={"session": {"id": "s1"}, "directive": {"kind": "stop"}}) as desk:
            out = self.run_pipeline("note", "scratch", "2", "--produced", self.VARIANTS + "=variants page",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertIn("pipeline: the desk says stop", out.stdout)
            bodies = desk.bodies()
            self.assertEqual(len(bodies), 2, "a stop sends exactly one more beat")
            self.assertEqual([b["ended"] for b in bodies], [False, True])
            self.assertIn("stopped", bodies[1]["statusLine"])
            self.assertIn("stopped from the desk", self.events_text())
            self.assertNotIn("t0k3n", out.stdout + out.stderr)

    # ALYDAS-173, S5. A note naming a document records a file, so it beats as
    # `--produced` does: pushed first, one beat carrying the document, and a stop
    # on it obeyed.
    def test_a_note_that_names_a_document_pushes_and_sends_one_beat_carrying_it(self):
        origin = self.repo_with_origin()
        head = self.phase_2_open_with_a_committed_page(origin)
        with Listener(watch=lambda: self.origin_refs(origin)) as desk:
            out = self.run_pipeline("note", "scratch", "2", "--doc",
                                    self.VARIANTS + "|Three ways to show it|The variants side by side.|B reads best.",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(len(desk.requests), 1, "a note with a document sends exactly one beat")
            body = desk.bodies()[0]
            phase = [p for p in body["ledger"]["phases"] if p["n"] == 2][0]
            self.assertIn({"path": self.VARIANTS, "note": "", "title": "Three ways to show it",
                           "about": "The variants side by side.", "inShort": "B reads best."}, phase["produced"])
            self.assertEqual(body.get("commit"), head)
            self.assertEqual(desk.observed[0].get("refs/heads/feature/alydas-135-s6"), head,
                             "the branch was not on origin when the beat arrived")

    def test_a_stop_on_a_document_notes_beat_ends_the_run_and_exits_3(self):
        origin = self.repo_with_origin()
        self.phase_2_open_with_a_committed_page(origin)
        with Listener(reply={"session": {"id": "s1"}, "directive": {"kind": "stop"}}) as desk:
            out = self.run_pipeline("note", "scratch", "2", "--doc", self.VARIANTS + "|Three ways to show it",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertEqual([b["ended"] for b in desk.bodies()], [False, True])

    def test_a_message_on_a_note_s_beat_is_printed_and_the_note_still_lands(self):
        origin = self.repo_with_origin()
        self.phase_2_open_with_a_committed_page(origin)
        with Listener(reply={"session": {"id": "s1"}, "directive": {"kind": "message", "text": "B please"}}) as desk:
            out = self.run_pipeline("note", "scratch", "2", "--produced", self.VARIANTS + "=variants page",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn("pipeline: message from the desk: B please", out.stdout)
            self.assertEqual(len(desk.requests), 1)
        self.assertIn("phase 2 noted", out.stdout)

    def test_a_note_after_the_run_ended_keeps_it_ended_and_a_stop_sends_nothing_more(self):
        """A produced file noted after Phase 9 closed does not bring the run back to the Now card."""
        origin = self.repo_with_origin()
        self.phase_2_open_with_a_committed_page(origin)
        out = self.run_pipeline("phase", "scratch", "9", "done", "--force", "a scratch run")
        self.assertEqual(out.returncode, 0, out.stderr)
        with Listener(reply={"session": {"id": "s1"}, "directive": {"kind": "stop"}}) as desk:
            out = self.run_pipeline("note", "scratch", "9", "--produced", "docs/handoffs/x.md=resume",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual([b["ended"] for b in desk.bodies()], [True])
            self.assertIn("pipeline: the desk says stop", out.stdout)


    # Gate 8, F2. The ledger is edited backwards by design, so a note may record
    # a file against an earlier phase. Its beat still says where the run is: the
    # current phase and status line, as the last boundary's beat said them. The
    # file itself stays on the phase it was noted against.

    def boundaries(self, env, *steps):
        """Run `phase` boundaries against the desk; answers the last one's body."""
        for n, event in steps:
            args = ("phase", "scratch", n, event) + (("--force", "x") if event == "done" else ())
            out = self.run_pipeline(*args, env=env)
            self.assertEqual(out.returncode, 0, out.stderr)

    def test_a_note_on_an_earlier_phase_beats_as_the_phase_the_run_is_in(self):
        for steps, expected in (
            ((("0", "start"), ("0", "done"), ("1", "start"), ("1", "done"), ("2", "start")),
             "Phase 2 · Prototype · running"),
            ((("0", "start"), ("0", "done"), ("1", "start"), ("1", "done")), "Phase 1 · Brainstorm · done"),
        ):
            with self.subTest(expected):
                self.tmp.cleanup(); os.makedirs(self.tmp.name)
                self.init_scratch()
                with Listener() as desk:
                    env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
                    self.boundaries(env, *steps)
                    boundary = desk.bodies()[-1]
                    self.assertEqual(boundary["statusLine"], expected)
                    desk.clear()
                    out = self.run_pipeline("note", "scratch", "0", "--produced", "docs/goal.md=late goal doc", env=env)
                    self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                    [body] = desk.bodies()
                self.assertEqual(body["statusLine"], boundary["statusLine"])
                self.assertEqual(body["phase"], boundary["phase"])
                self.assertFalse(body["ended"])
                phase_0 = [p for p in body["ledger"]["phases"] if p["n"] == 0][0]
                self.assertIn({"path": "docs/goal.md", "note": "late goal doc"}, phase_0["produced"])

    def test_a_stop_on_an_earlier_phase_s_note_is_logged_under_the_current_phase(self):
        self.init_scratch()
        with Listener() as desk:
            self.boundaries({URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"},
                            ("0", "start"), ("0", "done"), ("1", "start"), ("1", "done"), ("2", "start"))
        with Listener(reply={"session": {"id": "s1"}, "directive": {"kind": "stop"}}) as desk:
            out = self.run_pipeline("note", "scratch", "0", "--produced", "docs/goal.md=late goal doc",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
            self.assertEqual([(b["statusLine"], b["ended"]) for b in desk.bodies()],
                             [("Phase 2 · Prototype · running", False), ("Phase 2 · Prototype · stopped", True)])
        stops = [json.loads(line) for line in self.events_text().splitlines() if "stopped from the desk" in line]
        self.assertEqual([e["phase"] for e in stops], [2])


    # ---------- Gate 6 (ALYDAS-173): the residuals ----------
    #
    # Each case below closes a mutation that left the wave's own cases green.
    # The mutation each one closes is named in its docstring.

    def test_g6_a_plain_line_of_any_whitespace_the_desk_trims_cannot_close(self):
        """The S1 case tried one blank line, "   \\t ", so a check that strips
        only spaces and tabs passed it. Each whitespace JavaScript trims, one
        kind at a time, is no plain line, U+FEFF included: Python's `strip`
        keeps it, so a plain line of it closed a phase while the beat sent it
        blank (Gate 6; the check now asks `said`, the beat's own test)."""
        self.init_scratch()
        for blank in ("﻿", " ﻿\n"):
            with self.subTest(blank=blank):
                self.assertEqual(self.run_pipeline("note", "scratch", "1", "--plain", blank).returncode, 0)
                out = self.run_pipeline("phase", "scratch", "1", "done")
                self.assertEqual(out.returncode, 2, out.stdout)
                self.assertIn('no plain line: write one with note scratch 1 --plain "..."', out.stderr)
        for blank in ("\n", "\r\n", " ", "　", " ", "\v\f", "   "):
            with self.subTest(blank=blank):
                self.assertEqual(self.run_pipeline("note", "scratch", "1", "--plain", blank).returncode, 0)
                out = self.run_pipeline("phase", "scratch", "1", "done")
                self.assertEqual(out.returncode, 2, out.stdout)
                self.assertIn('no plain line: write one with note scratch 1 --plain "..."', out.stderr)

    def test_g6_frame_flags_are_refused_on_every_phase_but_frame_and_write_nothing(self):
        """The S2 case tried phases 1 and 9, so a check that refused those two
        and no other passed it. Every phase PHASES has but Frame refuses each of
        the three, and neither the ledger nor the events file changes."""
        P = pipeline_module()
        self.init_scratch()
        others = [n for n, _, _, _ in P.PHASES if n != 0]
        self.assertEqual(len(others), len(P.PHASES) - 1)
        for n in others:
            for flag in ("--backstory", "--fixed-means", "--wont-change"):
                with self.subTest(n=n, flag=flag):
                    before, events = self.board_bytes(), self.events_text()
                    out = self.run_pipeline("note", "scratch", str(n), "--scene", "Not written.", flag, "Belongs on Frame.")
                    self.assertEqual(out.returncode, 1, out.stdout)
                    self.assertIn(f"{flag} is Frame's: write it on phase 0", out.stderr)
                    self.assertEqual(self.board_bytes(), before)
                    self.assertEqual(self.events_text(), events)

    def test_g6_a_refused_call_leaves_the_events_file_as_it_was_too(self):
        """The wave's refusal cases compared the ledger JSON alone, so an event
        logged before the refusal (a lede, a line of what happened) left a line
        in the run log for a call that wrote nothing. And its per-field bounds
        were tried with the count already full, so the count refused them
        whatever the field's own bound said. Each refusal here, of note, step
        and story, leaves both files as they were, and each field's bound is
        tried with room under its count."""
        b = pipeline_module().LEDGER_BOUNDS
        self.init_scratch("--hold", "none")  # S5: starts a phase past Phase 2
        self.assertEqual(self.run_pipeline("phase", "scratch", "5", "start").returncode, 0)
        refused = [
            ("note", "scratch", "1", "--lede", "Not written.", "--option", "Shared check|maybe|why"),
            ("note", "scratch", "1", "--lede", "Not written.", "--number", "|what it means"),
            ("note", "scratch", "1", "--lede", "Not written.", "--assumed", " |later"),
            ("note", "scratch", "1", "--summary", "Not written.", "--doc", "docs/a.md"),
            ("note", "scratch", "1", "--summary", "Not written.", "--doc", "docs/a.md|" + "t" * (b["title"] + 1)),
            ("step", "scratch", "5", "9", "done"),
            ("step", "scratch", "5", "No such step", "done"),
            ("step", "scratch", "5", "1", "done", "--sub", "s" * (b["stepSub"] + 1)),
            ("step", "scratch", "5", "t" * (b["stepText"] + 1), "--add"),
            ("step", "scratch", "5", "   ", "--add"),
            ("story", "scratch", "--happened", "Not written.", "--term", "no equals sign"),
            ("story", "scratch", "--happened", "Not written.", "--happened", "x" * (b["happenedLine"] + 1)),
            ("story", "scratch", "--happened", "Not written.", "--stands", "x" * (b["stands"] + 1)),
            ("story", "scratch", "--happened", "Not written.", "--needs-you", "w" * (b["needWhat"] + 1) + "|why"),
            ("story", "scratch", "--happened", "Not written.", "--needs-you", "what|" + "y" * (b["needWhy"] + 1)),
            ("story", "scratch", "--stands", "Not written.", *[x for i in range(b["happened"] + 1) for x in ("--happened", f"Line {i}")]),
            ("story", "scratch", "--stands", "Not written.", *[x for i in range(b["needsYou"] + 1) for x in ("--needs-you", f"Need {i}")]),
        ]
        for call in refused:
            with self.subTest(call=" ".join(call)[:90]):
                before, events = self.board_bytes(), self.events_text()
                out = self.run_pipeline(*call)
                self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
                self.assertEqual(self.board_bytes(), before)
                self.assertEqual(self.events_text(), events)

    def full_frame(self):
        """Phase 0 started, with every key `note --clear` takes written."""
        self.init_scratch()
        self.assertEqual(self.run_pipeline("phase", "scratch", "0", "start").returncode, 0)
        out = self.run_pipeline(
            "note", "scratch", "0", "--plain", "Plain.", "--scene", "Scene.", "--backstory", "Backstory.",
            "--fixed-means", "Fixed.", "--wont-change", "Same.", "--lede", "Lede.", "--number", "1|one",
            "--option", "A|chosen|why", "--assumed", "t|later", "--left-out", "Left.", "--wrong", "Broke => Fixed.",
            "--decided", "Decided.", "--measured", "Measured.", "--overruled", "Overruled.",
            "--produced", "docs/a.md=a file", "--summary", "Summary.", "--next", "Next.")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_g6_clearing_one_key_leaves_every_other_key_as_it_was(self):
        """The wave cleared each key in turn and checked it blank, so a clear
        that also blanked the key after it (`--clear scene` taking the
        backstory) passed. Each clear here runs alone on a phase with every key
        written, and every other key of the phase is compared. The keys are the
        parser's own `--clear` choices."""
        from test_docs import build_parser, subcommands
        choices = subcommands(build_parser())["note"]._option_string_actions["--clear"].choices
        self.full_frame()
        # ALYDAS-225, Gate 8, 3. A running phase's steps are cleared only once
        # each is done or dropped, so the fixture's are ticked: still written,
        # and clearable like every other key.
        for i in range(1, len(self.board()["phases"][0]["steps"]) + 1):
            self.assertEqual(self.run_pipeline("step", "scratch", "0", str(i), "done").returncode, 0)
        written = self.board()
        for choice in choices:
            key = choice.replace("-", "_")
            with self.subTest(clear=choice):
                self.assertTrue(written["phases"][0][key], f"the fixture never wrote {key}")
                self.write_board(written)
                out = self.run_pipeline("note", "scratch", "0", "--clear", choice)
                self.assertEqual(out.returncode, 0, out.stderr)
                after = self.board()["phases"][0]
                self.assertIn(after[key], ([], ""))
                # The phase's telemetry is recomputed on every write, so it is not compared.
                self.assertEqual({k: v for k, v in after.items() if k not in (key, "telemetry")},
                                 {k: v for k, v in written["phases"][0].items() if k not in (key, "telemetry")})

    def test_g6_clear_on_a_ledger_from_before_the_lists_writes_lists_that_take_entries(self):
        """LIST_NOTES is for a ledger written before the list keys existed; the
        wave's clears all ran on a fresh ledger, which has every one, so an
        empty LIST_NOTES passed. On a ledger without them, each clear writes []
        and the next note or step appends to it."""
        self.init_scratch()
        lists = ("fixed_means", "wont_change", "numbers", "options", "assumed", "left_out", "problems", "steps")
        run = self.board()
        for key in lists:
            run["phases"][0].pop(key)
        self.write_board(run)
        for key in lists:
            out = self.run_pipeline("note", "scratch", "0", "--clear", key.replace("_", "-"))
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(self.board()["phases"][0][key], [], key)
        out = self.run_pipeline("note", "scratch", "0", "--fixed-means", "F.", "--wont-change", "W.", "--number", "1|one",
                                "--option", "A|chosen|why", "--assumed", "t", "--left-out", "L.", "--wrong", "a => b")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.run_pipeline("step", "scratch", "0", "S", "--add").returncode, 0)
        self.assertEqual({key: len(self.board()["phases"][0][key]) for key in lists}, {key: 1 for key in lists})

    def test_g6_every_half_of_a_packet_is_stored_trimmed(self):
        """Each half of what `note` and `story` split on a bar is trimmed; the
        wave's values had no space around a bar, so an untrimmed half passed."""
        self.init_scratch()
        out = self.run_pipeline("note", "scratch", "1", "--number", " 6 routes | six of them ",
                                "--option", " A | chosen | why ", "--assumed", " t | later ",
                                "--doc", " docs/a.md | T | about | short ", "--wrong", " what  =>  did ")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.run_pipeline("story", "scratch", "--needs-you", " what | why ", "--term", " C1 = one ").returncode, 0)
        one = self.board()["phases"][1]
        self.assertEqual(one["numbers"], [{"figure": "6 routes", "means": "six of them"}])
        self.assertEqual(one["options"], [{"name": "A", "chosen": True, "why": "why"}])
        self.assertEqual(one["assumed"], [{"text": "t", "later": "later"}])
        self.assertEqual(one["produced"], [{"path": "docs/a.md", "note": "", "title": "T", "about": "about", "in_short": "short"}])
        self.assertEqual(one["problems"], [{"what": "what", "did": "did"}])
        self.assertEqual(self.board()["story"]["needs_you"], [{"what": "what", "why": "why"}])
        self.assertEqual(self.board()["terms"], {"C1": "one"})

    def test_g6_a_log_event_with_no_zone_is_left_out_wherever_the_clock_is(self):
        """The S6 case put its zone-less event among 240 others. East of UTC a
        time with no zone reads as earlier than the fillers, so the newest-200
        cut dropped it whatever `log_stamp` did, and a stamp that took it passed
        (measured here at +10:00). Here it is the newest of two."""
        P = pipeline_module()
        run = {"phases": [], "deferred": [], "events": [
            {"ts": "2026-09-13T10:00:00.000Z", "phase": 1, "event": "log", "note": "kept"},
            {"ts": "2099-01-01T00:00:00", "phase": 1, "event": "log", "note": "no zone"},
            {"ts": "2099-01-01T00:00:00+14:00", "phase": 1, "event": "log", "note": "zoned"}]}
        self.assertEqual([(e["at"], e["text"]) for e in P.beat_ledger(run)["log"]],
                         [("2026-09-13T10:00:00.000Z", "kept"), ("2098-12-31T10:00:00.000Z", "zoned")])

    def filled_under_budget(self, fill):
        """The scratch board with every field the wave added written on Frame
        and Brainstorm, each text past its bound in `fill`, with odd entries
        beside (a fourth step state, halves only the desk trims away, a kind
        that is blank), and small enough that the beat is not trimmed. The
        entries that are plain ASCII are there whatever the fill, so each list
        reaches the desk in every alphabet."""
        b = pipeline_module().LEDGER_BOUNDS
        run = self.board()
        over = lambda field, extra=7: fill * (b[field] + extra)
        for p in run["phases"][:2]:
            p["plain"] = over("plain")
            p["purpose"], p["scene"], p["backstory"], p["lede"] = over("purpose"), over("scene"), over("backstory"), over("lede")
            p["fixed_means"] = [over("storyEntry"), " \ufeff", "kept"]
            p["wont_change"] = [over("storyEntry"), "kept"]
            p["left_out"] = [over("storyEntry"), "\ufeff", "kept"]
            p["numbers"] = [{"figure": over("figure"), "means": over("storyEntry")}, {"figure": "\ufeff ", "means": "x"},
                            {"figure": "1", "means": " \ufeff"}]
            p["options"] = [{"name": over("optionName"), "chosen": True, "why": over("storyEntry")},
                            {"name": "B", "chosen": "yes", "why": ""}, {"name": " ", "chosen": False, "why": "x"}]
            p["assumed"] = [{"text": over("storyEntry"), "later": over("storyEntry")}, {"text": "t", "later": "\ufeff"}]
            p["problems"] = [{"what": over("storyEntry"), "did": over("storyEntry")}, {"what": "w"}, {"what": "", "did": "x"}]
            p["steps"] = ([{"text": over("stepText"), "state": "doing", "sub": over("stepSub")},
                           {"text": "a fourth state", "state": "later", "sub": ""}, {"text": "no state"},
                           {"text": "done", "state": "done", "sub": " \ufeff "}, {"text": " ", "state": "todo"}]
                          + [{"text": f"s{i}", "state": "todo", "sub": ""} for i in range(b["steps"])])
            p["produced"] = [{"path": "docs/a.md", "note": over("note"), "title": over("title"), "about": over("about"),
                              "in_short": over("inShort")},
                             {"path": "docs/b.md", "note": "", "title": "\ufeff", "about": "a", "in_short": " "},
                             {"path": "docs/c.md", "note": "", "title": "C", "about": " ", "in_short": "s"}]
        run["terms"] = {over("termCode"): over("termPlain"), "c1": "Criterion 1", " ": "no code", "c2": "\ufeff"}
        run["story"] = {"happened": [over("happenedLine"), " ", "kept"], "stands": over("stands"),
                        "needs_you": [{"what": over("needWhat"), "why": over("needWhy")}, {"what": "w", "why": "\ufeff"},
                                      {"what": "v", "why": ""}, {"what": " ", "why": "x"}, {"why": "no what"}]}
        self.write_events(
            [{"ts": f"2026-09-13T10:00:{e:02d}.000Z", "phase": (None, 1, 150, 3)[e % 4],
              "event": ("log", "", " ", "\ufeff", over("logKind"), "step", "term")[e % 7],
              # ALYDAS-175, Gate 8 (C-2): logText is 1,300 now, so one entry in
              # four runs past it (every kind among them) and the beat stays
              # under the budget in the astral fill.
              "note": f"{e} " + (over("logText") if e % 4 == 0 else "kept")}
             for e in range(40)]
            + [{"ts": "2026-09-13T10:01:00.000Z", "phase": 1, "event": "log", "note": "\ufeff "}])
        return run

    def test_g6_a_beat_under_the_budget_carrying_every_new_field_is_one_the_desk_takes(self):
        """The S4 report's finding, confirmed here: the Gate 6 property case
        fills past every bound so far that the trim cuts every step and packet
        to nothing, so none reached the desk's check, and a fourth step state
        sent as is passed it. Here the ledger stays under the budget (no trim),
        every field the wave added reaches the desk in four alphabets, and the
        desk's own heartbeatRefusal judges each beat. Each field is first
        asserted to be on the wire, so a beat that sent none cannot pass."""
        self.init_scratch()
        fills = {"ascii": "x", "astral": "\U0001F600", "cjk": "界", "trimmed-away": "\ufeff "}
        sent = {}
        for label, fill in fills.items():
            self.write_board(self.filled_under_budget(fill))
            with Listener() as desk:
                out = self.run_pipeline("phase", "scratch", "1", "start", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
                self.assertEqual(out.returncode, 0, out.stderr)
                [request] = desk.requests
            self.assertNotIn('"trimmed"', self.events_text(), f"{label}: the beat was trimmed, so this proves nothing")
            body = json.loads(request["raw"])
            ledger = body["ledger"]
            for p in ledger["phases"][:2]:
                story = p["story"]
                with self.subTest(fill=label, n=p["n"]):
                    self.assertEqual({"fixedMeans", "wontChange", "leftOut", "numbers", "options", "assumed", "problems", "steps"}
                                     - set(story), set(), "a packet did not reach the desk")
                    self.assertIn({"text": "a fourth state", "state": "todo"}, story["steps"])
                    self.assertEqual({f["path"] for f in p["produced"]}, {"docs/a.md", "docs/b.md", "docs/c.md"})
            self.assertIn("c1", ledger["terms"])
            self.assertIn("kept", ledger["story"]["happened"])
            self.assertGreaterEqual(len(ledger["log"]), 30)
            sent[label] = body
        refusals = desk_refusals(self, list(sent.values()))
        self.assertEqual(dict(zip(sent, refusals)), {label: None for label in sent},
                         "the desk refused a beat the pipeline sent untrimmed")

    def test_g6_the_trim_cuts_every_text_it_sends(self):
        """The wave's trim cases each built a ledger of their own slice's fields
        and checked those, so a field left out of the trim (the scene, what
        won't change, the terms' plain names, a thing waiting's why, a
        document's about line) passed every case but its own, and some passed
        that too. Here the ledger is `beat_ledger`'s own, from a board with
        every field past its bound, and `shaped` at a width under every bound
        leaves no string on the wire longer than the width, but the ones the
        trim never cuts: a name, a path, a status, a kind, a time, a step's
        state and a term's code. Every field the wave added is first asserted
        present, so a ledger that lost one cannot pass."""
        P = pipeline_module()
        self.init_scratch()
        ledger = P.beat_ledger(self.filled_past_every_bound("x"))
        never_cut = {"name", "path", "status", "kind", "startedAt", "endedAt", "state", "at"}
        width = 5
        self.assertLess(width, min(v for k, v in P.LEDGER_BOUNDS.items() if k not in ("phases", "n")))
        seen, long = set(), []
        def walk(value, where):
            if isinstance(value, dict):
                for key, inner in value.items():
                    walk(inner, where + (key,) if where[-1:] != ("terms",) else where + ("*",))
            elif isinstance(value, list):
                for inner in value:
                    walk(inner, where)
            elif isinstance(value, str):
                seen.add(".".join(k for k in where if k != "phases"))
                if where[-1] not in never_cut and len(value) > width:
                    long.append((".".join(where), len(value)))
        walk(ledger, ())
        for field in ("plain", "story.purpose", "story.scene", "story.backstory", "story.lede", "story.fixedMeans",
                      "story.wontChange", "story.leftOut", "story.numbers.figure", "story.numbers.means",
                      "story.options.name", "story.options.why", "story.assumed.text", "story.assumed.later",
                      "story.problems.what", "story.problems.did", "story.steps.text", "story.steps.sub",
                      "produced.title", "produced.about", "produced.inShort", "terms.*",
                      "story.happened", "story.stands", "story.needsYou.what", "story.needsYou.why"):
            self.assertIn(field, seen, f"the fixture sends no {field}, so its cut is not tested")
        self.assertTrue(long, "nothing in the fixture is past the width")
        before = set(seen)
        long.clear(); seen.clear()
        walk(P.shaped({**ledger, "log": []}, width, 0, 0), ())
        self.assertEqual(sorted(set(field for field, _ in long)), [], "the trim left these past its width")
        # And cut is not dropped: a field the trim does not know is not sent at all.
        self.assertEqual(sorted(before - seen), [], "the trim dropped these instead of cutting them")

    # ---------- Gate 6 (ALYDAS-173): the local board, drawn ----------

    def drawn_board(self):
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.html"), encoding="utf-8") as f:
            return render_board_in_node(self, f.read())

    def test_g6_the_local_board_draws_each_part_of_the_story_under_its_own_heading(self):
        """The wave's board cases looked for strings in the page, and the page
        holds the template's headings as literals and the run as JSON, so a
        template that stopped drawing a section (`p.options` misread, the steps
        never listed, the short version never filled) still passed. Here the
        template's own script runs in node over a small DOM, and each heading
        is read with what is drawn under it."""
        self.init_scratch()
        calls = [
            ("phase", "scratch", "0", "start"),
            ("note", "scratch", "0", "--plain", "Wrote down what done looks like.", "--scene", "Where it starts.",
             "--backstory", "Why this run exists.", "--fixed-means", "One answer.", "--fixed-means", "A red test.",
             "--wont-change", "People see the same.", "--lede", "Five ways were weighed.",
             "--number", "18 of 18|Every route leaked.", "--option", "Shared check|chosen|One place.",
             "--option", "Six checks|rejected|", "--assumed", "People mint tokens|An agent can.",
             "--left-out", "Rate limits.", "--wrong", "Tests failed => Gave read.",
             "--doc", "docs/plans/plan.md|The plan|Four slices, each test first.",
             "--produced", "docs/raw.txt=a plain file"),
            ("step", "scratch", "0", "1", "done", "--sub", "Read it all."),
            ("step", "scratch", "0", "2", "doing", "--sub", "Writing it."),
            ("story", "scratch", "--happened", "Proved the leak.", "--stands", "Built, not pushed.",
             "--needs-you", "Decide how tokens behave|They drop off Now."),
        ]
        for call in calls:
            out = self.run_pipeline(*call)
            self.assertEqual(out.returncode, 0, out.stderr)
        steps = pipeline_module().PHASE_STEPS[0]
        drawn = self.drawn_board()

        rail = parse_html(drawn["rail"][0])
        self.assertEqual([n.text() for n in rail.by_class("plain")], ["Wrote down what done looks like."])
        self.assertEqual(parse_html(drawn["rail"][1]).by_class("plain"), [], "a phase with no plain line drew one")

        frame = parse_html(drawn["panels"]["p0"])
        self.assertEqual([n.text() for n in frame.by_class("purpose")],
                         ["What this phase is for. " + pipeline_module().PHASE_PURPOSE[0]])
        self.assertEqual([n.text() for n in frame.by_class("where")], ["Where the story is. Where it starts."])
        self.assertEqual([n.text() for n in frame.by_class("lede")], ["Five ways were weighed."])
        under = frame.under_headings()
        self.assertEqual(under["The backstory"].text(), "Why this run exists.")
        self.assertEqual(items(under['What "fixed" means']), ["One answer.", "A red test."])
        self.assertEqual(items(under["What won't change"]), ["People see the same."])
        self.assertEqual(frame.heading("Steps").text(), f"Steps1 of {len(steps)} done")
        self.assertEqual([(li.attrs.get("data-s"), li.first("vh"), li.first("st-text"), li.first("sub"))
                          for li in under["Steps"].children_named("li")],
                         [("done", "Done: ", steps[0], "Read it all."), ("doing", "Doing now: ", steps[1], "Writing it.")]
                         + [("todo", "To do: ", text, None) for text in steps[2:]])
        self.assertEqual(items(under["What the numbers mean"]), ["18 of 18 Every route leaked."])
        self.assertEqual([(li.first("verdict"), li.find("b")[0].text(), li.first("sub"))
                          for li in under["What was weighed"].children_named("li")],
                         [("Chosen", "Shared check", "One place."), ("Not chosen", "Six checks", None)])
        self.assertEqual([(li.children[0], li.first("sub")) for li in under["Assumed"].children_named("li")],
                         [("People mint tokens", "Later: An agent can.")])
        self.assertEqual(items(under["Left out on purpose"]), ["Rate limits."])
        self.assertEqual([(li.children[0], li.first("sub")) for li in under["What went wrong, and what we did"].children_named("li")],
                         [("Tests failed", "What we did: Gave read.")])
        self.assertEqual(items(under["Went wrong, kept in"]), ["Tests failed => Gave read."])
        self.assertEqual([(li.first("dtitle"), li.first("dabout"), li.first("path"), li.first("pnote"))
                          for li in under["Produced"].children_named("li")],
                         [(None, None, "docs/raw.txt", "a plain file"),
                          ("The plan", "Four slices, each test first.", "docs/plans/plan.md", None)])

        pending = parse_html(drawn["panels"]["p3"])
        self.assertEqual([n.text() for n in pending.by_class("purpose")],
                         ["What this phase is for. " + pipeline_module().PHASE_PURPOSE[3]])
        self.assertEqual([n.text() for n in pending.by_class("empty")], ["Not reached."])

        self.assertIsNotNone(drawn["short"], "the short version was not drawn")
        short = parse_html(drawn["short"])
        cards = short.by_class("card")
        self.assertEqual([c.find("h2")[0].text() for c in cards], ["What happened", "Waiting on you 1"])
        self.assertEqual(items(cards[0].find("ul")[0]), ["Proved the leak."])
        self.assertEqual([n.text() for n in cards[0].by_class("stands")], ["Built, not pushed."])
        self.assertEqual([(li.first("what"), li.first("why")) for li in cards[1].find("ul")[0].children_named("li")],
                         [("Decide how tokens behave", "They drop off Now.")])

    def test_g6_the_local_board_draws_no_short_version_when_none_is_written(self):
        """The other direction: nothing written, no block."""
        self.init_scratch()
        self.assertIsNone(self.drawn_board()["short"])

    def test_g6_story_clear_and_rewrite_in_one_call_keeps_what_the_call_wrote(self):
        """`--clear` empties a part of the story to rewrite it. The wave cleared
        each part alone, so a clear applied after the call's own writes, which
        empties what that same call wrote, passed. Cleared and rewritten in one
        call, each part holds what the call wrote and nothing older."""
        self.init_scratch()
        out = self.run_pipeline("story", "scratch", "--term", "OLD=old", "--happened", "Old.", "--stands", "Old.",
                                "--needs-you", "Old|why")
        self.assertEqual(out.returncode, 0, out.stderr)
        out = self.run_pipeline("story", "scratch", "--clear", "terms", "--term", "NEW=new", "--clear", "happened",
                                "--happened", "New.", "--clear", "stands", "--stands", "New.",
                                "--clear", "needs-you", "--needs-you", "New|why")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.board()["terms"], {"NEW": "new"})
        self.assertEqual(self.board()["story"], {"happened": ["New."], "stands": "New.",
                                                 "needs_you": [{"what": "New", "why": "why"}]})

    def test_g6_a_forced_close_is_recorded_on_its_event_and_read_in_the_desks_log(self):
        """`--force` is recorded twice: on the phase, which the wave checked,
        and on the done event, which the desk's log reads as "closed by hand".
        Without the second the log said the phase closed as if it had passed."""
        self.init_scratch()
        out = self.run_pipeline("phase", "scratch", "3", "done", "--force", "Sachit ruled it.")
        self.assertEqual(out.returncode, 0, out.stderr)
        done = [json.loads(line) for line in self.events_text().splitlines()]
        self.assertEqual([e.get("forced") for e in done if e["event"] == "done"], ["Sachit ruled it."])
        texts = [e["text"] for e in pipeline_module().beat_ledger(self.board())["log"]]
        self.assertIn("Phase 3 · Plan done (closed by hand: Sachit ruled it.)", texts)

    def test_g6_the_trim_keeps_the_widest_width_that_fits_when_the_short_version_is_the_longest_text(self):
        """The trim searches widths up to the longest text it counts. The wave's
        short-version case had a longer phase text beside it, so a count that
        left the short version out cut no more than it had to, and passed.
        Here the short version holds the longest texts: the width is the widest
        that fits, and one wider does not."""
        P = pipeline_module()
        blank = {"summary": "", "next": "", "decided": [], "measured": [], "wrong": [], "overruled": [],
                 "produced": [], "table": None, "skipReason": "", "forced": "", "plain": "p" * 20}
        ledger = {"phases": [{**blank, "n": n, "name": f"Phase {n}", "status": "done"} for n in range(10)],
                  "deferred": [], "terms": {},
                  "story": {"happened": [f"{i} " + "h" * 290 for i in range(8)], "stands": "t" * 300,
                            "needsYou": [{"what": "w" * 20, "why": "y" * 20}]}}
        budget = P.ledger_bytes(ledger) - 500
        out, report = P.trim_ledger(ledger, budget)
        self.assertLessEqual(P.ledger_bytes(out), budget)
        self.assertGreater(report["width"], 20, "the width fell to the phase texts' length")
        self.assertGreater(P.ledger_bytes(P.shaped(ledger, report["width"] + 1, 0, 0)), budget,
                           "a wider width fits, so the trim cut more than it had to")


class DeskReaderCase(unittest.TestCase):
    """Gate 6, P01b, P02 and P03. The pins read the desk's TypeScript; these
    cases hold the reader to never skipping on a shape and never passing on one
    it misread, against fake checkouts in a temp directory."""

    TABLE = "phases: 20,\n  n: 99,\n  summary: 1_000,\n"

    def checkout(self, source, relative=DESK_LEDGER_BOUNDS):
        root = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, root, True)
        path = os.path.join(root, relative)
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as f:
            f.write(source)
        return root

    def fails(self, call):
        """The failure message `call` raises. A skip is not a failure: unittest
        would count a skip inside a subTest as skipped and pass the case, which
        is the silent skip these cases exist to forbid, so it fails here."""
        try:
            call()
        except unittest.SkipTest as e:
            self.fail(f"skipped where it should fail: {e}")
        except self.failureException as e:
            return str(e)
        self.fail("read where it should fail")

    def test_the_table_is_read_the_same_whatever_follows_or_precedes_the_braces(self):
        for tail in ("export const LEDGER_BOUNDS = {\n  %s} as const;",
                     "export const LEDGER_BOUNDS = {\n  %s} as const satisfies Record<string, number>;",
                     "export const LEDGER_BOUNDS: Record<string, number> = {\n  %s};",
                     "// the wall\nexport const LEDGER_BOUNDS = {\n  // counted\n  %s} satisfies Bounds;"):
            with self.subTest(tail):
                repo = self.checkout("const X = 1;\n" + tail % self.TABLE)
                self.assertEqual(read_desk_table(repo, True, DESK_LEDGER_BOUNDS, "LEDGER_BOUNDS"),
                                 {"phases": 20, "n": 99, "summary": 1000})

    def test_a_declared_table_the_reader_cannot_read_fails_and_never_skips(self):
        for label, source in (
            ("a named constant as a value", "export const LEDGER_BOUNDS = {\n  phases: 20,\n  summary: SUMMARY_MAX,\n} as const;"),
            ("a spread", "export const LEDGER_BOUNDS = {\n  ...BASE,\n  phases: 20,\n} as const;"),
            ("a call around the literal", "export const LEDGER_BOUNDS = Object.freeze({ phases: 20 });"),
            ("an expression", "export const LEDGER_BOUNDS = { phases: 10 * 2 } as const;"),
        ):
            for named in (False, True):
                with self.subTest(label, named=named):
                    repo = self.checkout(source)
                    with self.assertRaises(DeskUnreadable):
                        read_desk_table(repo, named, DESK_LEDGER_BOUNDS, "LEDGER_BOUNDS")
                    self.fails(lambda: desk_constant(self, read_desk_table, DESK_LEDGER_BOUNDS, "LEDGER_BOUNDS",
                                                     repo=repo, named=named))
        repo = self.checkout("export const HEARTBEAT_BODY_MAX = 256 * 1024;", DESK_REQUEST_BOUNDS)
        self.fails(lambda: desk_constant(self, read_desk_number, DESK_REQUEST_BOUNDS, "HEARTBEAT_BODY_MAX",
                                         repo=repo, named=False))

    def test_a_number_is_read_with_or_without_a_type_or_as_const(self):
        for source in ("export const HEARTBEAT_BODY_MAX = 262_144;", "export const HEARTBEAT_BODY_MAX: number = 262144;",
                       "export const HEARTBEAT_BODY_MAX = 262_144 as const;"):
            with self.subTest(source):
                repo = self.checkout(source, DESK_REQUEST_BOUNDS)
                self.assertEqual(read_desk_number(repo, True, DESK_REQUEST_BOUNDS, "HEARTBEAT_BODY_MAX"), 262_144)

    def test_a_checkout_without_the_constant_skips_naming_it_or_fails_when_named(self):
        """Absent is a skip only when nobody named the checkout, and the reason
        says which checkout was read and why that one. Named, it fails."""
        for label, repo in (("no such file", tempfile.gettempdir() + "/no-desk-here"),
                            ("no declaration", self.checkout("export const HEARTBEAT_BOUNDS = { run: 200 } as const;"))):
            with self.subTest(label):
                with self.assertRaises(unittest.SkipTest) as skipped:
                    desk_constant(self, read_desk_table, DESK_LEDGER_BOUNDS, "LEDGER_BOUNDS", repo=repo, named=False)
                self.assertIn(repo, str(skipped.exception))
                self.assertIn("ALYDASH_REPO is unset", str(skipped.exception))
                self.assertIn("LEDGER_BOUNDS", str(skipped.exception))
                failed = self.fails(lambda: desk_constant(self, read_desk_table, DESK_LEDGER_BOUNDS, "LEDGER_BOUNDS",
                                                          repo=repo, named=True))
                self.assertIn(repo, failed)


class SkillDocsCase(unittest.TestCase):
    """ALYDAS-135, S8, criterion 3: the pipeline no longer publishes its board as a
    Claude artifact or asks for one. Every markdown file in the skill is read, so
    a new reference file cannot bring the step back unseen.

    S9 (Sachit, 2026-09-26: "no need to publish it anymore outside of the desk.
    So keep everything within."): the Phase 2 variants page is not published
    either. It is committed and shown in the desk's run view, so no file is
    exempt, prototype.md included."""

    SKILL = os.path.dirname(HERE)
    # A publish instruction, in any of the shapes the skill has used for one.
    FORBIDDEN = (
        re.compile(r"Artifact\("),
        re.compile(r"artifact_url"),
        re.compile(r"claude\.ai/(code/)?artifact", re.I),
        re.compile(r"\bArtifact tool\b"),
        # S9. The design skill the Artifact tool requires, which the variants
        # page was told to load: loading it is the first step of publishing one.
        re.compile(r"\bartifact-design\b"),
    )
    def markdown_files(self):
        found = []
        for folder, _, names in os.walk(self.SKILL):
            # Hidden folders inside the skill (`.git`), judged from the skill's
            # own root: the skill itself lives under `~/.claude`, so a dot
            # anywhere in the absolute path would skip every file.
            inside = os.path.relpath(folder, self.SKILL)
            if inside != "." and os.sep + "." in os.sep + inside:
                continue
            found += [os.path.join(folder, n) for n in names if n.endswith(".md")]
        return sorted(found)

    def test_no_markdown_in_the_skill_tells_the_agent_to_publish_an_artifact(self):
        files = self.markdown_files()
        self.assertIn(os.path.join(self.SKILL, "SKILL.md"), files)
        self.assertIn(os.path.join(self.SKILL, "references", "board.md"), files)
        hits = []
        for path in files:
            relative = os.path.relpath(path, self.SKILL)
            with open(path, encoding="utf-8") as f:
                for number, line in enumerate(f, 1):
                    for pattern in self.FORBIDDEN:
                        if pattern.search(line):
                            hits.append(f"{relative}:{number}: {line.strip()}")
        self.assertEqual(hits, [], "an Artifact publish instruction is still in the skill")

    def test_the_guard_sees_a_publish_instruction_wherever_it_is_written(self):
        """The patterns catch each shape the old step took, the board's and the
        variants page's alike."""
        for line in ('Artifact(file_path=<root>/boards/x.html, icon="map")',
                     'python3 "$PIPE" set <slug> artifact_url <url>',
                     "Board     https://claude.ai/code/artifact/abc",
                     "the board; published with the Artifact tool",
                     "| A. Variants page | `<root>/prototypes/x.html`, published with the Artifact tool |",
                     "  Load `artifact-design` as the Artifact tool requires, then spend the design",
                     "| A published page or shareable report | `artifact-design` |"):
            with self.subTest(line=line):
                self.assertTrue(any(p.search(line) for p in self.FORBIDDEN), line)


class TestDocsNameTheRunPage(unittest.TestCase):
    """ALYDAS-154, Gate 8 (ST-8). Since S7 the desk draws a run on its own page,
    `/runs/<id>`, and a work item's screen shows one line linking there. The
    skill's docs said the item's screen draws the whole run."""

    SKILL = os.path.dirname(HERE)

    def read(self, *parts):
        with open(os.path.join(self.SKILL, *parts), encoding="utf-8") as f:
            return " ".join(f.read().split())

    def test_the_docs_say_the_run_page_draws_the_run_and_the_item_links_to_it(self):
        for parts in (("SKILL.md",), ("references", "board.md")):
            with self.subTest(file=os.path.join(*parts)):
                text = self.read(*parts)
                self.assertNotRegex(text, r"(item's|whose) screen (then )?draws")
                self.assertRegex(text, r"run's own page(, `<desk>/runs/<id>`,| on the desk,? `<desk>/runs/<id>`,)? draws")


# The largest the non-ledger half of a beat can be, in bytes: each bounded
# field at its wall, six bytes a character (a `\uXXXX` escape), plus 512 for
# the keys, the numbers and the punctuation.
HEARTBEAT_BOUNDS_WORST = {"run": 200 * 6, "agent": 80 * 6, "statusLine": 200 * 6, "phase": 80 * 6,
                          "branch": 200 * 6, "boardUrl": 2000 * 6, "subject": (40 + 9) * 6, "keys": 512,
                          "title": 200 * 6, "goal": 1000 * 6, "inSession": 128 * 6}

# The keys a phase carries on the wire, S1's four and S2's.
TELEMETRY_KEYS = ("wallS", "activeS", "inputTokens", "cacheWriteTokens", "cacheReadTokens", "outputTokens",
                  "agents", "costUsd")
LEDGER_PHASE_KEYS = ("n", "name", "status", "kind", "summary", "next", "decided", "measured", "wrong", "overruled",
                     "produced", "table", "skipReason", "forced", "startedAt", "endedAt", "plain",
                     # ALYDAS-173, S2. The phase's story: purpose, scene, and Frame's three.
                     "story") + TELEMETRY_KEYS


# ---------- ALYDAS-175, S2: `hold` raises an Ask, waits, and reads the answer back ----------

def import_pipeline():
    """pipeline.py as a module, for its pure functions."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import pipeline
    return pipeline


def option(name, chosen=False, why=""):
    return {"name": name, "chosen": chosen, "why": why}


class HoldChoicesCase(unittest.TestCase):
    """The review Ask's choices, from one pure function of the phase's options."""

    def choices(self, options):
        return import_pipeline().hold_choices(options)

    def ids_and_labels(self, options):
        built, _ = self.choices(options)
        return [(c["id"], c["label"]) for c in built]

    def test_no_options_is_approve_and_send_back(self):
        self.assertEqual(self.ids_and_labels([]), [("approve", "Approve"), ("send-back", "Send it back")])

    def test_one_chosen_option_is_folded_into_approve(self):
        self.assertEqual(self.ids_and_labels([option("One list, newest first", chosen=True)]),
                         [("approve", "Approve: One list, newest first"), ("send-back", "Send it back")])

    def test_one_option_not_chosen_is_a_variant(self):
        self.assertEqual(self.ids_and_labels([option("A")]),
                         [("approve", "Approve"), ("variant-1", "Go with A"), ("send-back", "Send it back")])

    def test_three_options_the_recommended_one_in_approve_and_only_the_others_listed(self):
        """Phase 2 verdict item 5: Approve names the recommended variant, and it
        is not listed a second time. A variant's id is its place among the
        recorded options, so it maps back to the option it names."""
        self.assertEqual(self.ids_and_labels([option("A"), option("B", chosen=True), option("C")]), [
            ("approve", "Approve: B"), ("variant-1", "Go with A"), ("variant-3", "Go with C"),
            ("send-back", "Send it back")])

    def test_eight_choices_at_most_in_both_directions(self):
        """Six other options fill eight choices exactly and nothing is left
        out; a seventh is left out (named, for the detail) and the list stays
        at eight, whether or not one option was chosen."""
        six = [option("pick", chosen=True)] + [option(f"other {k}") for k in range(1, 7)]
        built, left_out = self.choices(six)
        self.assertEqual(len(built), 8)
        self.assertEqual(left_out, [])
        self.assertEqual(built[-2]["label"], "Go with other 6")
        seven = six + [option("other 7")]
        built, left_out = self.choices(seven)
        self.assertEqual(len(built), 8)
        self.assertEqual(left_out, ["other 7"])
        self.assertEqual([c["id"] for c in built][0], "approve")
        self.assertEqual([c["id"] for c in built][-1], "send-back")
        unchosen = [option(f"other {k}") for k in range(1, 8)]
        built, left_out = self.choices(unchosen)
        self.assertEqual(len(built), 8)
        self.assertEqual(left_out, ["other 7"])
        self.assertEqual(built[0]["label"], "Approve")

    def test_only_send_it_back_needs_a_note(self):
        """ALYDAS-303, S9 (c6). The choice says it needs a note (`note:
        'required'`), so the desk asks for one by the marker and not by the id."""
        for options in ([], [option("A"), option("B", chosen=True), option("C")]):
            with self.subTest(options=len(options)):
                built, _ = self.choices(options)
                self.assertEqual([c.get("note") for c in built], [None] * (len(built) - 1) + ["required"])
                self.assertEqual(built[-1]["id"], "send-back")

    def test_every_choice_is_within_the_desk_s_bounds(self):
        long_name = "a layout with a name far longer than any label the desk will draw on one line " * 3
        built, _ = self.choices([option(long_name, chosen=True), option(long_name), option("‮evil\x07")])
        ids = [c["id"] for c in built]
        self.assertEqual(len(ids), len(set(ids)))
        for c in built:
            with self.subTest(choice=c["id"]):
                self.assertRegex(c["id"], r"^[a-z0-9-]{1,40}$")
                # Gate 6: the desk's label bound is 200 (it was 80).
                self.assertLessEqual(len(c["label"]), 200)
                self.assertTrue(c["label"].strip())
                self.assertNotRegex(c["label"], "[\x00-\x1f\x7f‪-‮⁦-⁩]")
        self.assertTrue(built[0]["label"].startswith("Approve: a layout"))

    def test_a_name_past_80_and_within_200_is_sent_whole(self):
        """Gate 6. The desk takes a choice label up to 200 characters, as it
        does a question, so a long option name is no longer cut at 80."""
        name = "A layout whose name runs well past eighty characters but stays inside the desk's two hundred"
        built, _ = self.choices([option("B", chosen=True), option(name)])
        self.assertGreater(len("Go with " + name), 80)
        self.assertEqual(built[1]["label"], "Go with " + name)
        built, _ = self.choices([option(name, chosen=True)])
        self.assertEqual(built[0]["label"], "Approve: " + name)

    def test_every_choice_says_what_taking_it_does(self):
        """Carried from S2: each choice carries a one-line detail in plain words,
        within the desk's bound (`CHOICE_DETAIL_MAX`, 200, one line, no bidi)."""
        built, _ = self.choices([option("A"), option("B", chosen=True), option("C")])
        self.assertEqual({c["id"]: c["detail"] for c in built}, {
            "approve": "Build B, the option this phase recommends, and go on to the next phase.",
            "variant-1": "Build A instead of the recommended option, and go on to the next phase.",
            "variant-3": "Build C instead of the recommended option, and go on to the next phase.",
            "send-back": "Reopen this phase to work on it again, with your note as its brief."})
        built, _ = self.choices([])
        self.assertEqual(built[0]["detail"], "Build what this phase recommends, and go on to the next phase.")

    def test_every_detail_is_within_the_desk_s_bounds(self):
        long_name = "a layout with a name far longer than any line the desk will draw under a choice " * 4
        built, _ = self.choices([option(long_name, chosen=True), option(long_name + "x"), option("‮evil\n\x07")])
        for c in built:
            with self.subTest(choice=c["id"]):
                self.assertTrue(c["detail"].strip())
                self.assertLessEqual(len(c["detail"]), 200)
                self.assertNotRegex(c["detail"], "[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]")
                # No bare codes: never the id the desk answers with.
                self.assertNotIn(c["id"], c["detail"])

    def test_it_is_pure(self):
        options = [option("A"), option("B", chosen=True)]
        before = json.dumps(options)
        self.assertEqual(self.choices(options), self.choices(options))
        self.assertEqual(json.dumps(options), before)


class HoldHelpers:
    """`hold` as a real process against the stub desk, with the waits shortened.
    ALYDAS-175, S5: the helpers, shared by `HoldCase` and `HoldsCase` without
    either inheriting (and so running again) the other's tests."""

    INTERVAL = "0.2"
    ASK = {"id": "ask-1", "kind": "decide", "question": "q", "answer": None, "answerLabel": None}
    PLAIN = "The three ways to show a waiting run are drawn side by side; pick one."
    LEDE = "Three layouts for the run that waits on your review."

    def board(self):
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.json")) as f:
            return json.load(f)

    def events_text(self):
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.events.jsonl"), encoding="utf-8") as f:
            return f.read()

    def phase_2_ready(self, plain=True, init=(), repo=True, recorded="docs/prototypes/x.html"):
        """Phase 2 running, its design doc carrying the verdict, a page produced,
        three options recorded (B chosen) and, unless told not, the plain line.

        ALYDAS-206, S1. The temp directory is a repository on a feature branch
        with a bare origin, and the page it records is written and committed,
        so hold's push lands and the desk could open it. Idempotent:
        `phase_2_ready_again` calls it on a directory that is a repository
        already, whose page is committed already. S2: `repo=False` leaves the
        temp directory outside any repository, with the page written.
        ALYDAS-212, S1: `recorded` is the path the note records for that page."""
        if repo and not os.path.isdir(os.path.join(self.tmp.name, ".git")):
            self.origin = self.repo_with_origin()
            self.on_a_feature_branch(self.origin)
        if not os.path.exists(os.path.join(self.tmp.name, "docs", "prototypes", "x.html")):
            self.page(commit=repo)
        self.init_scratch(*init)
        specs = os.path.join(self.root, "specs")
        os.makedirs(specs, exist_ok=True)
        with open(os.path.join(specs, "2026-09-13-scratch-design.md"), "w") as f:
            f.write("# Design\n\n## Phase 2 verdict\n\n4. **Pick:** B, recommended.\n")
        for args in (("phase", "scratch", "2", "start"),
                     ("note", "scratch", "2", "--lede", self.LEDE, "--produced", f"{recorded}=variants page",
                      "--option", "A|rejected|too loud", "--option", "B|chosen|reads best",
                      "--option", "C|rejected|hides the preview"),
                     ("step", "scratch", "2", "1", "doing")):
            out = self.run_pipeline(*args)
            self.assertEqual(out.returncode, 0, out.stderr)
        if plain:
            out = self.run_pipeline("note", "scratch", "2", "--plain", self.PLAIN)
            self.assertEqual(out.returncode, 0, out.stderr)
        self.tick_steps(2)

    def tick_steps(self, n):
        """ALYDAS-225, K1. Every step of phase n still to-do, ticked, as an
        agent ready to close has them: a phase with one to-do does not close or
        hold. The step being done is left doing; the close finishes it."""
        for i, step in enumerate(self.board()["phases"][n].get("steps") or [], start=1):
            if step.get("state") == "todo":
                out = self.run_pipeline("step", "scratch", str(n), str(i), "done")
                self.assertEqual(out.returncode, 0, out.stderr)

    def page(self, commit=True):
        """ALYDAS-206, S2. The variants page the phase records, written and, unless told not, committed."""
        page = os.path.join(self.tmp.name, "docs", "prototypes", "x.html")
        os.makedirs(os.path.dirname(page), exist_ok=True)
        with open(page, "w", encoding="utf-8") as f:
            f.write("<!doctype html><title>variants</title>\n")
        if commit:
            self.git("add", "docs/prototypes/x.html")
            self.git("commit", "-q", "-m", "the variants page")

    def phase_2_ready_again(self):
        boards = os.path.join(self.root, "boards")
        if os.path.isdir(boards):
            for name in os.listdir(boards):
                os.remove(os.path.join(boards, name))
        self.phase_2_ready()

    def bounded(self, proc, desk, timeout=20):
        """The hold's exit, or a failure naming what reached the desk when it
        is still running after `timeout` (today's hold raises and then waits)."""
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            heard = [r["method"] + " " + r["path"].split("?", 1)[0] for r in desk.requests]
            self.fail(f"hold was still running after {timeout}s and was killed; the desk heard "
                      f"{len(heard)} requests, {heard.count('POST /api/asks')} of them POST /api/asks, "
                      f"first {heard[:3]}; stderr: {err!r}")
        return proc.returncode, out, err

    def check_events(self):
        return [json.loads(line) for line in self.events_text().splitlines()
                if line.strip() and json.loads(line).get("event") == "check"]

    def desk_env(self, desk):
        return {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n", "PIPELINE_HOLD_INTERVAL": self.INTERVAL}

    def start_hold(self, env, *extra, n="2", cwd=None):
        environment = dict(os.environ)
        for name in (URL_VARIABLE, TOKEN_VARIABLE, WORK_ITEM_VARIABLE, "CLAUDE_CODE_SESSION_ID") + PROXY_VARIABLES + RUN_VARIABLES:
            environment.pop(name, None)
        environment.update(env)
        proc = subprocess.Popen([sys.executable, PIPELINE, "--root", self.root, "hold", "scratch", n] + list(extra),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                cwd=cwd or self.tmp.name, env=environment)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    def wait_for(self, what, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        self.fail(f"waited {timeout}s for {what}")

    def finish(self, proc, timeout=30):
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out, err

    @staticmethod
    def of(desk, method, path):
        return [r for r in desk.requests if r["method"] == method and r["path"].split("?", 1)[0] == path]

    def asking_desk(self):
        return Listener(routes={("POST", "/api/asks"): (201, {"ask": dict(self.ASK)}),
                                ("GET", "/api/asks"): (200, {"ask": dict(self.ASK)})})

    def reads_back(self, desk, count=1):
        self.wait_for(f"{count} read-backs", lambda: len(self.of(desk, "GET", "/api/asks")) >= count)

    def answered(self, answer, label, note=None):
        ask = dict(self.ASK, answer=answer, answerLabel=label, answeredAt="2026-09-28T10:00:00.000Z")
        if note is not None:
            ask["note"] = note
        return {"ask": ask}


class HoldCase(HoldHelpers, ScratchRunCase):
    """`hold` as a real process against the stub desk, with the waits shortened."""

    def test_hold_raises_one_ask_and_an_approve_with_a_note_closes_the_phase(self):
        self.phase_2_ready()
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk, 2)
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Approved", "Ship it,\nbut keep the preview."))
            code, out, err = self.finish(proc)
        self.assertEqual(code, 0, out + err)

        raised = self.of(desk, "POST", "/api/asks")
        self.assertEqual(len(raised), 1, "exactly one Ask is raised")
        self.assertEqual(raised[0]["headers"]["authorization"], "bearer t0k3n")
        self.assertEqual(raised[0]["headers"]["content-type"], "application/json")
        body = json.loads(raised[0]["raw"])
        self.assertEqual(body["kind"], "decide")
        self.assertEqual(body["run"], "scratch")
        self.assertEqual(body["fingerprint"], "hold:2:1")
        self.assertIs(body["notes"], True)
        self.assertEqual(body["moduleId"], "runs")
        self.assertTrue(isinstance(body["appId"], str) and body["appId"].strip())
        self.assertEqual([(c["id"], c["label"]) for c in body["choices"]], [
            ("approve", "Approve: B"), ("variant-1", "Go with A"),
            ("variant-3", "Go with C"), ("send-back", "Send it back")])
        # ALYDAS-303, S9. Send it back is the one choice that needs a note.
        self.assertEqual([c.get("note") for c in body["choices"]], [None, None, None, "required"])
        # S3 (carried): each choice says what taking it does, on one line.
        self.assertEqual([c["detail"] for c in body["choices"]], [
            "Build B, the option this phase recommends, and go on to the next phase.",
            "Build A instead of the recommended option, and go on to the next phase.",
            "Build C instead of the recommended option, and go on to the next phase.",
            "Reopen this phase to work on it again, with your note as its brief."])
        self.assertIn("Phase 2", body["question"])
        self.assertLessEqual(len(body["question"]), 200)
        self.assertIn(self.LEDE, body["detail"])
        self.assertIn(self.PLAIN, body["detail"])

        # Read back by id and run, with the bearer, on the raised Ask.
        read = self.of(desk, "GET", "/api/asks")[0]
        self.assertEqual(read["path"], "/api/asks?id=ask-1&run=scratch")
        self.assertEqual(read["headers"]["authorization"], "bearer t0k3n")

        # The desk heard `blocked` before the Ask was raised, and `done` at the end.
        paths = [r["path"].split("?", 1)[0] + " " + r["method"] for r in desk.requests]
        first_beat = paths.index("/api/sessions/heartbeat POST")
        self.assertLess(first_beat, paths.index("/api/asks POST"))
        beats = [json.loads(r["raw"]) for r in self.of(desk, "POST", "/api/sessions/heartbeat")]
        self.assertEqual(beats[0]["statusLine"], "Phase 2 · Prototype · blocked")
        self.assertEqual(beats[-1]["statusLine"], "Phase 2 · Prototype · done")

        self.assertIn("pipeline: answered: approve", out)
        self.assertIn("pipeline: note: Ship it, but keep the preview.", out)
        phase = self.board()["phases"][2]
        self.assertEqual(phase["status"], "done")
        self.assertEqual(phase["hold"]["attempt"], 1)
        self.assertNotIn("doing", [s["state"] for s in phase["steps"]])
        self.assertIn("Answered on the desk: Approve: B. Note: Ship it,", self.events_text())
        self.assertNotIn("t0k3n", out + err)

    def test_a_variant_answered_with_no_note_closes_the_phase_and_says_no_note(self):
        self.phase_2_ready()
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk), "--question", "Which layout do we build?")
            self.reads_back(desk)
            desk.answer("GET", "/api/asks", 200, self.answered("variant-3", "Decided"))
            code, out, err = self.finish(proc)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(self.of(desk, "POST", "/api/asks")[0]["raw"])["question"], "Which layout do we build?")
        self.assertIn("pipeline: answered: variant-3", out)
        self.assertNotIn("pipeline: note:", out)
        self.assertEqual(self.board()["phases"][2]["status"], "done")
        log = self.events_text()
        self.assertIn("Answered on the desk: Go with C.", log)
        self.assertNotIn("Go with C. Note:", log)

    def test_send_back_reopens_the_phase_and_the_next_hold_asks_afresh(self):
        """S3. A send-back answered mid-loop: printed and logged as S2 made it,
        and the phase is running again, its scene the note, its steps the usual
        ones afresh, and its attempt moved on, so the next hold is a new Ask."""
        self.phase_2_ready()
        run = self.board()
        # The steps as the agent left them, one of them ticked and one added,
        # so a reseed that kept the old list (or only reset states) shows.
        self.assertEqual(self.run_pipeline("step", "scratch", "2", "1", "done", "--sub", "chose three layouts").returncode, 0)
        # ALYDAS-225, K1. Added as the one being done: a step added to-do would refuse the hold.
        self.assertEqual(self.run_pipeline("step", "scratch", "2", "An extra step of the agent's", "doing", "--add").returncode, 0)
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            desk.answer("GET", "/api/asks", 200, self.answered("send-back", "Decided", "Show C at 390 wide."))
            code, out, err = self.finish(proc)
            self.assertEqual(code, 0, out + err)
            beats = [json.loads(r["raw"]) for r in self.of(desk, "POST", "/api/sessions/heartbeat")]
        self.assertIn("pipeline: answered: send-back", out)
        self.assertIn("pipeline: note: Show C at 390 wide.", out)
        self.assertIn("Answered on the desk: Send it back. Note: Show C at 390 wide.", self.events_text())
        phase = self.board()["phases"][2]
        self.assertEqual(phase["status"], "active")
        self.assertEqual(phase["scene"], "Sent back: Show C at 390 wide.")
        fresh = import_pipeline().PHASE_STEPS[2]
        # ALYDAS-225, K3. Afresh, and the restart marks the first of them doing.
        self.assertEqual(phase["steps"], [{"text": t, "state": "todo" if i else "doing", "sub": ""} for i, t in enumerate(fresh)])
        self.assertEqual(phase["hold"]["attempt"], 2)
        self.assertNotIn("ask", phase["hold"], "the answered Ask is not the next attempt's")
        self.assertEqual(beats[-1]["statusLine"], "Phase 2 · Prototype · running", "the desk hears it reopen")

        # The next hold asks afresh: a new fingerprint, and it waits again.
        # ALYDAS-225, K1: once the fresh steps are ticked.
        self.tick_steps(2)
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Decided"))
            code, out, err = self.finish(proc)
        self.assertEqual(code, 0, out + err)
        raised = self.of(desk, "POST", "/api/asks")
        self.assertEqual(len(raised), 1)
        self.assertEqual(json.loads(raised[0]["raw"])["fingerprint"], "hold:2:2")
        self.assertEqual(self.board()["phases"][2]["status"], "done")

    def a_desk_that_would_raise_another(self):
        """A desk whose raise hands back a different Ask, so a re-hold that
        raised (and read what it raised) shows in the read-back's id too."""
        return Listener(routes={("POST", "/api/asks"): (201, {"ask": dict(self.ASK, id="ask-2")}),
                                ("GET", "/api/asks"): (200, {"ask": dict(self.ASK)})})

    def test_a_phase_blocked_by_an_unanswered_hold_is_held_again_on_the_same_ask(self):
        """S2's follow-up, as S7 corrects it. After a hold that ended without an
        answer (here a read-back the desk refused, exit 5), `hold` again resumes
        the wait on the Ask the ledger recorded: it reads that one back and
        raises nothing, not a refusal and not a new attempt."""
        self.phase_2_ready()
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 401, {"error": "no"})
            code, out, err = self.finish(self.start_hold(self.desk_env(desk)))
        self.assertEqual(code, 5, out + err)
        self.assertEqual(self.board()["phases"][2]["status"], "blocked")
        self.assertEqual(self.board()["phases"][2]["hold"], {"attempt": 1, "ask": "ask-1"})
        blocked_before = self.events_text().count('"event": "blocked"')
        with self.a_desk_that_would_raise_another() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk, 2)
            self.assertIsNone(proc.poll(), "an open Ask is waited on")
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Decided"))
            code, out, err = self.finish(proc)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.of(desk, "POST", "/api/asks"), [], "the recorded Ask is waited on, not raised again")
        self.assertEqual({r["path"] for r in self.of(desk, "GET", "/api/asks")}, {"/api/asks?id=ask-1&run=scratch"})
        phase = self.board()["phases"][2]
        self.assertEqual(phase["status"], "done")
        self.assertEqual(phase["hold"], {"attempt": 1, "ask": "ask-1"})
        self.assertEqual(self.events_text().count('"event": "blocked"'), blocked_before,
                         "resuming the wait is not a second blocked boundary")

    def test_an_answer_given_while_no_hold_ran_is_read_back_by_the_next_hold(self):
        """S7, carried from S3. The hold died after the person answered and
        before it read the answer back. The desk dedupes only an Ask not yet
        answered (`packages/core/src/ports.ts:321-322`), so raising again would
        open a second Ask and the first answer would never be read. The next
        hold reads the recorded Ask back first and acts on its answer at once."""
        self.phase_2_ready()
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            proc.kill()
            proc.communicate(timeout=10)
        self.assertEqual(self.board()["phases"][2]["status"], "blocked")
        self.assertEqual(self.board()["phases"][2]["hold"], {"attempt": 1, "ask": "ask-1"})
        with self.a_desk_that_would_raise_another() as desk:
            # Answered on the desk while nothing was waiting.
            desk.answer("GET", "/api/asks", 200, self.answered("variant-3", "Decided", "C after all."))
            # At once: with the waits at a minute, only a read before the first
            # wait finishes inside the time allowed.
            code, out, err = self.finish(self.start_hold(dict(self.desk_env(desk), PIPELINE_HOLD_INTERVAL="60")),
                                         timeout=15)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.of(desk, "POST", "/api/asks"), [], "no second Ask is raised")
        reads = self.of(desk, "GET", "/api/asks")
        self.assertEqual([r["path"] for r in reads], ["/api/asks?id=ask-1&run=scratch"], "read once, the recorded Ask")
        self.assertIn("pipeline: answered: variant-3", out)
        self.assertIn("pipeline: note: C after all.", out)
        self.assertEqual(self.board()["phases"][2]["status"], "done")
        self.assertEqual(self.events_text().count("Answered on the desk: Go with C. Note: C after all."), 1)

    def test_a_send_back_given_while_no_hold_ran_reopens_the_phase_on_the_next_hold(self):
        """The same, for a send-back: acted on exactly as the loop would, so the
        phase runs again with the note as its scene and nothing is raised."""
        self.phase_2_ready()
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 401, {"error": "no"})
            self.assertEqual(self.finish(self.start_hold(self.desk_env(desk)))[0], 5)
        with self.a_desk_that_would_raise_another() as desk:
            desk.answer("GET", "/api/asks", 200, self.answered("send-back", "Decided", "Show C at 390 wide."))
            code, out, err = self.finish(self.start_hold(self.desk_env(desk)))
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.of(desk, "POST", "/api/asks"), [])
        phase = self.board()["phases"][2]
        self.assertEqual(phase["status"], "active")
        self.assertEqual(phase["scene"], "Sent back: Show C at 390 wide.")
        self.assertEqual(phase["hold"], {"attempt": 2})

    def test_a_recorded_ask_that_expired_is_a_stop_on_the_next_hold(self):
        self.phase_2_ready()
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 401, {"error": "no"})
            self.assertEqual(self.finish(self.start_hold(self.desk_env(desk)))[0], 5)
        with self.a_desk_that_would_raise_another() as desk:
            desk.answer("GET", "/api/asks", 200, self.answered("expired", "Expired"))
            code, out, err = self.finish(self.start_hold(self.desk_env(desk)))
        self.assertEqual(code, 3, out + err)
        self.assertEqual(self.of(desk, "POST", "/api/asks"), [])
        self.assertIn("expired", out)
        self.assertEqual(json.loads(self.of(desk, "POST", "/api/sessions/heartbeat")[-1]["raw"])["ended"], True)

    def test_a_hold_whose_raise_failed_raises_on_the_next_hold_on_the_same_attempt(self):
        """No Ask recorded (the raise itself failed, exit 4): the next hold
        raises, on the same attempt, after a beat so the desk knows the run."""
        self.phase_2_ready()
        with self.asking_desk() as desk:
            desk.answer("POST", "/api/asks", 500, {"error": "down"})
            code, out, err = self.finish(self.start_hold(self.desk_env(desk)))
        self.assertEqual(code, 4, out + err)
        self.assertEqual(self.board()["phases"][2]["status"], "blocked")
        self.assertEqual(self.board()["phases"][2]["hold"], {"attempt": 1})
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Decided"))
            code, out, err = self.finish(proc)
        self.assertEqual(code, 0, out + err)
        raised = self.of(desk, "POST", "/api/asks")
        self.assertEqual(len(raised), 1)
        self.assertEqual(json.loads(raised[0]["raw"])["fingerprint"], "hold:2:1")
        # It beat before it raised, so the desk knows the run (`asks.ts:413`).
        paths = [r["path"].split("?", 1)[0] + " " + r["method"] for r in desk.requests]
        self.assertLess(paths.index("/api/sessions/heartbeat POST"), paths.index("/api/asks POST"))
        self.assertEqual(self.board()["phases"][2]["status"], "done")

    def test_a_phase_blocked_by_hand_is_not_held(self):
        """Only a hold's own wait is resumed: `phase n blocked` typed by hand,
        with no hold behind it, is still refused."""
        self.phase_2_ready()
        self.assertEqual(self.run_pipeline("phase", "scratch", "2", "blocked", "--note", "waiting on a key").returncode, 0)
        with self.asking_desk() as desk:
            code, out, err = self.finish(self.start_hold(self.desk_env(desk)))
            self.assertEqual(code, 2, out + err)
            self.assertIn("phase 2 is not running", err)
            self.assertEqual(desk.requests, [])

    def test_a_stop_directive_while_waiting_exits_3(self):
        self.phase_2_ready()
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            desk.answer("POST", "/api/sessions/heartbeat", 200, {"session": {"id": "s1"}, "directive": {"kind": "stop"}})
            code, out, err = self.finish(proc)
        self.assertEqual(code, 3, out + err)
        self.assertIn("pipeline: the desk says stop", out)
        self.assertEqual(json.loads(self.of(desk, "POST", "/api/sessions/heartbeat")[-1]["raw"])["ended"], True)
        self.assertIn("stopped from the desk", self.events_text())
        self.assertEqual(self.board()["phases"][2]["status"], "blocked")
        # Gate 6: the ended beat is a beat too, so it carries the holds
        # (init's default, Phases 2 and 8, since ALYDAS-174, S6).
        self.assertEqual({tuple(json.loads(r["raw"])["holds"]) for r in self.of(desk, "POST", "/api/sessions/heartbeat")},
                         {(2, 8)})

    def test_a_message_while_waiting_is_printed_and_the_wait_goes_on(self):
        self.phase_2_ready()
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            beats = len(self.of(desk, "POST", "/api/sessions/heartbeat"))
            desk.answer("POST", "/api/sessions/heartbeat", 200,
                        {"session": {"id": "s1"}, "directive": {"kind": "message", "text": "Looking now."}})
            self.wait_for("a beat after the message", lambda: len(self.of(desk, "POST", "/api/sessions/heartbeat")) > beats)
            self.assertIsNone(proc.poll(), "a message does not end the wait")
            desk.answer("POST", "/api/sessions/heartbeat", 200, {"session": {"id": "s1"}})
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Decided"))
            code, out, err = self.finish(proc)
        self.assertEqual(code, 0, out + err)
        self.assertIn("pipeline: message from the desk: Looking now.", out)
        self.assertIn("pipeline: answered: approve", out)

    def test_an_expired_ask_is_a_stop(self):
        self.phase_2_ready()
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            desk.answer("GET", "/api/asks", 200, self.answered("expired", "Expired"))
            code, out, err = self.finish(proc)
        self.assertEqual(code, 3, out + err)
        self.assertIn("expired", out)
        self.assertIn("pipeline: the desk says stop", out)
        self.assertEqual(json.loads(self.of(desk, "POST", "/api/sessions/heartbeat")[-1]["raw"])["ended"], True)

    def test_a_failed_read_back_costs_a_line_and_the_wait_goes_on(self):
        # Gate 6: 500 itself, the edge of "a server error is retried".
        for status in (500, 503, 429):
            with self.subTest(status=status):
                self.phase_2_ready_again()
                with self.asking_desk() as desk:
                    desk.answer("GET", "/api/asks", status, {"error": "busy"})
                    proc = self.start_hold(self.desk_env(desk))
                    self.reads_back(desk, 2)
                    desk.answer("GET", "/api/asks", 200, self.answered("approve", "Decided"))
                    code, out, err = self.finish(proc)
                self.assertEqual(code, 0, out + err)
                self.assertIn(f"HTTP Error {status}", err)
                self.assertEqual(self.board()["phases"][2]["status"], "done")

    def test_a_read_back_the_desk_refuses_exits_5(self):
        for status in (401, 403, 404):
            with self.subTest(status=status):
                self.phase_2_ready_again()
                with self.asking_desk() as desk:
                    desk.answer("GET", "/api/asks", status, {"error": "That Ask is not here."})
                    proc = self.start_hold(self.desk_env(desk))
                    code, out, err = self.finish(proc)
                self.assertEqual(code, 5, out + err)
                self.assertIn("could not read the answer back", err)
                self.assertEqual(len(self.of(desk, "GET", "/api/asks")), 1, "a refusal is not retried")
                self.assertEqual(self.board()["phases"][2]["status"], "blocked")

    def ledger_bytes(self):
        boards = os.path.join(self.root, "boards")
        found = {}
        for name in sorted(os.listdir(boards)):
            with open(os.path.join(boards, name), "rb") as f:
                found[name] = f.read()
        return found

    def test_no_desk_exits_4_at_once_and_leaves_the_ledger_alone(self):
        self.phase_2_ready()
        before = self.ledger_bytes()
        with Listener() as desk:
            for name, env in {"both unset": {}, "url only": {URL_VARIABLE: desk.url},
                              "token only": {TOKEN_VARIABLE: "t0k3n"},
                              "both empty": {URL_VARIABLE: " ", TOKEN_VARIABLE: ""}}.items():
                with self.subTest(name):
                    code, out, err = self.finish(self.start_hold(dict(env, PIPELINE_HOLD_INTERVAL=self.INTERVAL)))
                    self.assertEqual(code, 4, out + err)
                    self.assertIn("ask in chat", out + err)
                    self.assertEqual(len((out + err).strip().splitlines()), 1, "one line")
                    self.assertEqual(self.ledger_bytes(), before)
            self.assertEqual(desk.requests, [])

    def test_a_phase_that_cannot_close_is_not_held(self):
        """No plain line: exit 2 with `phase done`'s own words, nothing sent."""
        self.phase_2_ready(plain=False)
        with self.asking_desk() as desk:
            code, out, err = self.finish(self.start_hold(self.desk_env(desk)))
            self.assertEqual(code, 2, out + err)
            self.assertIn("phase 2 cannot close: no plain line", err)
            # ALYDAS-225, K1. For that alone: the helper ticked its steps.
            self.assertNotIn("is still to do", err)
            self.assertEqual(desk.requests, [], "nothing raised, nothing beaten")
        self.assertEqual(self.board()["phases"][2]["status"], "active")

    def test_hold_refuses_a_step_still_todo_and_force_does_not_pass_it(self):
        """ALYDAS-225, K1. A hold runs the same checks, so a step still to-do
        refuses it too: exit 2, the step named with both ways out, nothing sent
        and the phase still running. `hold --force` goes past the desk-can-open
        check only, so it is refused the same, and what a hold prints does not
        offer `--force` as a way past."""
        self.phase_2_ready()
        text = pipeline_module().PHASE_STEPS[2][2]
        self.assertEqual(self.run_pipeline("step", "scratch", "2", "3", "todo").returncode, 0)
        before = self.board()["phases"][2]
        for extra in ((), ("--force", "Sachit ruled in chat")):
            with self.subTest(extra=extra), self.asking_desk() as desk:
                code, out, err = self.bounded(self.start_hold(self.desk_env(desk), *extra), desk)
                self.assertEqual(code, 2, out + err)
                self.assertIn(f'phase 2 cannot close: step 3 is still to do ("{text}")', err)
                self.assertIn("step scratch 2 3 done", err)
                self.assertIn('step scratch 2 3 dropped --sub "<why>"', err)
                self.assertEqual(err.count("is still to do"), 1, err)
                self.assertNotIn("--force", err)
                self.assertEqual(desk.requests, [], "nothing raised, nothing beaten")
                self.assertEqual(self.board()["phases"][2], before)
        # Ticked, the same hold gets as far as asking.
        self.assertEqual(self.run_pipeline("step", "scratch", "2", "3", "done").returncode, 0)
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Decided"))
            code, out, err = self.finish(proc)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.board()["phases"][2]["status"], "done")

    def test_a_hold_finishes_the_step_in_hand_when_it_starts_to_wait(self):
        """Gate 6. K1 leans on this: a step being done does not refuse a hold
        because the hold finishes it, as a close does. So while the phase waits
        on a person, blocked, no step is shown as still being done."""
        self.phase_2_ready()
        self.assertEqual([s["state"] for s in self.board()["phases"][2]["steps"]].count("doing"), 1)
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            waiting = self.board()["phases"][2]
            self.assertEqual(waiting["status"], "blocked")
            self.assertEqual({s["state"] for s in waiting["steps"]}, {"done"})
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Decided"))
            code, out, err = self.finish(proc)
        self.assertEqual(code, 0, out + err)

    def test_a_phase_waiting_on_a_person_takes_no_step_that_would_be_left_to_do(self):
        """Gate 8, 2. A hold checks the steps before it asks, and the answer
        closes the phase without checking again. So while a hold waits, `step`
        refuses a call that would leave a step to-do (`--add` with no state or
        with `todo`, and `todo` on a step there), exit 1, nothing written,
        saying who the phase waits on and the ways on. A step added done or
        dropped, and a line alone, are taken. Approve then closes a phase with
        no step to-do. A phase blocked by hand is not held to it."""
        self.phase_2_ready()
        step_events = lambda: [line for line in self.events_text().splitlines() if '"event": "step"' in line]
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            self.assertEqual(self.board()["phases"][2]["status"], "blocked")
            add = ('step scratch 2 "<text>" done --add', 'step scratch 2 "<text>" dropped --sub "<why>" --add')
            for args, fixes in ((("A late step", "--add"), add), (("A late step", "todo", "--add"), add),
                                (("1", "todo"), ("leave step 1 as it is",)),
                                (("1", "todo", "--sub", "Not looked at after all."), ("leave step 1 as it is",))):
                before, logged = self.board()["phases"][2], step_events()
                out = self.run_pipeline("step", "scratch", "2", *args)
                self.assertEqual(out.returncode, 1, f"{args}: {out.stdout}{out.stderr}")
                self.assertIn("phase 2 is waiting on a person's answer on the desk", out.stderr)
                self.assertIn("the answer would close the phase over it", out.stderr)
                for fix in fixes + ("or wait for the answer",):
                    self.assertIn(fix, out.stderr, args)
                self.assertEqual(self.board()["phases"][2], before, f"a refused step wrote the ledger: {args}")
                self.assertEqual(step_events(), logged, args)
            # What leaves nothing to do is taken while it waits.
            for args in (("Checked on a phone", "done", "--add"),
                         ("Try a fourth layout", "dropped", "--sub", "Three were enough.", "--add"),
                         ("1", "--sub", "Chose the run page."), ("2", "done")):
                out = self.run_pipeline("step", "scratch", "2", *args)
                self.assertEqual(out.returncode, 0, f"{args}: {out.stdout}{out.stderr}")
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Decided"))
            code, out, err = self.finish(proc)
        self.assertEqual(code, 0, out + err)
        closed = self.board()["phases"][2]
        self.assertEqual(closed["status"], "done")
        usual = len(pipeline_module().PHASE_STEPS[2])
        self.assertEqual([s["state"] for s in closed["steps"]], ["done"] * usual + ["done", "dropped"])
        # Blocked by hand, with no hold behind it: nobody's answer will close it, so a step may wait there.
        self.assertEqual(self.run_pipeline("set", "scratch", "holds", "none").returncode, 0)
        self.assertEqual(self.run_pipeline("phase", "scratch", "3", "start").returncode, 0)
        self.assertEqual(self.run_pipeline("phase", "scratch", "3", "blocked", "--note", "a scratch run").returncode, 0)
        for args in (("One for later", "--add"), ("2", "todo")):
            out = self.run_pipeline("step", "scratch", "3", *args)
            self.assertEqual(out.returncode, 0, f"{args}: {out.stdout}{out.stderr}")
        self.assertIn("todo", [s["state"] for s in self.board()["phases"][3]["steps"]])

    def test_only_the_running_phase_is_held(self):
        self.phase_2_ready()
        with self.asking_desk() as desk:
            code, out, err = self.finish(self.start_hold(self.desk_env(desk), n="3"))
            self.assertEqual(code, 2, out + err)
            self.assertIn("phase 3 is not running", err)
            self.assertEqual(desk.requests, [])


class HoldPushedCase(HoldHelpers, ScratchRunCase):
    """ALYDAS-206. Before hold raises its question it checks that the desk can
    open every file the phase recorded, at the commit the desk will read.
    S1: a push that did not land."""

    BRANCH = "feature/alydas-135-s6"

    def refuse_pushes(self):
        """Origin refuses every push, through a pre-receive hook on the bare repository."""
        hook = os.path.join(self.origin, "hooks", "pre-receive")
        with open(hook, "w") as f:
            f.write("#!/bin/sh\necho 'scratch origin: this origin refuses every push' >&2\nexit 1\n")
        os.chmod(hook, 0o755)

    def assert_refused_for_the_push(self, desk, code, out, err):
        self.assertEqual(self.of(desk, "POST", "/api/asks"), [], "an Ask was raised for a file the desk cannot open")
        self.assertEqual(code, 2, out + err)
        self.assertIn("docs/prototypes/x.html", err)
        self.assertIn("not pushed", err)
        self.assertIn(self.BRANCH, err)
        checks = [e for e in self.check_events() if e.get("phase") == 2]
        self.assertEqual(len(checks), 1, f"one check event on phase 2: {checks}")
        for said in ("docs/prototypes/x.html", "not pushed", self.BRANCH):
            self.assertIn(said, checks[0]["note"])
        # S3. The fix for "not pushed" is the push.
        self.assertIn("push the branch so origin has this commit, then run hold scratch 2 again; "
                      "or pass --force \"reason\" to hold anyway (recorded on the board).", err)

    def test_hold_raises_nothing_when_the_push_did_not_land(self):
        self.phase_2_ready()
        self.refuse_pushes()
        self.assertNotIn("hold", self.board()["phases"][2])
        with self.asking_desk() as desk:
            code, out, err = self.bounded(self.start_hold(self.desk_env(desk)), desk)
        self.assert_refused_for_the_push(desk, code, out, err)
        phase = self.board()["phases"][2]
        self.assertEqual(phase["status"], "active")
        self.assertNotIn("hold", phase, "the refused hold saved a hold on the ledger")

    def test_a_resumed_hold_on_a_refused_push_stays_blocked_and_raises_nothing(self):
        """Blocked by an earlier hold that recorded no Ask (a crash before the
        desk took it): the resumed hold checks too, and leaves it blocked."""
        self.phase_2_ready()
        self.refuse_pushes()
        run = self.board()
        run["phases"][2]["status"] = "blocked"
        run["phases"][2]["hold"] = {"attempt": 1}
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.json"), "w") as f:
            json.dump(run, f)
        with self.asking_desk() as desk:
            code, out, err = self.bounded(self.start_hold(self.desk_env(desk)), desk)
        self.assert_refused_for_the_push(desk, code, out, err)
        phase = self.board()["phases"][2]
        self.assertEqual(phase["status"], "blocked")
        self.assertEqual(phase["hold"], {"attempt": 1})

    FORCE = "Sachit ruled in chat: hold on the local page"

    def test_hold_force_goes_past_the_check_in_amber(self):
        """S3. `hold --force` goes past the desk-can-open check only: it still
        pushes first, logs the files it went past with their reasons, raises the
        Ask as today, and records the reason where the board draws it in amber:
        the detail note and the log line (the tab and chip follow status only)."""
        self.phase_2_ready()
        self.refuse_pushes()
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk), "--force", self.FORCE)
            self.reads_back(desk)
            self.assertEqual(self.board()["phases"][2]["status"], "blocked")
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Approved"))
            code, out, err = self.finish(proc)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.of(desk, "POST", "/api/asks")), 1, "exactly one Ask is raised")
        self.assertIn(f"{self.BRANCH} was not pushed", err, "hold still pushes first")
        self.assertIn("docs/prototypes/x.html: not pushed", err, "stderr names what the force went past")

        phase = self.board()["phases"][2]
        self.assertEqual(phase["status"], "done")
        self.assertEqual(phase["forced"], self.FORCE)
        self.assertEqual(phase["hold"]["forced"], self.FORCE)
        events = [json.loads(line) for line in self.events_text().splitlines() if line.strip()]
        blocked = [e for e in events if e.get("phase") == 2 and e["event"] == "blocked"]
        self.assertEqual([e.get("forced") for e in blocked], [self.FORCE], "the blocked stamp carries the force")
        went_past = [e for e in self.check_events() if e.get("phase") == 2]
        self.assertEqual(len(went_past), 1, f"one line for what was gone past: {went_past}")
        self.assertEqual(went_past[0].get("forced"), self.FORCE)
        for said in ("docs/prototypes/x.html", "not pushed", self.BRANCH):
            self.assertIn(said, went_past[0]["note"])

        # The board as its own script draws it.
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.html"), encoding="utf-8") as f:
            drawn = render_board_in_node(self, f.read())
        panel = parse_html(drawn["panels"]["p2"])
        amber = [n.text() for n in panel.by_class("note") if "bad" not in (n.attrs.get("class") or "").split()]
        self.assertIn(f"Closed by force. {self.FORCE}", amber)
        rows = []
        for tr in parse_html(drawn["log"]).find("tr"):
            cells = tr.children_named("td")
            pills = tr.by_class("pill")
            if len(cells) == 4 and pills:
                rows.append((cells[1].text(), pills[0].text(), (pills[0].attrs.get("class") or "").split(),
                             cells[3].text()))
        blocked_rows = [r for r in rows if r[0] == "2" and r[1] == "phase blocked"]
        self.assertEqual(len(blocked_rows), 1, rows)
        self.assertIn("warn", blocked_rows[0][2], "the blocked line is amber")
        self.assertIn(f"(forced: {self.FORCE})", blocked_rows[0][3])
        check_rows = [r for r in rows if r[0] == "2" and r[1] == "check failed"]
        self.assertEqual(len(check_rows), 1, rows)
        self.assertIn("warn", check_rows[0][2], "the went-past line is amber, not red")

    def test_force_does_not_go_past_a_missing_plain_line(self):
        """S3. The artifact checks still refuse with `--force`: exit 2, nothing raised."""
        self.phase_2_ready(plain=False)
        self.refuse_pushes()
        with self.asking_desk() as desk:
            code, out, err = self.bounded(self.start_hold(self.desk_env(desk), "--force", self.FORCE), desk)
            self.assertEqual(desk.requests, [], "nothing raised, nothing beaten")
        self.assertEqual(code, 2, out + err)
        self.assertIn("phase 2 cannot close: no plain line", err)
        phase = self.board()["phases"][2]
        self.assertEqual(phase["status"], "active")
        self.assertFalse(phase.get("forced"), "a refused hold records no force")
        self.assertNotIn("hold", phase)


class DeskCannotOpenTable(unittest.TestCase):
    """ALYDAS-206, S2. The pure check over the repository's facts: every reason
    the design lists, in its order, and the rows that open."""

    SHA = "abc1234" + "0" * 33
    FILE = "docs/prototypes/x.html"

    def facts(self, present=(), trees=(), **given):
        P = pipeline_module()
        sha = self.SHA
        # A tree lookup answers only for the commit the desk reads, so a check
        # that asked about another commit (HEAD when origin lacks it) opens nothing.
        # ALYDAS-212, S3. `present` are files and `trees` folders, as
        # ALYDAS-218, S3's `entry` names them.
        def entry(commit, path):
            if commit != sha:
                return None
            return ("file", None) if path in present else ("dir", None) if path in trees else None
        # ALYDAS-212, S4. The commit's files are the files in `present`.
        def files(commit):
            return list(present) if commit == sha else []
        return P.RepoFacts(entry=entry, files=files, **given)

    def test_each_reason_and_each_row_that_opens(self):
        P = pipeline_module()
        sha, f = self.SHA, self.FILE
        unfetched = "origin does not have abc1234 as far as this checkout last fetched"
        rows = [
            ("no files recorded", [], self.facts(), []),
            ("outside any repository", [f], self.facts(), [(f, "not in a git repository")]),
            ("no origin", [f], self.facts(head=sha, branch="feature/x"),
             [(f, "the repository has no origin")]),
            ("detached, not on origin", [f], self.facts(head=sha, has_origin=True),
             [(f, "HEAD is detached, so no branch was pushed")]),
            ("detached, on origin", [f], self.facts((f,), head=sha, has_origin=True, on_github=sha), []),
            ("main, not on origin", [f], self.facts(head=sha, has_origin=True, branch="main"),
             [(f, f"on main, which the pipeline never pushes, and {unfetched}")]),
            ("master, not on origin", [f], self.facts(head=sha, has_origin=True, branch="master"),
             [(f, f"on master, which the pipeline never pushes, and {unfetched}")]),
            ("origin's default, not on origin", [f],
             self.facts(head=sha, has_origin=True, branch="trunk", default="trunk"),
             [(f, f"on trunk, which the pipeline never pushes, and {unfetched}")]),
            ("main, already on origin", [f],
             self.facts((f,), head=sha, has_origin=True, branch="main", on_github=sha), []),
            ("feature branch, not pushed", [f], self.facts(head=sha, has_origin=True, branch="feature/x"),
             [(f, "not pushed: feature/x is not on origin at abc1234")]),
            ("feature branch, pushed, file present", [f, "docs/..x.html"],
             self.facts((f, "docs/..x.html"), head=sha, has_origin=True, branch="feature/x", on_github=sha), []),
            ("feature branch, pushed, file not committed", [f],
             self.facts(head=sha, has_origin=True, branch="feature/x", on_github=sha),
             [(f, f"not committed: {f} is not in abc1234")]),
            ("mixed: one opens, one not committed", [f, "docs/y.html"],
             self.facts((f,), head=sha, has_origin=True, branch="feature/x", on_github=sha),
             [("docs/y.html", "not committed: docs/y.html is not in abc1234")]),
            ("paths outside, tested first", ["../e.html", "/abs/e.html", "docs/../../e.html", f], self.facts(),
             [("../e.html", "not a path inside the repository"),
              ("/abs/e.html", "not a path inside the repository"),
              ("docs/../../e.html", "not a path inside the repository"),
              (f, "not in a git repository")]),
            ("a path outside beside one that opens", [f, "../e.html"],
             self.facts((f, "../e.html"), head=sha, has_origin=True, branch="feature/x", on_github=sha),
             [("../e.html", "not a path inside the repository")]),
            # ALYDAS-212, S1. Looked up as the URL sends it; the reason names it as recorded.
            ("a dot segment, looked up as the URL sends it", ["docs/./prototypes/x.html", "docs/./y.html"],
             self.facts((f,), head=sha, has_origin=True, branch="feature/x", on_github=sha),
             [("docs/./y.html", "not committed: docs/./y.html is not in abc1234")]),
            # ALYDAS-212, S2. The desk's rule judges the path before anything
            # about the repository: a drive letter is outside, a backslash and
            # a NUL are refused by name. A backslash path whose `/` spelling is
            # committed says so, which `fixes_for` turns into "record it as".
            ("the desk's rule, before the repository", ["C:docs/prototypes/x.html", "docs\\x.html", "docs/x\0.html", f],
             self.facts(),
             [("C:docs/prototypes/x.html", "not a path inside the repository"),
              ("docs\\x.html", "a backslash, which the desk refuses"),
              ("docs/x\0.html", "a NUL, which the desk refuses"),
              (f, "not in a git repository")]),
            ("a backslash, its / spelling committed", ["docs\\prototypes\\x.html", "docs\\y.html"],
             self.facts((f,), head=sha, has_origin=True, branch="feature/x", on_github=sha),
             [("docs\\prototypes\\x.html", f"a backslash, which the desk refuses; {f} is in abc1234"),
              ("docs\\y.html", "a backslash, which the desk refuses")]),
            ("a backslash, its / spelling committed but the desk has no commit", ["docs\\prototypes\\x.html"],
             self.facts((f,), head=sha, has_origin=True, branch="feature/x"),
             [("docs\\prototypes\\x.html", "a backslash, which the desk refuses")]),
            ("a backslash with a dot segment, looked up as the URL sends it", ["docs\\.\\prototypes\\x.html"],
             self.facts((f,), head=sha, has_origin=True, branch="feature/x", on_github=sha),
             [("docs\\.\\prototypes\\x.html", "a backslash, which the desk refuses; docs/./prototypes/x.html is in abc1234")]),
            ("a NUL even where the file is committed", ["docs/prototypes/x.html\0"],
             self.facts((f, "docs/prototypes/x.html\0"), head=sha, has_origin=True, branch="feature/x", on_github=sha),
             [("docs/prototypes/x.html\0", "a NUL, which the desk refuses")]),
            # ALYDAS-212, S3. Only a file opens: the desk would show a folder's
            # listing as the file. Looked up as the URL sends it, named as recorded.
            ("a folder, by itself and with a trailing dot", ["docs/prototypes", "docs/prototypes/.", f],
             self.facts((f,), ("docs/prototypes", "docs/prototypes/"),
                        head=sha, has_origin=True, branch="feature/x", on_github=sha),
             [("docs/prototypes", "docs/prototypes is a directory in abc1234, not a file"),
              ("docs/prototypes/.", "docs/prototypes/. is a directory in abc1234, not a file")]),
            ("a backslash whose / spelling is a folder", ["docs\\prototypes"],
             self.facts((f,), ("docs/prototypes",), head=sha, has_origin=True, branch="feature/x", on_github=sha),
             [("docs\\prototypes", "a backslash, which the desk refuses")]),
            # ALYDAS-212, S4. A miss is explained from the commit's files; the
            # reason names the path as recorded and the spelling the commit has.
            ("a miss, explained from the commit's files", ["docs//prototypes/x.html", "docs/./Prototypes/x.html", f],
             self.facts((f,), head=sha, has_origin=True, branch="feature/x", on_github=sha),
             [("docs//prototypes/x.html", f"a doubled /, which GitHub does not find; {f} is in abc1234"),
              ("docs/./Prototypes/x.html", f"in other capital letters, which GitHub does not find; {f} is in abc1234")]),
        ]
        for name, paths, facts, want in rows:
            with self.subTest(name):
                self.assertEqual(P.desk_cannot_open(paths, facts), want)

    def test_each_distinct_reason_gets_its_own_fix_once_in_order(self):
        """S3. The refusal's closing line: one fix per distinct kind of reason,
        in the order the reasons came, and the push only where it is the fix.
        ALYDAS-220, S1: each reason is built by `desk_cannot_open`, as hold
        builds it, so `fixes_for` reads its case and spelling, never its text."""
        P = pipeline_module()
        sha, f = self.SHA, self.FILE
        push = "push the branch so origin has this commit"
        branch = "commit it on a feature branch, which hold pushes (the pipeline never pushes main, origin's default or a detached HEAD)"
        pushed = dict(head=sha, has_origin=True, branch="feature/x", on_github=sha)

        def told(path, facts):
            (pair,) = P.desk_cannot_open([path], facts)
            self.assertIsInstance(pair[1], P.Reason)
            return pair
        rows = [
            ("not pushed", [told("a", self.facts(head=sha, has_origin=True, branch="f"))], [push]),
            ("main", [told("a", self.facts(head=sha, has_origin=True, branch="main"))], [branch]),
            ("detached", [told("a", self.facts(head=sha, has_origin=True))], [branch]),
            ("not committed, twice", [told("a", self.facts(**pushed)), told("b", self.facts(**pushed))],
             ["commit the file (hold pushes it)"]),
            ("no origin", [told("a", self.facts(head=sha, branch="feature/x"))],
             ["add the repository's GitHub remote as origin"]),
            ("no repository", [told("a", self.facts())], ["change to a directory inside the repository"]),
            ("outside, then not committed", [told("../a", self.facts(**pushed)), told("b", self.facts(**pushed))],
             ["record the file by its path from the repository root", "commit the file (hold pushes it)"]),
            # ALYDAS-212, S2.
            ("a backslash, its / spelling committed", [told("docs\\prototypes\\x.html", self.facts((f,), **pushed))],
             ["record it as docs/prototypes/x.html"]),
            ("a backslash, nothing committed under its / spelling", [told("docs\\y.html", self.facts(**pushed))],
             ["record the path with / between folders, never \\"]),
            ("a NUL", [told("docs/x\0.html", self.facts(**pushed))], ["record the path without the NUL"]),
            # ALYDAS-212, S3.
            ("a folder, twice", [told("docs", self.facts(trees=("docs",), **pushed)),
                                 told("docs/a", self.facts(trees=("docs/a",), **pushed))],
             ["record a file inside it"]),
            # ALYDAS-212, S4. One spelling named: record it as that. Several: the generic fix.
            ("a doubled /", [told("docs//x.html", self.facts(("docs/x.html",), **pushed))],
             ["record it as docs/x.html"]),
            ("a trailing /", [told("docs/x.html/", self.facts(("docs/x.html",), **pushed))],
             ["record it as docs/x.html"]),
            ("other capital letters", [told("Docs/x.html", self.facts(("docs/x.html",), **pushed))],
             ["record it as docs/x.html"]),
            ("other capital letters, two files", [told("X.html", self.facts(("x.html", "x.HTML"), **pushed))],
             ["record it in the capital letters the commit has"]),
            ("from a subfolder", [told("x.html", self.facts(("docs/x.html",), **pushed))],
             ["record it as docs/x.html"]),
            ("from a subfolder, two files", [told("x.html", self.facts(("a/x.html", "b/x.html"), **pushed))],
             ["record the file by its path from the repository root"]),
            ("two shapes, two spellings, then a real miss",
             [told("docs//x.html", self.facts(("docs/x.html",), **pushed)),
              told("y.html", self.facts(("docs/y.html",), **pushed)),
              told("z.html", self.facts(**pushed))],
             ["record it as docs/x.html", "record it as docs/y.html", "commit the file (hold pushes it)"]),
            # ALYDAS-220, S1. A path whose words are another reason's: the fix is its own case's.
            ("not committed, its name another reason's words",
             [told("docs/a, not a file.html", self.facts(**pushed)),
              told("docs/in other capital letters, which GitHub does not find; docs/y.html is in abc1234",
                   self.facts(**pushed)),
              told("a, which the pipeline never pushes.html", self.facts(**pushed))],
             ["commit the file (hold pushes it)"]),
        ]
        for name, bad, want in rows:
            with self.subTest(name):
                self.assertEqual(P.fixes_for(bad), want)


class ExplainMissTable(unittest.TestCase):
    """ALYDAS-212, S4. `explain_miss` names why a path the commit has no file at
    missed, from the commit's list of files. The checks run in the design's
    order and the first that finds a file wins. Checks after the slash one look
    at the path with its empty segments dropped, so the spelling a reason names
    mends every shape the path has at once."""

    SHA = "abc1234" + "0" * 33
    FILES = ["first.txt", "docs/prototypes/x.html", "docs/prototypes/ax.html"]

    def test_each_case_of_a_miss(self):
        P = pipeline_module()
        f = "docs/prototypes/x.html"
        doubled = "a doubled /, which GitHub does not find; "
        trailing = "a trailing /, which GitHub does not find; "
        cased = "in other capital letters, which GitHub does not find; "
        under = "written from a subfolder, not the repository root; "
        many = ["a/x.html", "b/x.html", "c/x.html", "d/x.html", "e.html"]
        rows = [
            ("a doubled /", "docs//prototypes/x.html", self.FILES, f"{doubled}{f} is in abc1234"),
            ("two doubled /", "docs///prototypes//x.html", self.FILES, f"{doubled}{f} is in abc1234"),
            ("a trailing /", "docs/prototypes/x.html/", self.FILES, f"{trailing}{f} is in abc1234"),
            ("a doubled and a trailing /: the doubled is named", "docs//prototypes/x.html/", self.FILES,
             f"{doubled}{f} is in abc1234"),
            ("other capital letters", "Docs/prototypes/x.html", self.FILES, f"{cased}{f} is in abc1234"),
            # ALYDAS-219, S4. Both shapes named, and the one spelling mends both.
            ("other capital letters and a doubled /: both named, its spelling mends both",
             "Docs//prototypes/x.html", self.FILES, f"a doubled / and in other capital letters; {f} is in abc1234"),
            ("other capital letters, two files", "X.html", ["x.html", "x.HTML"],
             f"{cased}x.html and x.HTML are in abc1234"),
            ("from a subfolder", "prototypes/x.html", self.FILES, f"{under}{f} is in abc1234"),
            ("from a subfolder, with a doubled /: both named", "prototypes//x.html", self.FILES,
             f"a doubled / and written from a subfolder; {f} is in abc1234"),
            ("from a subfolder, two files", "x.html", ["a/x.html", "b/x.html"],
             f"{under}a/x.html and b/x.html are in abc1234"),
            ("from a subfolder, three files", "x.html", many[:3],
             f"{under}a/x.html, b/x.html and c/x.html are in abc1234"),
            ("from a subfolder, four files: three named", "x.html", many,
             f"{under}a/x.html, b/x.html, c/x.html and 1 more are in abc1234"),
            ("a name that only ends the same is not from a subfolder", "x.html", ["docs/ax.html"],
             "not committed: x.html is not in abc1234"),
            ("a real miss", "docs/prototypes/nope.html", self.FILES,
             "not committed: docs/prototypes/nope.html is not in abc1234"),
            ("a real miss with a doubled /", "docs//prototypes/nope.html", self.FILES,
             "not committed: docs//prototypes/nope.html is not in abc1234"),
            ("no files at all", f, [], f"not committed: {f} is not in abc1234"),
            ("a name with a quote, a space and CJK, in other capital letters", 'Docs/\u65e5\u672c "x".html',
             ['docs/\u65e5\u672c "x".html'], f'{cased}docs/\u65e5\u672c "x".html is in abc1234'),
        ]
        for name, path, files, want in rows:
            with self.subTest(name):
                self.assertEqual(P.explain_miss(path, P.as_the_url_sends_it(path), self.SHA, files), want)

    def test_the_reason_names_the_path_as_recorded_and_looks_up_the_path_as_sent(self):
        """A dot segment is dropped before the lookup, as the URL does; a real miss still names the path as typed."""
        P = pipeline_module()
        self.assertEqual(P.explain_miss("docs/./Prototypes/x.html", "docs/Prototypes/x.html", self.SHA, self.FILES),
                         "in other capital letters, which GitHub does not find; docs/prototypes/x.html is in abc1234")
        self.assertEqual(P.explain_miss("docs/./nope.html", "docs/nope.html", self.SHA, self.FILES),
                         "not committed: docs/./nope.html is not in abc1234")

    # ALYDAS-219, S4 (c1, c2). Every shape at once: empty segments dropped,
    # capital letters, Unicode spelling and a subfolder, each one needed named.
    CAFE, CAFE_NFD = "docs/caf\u00e9.html", "docs/cafe\u0301.html"
    UNICODE = "in other Unicode spelling (its accents are stored as different bytes)"

    def test_every_shape_at_once(self):
        P = pipeline_module()
        f = "docs/prototypes/x.html"
        files = self.FILES + [self.CAFE]
        folder = "docs/prototypes is a directory in abc1234, not a file"
        rows = [
            ("capital letters from a subfolder, in the folder", "Prototypes/x.html", files,
             f"in other capital letters and written from a subfolder; {f} is in abc1234"),
            ("capital letters from a subfolder, in the file", "prototypes/X.html", files,
             f"in other capital letters and written from a subfolder; {f} is in abc1234"),
            ("a doubled /, capital letters and a subfolder", "Prototypes//x.html", files,
             f"a doubled / and in other capital letters and written from a subfolder; {f} is in abc1234"),
            ("a trailing / and capital letters", "Docs/prototypes/x.html/", files,
             f"a trailing / and in other capital letters; {f} is in abc1234"),
            ("Unicode spelling", self.CAFE_NFD, files, f"{self.UNICODE}, which GitHub does not find; "
                                                       f"{self.CAFE} is in abc1234"),
            ("Unicode spelling from a subfolder", "cafe\u0301.html", files,
             f"{self.UNICODE} and written from a subfolder; {self.CAFE} is in abc1234"),
            ("Unicode spelling and capital letters", "Docs/CAFE\u0301.html", files,
             f"in other capital letters and {self.UNICODE}; {self.CAFE} is in abc1234"),
            ("NFC sent, NFD committed", self.CAFE, ["docs/cafe\u0301.html"],
             f"{self.UNICODE}, which GitHub does not find; docs/cafe\u0301.html is in abc1234"),
            ("the whole path wins over a subfolder", "Docs/prototypes/x.html", files + ["a/docs/prototypes/x.html"],
             f"in other capital letters, which GitHub does not find; {f} is in abc1234"),
            ("the whole path wins over a subfolder listed first", "Docs/prototypes/x.html",
             ["a/docs/prototypes/x.html"] + files, f"in other capital letters, which GitHub does not find; {f} is in abc1234"),
            ("two files, both shapes: no spelling", "X.html", ["a/x.html", "b/x.html"],
             "in other capital letters and written from a subfolder; a/x.html and b/x.html are in abc1234"),
            ("a folder in other capital letters", "Docs/prototypes", files, f"in other capital letters; {folder}"),
            ("a folder with a doubled /", "docs//prototypes", files, f"a doubled /; {folder}"),
            ("a folder with a doubled / and a trailing /: the trailing is the folder's own", "docs//prototypes/",
             files, f"a doubled /; {folder}"),
            ("a folder from a subfolder", "prototypes", files, f"written from a subfolder; {folder}"),
            ("a folder, a doubled / and capital letters", "Docs//Prototypes", files,
             f"a doubled / and in other capital letters; {folder}"),
            ("two folders", "z", ["a/z/1.html", "b/z/2.html"],
             "written from a subfolder; a/z and b/z are directories in abc1234, not files"),
            ("a file wins over a folder", "X", ["x", "a/X/1.html"],
             "in other capital letters, which GitHub does not find; x is in abc1234"),
            ("a name that is not UTF-8 is never named", "x.html", ["bad-\udcff/x.html"],
             "not committed: x.html is not in abc1234"),
            ("a name that is not UTF-8 leaves the others", "Docs/prototypes/x.html", ["docs/bad-\udcff.html", f],
             f"in other capital letters, which GitHub does not find; {f} is in abc1234"),
            # Gate 6. The whole path wins though it needs more shapes than a
            # subfolder fit; and a letter whose casefold is not NFC (Greek
            # upsilon with dialytika and tonos) still fits once renormalised.
            ("the whole path wins over a subfolder needing fewer shapes", "Docs/CAFE\u0301.html",
             [self.CAFE, "a/Docs/CAFE\u0301.html"], f"in other capital letters and {self.UNICODE}; {self.CAFE} is in abc1234"),
            ("tied fits needing different shapes name both", "Cafe\u0301.html", ["cafe\u0301.html", "Caf\u00e9.html"],
             f"in other capital letters and {self.UNICODE}; cafe\u0301.html and Caf\u00e9.html are in abc1234"),
            ("a casefold that is not NFC still fits", "docs/\u03a5\u0308\u0301.html", ["docs/\u03b0.html"],
             "in other capital letters, which GitHub does not find; docs/\u03b0.html is in abc1234"),
        ]
        for name, path, files_, want in rows:
            with self.subTest(name):
                self.assertEqual(P.explain_miss(path, P.as_the_url_sends_it(path), self.SHA, files_), want)

    def test_each_combination_s_fix(self):
        """One spelling: the fix records it. Several: the generic fix. A folder: the directory's fix."""
        P = pipeline_module()
        files = self.FILES + [self.CAFE]
        rows = [
            ("Prototypes/x.html", files, "record it as docs/prototypes/x.html"),
            ("cafe\u0301.html", files, f"record it as {self.CAFE}"),
            (self.CAFE_NFD, files, f"record it as {self.CAFE}"),
            ("X.html", ["a/x.html", "b/x.html"], "record it as the commit spells it, from the repository root"),
            # Gate 6. Unicode alone, two committed spellings: its own fix, no spelling.
            ("docs/\u00e9e\u0301.html", ["docs/\u00e9\u00e9.html", "docs/e\u0301e\u0301.html"],
             "record it in the Unicode spelling the commit has"),
            ("Docs/prototypes", files, "record a file inside it"),
            ("docs//prototypes", files, "record a file inside it"),
        ]
        for path, files_, fix in rows:
            with self.subTest(path):
                why = P.explain_miss(path, P.as_the_url_sends_it(path), self.SHA, files_)
                self.assertEqual(P.fixes_for([(path, why)]), [fix])

    def test_a_file_in_the_working_tree_stops_the_subfolder_guess_in_every_combination(self):
        """Gate 8, C1, kept: `here` stops a subfolder match whatever else it needs."""
        P = pipeline_module()
        for path in ("prototypes/x.html", "Prototypes/x.html", "cafe\u0301.html"):
            with self.subTest(path):
                self.assertEqual(P.explain_miss(path, path, self.SHA, self.FILES + [self.CAFE], here=True),
                                 f"not committed: {path} is not in abc1234")


class AsTheUrlSendsItTable(unittest.TestCase):
    """ALYDAS-212, S1. Each row's answer is what node measured: the gateway's
    per-segment encoding under `/repos/o/r/contents/`, then
    `new URL(...).pathname`, decoded, with that prefix taken off."""

    def test_dot_segments_go_as_the_url_parser_drops_them(self):
        P = pipeline_module()
        rows = [
            ("docs/prototypes/x.html", "docs/prototypes/x.html"),
            ("docs/./prototypes/x.html", "docs/prototypes/x.html"),
            ("docs/prototypes/./x.html", "docs/prototypes/x.html"),
            ("docs/prototypes/.", "docs/prototypes/"),
            ("./x", "x"),
            ("a/./b", "a/b"),
            ("a/.", "a/"),
            ("a/b/.", "a/b/"),
            (".", ""),
            ("./.", ""),
            ("./", ""),
            ("a/./", "a/"),
            ("a/././b", "a/b"),
            ("a//./b", "a//b"),
            ("a/.b", "a/.b"),
            ("a/b.", "a/b."),
        ]
        for recorded, sent in rows:
            with self.subTest(recorded):
                self.assertEqual(P.as_the_url_sends_it(recorded), sent)


class ResolveLinkTable(unittest.TestCase):
    """ALYDAS-218, S3. A symlink's target, resolved from the link's folder the
    way GitHub resolved each one at Gate 4 (`scratch/alydas-218-e1`): `..`, `./`
    and `//` are followed; an absolute target is not, and neither is one that
    leaves the repository. "" is the repository root."""

    def test_each_target_as_github_resolved_it(self):
        P = pipeline_module()
        rows = [
            ("docs/e218", "real-dir/page.html", "docs/e218/real-dir/page.html"),
            ("docs/e218", "../e218/real-dir/page.html", "docs/e218/real-dir/page.html"),
            ("docs/e218", "./real-dir/page.html", "docs/e218/real-dir/page.html"),
            ("docs/e218", "real-dir//page.html", "docs/e218/real-dir/page.html"),
            ("docs/e218", "../../package.json", "package.json"),
            ("docs", "..", ""),
            ("", "x.html", "x.html"),
            ("docs/e218", "/docs/e218/real-dir/page.html", None),
            ("docs/e218", "../../../x.html", None),
            ("", "../x.html", None),
        ]
        for folder, target, resolved in rows:
            with self.subTest(folder=folder, target=target):
                self.assertEqual(P.resolve_link(folder, target), resolved)


class LinkFixesTable(unittest.TestCase):
    """ALYDAS-218, Gate 6. `follow_links` and the through-a-link reasons over a
    fake tree of entries (whole paths), for the two cases no real hold reaches:
    a link that resolves to the repository root, and a path through a link
    whose real path is not committed."""

    SHA = "abc1234" + "0" * 33

    def entry_from(self, tree):
        def entry(commit, path):
            return tree.get(path) if commit == self.SHA else None
        return entry

    def test_a_link_to_the_root_is_followed_to_the_path_below_it(self):
        P = pipeline_module()
        # Gate 8 (C2): `..` climbs out of `docs`, which a real commit lists as a folder.
        tree = {"docs/up/prototypes/x.html": ("through-symlink", "docs/up"), "docs/up": ("symlink", ".."),
                "docs": ("dir", None), "prototypes/x.html": ("file", None)}
        self.assertEqual(P.follow_links(self.SHA, "docs/up/prototypes/x.html", self.entry_from(tree)),
                         (("file", None), "prototypes/x.html"))

    def test_through_a_link_with_no_real_file_gets_its_own_fix(self):
        P = pipeline_module()
        tree = {"docs/link-dir/x.html": ("through-symlink", "docs/link-dir"), "docs/link-dir": ("symlink", "gone")}
        facts = P.RepoFacts(head=self.SHA, has_origin=True, branch="feature/x", on_github=self.SHA,
                            entry=self.entry_from(tree))
        bad = P.desk_cannot_open(["docs/link-dir/x.html"], facts)
        self.assertEqual(bad, [("docs/link-dir/x.html", "goes through the link docs/link-dir, which GitHub does not follow")])
        self.assertEqual(P.fixes_for(bad), ["record the file by its path without the link"])

    def test_a_real_path_the_desk_refuses_is_not_offered(self):
        """ALYDAS-218, Gate 6. The link reaches a committed file whose path has a
        backslash, which the desk refuses, so that path is never the fix's spelling."""
        P = pipeline_module()
        tree = {"docs/link-dir/x.html": ("through-symlink", "docs/link-dir"), "docs/link-dir": ("symlink", "we\\ird"),
                "docs/we\\ird/x.html": ("file", None)}
        facts = P.RepoFacts(head=self.SHA, has_origin=True, branch="feature/x", on_github=self.SHA,
                            entry=self.entry_from(tree))
        bad = P.desk_cannot_open(["docs/link-dir/x.html"], facts)
        self.assertEqual(bad, [("docs/link-dir/x.html", "goes through the link docs/link-dir, which GitHub does not follow")])
        self.assertIsNone(bad[0][1].spelling)
        self.assertEqual(P.desk_cannot_open(["docs/we\\ird/x.html"], facts)[0][1].case, "backslash")


class DeskPathProblemTable(unittest.TestCase):
    """ALYDAS-212, S2. `desk_path_problem` is the desk's `runFilePathProblem`
    (`merge-notes.ts`): same checks, same order, same names. Each row's answer
    is what node gave for the desk's own function, so a path that breaks two
    checks gets the first one's name."""

    def test_each_verdict_in_the_desks_order(self):
        P = pipeline_module()
        rows = [
            ("", "empty"),
            ("/abs/x.html", "absolute"),
            ("\\abs\\x.html", "absolute"),
            ("C:docs/prototypes/x.html", "absolute"),
            ("c:\\x", "absolute"),
            ("C:", "absolute"),
            ("/..", "absolute"),
            ("\\..", "absolute"),
            ("../e.html", "climbs-out"),
            ("docs/../../e.html", "climbs-out"),
            ("a/..", "climbs-out"),
            ("docs\\..\\x.html", "climbs-out"),
            ("\0/..", "climbs-out"),
            ("docs\\prototypes\\x.html", "backslash"),
            ("docs\\x\0", "backslash"),
            ("docs/x\0", "nul"),
            ("docs/prototypes/x.html", None),
            ("docs/..x.html", None),
            ("1:x", None),
            ("ab:x", None),
            ("docs/C:x", None),
        ]
        for path, want in rows:
            with self.subTest(path=path):
                self.assertEqual(P.desk_path_problem(path), want)


class HoldReasonsCase(HoldHelpers, ScratchRunCase):
    """ALYDAS-206, S2. One real `hold` per reason, in a real scratch repository,
    against the stub desk. Each scenario builds the repository before
    `phase_2_ready`, which leaves a repository and a page it finds alone."""

    def assert_refused(self, desk, code, out, err, said):
        """Nothing asked, exit 2, the reason on stderr and the check event, phase 2 still active."""
        self.assertEqual(self.of(desk, "POST", "/api/asks"), [], "an Ask was raised for a file the desk cannot open")
        self.assertEqual(code, 2, out + err)
        self.assertIn(said, err)
        checks = [e for e in self.check_events() if e.get("phase") == 2]
        self.assertEqual(len(checks), 1, f"one check event on phase 2: {checks}")
        self.assertIn(said, checks[0]["note"])
        self.assertEqual(self.board()["phases"][2]["status"], "active")

    # S3. The closing line gives the fix that fits the reason.
    PUSH = "push the branch so origin has this commit"
    BRANCH = "commit it on a feature branch, which hold pushes (the pipeline never pushes main, origin's default or a detached HEAD)"
    COMMIT = "commit the file (hold pushes it)"
    ORIGIN = "add the repository's GitHub remote as origin"
    INSIDE = "change to a directory inside the repository"
    ROOTED = "record the file by its path from the repository root"

    def hold_refused(self, said, fix):
        with self.asking_desk() as desk:
            code, out, err = self.bounded(self.start_hold(self.desk_env(desk)), desk)
        self.assert_refused(desk, code, out, err, said)
        self.assertIn(f"\n{fix}, then run hold scratch 2 again; or pass --force \"reason\" to hold anyway "
                      f"(recorded on the board).\n", err)
        if fix != self.PUSH:
            self.assertNotIn(self.PUSH, err, "pushing is not the fix for this reason")
        return err

    def short_head(self):
        return self.git("rev-parse", "HEAD")[:7]

    def test_outside_any_repository(self):
        self.phase_2_ready(repo=False)
        self.hold_refused("docs/prototypes/x.html: not in a git repository", self.INSIDE)

    def test_a_repository_with_no_origin(self):
        self.git("init", "-q", "-b", "feature/x")
        self.commit("first.txt")
        self.phase_2_ready()
        self.hold_refused("docs/prototypes/x.html: the repository has no origin", self.ORIGIN)

    def test_main_not_pushed(self):
        origin = self.repo_with_origin()
        self.phase_2_ready()
        self.hold_refused(f"docs/prototypes/x.html: on main, which the pipeline never pushes, and origin does "
                          f"not have {self.short_head()} as far as this checkout last fetched", self.BRANCH)
        self.assertNotEqual(self.origin_refs(origin)["refs/heads/main"], self.git("rev-parse", "HEAD"))

    def test_main_already_on_origin_raises_its_ask(self):
        self.repo_with_origin()
        self.page()
        self.git("push", "-q", "origin", "main")
        self.phase_2_ready()
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Approved"))
            code, out, err = self.bounded(self.start_hold(self.desk_env(desk)), desk)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.of(desk, "POST", "/api/asks")), 1)
        self.assertEqual(self.check_events(), [])

    def test_a_stale_origin_main_is_refused_as_far_as_this_checkout_last_fetched(self):
        """Origin has HEAD, but `origin/main` here was not fetched since. Hold
        does not fetch, and the beat names `commit_on_github()`, which reads
        the same local refs, so the desk would not be told this commit: refused."""
        origin = self.repo_with_origin()
        first = self.git("rev-parse", "HEAD")
        self.page()
        self.git("push", "-q", "origin", "main")
        self.git("update-ref", "refs/remotes/origin/main", first)
        head = self.git("rev-parse", "HEAD")
        self.assertEqual(self.origin_refs(origin)["refs/heads/main"], head, "origin has the commit")
        self.phase_2_ready()
        self.hold_refused(f"docs/prototypes/x.html: on main, which the pipeline never pushes, and origin does "
                          f"not have {head[:7]} as far as this checkout last fetched", self.BRANCH)

    def test_detached_head(self):
        self.repo_with_origin()
        self.page()
        self.git("checkout", "-q", "--detach")
        self.phase_2_ready()
        self.hold_refused("docs/prototypes/x.html: HEAD is detached, so no branch was pushed", self.BRANCH)

    def test_a_file_never_committed(self):
        self.origin = self.repo_with_origin()
        self.on_a_feature_branch(self.origin)
        self.page(commit=False)
        self.phase_2_ready()
        head = self.git("rev-parse", "HEAD")
        err = self.hold_refused(f"docs/prototypes/x.html: not committed: docs/prototypes/x.html is not in {head[:7]}",
                                self.COMMIT)
        self.assertEqual(self.origin_refs(self.origin)["refs/heads/feature/alydas-135-s6"], head, "the push landed")
        self.assertNotIn("not pushed", err)

    def test_a_path_outside_the_repository(self):
        self.phase_2_ready()
        out = self.run_pipeline("note", "scratch", "2", "--produced", "../elsewhere.html=a page outside")
        self.assertEqual(out.returncode, 0, out.stderr)
        # ALYDAS-212, Gate 8, ST1. The fix names the note that drops the path.
        err = self.hold_refused("../elsewhere.html: not a path inside the repository",
                                f"{self.ROOTED} (note scratch 2 --drop-produced ../elsewhere.html then --produced "
                                f"the file's path from the repository root)")
        self.assertNotIn("docs/prototypes/x.html", err, "the committed, pushed page opens")


class HoldPathShapesCase(HoldHelpers, ScratchRunCase):
    """ALYDAS-212. One real `hold` per shape of recorded path, against the stub
    desk: hold judges a path the way the desk's request for it does."""

    def hold_raises_its_ask(self, recorded, entries=()):
        """The committed, pushed page, recorded as `recorded`: one Ask, approved, exit 0, no check event.
        ALYDAS-218, S3: `entries` are committed with plumbing first (`plumb`)."""
        self.phase_2_ready(recorded=recorded)
        if entries:
            self.plumb(*entries)
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Approved"))
            code, out, err = self.bounded(self.start_hold(self.desk_env(desk)), desk)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.of(desk, "POST", "/api/asks")), 1, "one Ask, for the page the desk opens")
        self.assertEqual(self.check_events(), [])
        self.assertEqual([e["path"] for e in self.board()["phases"][2]["produced"]], [recorded],
                         "the path is kept as recorded")

    # S1. The desk's URL drops a `.` segment, so the desk opens these.
    def test_a_dot_segment_in_the_middle_raises_its_ask(self):
        self.hold_raises_its_ask("docs/./prototypes/x.html")

    def test_a_dot_segment_before_the_file_raises_its_ask(self):
        self.hold_raises_its_ask("docs/prototypes/./x.html")

    # S2. The desk's rule refuses these before any lookup, so hold names the
    # rule, not "not committed". 206's refusal helpers, borrowed as they are.
    PUSH = HoldReasonsCase.PUSH
    ROOTED = HoldReasonsCase.ROOTED
    assert_refused = HoldReasonsCase.assert_refused
    hold_refused = HoldReasonsCase.hold_refused

    def test_a_backslash_is_refused_and_its_slash_spelling_named(self):
        self.phase_2_ready(recorded="docs\\prototypes\\x.html")
        err = self.hold_refused("docs\\prototypes\\x.html: a backslash, which the desk refuses",
                                self.recording("record it as docs/prototypes/x.html", "docs\\prototypes\\x.html",
                                               "docs/prototypes/x.html"))
        self.assertNotIn("not committed", err)
        self.follow_the_fix(err, "docs/prototypes/x.html")

    def test_a_drive_letter_is_refused_as_outside(self):
        self.phase_2_ready(recorded="C:docs/prototypes/x.html")
        err = self.hold_refused("C:docs/prototypes/x.html: not a path inside the repository",
                                self.recording(self.ROOTED, "C:docs/prototypes/x.html"))
        self.assertNotIn("not committed", err)

    # S3. Only a file opens. The desk would fetch GitHub's listing of a folder
    # and show it as the file, so hold refuses a path that is a folder at the
    # commit the desk reads, as the URL sends it.
    INSIDE_IT = "record a file inside it"

    def hold_refused_as_a_directory(self, recorded):
        self.phase_2_ready(recorded=recorded)
        sha = self.git("rev-parse", "HEAD")[:7]
        err = self.hold_refused(f"{recorded}: {recorded} is a directory in {sha}, not a file",
                                self.recording(self.INSIDE_IT, recorded))
        self.assertNotIn("not committed", err)

    def test_a_folder_is_refused_as_a_directory(self):
        self.hold_refused_as_a_directory("docs/prototypes")

    def test_a_folder_with_a_trailing_dot_is_refused_as_a_directory(self):
        """Sent as `docs/prototypes/`, the folder's own URL."""
        self.hold_refused_as_a_directory("docs/prototypes/.")

    def test_a_folder_named_like_a_file_is_refused_as_a_directory(self):
        self.origin = self.repo_with_origin()
        self.on_a_feature_branch(self.origin)
        self.page()
        os.makedirs(os.path.join(self.tmp.name, "docs", "notes.md"))
        self.commit(os.path.join("docs", "notes.md", "inner.txt"))
        self.hold_refused_as_a_directory("docs/notes.md")

    def test_the_repository_root_is_refused_as_a_directory(self):
        """`.` passes the desk's rule and is sent as the empty path, the root's listing."""
        self.hold_refused_as_a_directory(".")

    def test_a_backslash_folder_is_not_named_by_its_slash_spelling(self):
        self.phase_2_ready(recorded="docs\\prototypes")
        err = self.hold_refused("docs\\prototypes: a backslash, which the desk refuses",
                                self.recording("record the path with / between folders, never \\", "docs\\prototypes"))
        self.assertNotIn("docs/prototypes is in", err)
        self.assertNotIn("record it as docs/prototypes", err)

    # S4. A path the commit has no file at is explained from the commit's
    # files: each shape names its case and the spelling to record, and only a
    # file truly not in the commit says "not committed".
    FILE = "docs/prototypes/x.html"
    COMMIT = HoldReasonsCase.COMMIT

    def hold_refused_as_a_shape(self, recorded, why, fix):
        self.phase_2_ready(recorded=recorded)
        sha = self.git("rev-parse", "HEAD")[:7]
        err = self.hold_refused(f"{recorded}: {why}; {self.FILE} is in {sha}", self.recording(fix, recorded, self.FILE))
        self.assertNotIn("not committed", err)
        self.assertNotIn(self.COMMIT, err)
        self.follow_the_fix(err, self.FILE)

    # Gate 8, ST1 (Sachit: note can drop one path). A "record" fix names the
    # note that mends it, and following that note word for word lets the next
    # hold ask: `--produced` alone only adds, and the wrong path stayed.
    def recording(self, fix, recorded, spelling=None, note="variants page"):
        then = (f"--produced {shlex.quote(spelling + '=' + note)}" if spelling
                else "then --produced the file's path from the repository root")
        return f"{fix} (note scratch 2 --drop-produced {shlex.quote(recorded)} {then})"

    def follow_the_fix(self, err, spelling, note="variants page"):
        """Run the note the refusal names, as printed, then hold: one Ask, and
        the phase records only `spelling`, with its note kept."""
        commands = re.findall(r"\(note (scratch 2 --drop-produced .*? --produced .*?)\)", err)
        self.assertEqual(len(commands), 1, err)
        out = self.run_pipeline("note", *shlex.split(commands[0]))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual([(e["path"], e["note"]) for e in self.board()["phases"][2]["produced"]], [(spelling, note)])
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Approved"))
            code, out, err = self.bounded(self.start_hold(self.desk_env(desk)), desk)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.of(desk, "POST", "/api/asks")), 1, "following the fix lets hold ask")

    def test_drop_produced_refuses_a_path_the_phase_never_recorded(self):
        self.phase_2_ready()
        before = self.board()["phases"][2]["produced"]
        out = self.run_pipeline("note", "scratch", "2", "--drop-produced", "docs/prototypes/y.html")
        self.assertEqual(out.returncode, 1)
        self.assertIn("phase 2 recorded no file docs/prototypes/y.html", out.stderr)
        self.assertEqual(self.board()["phases"][2]["produced"], before)

    def test_an_uncommitted_file_at_the_root_is_not_taken_for_a_committed_one_in_a_folder(self):
        """Gate 8, C1. `prototypes/x.html` is right where it was recorded, only
        not committed; `docs/prototypes/x.html`, an older file, is. Naming that
        one would have the person approve a different document."""
        self.phase_2_ready(recorded="prototypes/x.html")
        os.makedirs(os.path.join(self.tmp.name, "prototypes"))
        with open(os.path.join(self.tmp.name, "prototypes", "x.html"), "w", encoding="utf-8") as f:
            f.write("<!doctype html><title>the new variants</title>\n")
        sha = self.git("rev-parse", "HEAD")[:7]
        err = self.hold_refused(f"prototypes/x.html: not committed: prototypes/x.html is not in {sha}", self.COMMIT)
        self.assertNotIn("written from a subfolder", err)
        self.assertNotIn("docs/prototypes/x.html", err)

    def test_a_file_where_hold_runs_is_not_taken_for_one_at_the_repository_root(self):
        """Gate 8, re-run of Gate 6 (M7). The working tree is read from the
        repository root, as the desk reads paths, not from where hold runs:
        `sub/prototypes/x.html` beside hold's cwd is not `prototypes/x.html`."""
        self.phase_2_ready(recorded="prototypes/x.html")
        os.makedirs(os.path.join(self.tmp.name, "sub", "prototypes"))
        with open(os.path.join(self.tmp.name, "sub", "prototypes", "x.html"), "w", encoding="utf-8") as f:
            f.write("<!doctype html><title>elsewhere</title>\n")
        sha = self.git("rev-parse", "HEAD")[:7]
        with self.asking_desk() as desk:
            code, out, err = self.bounded(self.start_hold(self.desk_env(desk), cwd=os.path.join(self.tmp.name, "sub")),
                                          desk)
        self.assert_refused(desk, code, out, err, f"prototypes/x.html: written from a subfolder, not the repository "
                                                  f"root; {self.FILE} is in {sha}")

    def test_a_doubled_slash_is_named_with_its_spelling(self):
        self.hold_refused_as_a_shape("docs//prototypes/x.html", "a doubled /, which GitHub does not find",
                                     f"record it as {self.FILE}")

    def test_a_trailing_slash_is_named_with_its_spelling(self):
        self.hold_refused_as_a_shape("docs/prototypes/x.html/", "a trailing /, which GitHub does not find",
                                     f"record it as {self.FILE}")

    def test_another_case_is_named_with_its_spelling(self):
        self.hold_refused_as_a_shape("Docs/prototypes/x.html", "in other capital letters, which GitHub does not find",
                                     f"record it as {self.FILE}")

    def test_other_capital_letters_with_two_files_get_the_generic_fix_and_following_it_asks(self):
        """ALYDAS-220, S2 (c2). Two committed files differ from the path only in
        capital letters, so no spelling is named and the fix says which to
        record in words a person reads. `x.HTML` is added with plumbing, since
        a case-insensitive disk cannot hold both. Recording one, as the fix
        says, lets hold ask."""
        recorded = "docs/prototypes/X.html"
        self.phase_2_ready(recorded=recorded)
        blob = self.git("hash-object", "-w", os.path.join(self.tmp.name, self.FILE))
        self.git("update-index", "--add", "--cacheinfo", f"100644,{blob},docs/prototypes/x.HTML")
        self.git("commit", "-q", "-m", "the variants page, twice in other capital letters")
        sha = self.git("rev-parse", "HEAD")[:7]
        fix = "record it in the capital letters the commit has"
        err = self.hold_refused(f"{recorded}: in other capital letters, which GitHub does not find; "
                                f"docs/prototypes/x.HTML and {self.FILE} are in {sha}",
                                self.recording(fix, recorded))
        self.assertNotIn("in another case", err)
        self.assertNotIn("record it as", err)
        out = self.run_pipeline("note", "scratch", "2", "--drop-produced", recorded,
                                "--produced", f"{self.FILE}=variants page")
        self.assertEqual(out.returncode, 0, out.stderr)
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Approved"))
            code, out, err = self.bounded(self.start_hold(self.desk_env(desk)), desk)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.of(desk, "POST", "/api/asks")), 1, "following the fix lets hold ask")

    def test_a_path_from_a_subfolder_is_named_with_its_spelling(self):
        self.hold_refused_as_a_shape("prototypes/x.html", "written from a subfolder, not the repository root",
                                     f"record it as {self.FILE}")

    def test_a_file_truly_not_committed_still_says_not_committed(self):
        self.phase_2_ready(recorded="docs/prototypes/nope.html")
        sha = self.git("rev-parse", "HEAD")[:7]
        self.hold_refused(f"docs/prototypes/nope.html: not committed: docs/prototypes/nope.html is not in {sha}",
                          self.COMMIT)

    def test_a_name_that_reads_like_a_folder_reason_still_gets_the_commit_fix_and_committing_it_asks(self):
        """ALYDAS-220, S1 (c1, c3). The fix is picked by the reason's case, not
        by a substring of its sentence: a file committed nowhere whose name has
        ", not a file" in it was given the directory fix, "record a file inside
        it". Following the fix it gets, committing the file, lets hold ask."""
        recorded = "docs/a, not a file.html"
        self.phase_2_ready(recorded=recorded)
        with open(os.path.join(self.tmp.name, recorded), "w", encoding="utf-8") as f:
            f.write("<!doctype html><title>variants</title>\n")
        sha = self.git("rev-parse", "HEAD")[:7]
        err = self.hold_refused(f"{recorded}: not committed: {recorded} is not in {sha}", self.COMMIT)
        self.assertNotIn(self.INSIDE_IT, err)
        self.git("add", recorded)
        self.git("commit", "-q", "-m", "the variants page, committed as the fix says")
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Approved"))
            code, out, err = self.bounded(self.start_hold(self.desk_env(desk)), desk)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.of(desk, "POST", "/api/asks")), 1, "following the fix lets hold ask")

    def test_a_reason_s_case_and_spelling_never_reach_the_ledger_or_the_event_log(self):
        """ALYDAS-220, S1. A reason carries its case and the spelling to record,
        for `fixes_for`; only its text is logged. Both places a reason is logged:
        the refusal's check event, then `--force`'s."""
        self.phase_2_ready(recorded="docs//prototypes/x.html")
        out = self.run_pipeline("note", "scratch", "2", "--produced", "docs/nope.html=never committed")
        self.assertEqual(out.returncode, 0, out.stderr)
        sha = self.git("rev-parse", "HEAD")[:7]
        said = (f"docs//prototypes/x.html: a doubled /, which GitHub does not find; {self.FILE} is in {sha}; "
                f"docs/nope.html: not committed: docs/nope.html is not in {sha}")
        with self.asking_desk() as desk:
            code, out, err = self.bounded(self.start_hold(self.desk_env(desk)), desk)
        self.assertEqual(code, 2, out + err)
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Approved"))
            code, out, err = self.bounded(self.start_hold(self.desk_env(desk), "--force", "a ruling"), desk)
        self.assertEqual(code, 0, out + err)
        checks = [e for e in self.check_events() if e.get("phase") == 2]
        self.assertEqual([e["note"] for e in checks],
                         [f"the desk could not open what phase 2 would ask about: {said}",
                          f"held anyway: the desk could not open what phase 2 asks about: {said}"])
        self.assertEqual([sorted(e) for e in checks],
                         [["cwd", "event", "note", "phase", "session", "ts"],
                          ["cwd", "event", "forced", "note", "phase", "session", "ts"]])

        def keys(value):
            if isinstance(value, dict):
                return set(value).union(*(keys(v) for v in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(v) for v in value))
            return set()
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.json"), encoding="utf-8") as f:
            ledger = f.read()
        events = self.events_text()
        for name, text, value in (("the ledger", ledger, json.loads(ledger)),
                                  ("the event log", events, [json.loads(line) for line in events.splitlines()
                                                             if line.strip()])):
            with self.subTest(name):
                self.assertEqual(keys(value) & {"case", "spelling"}, set())
                for case in ("doubled-slash", "not-committed"):
                    self.assertNotIn(case, text)

    def test_a_name_git_would_quote_is_read_as_committed(self):
        """`ls-tree` quotes a name with a `"` or a non-ASCII letter unless told
        `-z`; read quoted, this one would never match and would say "not committed"."""
        name = 'docs/prototypes/\u65e5\u672c "x".html'
        self.origin = self.repo_with_origin()
        self.on_a_feature_branch(self.origin)
        self.page()
        self.commit(name)
        self.phase_2_ready(recorded="D" + name[1:])
        sha = self.git("rev-parse", "HEAD")[:7]
        err = self.hold_refused(f"D{name[1:]}: in other capital letters, which GitHub does not find; {name} is in {sha}",
                                self.recording(f"record it as {name}", "D" + name[1:], name))
        self.assertNotIn("not committed", err)

    # ALYDAS-218, S3. A path is looked up through the commit's tree entries,
    # byte for byte, so a symlink, a submodule and another Unicode spelling are
    # judged as GitHub serves them (the design's table, and Gate 4 for links).
    # The entries are committed with plumbing, as the design's probe builds
    # them, since a working tree cannot hold a gitlink or two spellings.
    GITLINK = b"c1fe14c9c1c7dc741737adc1a5f821ad2eb55f20"
    LINK, MODULE = b"120000", b"160000"

    def plumb(self, *entries):
        """Commit `entries`, each (mode, link text or object, path) as bytes:
        a symlink's text is written as its blob. Answers the commit, short."""
        def raw(*args, data):
            out = subprocess.run(["git", "-c", "core.precomposeunicode=false"] + list(args), cwd=self.tmp.name,
                                 input=data, capture_output=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            return out.stdout.strip()
        info = b""
        for mode, what, path in entries:
            obj = raw("hash-object", "-w", "--stdin", data=what) if mode == self.LINK else what
            info += mode + b" " + obj + b"\t" + path + b"\0"
        raw("update-index", "-z", "--index-info", data=info)
        self.git("commit", "-q", "-m", "tree entries a working tree cannot hold")
        return self.git("rev-parse", "HEAD")[:7]

    def follow_the_generic_fix(self, err, recorded):
        """A fix with no spelling names the note that drops `recorded`; drop it
        as printed and record the page, then hold: one Ask."""
        dropped = re.findall(r"\(note (scratch 2 --drop-produced .*?) then --produced the file's path from the "
                             r"repository root\)", err)
        self.assertEqual(dropped, [f"scratch 2 --drop-produced {shlex.quote(recorded)}"], err)
        out = self.run_pipeline("note", *shlex.split(dropped[0]), "--produced", f"{self.FILE}=variants page")
        self.assertEqual(out.returncode, 0, out.stderr)
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Approved"))
            code, out, err = self.bounded(self.start_hold(self.desk_env(desk)), desk)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.of(desk, "POST", "/api/asks")), 1, "following the fix lets hold ask")

    def page_blob(self):
        """Gate 8. The committed page's blob, to commit again under another name."""
        return self.git("rev-parse", "HEAD:" + self.FILE).encode()

    def hold_refused_by_an_entry(self, recorded, entries, why, fix):
        """`recorded` refused for `why` (`{sha}` is the commit) with the generic
        `fix`, never "not committed"; the fix followed lets hold ask. Gate 8:
        `entries` may be a function, called once the repository exists."""
        self.phase_2_ready(recorded=recorded)
        sha = self.plumb(*(entries() if callable(entries) else entries))
        err = self.hold_refused(f"{recorded}: {why.format(sha=sha)}", self.recording(fix, recorded))
        self.assertNotIn("not committed", err)
        # Gate 6. No spelling is offered, so none is claimed to open.
        self.assertNotIn("record it as", err)
        self.follow_the_generic_fix(err, recorded)

    def test_a_link_to_a_folder_is_refused(self):
        """GitHub answers the link's text, `prototypes`, which the desk would show as the page."""
        self.hold_refused_by_an_entry("docs/link-dir", [(self.LINK, b"prototypes", b"docs/link-dir")],
                                      "a link to the folder prototypes, not a file",
                                      "record the file itself, not a link to its folder")

    def test_a_dangling_link_is_refused(self):
        self.hold_refused_by_an_entry("docs/dangling.html", [(self.LINK, b"nowhere.html", b"docs/dangling.html")],
                                      "a link to nowhere.html, which is not in the commit",
                                      "record the file itself, not the link")

    def test_a_link_with_an_absolute_target_is_refused(self):
        """Gate 4: GitHub does not follow `/docs/...`, and answers the link's text."""
        self.hold_refused_by_an_entry("docs/rooted.html",
                                      [(self.LINK, b"/docs/prototypes/x.html", b"docs/rooted.html")],
                                      "a link to /docs/prototypes/x.html, which is not in the commit",
                                      "record the file itself, not the link")

    def test_a_link_leaving_the_repository_is_refused(self):
        self.hold_refused_by_an_entry("docs/out.html", [(self.LINK, b"../../x.html", b"docs/out.html")],
                                      "a link to ../../x.html, which is not in the commit",
                                      "record the file itself, not the link")

    def test_a_link_whose_target_climbs_out_of_a_linked_folder_is_refused(self):
        """ALYDAS-218, Gate 8 (C2). `..` after a link climbs out of where the
        link points, not out of the link's name: git calls this link dangling,
        and GitHub answered its text, measured on scratch/alydas-218-c2 at
        c6e8d8b. The page it reads as by string is committed, and is not it."""
        self.hold_refused_by_an_entry("docs/page.html",
                                      lambda: [(b"100644", self.page_blob(), b"site/real/keep.html"),
                                               (self.LINK, b"../site/real", b"docs/link-dir"),
                                               (self.LINK, b"link-dir/../prototypes/x.html", b"docs/page.html")],
                                      "a link to link-dir/../prototypes/x.html, which is not in the commit",
                                      "record the file itself, not the link")

    def test_a_link_whose_text_is_not_utf8_is_refused_never_a_crash(self):
        """ALYDAS-218, Gate 8 (C1). The text is shown with its odd byte escaped."""
        self.hold_refused_by_an_entry("docs/dangling.html", [(self.LINK, b"nowhere-\xff.html", b"docs/dangling.html")],
                                      "a link to nowhere-\\xff.html, which is not in the commit",
                                      "record the file itself, not the link")

    def test_a_link_to_a_folder_whose_name_is_not_utf8_is_refused(self):
        self.hold_refused_by_an_entry("docs/link-dir", lambda: [(b"100644", self.page_blob(), b"docs/r\xff/x.html"),
                                                                (self.LINK, b"r\xff", b"docs/link-dir")],
                                      "a link to the folder r\\xff, not a file",
                                      "record the file itself, not a link to its folder")

    def test_a_path_through_a_link_to_a_folder_whose_name_is_not_utf8_offers_no_spelling(self):
        """The file it reaches has a name `note` would refuse, so it is not the fix."""
        self.hold_refused_by_an_entry("docs/link-dir/x.html",
                                      lambda: [(b"100644", self.page_blob(), b"docs/r\xff/x.html"),
                                               (self.LINK, b"r\xff", b"docs/link-dir")],
                                      "goes through the link docs/link-dir, which GitHub does not follow",
                                      "record the file by its path without the link")

    def test_force_holds_past_a_link_whose_text_is_not_utf8(self):
        self.phase_2_ready(recorded="docs/dangling.html")
        self.plumb((self.LINK, b"nowhere-\xff.html", b"docs/dangling.html"))
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Approved"))
            code, out, err = self.bounded(self.start_hold(self.desk_env(desk), "--force", "Gate 8 C1"), desk)
        self.assertEqual(code, 0, out + err)
        self.assertIn("docs/dangling.html: a link to nowhere-\\xff.html, which is not in the commit", err)
        self.assertEqual(len(self.of(desk, "POST", "/api/asks")), 1)

    def test_a_link_to_itself_is_refused_after_five_hops(self):
        self.hold_refused_by_an_entry("docs/loop.html", [(self.LINK, b"loop.html", b"docs/loop.html")],
                                      "a link to loop.html, which is not in the commit",
                                      "record the file itself, not the link")

    def test_a_link_to_a_committed_file_raises_its_ask(self):
        """GitHub serves the target's page; the target is resolved from the
        link's folder with `..`, `./` and `//` followed, as measured at Gate 4."""
        self.hold_raises_its_ask("docs/link.html", [(self.LINK, b"../docs/./prototypes//x.html", b"docs/link.html")])

    def test_a_chain_of_two_links_to_a_committed_file_raises_its_ask(self):
        self.hold_raises_its_ask("docs/one.html", [(self.LINK, b"two.html", b"docs/one.html"),
                                                   (self.LINK, b"prototypes/x.html", b"docs/two.html")])

    def test_a_link_whose_target_goes_through_a_linked_folder_raises_its_ask(self):
        """ALYDAS-218, Gate 6 (Q1). GitHub follows a link in a target's folders:
        measured on scratch/alydas-218-e1 at 2408de7, 200 with the page, 3 of 3."""
        self.hold_raises_its_ask("docs/via.html", [(self.LINK, b"prototypes", b"docs/link-dir"),
                                                   (self.LINK, b"link-dir/x.html", b"docs/via.html")])

    def test_a_path_through_a_linked_folder_is_named_with_its_real_path(self):
        """GitHub 404s `docs/link-dir/x.html`; the file is committed as
        `docs/prototypes/x.html`, which the fix records."""
        recorded = "docs/link-dir/x.html"
        self.phase_2_ready(recorded=recorded)
        sha = self.plumb((self.LINK, b"prototypes", b"docs/link-dir"))
        err = self.hold_refused(f"{recorded}: goes through the link docs/link-dir, which GitHub does not follow; "
                                f"{self.FILE} is in {sha}",
                                self.recording(f"record it as {self.FILE}", recorded, self.FILE))
        self.assertNotIn("not committed", err)
        self.follow_the_fix(err, self.FILE)

    def test_a_submodule_is_refused(self):
        self.hold_refused_by_an_entry("docs/sub", [(self.MODULE, self.GITLINK, b"docs/sub")],
                                      "a submodule, not a file", "record a file from this repository")

    def test_a_path_inside_a_submodule_is_refused(self):
        self.hold_refused_by_an_entry("docs/sub/README.md", [(self.MODULE, self.GITLINK, b"docs/sub")],
                                      "inside the submodule docs/sub, which the desk cannot read",
                                      "record a file from this repository")

    # ALYDAS-218, Gate 6 (DF1 to DF3). A spelling a fix offers is one hold
    # passes: a path whose shapes reach a link or a submodule is told what that
    # entry is, never "not committed", and never a spelling hold then refuses.
    THROUGH = "docs/link-dir/x.html goes through the link docs/link-dir, which GitHub does not follow"

    def hold_refused_through_the_link(self, recorded, why):
        """`recorded` reaches `docs/link-dir/x.html`, a path through the link
        `docs/link-dir -> prototypes`: the real file is named, and recording
        it, as the fix prints, lets hold ask."""
        self.phase_2_ready(recorded=recorded)
        sha = self.plumb((self.LINK, b"prototypes", b"docs/link-dir"))
        err = self.hold_refused(f"{recorded}: {why}; {self.THROUGH}; {self.FILE} is in {sha}",
                                self.recording(f"record it as {self.FILE}", recorded, self.FILE))
        self.assertNotIn("not committed", err)
        self.assertNotIn("record it as docs/link-dir/x.html", err)
        self.follow_the_fix(err, self.FILE)

    def test_a_backslash_whose_slash_spelling_goes_through_a_link_names_the_real_file(self):
        """DF1. `docs/link-dir/x.html` was named, which hold then refused."""
        self.hold_refused_through_the_link("docs\\link-dir\\x.html", "a backslash, which the desk refuses")

    def test_other_capital_letters_through_a_link_name_the_real_file(self):
        """DF2. This read "not committed", whose fix cannot mend it."""
        self.hold_refused_through_the_link("Docs/link-dir/x.html",
                                           "in other capital letters, which GitHub does not find")

    def test_a_link_named_in_other_capital_letters_names_the_real_file(self):
        """The link's own name is the one in other capital letters."""
        self.hold_refused_through_the_link("docs/Link-dir/x.html",
                                           "in other capital letters, which GitHub does not find")

    def test_a_committed_file_is_named_before_the_same_file_through_a_link(self):
        """`site/prototypes -> ../docs/prototypes` puts the page at
        `site/prototypes/x.html` too, which fits as well; the committed path is
        named, not both as a guess."""
        recorded = "Prototypes/x.html"
        self.phase_2_ready(recorded=recorded)
        sha = self.plumb((self.LINK, b"../docs/prototypes", b"site/prototypes"))
        err = self.hold_refused(f"{recorded}: in other capital letters and written from a subfolder; {self.FILE} is in "
                                f"{sha}", self.recording(f"record it as {self.FILE}", recorded, self.FILE))
        self.assertNotIn("site/prototypes/x.html", err)
        self.follow_the_fix(err, self.FILE)

    def test_a_doubled_slash_through_a_link_names_the_real_file(self):
        self.hold_refused_through_the_link("docs//link-dir/x.html", "a doubled /, which GitHub does not find")

    def test_other_capital_letters_inside_a_submodule_name_the_submodule(self):
        """DF2. The submodule's files are not in this commit, so no file is named."""
        self.hold_refused_by_an_entry("Docs/sub/README.md", [(self.MODULE, self.GITLINK, b"docs/sub")],
                                      "in other capital letters, which GitHub does not find; docs/sub/README.md "
                                      "is inside the submodule docs/sub, which the desk cannot read",
                                      "record a file from this repository")

    def test_other_capital_letters_on_a_submodule_name_the_submodule(self):
        """DF3. `docs/sub` was named, which hold then refused as a submodule."""
        self.hold_refused_by_an_entry("Docs/sub", [(self.MODULE, self.GITLINK, b"docs/sub")],
                                      "in other capital letters, which GitHub does not find; docs/sub is a "
                                      "submodule, not a file", "record a file from this repository")

    def test_a_doubled_slash_on_a_submodule_names_the_submodule(self):
        self.hold_refused_by_an_entry("docs//sub", [(self.MODULE, self.GITLINK, b"docs/sub")],
                                      "a doubled /, which GitHub does not find; docs/sub is a submodule, not a file",
                                      "record a file from this repository")

    def test_other_capital_letters_on_a_link_to_a_folder_name_the_link(self):
        """DF3. `docs/link-dir` was named, which hold then refused as a link to a folder."""
        self.hold_refused_by_an_entry("docs/Link-dir", [(self.LINK, b"prototypes", b"docs/link-dir")],
                                      "in other capital letters, which GitHub does not find; docs/link-dir is a "
                                      "link to the folder prototypes, not a file",
                                      "record the file itself, not a link to its folder")

    # ALYDAS-218, S4. The NFC and NFD spellings of `café`: the same letters,
    # the accent one code point or two.
    NFC_CAFE, NFD_CAFE = "docs/café.html", "docs/café.html"
    UNICODE = "in other Unicode spelling (its accents are stored as different bytes)"

    def commit_the_nfc_cafe(self):
        """`docs/café.html`, NFC, committed with plumbing beside the page. Answers the commit, short."""
        blob = self.git("hash-object", "-w", os.path.join(self.tmp.name, self.FILE)).encode()
        return self.plumb((b"100644", blob, self.NFC_CAFE.encode()))

    def test_the_other_unicode_spelling_of_a_committed_name_is_refused(self):
        """The commit holds `café.html` in NFC; GitHub 404s the NFD bytes, which
        a macOS `cat-file` precomposed and passed. S4: the reason names the NFC
        spelling, and recording it, as the fix prints, lets hold ask."""
        self.phase_2_ready(recorded=self.NFD_CAFE)
        sha = self.commit_the_nfc_cafe()
        err = self.hold_refused(f"{self.NFD_CAFE}: {self.UNICODE}, which GitHub does not find; "
                                f"{self.NFC_CAFE} is in {sha}",
                                self.recording(f"record it as {self.NFC_CAFE}", self.NFD_CAFE, self.NFC_CAFE))
        self.assertNotIn("not committed", err)
        self.follow_the_fix(err, self.NFC_CAFE)

    def test_the_other_unicode_spelling_from_a_subfolder_names_both(self):
        recorded = "café.html"
        self.phase_2_ready(recorded=recorded)
        sha = self.commit_the_nfc_cafe()
        err = self.hold_refused(f"{recorded}: {self.UNICODE} and written from a subfolder; {self.NFC_CAFE} is in {sha}",
                                self.recording(f"record it as {self.NFC_CAFE}", recorded, self.NFC_CAFE))
        self.assertNotIn("not committed", err)
        self.follow_the_fix(err, self.NFC_CAFE)

    # ALYDAS-219, S4 (c1, c2). Every shape a path needs is named, and the
    # spelling named mends them all at once.
    def test_a_folder_in_other_capital_letters_from_a_subfolder_names_both(self):
        self.hold_refused_as_a_shape("Prototypes/x.html", "in other capital letters and written from a subfolder",
                                     f"record it as {self.FILE}")

    def test_a_file_in_other_capital_letters_from_a_subfolder_names_both(self):
        self.hold_refused_as_a_shape("prototypes/X.html", "in other capital letters and written from a subfolder",
                                     f"record it as {self.FILE}")

    def hold_refused_as_a_found_directory(self, recorded, shapes):
        """`recorded` reaches the folder `docs/prototypes` through `shapes`:
        refused as a directory, with the directory's fix, which followed lets
        hold ask."""
        self.phase_2_ready(recorded=recorded)
        sha = self.git("rev-parse", "HEAD")[:7]
        err = self.hold_refused(f"{recorded}: {shapes}; docs/prototypes is a directory in {sha}, not a file",
                                self.recording(self.INSIDE_IT, recorded))
        self.assertNotIn("not committed", err)
        self.follow_the_generic_fix(err, recorded)

    def test_a_folder_in_other_capital_letters_is_named_a_directory(self):
        self.hold_refused_as_a_found_directory("Docs/prototypes", "in other capital letters")

    def test_a_folder_with_a_doubled_slash_is_named_a_directory(self):
        """P2: the doubled / is named as well as the directory."""
        self.hold_refused_as_a_found_directory("docs//prototypes", "a doubled /")

    def test_a_name_that_is_not_utf8_leaves_the_other_misses_named(self):
        """C3. The commit also holds `docs/bad-\\xff.html`, a name that is not
        UTF-8; read as text, that one name emptied the commit's files and every
        miss said "not committed". Both fixes, followed as printed, let hold ask.
        P2: the commit's files are still listed once, for both misses."""
        self.phase_2_ready(recorded="Docs/prototypes/x.html")
        out = self.run_pipeline("note", "scratch", "2", "--produced", "prototypes/x.html=the same page")
        self.assertEqual(out.returncode, 0, out.stderr)
        blob = self.git("hash-object", "-w", os.path.join(self.tmp.name, self.FILE)).encode()
        sha = self.plumb((b"100644", blob, b"docs/bad-\xff.html"))
        with self.asking_desk() as desk:
            code, out, err, calls = self.counted_hold(desk)
        self.assertEqual(self.of(desk, "POST", "/api/asks"), [])
        self.assertEqual(code, 2, out + err)
        self.assertIn(f"Docs/prototypes/x.html: in other capital letters, which GitHub does not find; "
                      f"{self.FILE} is in {sha}", err)
        self.assertIn(f"prototypes/x.html: written from a subfolder, not the repository root; {self.FILE} is in {sha}",
                      err)
        self.assertNotIn("not committed", err)
        self.assertEqual(len(self.recursive(calls)), 1, calls)
        commands = re.findall(r"\(note (scratch 2 --drop-produced .*? --produced .*?)\)", err)
        self.assertEqual(len(commands), 2, err)
        for command in commands:
            out = self.run_pipeline("note", *shlex.split(command))
            self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual([e["path"] for e in self.board()["phases"][2]["produced"]], [self.FILE])
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Approved"))
            code, out, err = self.bounded(self.start_hold(self.desk_env(desk)), desk)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.of(desk, "POST", "/api/asks")), 1, "following both fixes lets hold ask")

    # S4, P2. The commit's files are read only after a miss, and once.
    def counted_hold(self, desk):
        """A hold with a `git` first on PATH that logs each call and then runs
        the real git. Answers its exit, output and the logged calls, each a list
        of the arguments git was given."""
        real = shutil.which("git")
        self.assertTrue(real, "git is on PATH")
        wrapper = tempfile.TemporaryDirectory()
        self.addCleanup(wrapper.cleanup)
        log = os.path.join(wrapper.name, "calls.log")
        script = os.path.join(wrapper.name, "git")
        with open(script, "w", encoding="utf-8") as f:
            f.write(f'#!/bin/sh\nprintf "%s\\0" "$@" >> "{log}"\nprintf "\\n" >> "{log}"\nexec "{real}" "$@"\n')
        os.chmod(script, 0o755)
        env = dict(self.desk_env(desk), PATH=wrapper.name + os.pathsep + os.environ.get("PATH", ""))
        code, out, err = self.bounded(self.start_hold(env), desk)
        with open(log, encoding="utf-8") as f:
            calls = [line.split("\0") for line in f.read().split("\0\n") if line]
        return code, out, err, calls

    @staticmethod
    def ran(calls, subcommand):
        return [c for c in calls if subcommand in c]

    def recursive(self, calls):
        """ALYDAS-218, S3. The commit's whole listing, `ls-tree -r`, which `files` reads."""
        return [c for c in self.ran(calls, "ls-tree") if "-r" in c]

    def listings(self, calls):
        """ALYDAS-218, S3. Each one-folder `ls-tree`, by the tree it listed (its last argument)."""
        return [c[-1] for c in self.ran(calls, "ls-tree") if "-r" not in c]

    def test_a_hold_whose_paths_all_open_never_lists_the_commit(self):
        self.phase_2_ready()
        out = self.run_pipeline("note", "scratch", "2", "--produced", "docs/./prototypes/x.html=the same page")
        self.assertEqual(out.returncode, 0, out.stderr)
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Approved"))
            code, out, err, calls = self.counted_hold(desk)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.of(desk, "POST", "/api/asks")), 1)
        # ALYDAS-218, S3, P3. The wrapper saw the hold's git: both paths were
        # looked up through the folders' listings, each folder (the root,
        # docs, docs/prototypes) listed once though two paths walk it.
        self.assertEqual(self.recursive(calls), [], "the commit was listed though every path opened")
        listed = self.listings(calls)
        self.assertEqual(len(listed), 3, calls)
        self.assertEqual(len(set(listed)), len(listed), f"a folder was listed twice: {calls}")

    def test_a_hold_with_two_misses_lists_the_commit_once(self):
        self.phase_2_ready(recorded="docs//prototypes/x.html")
        out = self.run_pipeline("note", "scratch", "2", "--produced", "prototypes/x.html=the same page")
        self.assertEqual(out.returncode, 0, out.stderr)
        with self.asking_desk() as desk:
            code, out, err, calls = self.counted_hold(desk)
        sha = self.git("rev-parse", "HEAD")[:7]
        self.assertEqual(code, 2, out + err)
        self.assertEqual(self.of(desk, "POST", "/api/asks"), [])
        self.assertIn(f"docs//prototypes/x.html: a doubled /, which GitHub does not find; {self.FILE} is in {sha}", err)
        self.assertIn(f"prototypes/x.html: written from a subfolder, not the repository root; {self.FILE} is in {sha}",
                      err)
        self.assertEqual(len(self.recursive(calls)), 1, calls)
        listed = self.listings(calls)
        self.assertEqual(len(set(listed)), len(listed), f"a folder was listed twice: {calls}")


# ALYDAS-212, S5 (E2). The desk's run file rule and the gateway's contents URL,
# read for the table below. The rule is run, not copied: `node` imports
# merge-notes.ts (a file with no imports) with its types stripped. The URL is
# copied, and `read_desk_content_encoding` pins the copy to github.ts.
DESK_RUN_FILE_RULE = os.path.join("packages", "modules", "work", "src", "merge-notes.ts")
DESK_GITHUB = os.path.join("packages", "gateway", "src", "github.ts")
CONTENTS_PREFIX = "/repos/o/r/contents/"

# Per path: the desk's `runFilePathProblem`, and when it passes, the path GitHub
# is asked for: each segment `encodeURIComponent`ed and joined by `/` (the
# gateway's `github.content`), parsed by `new URL`, which drops `.` segments,
# then read back off the URL's path. A path the URL cannot be read back from is
# `sent: null`, which opens nothing.
DESK_SIDE_JS = """
import { readFileSync } from 'node:fs';
const { runFilePathProblem } = await import(RULE_URL);
const prefix = PREFIX;
const answers = JSON.parse(readFileSync(0, 'utf8')).map((path) => {
  const problem = runFilePathProblem(path);
  if (problem !== null) return { problem, sent: null };
  const encoded = path.split('/').map((segment) => encodeURIComponent(segment)).join('/');
  const pathname = new URL('https://api.github.com' + prefix + encoded).pathname;
  if (!pathname.startsWith(prefix)) return { problem, sent: null };
  try {
    return { problem, sent: pathname.slice(prefix.length).split('/').map(decodeURIComponent).join('/') };
  } catch {
    return { problem, sent: null };
  }
});
process.stdout.write(JSON.stringify(answers));
"""


def read_desk_rule_file(repo, named, relative, name):
    """ALYDAS-212, S5. The desk file that declares `name`, for `node` to import. Raises DeskAbsent."""
    path, _ = _declaration(repo, named, relative, name)
    return path


def read_desk_content_encoding(repo, named, relative, name):
    """ALYDAS-212, S5. The gateway's `github.content` handler, checked to send
    each segment `encodeURIComponent`ed on its own and joined by `/`, as
    `DESK_SIDE_JS` copies it. Raises DeskAbsent when the file or the handler is
    not there, and DeskUnreadable, a failure, when it encodes any other way."""
    path = os.path.join(repo, relative)
    where = desk_checkout_words(repo, named)
    if not os.path.exists(path):
        raise DeskAbsent(f"{name} was not read: {relative} is not in {where}")
    with open(path, encoding="utf-8") as f:
        source = _no_comments(f.read())
    if f"'{name}'" not in source:
        raise DeskAbsent(f"{name} is not a handler in {path}, in {where}")
    handler = re.search(rf"'{re.escape(name)}'\s*:\s*async\s*\([^)]*\)[^{{]*=>\s*\{{(.*?)\n\s*\}},", source, re.S)
    copied = "each segment encodeURIComponent'ed and joined by /, as the S5 table copies"
    if handler is None:
        raise DeskUnreadable(f"{name} in {path} is not a handler this pin can read; it should send {copied}")
    body = handler.group(1)
    if not re.search(r"const\s+encoded\s*=\s*path\.split\('/'\)\.map\(segment\)\.join\('/'\);", body) \
            or not re.search(r"/contents/\$\{encoded\}", body):
        raise DeskUnreadable(f"{name} in {path} no longer sends {copied}: {body.strip()!r}")
    if not re.search(r"function\s+segment\s*\(\s*value\s*:\s*string\s*\)\s*:\s*string\s*\{\s*"
                     r"return\s+encodeURIComponent\(value\);\s*\}", source):
        raise DeskUnreadable(f"segment in {path} is no longer encodeURIComponent, so {name} no longer sends {copied}")
    return True


class PathShapesAgreeCase(ScratchRunCase):
    """ALYDAS-212, S5 (E2). One table of recorded paths, each against the tree
    entries of a real commit that origin has, judged twice. The desk: its own
    `runFilePathProblem`, run in node, then the path its URL sends, opened only
    when GitHub's contents API would serve a page for it (`github_serves`, the
    measured model below). Hold: `desk_cannot_open` with `repo_facts()`. The two
    must agree on every row, and each row also says what the desk does, so the
    desk side cannot pass by refusing everything.

    ALYDAS-218, S6. The commits hold symlinks, gitlinks and names in other
    Unicode spellings or not UTF-8, built with plumbing, and the model is
    written apart from pipeline.py's own lookup (it never calls `entry`,
    `follow_links` or `resolve_link`), so the table compares two separate
    readings of GitHub's measured answers."""

    FILE = "docs/prototypes/x.html"
    ODD = "docs/prototypes/50% off? #1.html"
    # ALYDAS-218, S6. Tree entries a working tree cannot hold, as (mode, name,
    # link text); a bare name is a regular file. The design's e218 folder.
    REGULAR, FOLDER, LINK, GITLINK = b"100644", b"040000", b"120000", b"160000"
    SUBMODULE_COMMIT = b"c1fe14c9c1c7dc741737adc1a5f821ad2eb55f20"
    E = "docs/e218"
    NFC = "docs/e218/café.html"
    NFD = "docs/e218/café.html"
    PAGE = "docs/e218/real-dir/page.html"
    LINKED = (PAGE, (LINK, E + "/link-dir", "real-dir"),
              (LINK, E + "/link-file.html", "real-dir/page.html"),
              (LINK, E + "/dangling.html", "nowhere.html"),
              (LINK, E + "/absolute.html", "/" + PAGE),
              (LINK, E + "/chain.html", "link-file.html"),
              (LINK, E + "/up.html", "../e218/real-dir/page.html"),
              # ALYDAS-218, Gate 6 (Q1). A link whose target goes through
              # the link `link-dir`.
              (LINK, E + "/via.html", "link-dir/page.html"),
              (GITLINK, E + "/sub", None))
    # ALYDAS-218, Gate 6. An executable page, and chains of five and six links
    # (h1..h5 and g1..g6) to it, for the plan's "five hops" on both sides.
    EXECUTABLE = (PAGE, (b"100755", E + "/run.html", None),
                  (LINK, E + "/h1.html", "h2.html"), (LINK, E + "/h2.html", "h3.html"),
                  (LINK, E + "/h3.html", "h4.html"), (LINK, E + "/h4.html", "h5.html"),
                  (LINK, E + "/h5.html", "real-dir/page.html"),
                  (LINK, E + "/g1.html", "g2.html"), (LINK, E + "/g2.html", "g3.html"),
                  (LINK, E + "/g3.html", "g4.html"), (LINK, E + "/g4.html", "g5.html"),
                  (LINK, E + "/g5.html", "g6.html"), (LINK, E + "/g6.html", "real-dir/page.html"))
    ROWS = [
        # (what, recorded path, entries in the commit, the desk opens it)
        ("a plain committed file", FILE, (FILE,), True),
        ("./ before the path", "./docs/prototypes/x.html", (FILE,), True),
        ("./ in the middle", "docs/./prototypes/x.html", (FILE,), True),
        ("./ before the file", "docs/prototypes/./x.html", (FILE,), True),
        ("a name the URL must encode", ODD, (ODD,), True),
        ("a backslash, its / spelling committed", "docs\\prototypes\\x.html", (FILE,), False),
        ("a backslash, its / spelling not committed", "docs\\prototypes\\y.html", (FILE,), False),
        ("a drive letter", "C:docs/prototypes/x.html", (FILE,), False),
        ("absolute", "/docs/prototypes/x.html", (FILE,), False),
        ("a .. that resolves to the file", "docs/x/../prototypes/x.html", (FILE,), False),
        # Gate 6: two rows for the rule's separators and its order, which the
        # rule check below compares against the desk's own answer.
        ("a leading backslash", "\\docs\\prototypes\\x.html", (FILE,), False),
        ("a .. between backslashes", "docs\\x\\..\\prototypes\\x.html", (FILE,), False),
        ("empty", "", (FILE,), False),
        ("a NUL", "docs/prototypes/x.html\0", (FILE,), False),
        ("a folder", "docs/prototypes", (FILE,), False),
        ("a folder as folder/.", "docs/prototypes/.", (FILE,), False),
        ("the root as .", ".", (FILE,), False),
        ("a folder named notes.md", "docs/notes.md", ("docs/notes.md/a.md",), False),
        ("a doubled /", "docs//prototypes/x.html", (FILE,), False),
        ("a trailing /", "docs/prototypes/x.html/", (FILE,), False),
        ("a file then /.", "docs/prototypes/x.html/.", (FILE,), False),
        ("other capital letters", "Docs/prototypes/x.html", (FILE,), False),
        ("from a subfolder", "prototypes/x.html", (FILE,), False),
        ("from a subfolder, two matches", "prototypes/x.html", (FILE, "site/prototypes/x.html"), False),
        ("a real miss", "docs/prototypes/nope.html", (FILE,), False),
        ("a folder with a doubled /", "docs//prototypes", (FILE,), False),
        # ALYDAS-218, S6. Spellings, links and submodules, as GitHub answered
        # them (the design's "What was measured", and the plan's Gate 4 answer).
        ("an NFC name", NFC, (NFC,), True),
        ("the NFD spelling of an NFC name", NFD, (NFC,), False),
        ("a symlink to a folder", E + "/link-dir", LINKED, False),
        ("a symlink to a file", E + "/link-file.html", LINKED, True),
        ("a dangling symlink", E + "/dangling.html", LINKED, False),
        ("a symlink with an absolute target", E + "/absolute.html", LINKED, False),
        ("a chain of two links to a file", E + "/chain.html", LINKED, True),
        ("a link whose target climbs with ../", E + "/up.html", LINKED, True),
        ("a link whose target goes through a linked folder", E + "/via.html", LINKED, True),
        ("a path through a symlinked folder", E + "/link-dir/page.html", LINKED, False),
        ("a gitlink", E + "/sub", LINKED, False),
        ("inside a gitlink", E + "/sub/README.md", LINKED, False),
        ("a file beside a name that is not UTF-8", FILE, (FILE, b"docs/bad-\xff.html"), True),
        ("other capital letters beside a name that is not UTF-8", "Docs/prototypes/x.html", (FILE, b"docs/bad-\xff.html"), False),
        ("other capital letters and from a subfolder", "Prototypes/x.html", (FILE,), False),
        ("a folder in other capital letters", "Docs/prototypes", (FILE,), False),
        # Gate 6. GitHub serves an executable file (mode 100755) as a page;
        # the plan keeps five hops, so a chain of five opens and six does not.
        ("an executable committed file", E + "/run.html", EXECUTABLE, True),
        ("a chain of five links to a file", E + "/h1.html", EXECUTABLE, True),
        ("a chain of six links to a file", E + "/g1.html", EXECUTABLE, False),
        # Gate 8 (C2). Read as a string, the target is the committed page.
        ("a link whose target climbs out of a linked folder", E + "/lex.html",
         LINKED + ("docs/e218-site/real/keep.html", (LINK, E + "/lex-dir", "../e218-site/real"),
                   (LINK, E + "/lex.html", "lex-dir/../real-dir/page.html")), False),
    ]

    # ALYDAS-218, S6. GitHub's answers as measured with the desk's own request,
    # cited from docs/specs/2026-10-02-alydas-218-path-gaps-design.md ("What was
    # measured", scratch commit c910e08) and the plan's "Open" answer measured
    # at Gate 4 (docs/plans/2026-10-02-alydas-218-path-gaps.md, de9597a and the
    # commit after it). The pin below holds `github_serves` to these.
    MEASURED_TREE = LINKED + (NFC, "package.json",
                              (LINK, E + "/dot.html", "./real-dir/page.html"),
                              (LINK, E + "/double.html", "real-dir//page.html"),
                              (LINK, E + "/package.json", "../../package.json"),
                              (LINK, E + "/no-readme.md", "../../README.md"),
                              # Gate 8 (C2). `..` after the link `lex-dir`.
                              "docs/e218-site/real/keep.html",
                              (LINK, E + "/lex-dir", "../e218-site/real"),
                              (LINK, E + "/lex.html", "lex-dir/../real-dir/page.html"))
    MEASURED = [
        (NFC, "200, the page"),
        (NFD, "404"),
        (PAGE, "200, the page"),
        (E + "/link-dir/page.html", "404"),
        (E + "/link-dir", "200, the link's text real-dir"),
        (E + "/link-file.html", "200, the page"),
        (E + "/dangling.html", "200, the link's text nowhere.html"),
        (E + "/sub", "200, JSON about the submodule"),
        (E + "/sub/README.md", "404"),
        (E + "/chain.html", "200, the page"),
        (E + "/up.html", "200, the page"),
        (E + "/dot.html", "200, the page"),
        (E + "/double.html", "200, the page"),
        (E + "/package.json", "200, the page"),
        (E + "/absolute.html", "200, the link's text /" + PAGE),
        (E + "/no-readme.md", "200, the link's text ../../README.md"),
        # Gate 6 (Q1), measured on scratch/alydas-218-e1 at 2408de7: the raw
        # contents of a link whose target goes through another link answered
        # 200 with the page, 3 times of 3.
        (E + "/via.html", "200, the page"),
        # Gate 8 (C2), measured on scratch/alydas-218-c2 at c6e8d8b: `..`
        # after a link climbs out of where it points, which holds no
        # real-dir, so GitHub answered the text; git calls it dangling.
        (E + "/lex.html", "200, the link's text lex-dir/../real-dir/page.html"),
    ]

    def committed(self, entries):
        """A repository holding `entries`, committed on a feature branch and
        pushed to a bare origin, so `repo_facts()` finds a commit the desk could
        read. ALYDAS-218, S6: written with plumbing (`update-index -z
        --index-info`), so a symlink, a gitlink and a name's exact bytes are
        committed as given. Answers its directory and the commit."""
        repo = tempfile.mkdtemp(dir=self.tmp.name)
        self.git("init", "-q", "-b", "feature/alydas-212-s5", cwd=repo)
        def raw(*args, data=b""):
            out = subprocess.run(["git", "-c", "core.precomposeunicode=false"] + list(args), cwd=repo,
                                 input=data, capture_output=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            return out.stdout.strip()
        info = b""
        for one in entries:
            mode, name, text = one if isinstance(one, tuple) else (self.REGULAR, one, None)
            name = name if isinstance(name, bytes) else name.encode("utf-8")
            if mode == self.GITLINK:
                obj = self.SUBMODULE_COMMIT
            else:
                obj = raw("hash-object", "-w", "--stdin", data=text.encode("utf-8") if mode == self.LINK else name + b"\n")
            info += mode + b" " + obj + b"\t" + name + b"\0"
        raw("update-index", "-z", "--index-info", data=info)
        self.git("commit", "-q", "-m", "the entries", cwd=repo)
        origin = repo + ".git"
        self.git("init", "-q", "--bare", origin, cwd=repo)
        self.git("remote", "add", "origin", origin, cwd=repo)
        self.git("push", "-q", "origin", "HEAD:refs/heads/feature/alydas-212-s5", cwd=repo)
        return repo, self.git("rev-parse", "HEAD", cwd=repo)

    def tree_of(self, repo, sha):
        """ALYDAS-218, S6. Every entry of the commit, folders too, as
        {path: (mode, link text or None)}, paths and texts as bytes: read from
        `ls-tree -r -t -z` without decoding, so no name is lost or respelled."""
        def raw(*args):
            out = subprocess.run(["git", "-C", repo, "-c", "core.precomposeunicode=false"] + list(args),
                                 capture_output=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            return out.stdout
        tree = {}
        for line in raw("ls-tree", "-r", "-t", "-z", sha).split(b"\0"):
            if line:
                meta, _, path = line.partition(b"\t")
                mode, _, obj = meta.split(b" ")
                tree[path] = (mode, raw("cat-file", "blob", obj.decode("ascii")) if mode == self.LINK else None)
        return tree

    @classmethod
    def github_serves(cls, tree, sent):
        """ALYDAS-218, S6. What GitHub's contents API answers for `sent` (bytes)
        in a commit whose entries are `tree`, as measured (MEASURED): "404",
        "200, the page", "200, the link's text <text>", or "200, JSON about the
        submodule" (a folder: "200, JSON about the folder"). Only the page opens
        on the desk. Exact bytes, so another Unicode spelling is a miss; a path
        through a link or a gitlink is a miss; a link is followed only to a
        regular file, its target joined to the link's folder with `..`, `./`
        and `//` normalised, never when absolute, and at most five hops.
        Gate 6 (Q1, measured at 2408de7): a link in the folders of a target is
        followed too, each such link one of the five hops."""
        regular = (cls.REGULAR, b"100755")
        def normalised(folder, target):
            # Gate 8 (C2, measured at c6e8d8b): `..` climbs only out of a
            # real folder; past a link, GitHub answers the text.
            parts = []
            for segment in (folder + b"/" + target).split(b"/"):
                if segment in (b"", b"."):
                    continue
                if segment == b"..":
                    if not parts:
                        return None
                    climbed = tree.get(b"/".join(parts))
                    if climbed is None or climbed[0] != cls.FOLDER:
                        return None
                    parts.pop()
                else:
                    parts.append(segment)
            return b"/".join(parts)
        segments = sent.split(b"/")
        for i in range(1, len(segments)):
            above = tree.get(b"/".join(segments[:i]))
            if above is not None and above[0] != cls.FOLDER:
                return "404"
        found = tree.get(sent)
        if found is None:
            return "404"
        mode, text = found
        if mode in regular:
            return "200, the page"
        if mode == cls.GITLINK:
            return "200, JSON about the submodule"
        if mode != cls.LINK:
            return "200, JSON about the folder"
        said = "200, the link's text " + text.decode("utf-8", "surrogateescape")
        link, rest = sent, b""
        for _ in range(5):
            target = tree[link][1]
            if target.startswith(b"/"):
                return said
            to = normalised(link.rpartition(b"/")[0], target)
            if to is None:
                return said
            at = b"/".join(part for part in (to, rest) if part)
            # The first link on the way to `at`, its folders before itself;
            # anything else there that is not a folder ends the walk.
            parts, link = at.split(b"/"), None
            for i in range(1, len(parts) + 1):
                reached = tree.get(b"/".join(parts[:i]))
                if reached is None or reached[0] == cls.LINK or i == len(parts):
                    break
                if reached[0] != cls.FOLDER:
                    return said
            if reached is None:
                return said
            if reached[0] != cls.LINK:
                return "200, the page" if reached[0] in regular else said
            link, rest = b"/".join(parts[:i]), b"/".join(parts[i:])
        return said

    def desk_answers(self, rule, paths):
        """What the desk makes of each path, from one node run: {problem, sent}."""
        node = shutil.which("node")
        if node is None:
            self.fail("node is not on PATH, so the desk's runFilePathProblem cannot be run")
        script = (DESK_SIDE_JS.replace("RULE_URL", json.dumps("file://" + os.path.abspath(rule)))
                  .replace("PREFIX", json.dumps(CONTENTS_PREFIX)))
        out = subprocess.run([node, "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", script],
                             input=json.dumps(paths), capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, f"node on {rule}: {out.stderr}")
        answers = json.loads(out.stdout)
        self.assertEqual(len(answers), len(paths), out.stdout)
        return answers

    def test_the_github_model_gives_the_measured_answers(self):
        """ALYDAS-218, S6. The pin: the model's answer for each symlink,
        submodule and spelling GitHub was asked about is the answer measured
        (the design's table and the plan's Gate 4 answer, cited at MEASURED)."""
        repo, sha = self.committed(self.MEASURED_TREE)
        tree = self.tree_of(repo, sha)
        self.assertEqual(tree[self.NFC.encode("utf-8")][0], self.REGULAR, "the NFC name was committed as its bytes")
        self.assertNotIn(self.NFD.encode("utf-8"), tree, "git respelled the NFC name")
        for sent, measured in self.MEASURED:
            with self.subTest(sent):
                self.assertEqual(self.github_serves(tree, sent.encode("utf-8")), measured)

    def test_hold_and_the_desk_agree_on_every_row(self):
        rule = desk_constant(self, read_desk_rule_file, DESK_RUN_FILE_RULE, "runFilePathProblem")
        answers = self.desk_answers(rule, [path for _, path, _, _ in self.ROWS])
        P = pipeline_module()
        here = os.getcwd()
        self.addCleanup(os.chdir, here)
        built = {}
        for (what, path, entries, opens), answer in zip(self.ROWS, answers):
            if entries not in built:
                repo, sha = self.committed(entries)
                # In process, so in the scratch repository: hold reads the one `repo_root` finds.
                os.chdir(repo)
                self.assertEqual(os.path.realpath(P.repo_root()), os.path.realpath(repo))
                facts = P.repo_facts()
                self.assertEqual(facts.on_github, sha, "origin has the commit, so a row tests its path, not a push")
                built[entries] = (repo, self.tree_of(repo, sha), facts)
            repo, tree, facts = built[entries]
            os.chdir(repo)
            with self.subTest(what, path=path):
                # Gate 6. The rule itself, row for row: a desk rule that stopped
                # refusing a backslash or an empty path still refuses these rows
                # by what GitHub is sent, so only this sees it.
                self.assertEqual(P.desk_path_problem(path), answer["problem"],
                                 f"{path!r} ({what}): the desk's runFilePathProblem says {answer['problem']!r}, "
                                 f"hold's desk_path_problem says {P.desk_path_problem(path)!r}")
                serves = (None if answer["problem"] is not None or answer["sent"] is None
                          else self.github_serves(tree, answer["sent"].encode("utf-8")))
                desk = serves == "200, the page"
                hold = P.desk_cannot_open([path], facts) == []
                verdict = {True: "opens it", False: "refuses it"}
                self.assertEqual(hold, desk, f"{path!r} ({what}): the desk {verdict[desk]} "
                                             f"(rule {answer['problem']}, sent {answer['sent']!r}, GitHub {serves}), "
                                             f"hold {verdict[hold]}")
                self.assertEqual(desk, opens, f"{path!r} ({what}): the desk {verdict[desk]} "
                                              f"(rule {answer['problem']}, sent {answer['sent']!r}, GitHub {serves}), "
                                              f"the row says it {verdict[opens]}")

    def test_the_gateway_still_encodes_each_segment_as_copied(self):
        self.assertTrue(desk_constant(self, read_desk_content_encoding, DESK_GITHUB, "github.content"))


class HoldForceCase(HoldHelpers, ScratchRunCase):
    """ALYDAS-206, S3. What `hold --force` leaves behind for the next pass, and
    what it leaves alone."""

    FORCE = "Sachit ruled in chat: hold on the local page"
    PLAN = os.path.join("plans", "2026-09-13-scratch.md")

    def plan(self, verdict):
        with open(os.path.join(self.root, self.PLAN), "w", encoding="utf-8") as f:
            f.write(f"# Plan\n\n## Brief verification\n\n| # | Claim | Verdict |\n|---|---|---|\n| 1 | a claim | {verdict} |\n")

    def gate_4_ready(self):
        """Gate 4 running on a run that holds only it, on a pushed feature
        branch, its page recorded but never committed, so the desk-can-open
        check refuses it ("not committed") while the artifact checks pass."""
        self.origin = self.repo_with_origin()
        self.on_a_feature_branch(self.origin)
        self.page(commit=False)
        self.init_scratch("--hold", "4")
        os.makedirs(os.path.join(self.root, "plans"), exist_ok=True)
        self.plan("CONFIRMED")
        for args in (("phase", "scratch", "4", "start"),
                     ("note", "scratch", "4", "--plain", self.PLAIN,
                      "--produced", "docs/prototypes/x.html=the verified brief")):
            out = self.run_pipeline(*args)
            self.assertEqual(out.returncode, 0, out.stderr)
        self.tick_steps(4)  # ALYDAS-225, K1

    def hold_4(self, *extra, answer=None):
        """One hold on Gate 4: answered with `answer` once it has read back,
        or bounded when it is expected to refuse."""
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk), *extra, n="4")
            if answer is None:
                code, out, err = self.bounded(proc, desk)
            else:
                self.reads_back(desk)
                desk.answer("GET", "/api/asks", 200, answer)
                code, out, err = self.finish(proc)
        return desk, code, out, err

    def test_a_forced_hold_sent_back_does_not_waive_an_unconfirmed_verdict_next_pass(self):
        """The Gate 4 check skips a NOT CONFIRMED verdict when `p["forced"]`
        is set. A forced hold records `p["forced"]`, so its send-back drops it,
        or the next pass's hold would go past the verdict unasked."""
        self.gate_4_ready()
        # With the verdict present, --force does not go past it: the artifact checks still refuse.
        self.plan("NOT CONFIRMED")
        desk, code, out, err = self.hold_4("--force", self.FORCE)
        self.assertEqual((code, self.of(desk, "POST", "/api/asks")), (2, []), out + err)
        self.assertIn("NOT CONFIRMED", err)
        self.plan("CONFIRMED")

        desk, code, out, err = self.hold_4("--force", self.FORCE,
                                           answer=self.answered("send-back", "Decided", "Recheck claim 1."))
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.of(desk, "POST", "/api/asks")), 1)
        phase = self.board()["phases"][4]
        self.assertEqual(phase["status"], "active")
        self.assertFalse(phase.get("forced"), "the send-back kept the hold's own force")
        self.assertEqual(phase["hold"], {"attempt": 2})

        # The next pass: the page committed (so the desk could open it), and the recheck turned claim 1 over.
        self.git("add", "docs/prototypes/x.html")
        self.git("commit", "-q", "-m", "the verified brief")
        self.plan("NOT CONFIRMED")
        self.tick_steps(4)  # ALYDAS-225, K1: the send-back's fresh steps, so the verdict is the one thing refused
        desk, code, out, err = self.hold_4()
        self.assertEqual(self.of(desk, "POST", "/api/asks"), [], "the unconfirmed verdict was gone past")
        self.assertEqual(code, 2, out + err)
        self.assertIn("phase 4 cannot close: a NOT CONFIRMED verdict is present", err)

    def test_a_forced_hold_that_fell_back_does_not_waive_an_unconfirmed_verdict_on_the_next_hold(self):
        """Gate 8, C1. A forced Gate 4 hold the desk would not take (exit 4)
        leaves its force on the phase. Only a force given by hand waives the
        unconfirmed verdict, so the next hold still refuses it and asks nothing."""
        self.gate_4_ready()
        with Listener(routes={("POST", "/api/asks"): (500, {"error": "down"})}) as down:
            code, out, err = self.bounded(self.start_hold(self.desk_env(down), "--force", self.FORCE, n="4"), down)
        self.assertEqual(code, 4, out + err)
        self.git("add", "docs/prototypes/x.html")
        self.git("commit", "-q", "-m", "the verified brief")
        self.plan("NOT CONFIRMED")
        desk, code, out, err = self.hold_4()
        self.assertEqual(self.of(desk, "POST", "/api/asks"), [], "the hold's own force waived the unconfirmed verdict")
        self.assertEqual(code, 2, out + err)
        self.assertIn("phase 4 cannot close: a NOT CONFIRMED verdict is present", err)

    def test_a_force_given_by_hand_is_not_dropped_by_a_send_back(self):
        """Only the hold's own force is dropped: a start forced by hand keeps its reason."""
        self.gate_4_ready()
        self.page()
        out = self.run_pipeline("phase", "scratch", "4", "start", "--force", "started by hand past the order")
        self.assertEqual(out.returncode, 0, out.stderr)
        desk, code, out, err = self.hold_4(answer=self.answered("send-back", "Decided", "Again."))
        self.assertEqual(code, 0, out + err)
        phase = self.board()["phases"][4]
        self.assertEqual(phase["status"], "active")
        self.assertEqual(phase["forced"], "started by hand past the order")

    def test_a_forced_hold_sent_back_gives_back_the_force_given_by_hand(self):
        """Gate 6, P3. A forced hold on a phase already forced by hand shows its
        own reason while it waits; its send-back puts the hand's reason back,
        rather than clearing both."""
        self.gate_4_ready()
        self.page()
        out = self.run_pipeline("phase", "scratch", "4", "start", "--force", "started by hand past the order")
        self.assertEqual(out.returncode, 0, out.stderr)
        desk, code, out, err = self.hold_4("--force", self.FORCE,
                                           answer=self.answered("send-back", "Decided", "Again."))
        self.assertEqual(code, 0, out + err)
        phase = self.board()["phases"][4]
        self.assertEqual(phase["status"], "active")
        self.assertEqual(phase["forced"], "started by hand past the order", "the send-back lost the hand's force")

    def test_force_on_a_hold_resumed_on_its_recorded_ask_changes_nothing(self):
        """A hold that already raised its Ask checks nothing on resume, so a
        force has nothing to go past: it is said on stderr and not recorded."""
        self.gate_4_ready()
        self.page()
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 401, {"error": "no"})
            code, out, err = self.finish(self.start_hold(self.desk_env(desk), n="4"))
        self.assertEqual(code, 5, out + err)
        before = self.board()["phases"][4]
        self.assertEqual(before["hold"], {"attempt": 1, "ask": "ask-1"})
        events_before = self.events_text()
        desk, code, out, err = self.hold_4("--force", self.FORCE, answer=self.answered("approve", "Decided"))
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.of(desk, "POST", "/api/asks"), [])
        self.assertIn("--force changes nothing", err)
        phase = self.board()["phases"][4]
        self.assertEqual(phase["status"], "done")
        self.assertFalse(phase.get("forced"))
        self.assertEqual(phase["hold"], {"attempt": 1, "ask": "ask-1"})
        self.assertNotIn(self.FORCE, self.events_text()[len(events_before):])


class HoldG6Case(HoldHelpers, ScratchRunCase):
    """ALYDAS-206, Gate 6. Guards for what a wrong hold survived: a second push,
    a check on a resumed Ask, a `--doc` file left unchecked, and a forced hold
    resumed without its Ask."""

    FORCE = "Sachit ruled in chat: hold on the local page"

    def counting_refusals(self):
        """Origin refuses every push and counts each attempt, one line per push."""
        self.pushes = os.path.join(self.tmp.name, "..", os.path.basename(self.tmp.name) + "-pushes")
        self.addCleanup(lambda: os.path.exists(self.pushes) and os.remove(self.pushes))
        hook = os.path.join(self.origin, "hooks", "pre-receive")
        with open(hook, "w") as f:
            f.write(f"#!/bin/sh\necho push >> '{self.pushes}'\necho 'scratch origin: refused' >&2\nexit 1\n")
        os.chmod(hook, 0o755)

    def push_count(self):
        if not os.path.exists(self.pushes):
            return 0
        with open(self.pushes) as f:
            return len(f.read().split())

    def test_hold_pushes_once_before_it_asks(self):
        """The plan's "pushes once": hold's own push, and not again at the blocked stamp."""
        self.phase_2_ready()
        self.counting_refusals()
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk), "--force", self.FORCE)
            self.reads_back(desk)
            self.assertEqual(self.push_count(), 1, "hold pushed more than once before it asked")
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Approved"))
            code, out, err = self.finish(proc)
        self.assertEqual(code, 0, out + err)

    def test_a_hold_resumed_on_its_recorded_ask_neither_pushes_nor_checks(self):
        """A recorded Ask raises nothing new: its resume reads the answer back,
        even when HEAD has since moved on and origin refuses the push."""
        self.phase_2_ready()
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 401, {"error": "no"})
            code, out, err = self.finish(self.start_hold(self.desk_env(desk)))
        self.assertEqual(code, 5, out + err)
        self.assertEqual(self.board()["phases"][2]["hold"], {"attempt": 1, "ask": "ask-1"})
        self.counting_refusals()
        self.commit("later.txt")
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            self.assertEqual(self.push_count(), 0, "the resumed hold pushed")
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Approved"))
            code, out, err = self.finish(proc)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.of(desk, "POST", "/api/asks"), [])
        self.assertEqual(self.check_events(), [])

    def test_a_file_recorded_only_by_doc_is_checked_too(self):
        """`note --doc` records a file the desk opens as well as `--produced` does."""
        self.phase_2_ready()
        doc = os.path.join(self.tmp.name, "docs", "specs", "loose.md")
        with open(doc, "w", encoding="utf-8") as f:
            f.write("# never committed\n")
        out = self.run_pipeline("note", "scratch", "2", "--doc", "docs/specs/loose.md|A loose doc")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual([x.get("note") for x in self.board()["phases"][2]["produced"]
                          if x["path"] == "docs/specs/loose.md"], [""], "a --doc entry carries no note")
        with self.asking_desk() as desk:
            code, out, err = self.bounded(self.start_hold(self.desk_env(desk)), desk)
        self.assertEqual(self.of(desk, "POST", "/api/asks"), [])
        self.assertEqual(code, 2, out + err)
        self.assertIn("docs/specs/loose.md: not committed", err)
        self.assertNotIn("docs/prototypes/x.html:", err)

    def test_a_forced_hold_resumed_without_its_ask_is_amber_on_the_board(self):
        """Blocked by a hold that recorded no Ask, resumed with --force on a
        refused push: the Ask is raised, and the force is recorded where the
        board draws it in amber (the note and the went-past line). The resume
        stamps no second blocked line, so that line is not forced. Gate 6 (P1):
        the amber is on the board while the hold waits, not only once it closes."""
        self.phase_2_ready()
        self.counting_refusals()
        run = self.board()
        run["phases"][2]["status"] = "blocked"
        run["phases"][2]["hold"] = {"attempt": 1}
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.json"), "w") as f:
            json.dump(run, f)
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk), "--force", self.FORCE)
            self.reads_back(desk)
            phase = self.board()["phases"][2]
            self.assertEqual((phase["status"], phase.get("forced"), phase["hold"].get("forced")),
                             ("blocked", self.FORCE, self.FORCE))
            with open(os.path.join(self.root, "boards", "2026-09-13-scratch.html"), encoding="utf-8") as f:
                waiting = parse_html(render_board_in_node(self, f.read())["panels"]["p2"])
            self.assertIn(f"Closed by force. {self.FORCE}",
                          [n.text() for n in waiting.by_class("note") if "bad" not in (n.attrs.get("class") or "").split()],
                          "the forced hold shows no amber while it waits")
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Approved"))
            code, out, err = self.finish(proc)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.of(desk, "POST", "/api/asks")), 1)
        went_past = [e for e in self.check_events() if e.get("phase") == 2]
        self.assertEqual([e.get("forced") for e in went_past], [self.FORCE])
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.html"), encoding="utf-8") as f:
            drawn = render_board_in_node(self, f.read())
        panel = parse_html(drawn["panels"]["p2"])
        amber = [n.text() for n in panel.by_class("note") if "bad" not in (n.attrs.get("class") or "").split()]
        self.assertIn(f"Closed by force. {self.FORCE}", amber)
        went_past_rows = [tr for tr in parse_html(drawn["log"]).find("tr")
                          if any(p.text() == "check failed" for p in tr.by_class("pill"))]
        self.assertEqual(len(went_past_rows), 1)
        self.assertIn("warn", (went_past_rows[0].by_class("pill")[0].attrs.get("class") or "").split())


class HoldsCase(HoldHelpers, ScratchRunCase):
    """ALYDAS-175, S5. Which phases wait for a person, set per run, and the
    two refusals that make the wait real: a holding phase is not closed by
    hand, and no later phase starts while one is unanswered."""

    def holds(self):
        return self.board().get("holds", "absent")

    def reinit(self, *extra):
        boards = os.path.join(self.root, "boards")
        if os.path.isdir(boards):
            for name in os.listdir(boards):
                os.remove(os.path.join(boards, name))
        return self.run_pipeline("init", "scratch", "--title", "Scratch", "--goal", "g",
                                 "--branch", "b", "--date", "2026-09-13", *extra)

    def test_init_holds_phases_2_and_8_by_default_and_takes_a_list_or_none(self):
        """ALYDAS-174, S6 (Ask 1126cfb8, "Hold at 2 and 8 by default"): a fresh
        run waits for a person at Phase 2 and at Gate 8, where the desk's
        review sheet is; `--hold 2` and `--hold none` still win."""
        for extra, want in (((), [2, 8]), (("--hold", "2"), [2]), (("--hold", "2,8"), [2, 8]),
                            (("--hold", "8, 2"), [2, 8]), (("--hold", "none"), []), (("--hold", "0,9"), [0, 9])):
            with self.subTest(extra=extra):
                out = self.reinit(*extra)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(self.holds(), want)

    def test_init_refuses_a_hold_that_is_not_phase_numbers(self):
        for bad in ("2,x", "12", "-1", "2,2", "", "2,,8", "None,2", "2.0"):
            with self.subTest(bad=bad):
                out = self.reinit("--hold", bad)
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                self.assertFalse(os.path.exists(os.path.join(self.root, "boards", "2026-09-13-scratch.json")),
                                 "no ledger is written")

    def test_a_ledger_written_before_holds_reads_as_holding_phase_2(self):
        """Declared: no `holds` key is the legacy default, [2], so a run begun
        before this change still waits at Phase 2 (in the refusal and on the
        beat). ALYDAS-174, S6: and only there; init's new default, [2, 8], is
        not read into a ledger that never said."""
        pipeline = import_pipeline()
        self.assertEqual(pipeline.run_holds({"slug": "old"}), [2])
        self.assertEqual(pipeline.run_holds({"slug": "old", "holds": "not a list"}), [2])
        self.phase_2_ready()
        path = os.path.join(self.root, "boards", "2026-09-13-scratch.json")
        run = self.board()
        del run["holds"]
        with open(path, "w") as f:
            json.dump(run, f)
        pipeline = import_pipeline()
        self.assertEqual(pipeline.run_holds(run), [2])
        out = self.run_pipeline("phase", "scratch", "2", "done")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        with Listener() as desk:
            self.assertEqual(self.run_pipeline("step", "scratch", "2", "1", "done",
                                               env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}).returncode, 0)
        self.assertEqual(desk.bodies()[-1]["holds"], [2])

    def test_phase_2_done_is_refused_on_a_holding_run(self):
        self.phase_2_ready()
        out = self.run_pipeline("phase", "scratch", "2", "done")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("hold scratch 2", out.stderr)
        self.assertEqual(self.board()["phases"][2]["status"], "active")
        # --force still closes it, recorded, as a forced close is today.
        out = self.run_pipeline("phase", "scratch", "2", "done", "--force", "Sachit approved in chat")
        self.assertEqual(out.returncode, 0, out.stderr)
        phase = self.board()["phases"][2]
        self.assertEqual(phase["status"], "done")
        self.assertEqual(phase["forced"], "Sachit approved in chat")

    def test_phase_2_done_is_allowed_on_a_run_that_holds_nothing(self):
        self.phase_2_ready(init=("--hold", "none"))
        out = self.run_pipeline("phase", "scratch", "2", "done")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.board()["phases"][2]["status"], "done")
        self.assertFalse(self.board()["phases"][2]["forced"])

    def test_only_a_holding_phase_refuses_done(self):
        """--hold 8: Phase 2 closes by hand, and Phase 8 would not."""
        self.phase_2_ready(init=("--hold", "8"))
        out = self.run_pipeline("phase", "scratch", "2", "done")
        self.assertEqual(out.returncode, 0, out.stderr)
        # Phase 8 passes its own checks, so the refusal is the hold's.
        measurements = os.path.join(self.root, "measurements")
        os.makedirs(measurements, exist_ok=True)
        with open(os.path.join(measurements, "2026-09-13-scratch-review.md"), "w") as f:
            f.write("# Review\n\nOne finding, ruled defer.\n")
        self.assertEqual(self.run_pipeline("note", "scratch", "8", "--plain", "The reviewers found one thing to do later.").returncode, 0)
        # ALYDAS-223, S1. Phase 8's checks now ask about the UI gate too; this run draws nothing.
        self.assertEqual(self.run_pipeline("ui-gate", "skip", "scratch", "--reason", "a scratch run, nothing is drawn").returncode, 0)
        self.record_a_review()  # ALYDAS-174, S1: Phase 8 needs its review recorded too
        out = self.run_pipeline("phase", "scratch", "8", "done")
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("hold scratch 8", out.stderr)

    def test_a_later_phase_waits_on_an_earlier_holding_one(self):
        """Phase 3 is refused while Phase 2 is running, never held, or blocked,
        and starts once Phase 2 is approved on the desk."""
        self.init_scratch()
        out = self.run_pipeline("phase", "scratch", "3", "start")
        self.assertEqual(out.returncode, 2, "Phase 2 never started: " + out.stdout + out.stderr)
        self.assertIn("Phase 2", out.stderr)
        self.assertIn("hold scratch 2", out.stderr)
        self.assertEqual(self.board()["phases"][3]["status"], "pending")
        self.phase_2_ready_again()
        out = self.run_pipeline("phase", "scratch", "3", "start")
        self.assertEqual(out.returncode, 2, "Phase 2 running: " + out.stdout + out.stderr)
        # Phase 4 is refused too: every later phase waits, not only the next.
        self.assertEqual(self.run_pipeline("phase", "scratch", "4", "start").returncode, 2)
        # An earlier phase is not held back by a later holding one.
        self.assertEqual(self.run_pipeline("phase", "scratch", "1", "start").returncode, 0)
        self.assertEqual(self.run_pipeline("phase", "scratch", "2", "start").returncode, 0)
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 401, {"error": "no"})
            code, out, err = self.finish(self.start_hold(self.desk_env(desk)))
        self.assertEqual(code, 5, out + err)
        self.assertEqual(self.board()["phases"][2]["status"], "blocked")
        out = self.run_pipeline("phase", "scratch", "3", "start")
        self.assertEqual(out.returncode, 2, "Phase 2 blocked: " + out.stdout + out.stderr)
        self.assertEqual(self.board()["phases"][3]["status"], "pending")
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Decided"))
            code, out, err = self.finish(proc)
        self.assertEqual(code, 0, out + err)
        out = self.run_pipeline("phase", "scratch", "3", "start")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.board()["phases"][3]["status"], "active")

    def test_a_skipped_holding_phase_does_not_hold_the_next_back(self):
        """ALYDAS-174, Gate 8 W-3: a holding phase is skipped only with
        `--force "<reason>"`, recorded; once skipped, it holds nothing back."""
        self.init_scratch()
        out = self.run_pipeline("phase", "scratch", "2", "skipped", "--note", "nothing to show")
        self.assertEqual(out.returncode, 2, "a note alone does not skip a hold: " + out.stdout + out.stderr)
        self.assertIn("phase 2 waits for your review on the desk, so it is not skipped by hand", out.stderr)
        self.assertEqual(self.board()["phases"][2]["status"], "pending")
        self.assertEqual(self.run_pipeline("phase", "scratch", "2", "skipped", "--note", "nothing to show",
                                           "--force", "Sachit ruled no prototype").returncode, 0)
        self.assertEqual(self.board()["phases"][2].get("forced"), "Sachit ruled no prototype")
        out = self.run_pipeline("phase", "scratch", "3", "start")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_force_starts_a_later_phase_anyway_and_says_why(self):
        self.init_scratch()
        out = self.run_pipeline("phase", "scratch", "3", "start", "--force", "Sachit said go on")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.board()["phases"][3]["status"], "active")
        self.assertIn("Sachit said go on", self.events_text())

    def test_a_run_that_holds_nothing_starts_any_phase(self):
        self.init_scratch("--hold", "none")
        out = self.run_pipeline("phase", "scratch", "3", "start")
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_set_holds_round_trips_and_refuses_what_is_not_a_phase(self):
        self.init_scratch()
        for value, want in (("2,8", [2, 8]), ("none", []), ("8", [8]), ("2", [2])):
            with self.subTest(value=value):
                out = self.run_pipeline("set", "scratch", "holds", value)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(self.holds(), want)
        for bad in ("2,x", "12", "2,2", "", "-1"):
            with self.subTest(bad=bad):
                out = self.run_pipeline("set", "scratch", "holds", bad)
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                self.assertEqual(self.holds(), [2], "a refused value changes nothing")
        # Other list keys stay refused, as before.
        out = self.run_pipeline("set", "scratch", "phases", "2")
        self.assertNotEqual(out.returncode, 0)

    def test_set_holds_is_what_the_refusals_read(self):
        """`set holds none` lifts the wait; `set holds 2` puts it back."""
        self.phase_2_ready()
        self.assertEqual(self.run_pipeline("set", "scratch", "holds", "none").returncode, 0)
        self.assertEqual(self.run_pipeline("phase", "scratch", "3", "start").returncode, 0)

    def test_every_beat_sends_holds(self):
        for extra, want in (((), [2, 8]), (("--hold", "2,8"), [2, 8]), (("--hold", "none"), [])):
            with self.subTest(extra=extra):
                self.assertEqual(self.reinit(*extra).returncode, 0)
                with Listener() as desk:
                    env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
                    for args in (("phase", "scratch", "0", "start"), ("step", "scratch", "0", "1", "doing"),
                                 ("log", "scratch", "a line")):
                        self.assertEqual(self.run_pipeline(*args, env=env).returncode, 0)
                bodies = [b for b in desk.bodies()]
                self.assertEqual(len(bodies), 3)
                for body in bodies:
                    self.assertEqual(body["holds"], want)
                    self.assertTrue(all(type(h) is int for h in body["holds"]))



# ---------- ALYDAS-175, Gate 6: the residuals the breaks left green ----------

class HoldBoundsCase(unittest.TestCase):
    """The Ask's question and detail within the desk's bounds whatever the
    ledger holds, and `run_holds` reading only phase numbers."""

    def test_a_long_question_is_cut_to_the_desk_s_bound(self):
        P = import_pipeline()
        run, p = {"slug": "scratch", "title": "T"}, {"n": 2, "name": "Prototype", "kind": "phase"}
        for given in ("q" * 500, None):
            question = P.hold_question(dict(run, title="t" * 500), p, given)
            self.assertLessEqual(len(question), 200)
            self.assertTrue(question.strip())

    def test_the_detail_is_cut_cleaned_and_names_what_the_choices_left_out(self):
        P = import_pipeline()
        p = {"lede": "A lede‮ with a bidi mark\x07.", "plain": "Plain words.\n\nA second paragraph. " + "x" * 3000}
        detail = P.hold_detail(p, ["other 7", "other 8"])
        self.assertLessEqual(len(detail), 2000)
        self.assertNotRegex(detail, "[\x00-\x08\x0b-\x1f\x7f‪-‮⁦-⁩]")
        self.assertIn("A lede with a bidi mark.", detail)
        self.assertIn("\n", detail, "newlines are kept")
        short = P.hold_detail({"lede": "L.", "plain": "P."}, ["other 7", "other 8"])
        self.assertIn("other 7", short)
        self.assertIn("other 8", short)
        self.assertNotIn("Also weighed", P.hold_detail({"lede": "L.", "plain": "P."}, []))

    def test_run_holds_reads_only_phase_numbers(self):
        P = import_pipeline()
        self.assertEqual(P.run_holds({"holds": [True, 2, 10, -1, "3", 2.0]}), [2], "a bool is not phase 1")
        for broken in ("2", {"2": 1}, None, 2):
            with self.subTest(holds=broken):
                self.assertEqual(P.run_holds({"holds": broken}), [2], "a holds that is not a list reads as the default")
        self.assertEqual(P.run_holds({"holds": []}), [])

    def test_the_phase_range_is_the_ledger_s_and_the_holds_stay_inside_it(self):
        """ALYDAS-303, S9 (c6). The beat sends the phases the run's arc has, the
        lowest and highest phase number on the ledger, and the holds within it;
        the skill's own arc when the ledger has no phase numbers."""
        P = import_pipeline()
        arc = lambda first, last: [{"n": n} for n in range(first, last + 1)]
        self.assertEqual(P.run_phase_range({"phases": arc(0, 9)}), [0, 9])
        self.assertEqual(P.run_phase_range({"phases": [{"n": 3}, {"n": True}, {"n": "8"}, {"n": 7}, {"n": 120},
                                                       {"n": -1}, "x"]}), [3, 7])
        for empty in ({}, {"phases": []}, {"phases": "x"}, {"phases": [{"n": True}]}):
            with self.subTest(run=empty):
                self.assertEqual(P.run_phase_range(empty), [0, 9])
        self.assertEqual(P.run_holds({"phases": arc(3, 7), "holds": [2, 3, 7, 8]}), [3, 7])
        self.assertEqual(P.run_holds({"phases": arc(0, 12), "holds": [12, 2]}), [2, 12])
        self.assertEqual(P.run_holds({"phases": arc(0, 9), "holds": [2, 10]}), [2])


class HoldCadenceCase(HoldHelpers, ScratchRunCase):
    """An hour of `hold` on a virtual clock, at the real intervals (no
    PIPELINE_HOLD_INTERVAL), against the desk's own numbers: `/api/asks` is
    2,700 an hour per token for up to twenty holding runs (plan, Gate 4 pacing:
    read back every 30 s, an answer lands inside a minute); beats are 4,800 an
    hour per token for up to twenty runs (runs' `route-limits.ts`
    HEARTBEAT_LIMIT, `heartbeat.ts` RUNS_PER_TOKEN, both raised by ALYDAS-127's
    Ask 09d47185), and the beat stays at 20 s."""

    def test_an_hour_of_waiting_keeps_to_the_desk_s_budgets(self):
        from unittest import mock
        import contextlib, io
        self.phase_2_ready()
        P = import_pipeline()
        clock = [1000.0]
        reads, beats, timeouts = [], [], []
        answered_at = 3600.0

        def request(url, token, method, route, body=None, timeout=None):
            timeouts.append(timeout)
            if method == "POST":
                return json.dumps({"ask": dict(self.ASK)}).encode()
            reads.append(clock[0] - 1000.0)
            if clock[0] - 1000.0 >= answered_at:
                return json.dumps(self.answered("approve", "Decided")).encode()
            return json.dumps({"ask": dict(self.ASK)}).encode()

        def beat(*args, **kwargs):
            beats.append(clock[0] - 1000.0)

        env = {k: v for k, v in os.environ.items() if k != "PIPELINE_HOLD_INTERVAL"}
        # ALYDAS-206, S1. In process, so run in the temp repository and never
        # the skill's own: hold reads the repository before it asks. The
        # branch is pushed here, as hold's own push (patched out below) would.
        here = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, here)
        self.assertEqual(os.path.realpath(P.repo_root()), os.path.realpath(self.tmp.name))
        self.git("push", "-q", "origin", "HEAD:refs/heads/feature/alydas-135-s6")
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(P.time, "monotonic", lambda: clock[0]), \
                mock.patch.object(P.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + max(0.0, s))), \
                mock.patch.object(P, "desk_target", lambda: ("http://127.0.0.1:9", "t0k3n")), \
                mock.patch.object(P, "desk_request", request), \
                mock.patch.object(P, "heartbeat", beat), \
                mock.patch.object(P, "push_branch", lambda *a, **k: None), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            P.cmd_hold(argparse.Namespace(slug="scratch", n=2, root=self.root, question=None, force=None))
        self.assertEqual(self.board()["phases"][2]["status"], "done")

        hour = [t for t in reads if t < 3600]
        self.assertLessEqual(len(hour), 2700 // 20, f"{len(hour)} read-backs an hour: twenty runs overrun the 2,700")
        gaps = [b - a for a, b in zip([0.0] + reads, reads)]
        self.assertLessEqual(max(gaps), 60, "an answer lands inside a minute")
        self.assertLessEqual(reads[-1] - answered_at, 60)
        waiting = [t for t in beats if t < 3600]
        self.assertLessEqual(len(waiting), 4800 // 20, f"{len(waiting)} beats an hour: twenty runs overrun the 4,800")
        self.assertLessEqual(max(b - a for a, b in zip([0.0] + waiting, waiting)), 20, "the beat stays at 20 s")
        self.assertTrue(timeouts and all(t is not None and t < 20 for t in timeouts),
                        f"a request that hangs must not swallow a beat: {sorted(set(timeouts))}")


class HoldGateSixCase(HoldHelpers, ScratchRunCase):
    """Refusals and re-holds the breaks got past."""

    a_desk_that_would_raise_another = HoldCase.a_desk_that_would_raise_another

    def test_every_later_phase_waits_on_every_holding_phase(self):
        """--hold 2,8: while Phase 2 is unanswered every phase from 3 to 9 is
        refused, not only the next two; a failed Phase 2 is not an answer;
        once Phase 2 is skipped, 3 to 8 start and 9 still waits on Phase 8,
        and Phase 8 does not close by hand."""
        self.phase_2_ready(init=("--hold", "2,8"))
        for n in range(3, 10):
            with self.subTest(phase=n, phase_2="active"):
                out = self.run_pipeline("phase", "scratch", str(n), "start")
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                self.assertIn("hold scratch 2", out.stderr)
        self.assertEqual(self.run_pipeline("phase", "scratch", "2", "failed", "--note", "the page broke").returncode, 0)
        out = self.run_pipeline("phase", "scratch", "3", "start")
        self.assertEqual(out.returncode, 2, "a failed hold is not answered: " + out.stdout + out.stderr)
        self.assertEqual(self.run_pipeline("phase", "scratch", "2", "skipped", "--note", "nothing to show",
                                           "--force", "the person ruled it skipped").returncode, 0)
        for n in range(3, 9):
            with self.subTest(phase=n, phase_2="skipped"):
                out = self.run_pipeline("phase", "scratch", str(n), "start")
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        out = self.run_pipeline("phase", "scratch", "9", "start")
        self.assertEqual(out.returncode, 2, "Phase 8 still waits: " + out.stdout + out.stderr)
        self.assertIn("hold scratch 8", out.stderr)
        measurements = os.path.join(self.root, "measurements")
        os.makedirs(measurements, exist_ok=True)
        with open(os.path.join(measurements, "2026-09-13-scratch-review.md"), "w") as f:
            f.write("# Review\n\nOne finding, ruled defer.\n")
        self.assertEqual(self.run_pipeline("note", "scratch", "8", "--plain", "The reviewers found one thing to do later.").returncode, 0)
        # ALYDAS-223, S1. Phase 8's checks now ask about the UI gate too; this run draws nothing.
        self.assertEqual(self.run_pipeline("ui-gate", "skip", "scratch", "--reason", "a scratch run, nothing is drawn").returncode, 0)
        self.record_a_review()  # ALYDAS-174, S1: Phase 8 needs its review recorded too
        self.tick_steps(8)  # ALYDAS-225, K1: so the hold is what refuses the hand close, not a step
        out = self.run_pipeline("phase", "scratch", "8", "done")
        self.assertEqual(out.returncode, 2, "the second hold refuses a hand close: " + out.stdout + out.stderr)
        self.assertIn("hold scratch 8", out.stderr)

    def blocked_on_ask_1(self):
        """Phase 2 blocked by a hold whose read-back the desk refused, ask-1 recorded."""
        self.phase_2_ready()
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 401, {"error": "no"})
            self.assertEqual(self.finish(self.start_hold(self.desk_env(desk)))[0], 5)
        self.assertEqual(self.board()["phases"][2]["hold"], {"attempt": 1, "ask": "ask-1"})

    def test_a_stop_on_the_resuming_beat_wins_over_an_answer_read_after_it(self):
        """A person pressed Stop while nothing waited, and the Ask was answered
        too. The re-hold's first beat hears the Stop: exit 3, the phase stays
        blocked, and the answer is not acted on."""
        self.blocked_on_ask_1()
        with self.a_desk_that_would_raise_another() as desk:
            desk.answer("POST", "/api/sessions/heartbeat", 200, {"session": {"id": "s1"}, "directive": {"kind": "stop"}})
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Decided"))
            code, out, err = self.finish(self.start_hold(self.desk_env(desk)))
        self.assertEqual(code, 3, out + err)
        self.assertEqual(self.board()["phases"][2]["status"], "blocked")
        self.assertNotIn("pipeline: answered:", out)

    def test_a_phase_restarted_by_hand_reads_its_recorded_ask_first(self):
        """S7 deviation 5, untested until now: a phase started again by hand
        with an Ask still recorded reads that Ask back rather than raising."""
        self.blocked_on_ask_1()
        self.assertEqual(self.run_pipeline("phase", "scratch", "2", "start").returncode, 0)
        with self.a_desk_that_would_raise_another() as desk:
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Decided"))
            code, out, err = self.finish(self.start_hold(self.desk_env(desk)))
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.of(desk, "POST", "/api/asks"), [], "no second Ask is raised")
        self.assertEqual({r["path"] for r in self.of(desk, "GET", "/api/asks")}, {"/api/asks?id=ask-1&run=scratch"})
        self.assertEqual(self.board()["phases"][2]["status"], "done")

    def test_an_answer_the_hold_did_not_offer_leaves_the_phase_blocked(self):
        """`hold_answered`'s own rule: an answer that is not Approve, a variant
        or a send-back is reported and closes nothing."""
        self.phase_2_ready()
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            desk.answer("GET", "/api/asks", 200, self.answered("maybe", "Maybe"))
            code, out, err = self.finish(proc)
        self.assertIn("pipeline: answered: maybe", out)
        self.assertEqual(self.board()["phases"][2]["status"], "blocked")


class HoldGateEightCase(HoldHelpers, ScratchRunCase):
    """Gate 8 of ALYDAS-175: the fixes the reviewers asked for on the skill side."""

    # ST-5. The one route that closes a holding phase without the desk: the
    # person is asked in chat and their ruling is the force reason.
    CHAT_CLOSE = 'phase scratch 2 done --force "<their ruling>"'

    def close_as_told(self, said):
        """The command the output names, run with a ruling in place of the
        placeholder: it must close the phase and record the ruling."""
        self.assertIn(self.CHAT_CLOSE, said, "the output names the command that works")
        self.assertIsNone(re.search(r"phase scratch 2 done(?! --force)", said),
                          "no plain `phase done`, which a holding phase refuses: " + said)
        out = self.run_pipeline("phase", "scratch", "2", "done", "--force", "Sachit in chat: build B.")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        phase = self.board()["phases"][2]
        self.assertEqual(phase["status"], "done")
        self.assertEqual(phase["forced"], "Sachit in chat: build B.")

    def test_every_way_a_hold_falls_back_to_chat_names_the_close_that_works(self):
        for path in ("no desk", "raise refused", "read-back refused"):
            with self.subTest(path):
                self.phase_2_ready_again()
                if path == "no desk":
                    code, out, err = self.finish(self.start_hold({"PIPELINE_HOLD_INTERVAL": self.INTERVAL}))
                    self.assertEqual(code, 4, out + err)
                else:
                    with self.asking_desk() as desk:
                        if path == "raise refused":
                            desk.answer("POST", "/api/asks", 400, {"error": "no"})
                        else:
                            desk.answer("GET", "/api/asks", 403, {"error": "no"})
                        code, out, err = self.finish(self.start_hold(self.desk_env(desk)))
                    self.assertEqual(code, 4 if path == "raise refused" else 5, out + err)
                self.close_as_told(out + err)

    def test_the_skill_s_docs_name_the_chat_close_and_never_plain_done_for_a_hold(self):
        skill = os.path.dirname(HERE)
        with open(os.path.join(skill, "references", "phases.md"), encoding="utf-8") as f:
            phases = f.read()
        self.assertNotIn("with a desk set", phases, "phase 2 done is refused with or without a desk")
        self.assertIn('phase <slug> 2 done --force "<their ruling>"', phases)
        with open(os.path.join(skill, "SKILL.md"), encoding="utf-8") as f:
            waits = f.read().split("## How a run waits", 1)[1].split("\n## ", 1)[0]
        for code in ("**4**", "**5**"):
            bullet = waits.split(f"- {code}", 1)[1].split("\n- ", 1)[0]
            with self.subTest(exit=code):
                self.assertIn('phase <slug> <n> done --force "<their ruling>"', " ".join(bullet.split()))


    # C-2. The note is up to 1,000 characters (the desk's NOTE_MAX) and a
    # choice's label up to 200; the desk's run log and scene take both whole.
    LONG_NOTE = ("Keep A's header, but move the preview up. " * 30)[:988] + " END-OF-NOTE"

    def long_label_ready(self):
        """Phase 2 ready, the chosen option's name long enough that Approve's
        label is the desk's 200."""
        self.phase_2_ready()
        path = os.path.join(self.root, "boards", "2026-09-13-scratch.json")
        run = self.board()
        for option in run["phases"][2]["options"]:
            if option.get("chosen"):
                option["name"] = "B, the layout with the preview on top " + "x" * 200
        with open(path, "w") as f:
            json.dump(run, f)

    def held_answer(self, answer, note):
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            desk.answer("GET", "/api/asks", 200, self.answered(answer, "Decided", note))
            code, out, err = self.finish(proc)
            self.assertEqual(code, 0, out + err)
            label = json.loads(self.of(desk, "POST", "/api/asks")[0]["raw"])["choices"][0]["label"]
            return label, json.loads(self.of(desk, "POST", "/api/sessions/heartbeat")[-1]["raw"])["ledger"]

    def test_a_long_label_and_a_full_note_reach_the_desk_s_run_log_whole(self):
        self.assertEqual(len(self.LONG_NOTE), 1000)
        self.long_label_ready()
        label, ledger = self.held_answer("approve", self.LONG_NOTE)
        self.assertEqual(len(label), 200)
        decisions = [e["text"] for e in ledger["log"] if e["kind"] == "decision"]
        self.assertEqual(decisions, [f"Decision: Answered on the desk: {label}. Note: {self.LONG_NOTE}"])

    def test_a_send_back_s_full_note_reaches_the_desk_s_scene_whole(self):
        self.phase_2_ready()
        _, ledger = self.held_answer("send-back", self.LONG_NOTE)
        phase = next(p for p in ledger["phases"] if p["n"] == 2)
        self.assertEqual(phase["story"]["scene"], "Sent back: " + self.LONG_NOTE)


    # S-1. The desk's `/api/asks` is 300 an hour per token, shared by every
    # run on it. A 429 says how long to wait (Retry-After); the hold waits that
    # long, beating all the while so a Stop still arrives, and asks again.
    BUSY = {"error": "That is 300 requests to /api/asks from this credential in 60 minutes."}

    def beats_between(self, desk, start, end):
        return [r for r in self.of(desk, "POST", "/api/sessions/heartbeat") if start < r["at"] < end]

    def test_a_429_on_the_read_back_waits_its_retry_after_and_keeps_beating(self):
        self.phase_2_ready()
        with self.asking_desk() as desk:
            desk.answer("GET", "/api/asks", 429, self.BUSY, {"Retry-After": "2"})
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Decided"))
            code, out, err = self.finish(proc)
            reads = self.of(desk, "GET", "/api/asks")
            waited = reads[1]["at"] - reads[0]["at"]
            beats = self.beats_between(desk, reads[0]["at"], reads[1]["at"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(reads), 2, "one read refused, the next one answered")
        self.assertGreaterEqual(waited, 1.9, "the read waited the desk's Retry-After, not the 0.2 s read interval")
        self.assertGreaterEqual(len(beats), 3, "it beat while it waited, so a Stop still arrives")
        self.assertEqual(self.board()["phases"][2]["status"], "done")

    def test_a_429_on_the_raise_waits_its_retry_after_and_asks_again(self):
        self.phase_2_ready()
        with self.asking_desk() as desk:
            desk.answer("POST", "/api/asks", 429, self.BUSY, {"Retry-After": "2"})
            proc = self.start_hold(self.desk_env(desk))
            self.wait_for("the refused raise", lambda: self.of(desk, "POST", "/api/asks"))
            desk.answer("POST", "/api/asks", 201, {"ask": dict(self.ASK)})
            self.reads_back(desk)
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Decided"))
            code, out, err = self.finish(proc)
            raised = self.of(desk, "POST", "/api/asks")
            beats = self.beats_between(desk, raised[0]["at"], raised[-1]["at"])
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(raised), 2, "the refused raise is sent again")
        self.assertEqual({json.loads(r["raw"])["fingerprint"] for r in raised}, {"hold:2:1"})
        self.assertGreaterEqual(raised[1]["at"] - raised[0]["at"], 1.9)
        self.assertGreaterEqual(len(beats), 3, "it beat while it waited")
        self.assertEqual(self.board()["phases"][2]["hold"], {"attempt": 1, "ask": "ask-1"})
        self.assertEqual(self.board()["phases"][2]["status"], "done")

    def test_a_stop_while_a_429_is_waited_out_still_stops(self):
        self.phase_2_ready()
        with self.asking_desk() as desk:
            desk.answer("POST", "/api/asks", 429, self.BUSY, {"Retry-After": "600"})
            proc = self.start_hold(self.desk_env(desk))
            self.wait_for("the refused raise", lambda: self.of(desk, "POST", "/api/asks"))
            desk.answer("POST", "/api/sessions/heartbeat", 200, {"session": {"id": "s1"}, "directive": {"kind": "stop"}})
            code, out, err = self.finish(proc)
        self.assertEqual(code, 3, out + err)
        self.assertEqual(len(self.of(desk, "POST", "/api/asks")), 1)

    def test_retry_after_is_read_in_seconds_and_bounded(self):
        P = import_pipeline()
        cases = {"2": 2.0, "0": 5.0, "-4": 5.0, "soon": 5.0, None: 5.0, "3447": 3447.0,
                 "86400": P.HOLD_RETRY_MAX_S, "Wed, 21 Oct 2026 07:28:00 GMT": 5.0}
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(P.retry_after({} if raw is None else {"Retry-After": raw}, 5.0), want)
        self.assertLessEqual(P.HOLD_RETRY_MAX_S, 3600, "never past the desk's hourly window")


    # D1 (the skill's side). A run stopped while a phase waited is over, so
    # the phase no longer says it waits: it says it was stopped, on the local
    # board and in the ended beat the desk hears last.
    STOPPED_WAITING = "Stopped from the desk while it waited on your review."

    def test_a_stop_while_holding_says_so_on_the_phase(self):
        self.phase_2_ready()
        with self.asking_desk() as desk:
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            desk.answer("POST", "/api/sessions/heartbeat", 200, {"session": {"id": "s1"}, "directive": {"kind": "stop"}})
            code, out, err = self.finish(proc)
            ended = json.loads(self.of(desk, "POST", "/api/sessions/heartbeat")[-1]["raw"])
        self.assertEqual(code, 3, out + err)
        self.assertIs(ended["ended"], True)
        self.assertEqual(self.board()["phases"][2]["scene"], self.STOPPED_WAITING)
        self.assertEqual(next(p for p in ended["ledger"]["phases"] if p["n"] == 2)["story"]["scene"], self.STOPPED_WAITING)
        with open(os.path.join(self.root, "boards", "2026-09-13-scratch.html"), encoding="utf-8") as f:
            self.assertIn(self.STOPPED_WAITING, f.read())

    def test_a_stop_on_a_phase_that_is_not_held_leaves_its_scene_alone(self):
        """Control: only a held, blocked phase is restamped; a running one keeps its scene."""
        self.init_scratch()
        self.assertEqual(self.run_pipeline("note", "scratch", "0", "--scene", "Framing it.").returncode, 0)
        with Listener(reply={"session": {"id": "s1"}, "directive": {"kind": "stop"}}) as desk:
            out = self.run_pipeline("phase", "scratch", "0", "start", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertEqual(self.board()["phases"][0]["scene"], "Framing it.")



class BudgetCase(HoldHelpers, ScratchRunCase):
    """ALYDAS-17, S5. The run states a budget on its beats, and a budget stop
    from the desk is a wait, not an end."""

    BEAT = "/api/sessions/heartbeat"

    def beats(self, desk):
        return [json.loads(r["raw"]) for r in self.of(desk, "POST", self.BEAT)]

    def test_init_budget_is_sent_on_every_beat_and_no_budget_sends_no_key(self):
        for name, extra, expected in (("a budget", ("--budget", "5"), 5), ("a fraction", ("--budget", "2.5"), 2.5),
                                      ("none", (), None)):
            with self.subTest(name):
                self.setUp()
                self.init_scratch(*extra)
                with Listener() as desk:
                    env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
                    for args in (("phase", "scratch", "0", "start"), ("note", "scratch", "0", "--scene", "x"),
                                 ("log", "scratch", "a line")):
                        out = self.run_pipeline(*args, env=env)
                        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
                    bodies = self.beats(desk)
                self.assertEqual(len(bodies), 3)
                for body in bodies:
                    if expected is None:
                        self.assertNotIn("budgetUsd", body, "no budget is no key, never null")
                    else:
                        self.assertEqual(body["budgetUsd"], expected)
                        self.assertIsInstance(body["budgetUsd"], (int, float))

    BAD = ("0", "-1", "abc", "nan", "inf", "-inf", "", "1e9", "5 dollars")

    def test_init_refuses_a_budget_that_is_not_a_positive_amount_within_the_desks_bound(self):
        for value in self.BAD:
            with self.subTest(value=value):
                out = self.run_pipeline("init", "scratch", "--title", "S", "--goal", "g", "--date", "2026-09-13",
                                        "--branch", "b", "--budget=" + value)
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                # The refusal's own words, not argparse's "unrecognized arguments".
                self.assertIn("argument --budget:", out.stderr)
                self.assertIn("at least $0.01", out.stderr)
                self.assertFalse(os.path.exists(os.path.join(self.root, "boards", "2026-09-13-scratch.json")))

    UNDER_A_CENT = ("1e-7", "0.00004", "0.009")

    def test_a_budget_under_a_cent_is_refused_and_a_cent_is_a_budget(self):
        """Gate 8, N1 / SEC-1. The desk stores a budget as numeric(12,4), so a
        budget under $0.00005 lands as $0 and strands the run: Approve and Deny
        both do nothing. The floor is the desk's, $0.01, on every way in."""
        for value in self.UNDER_A_CENT:
            with self.subTest(command="init", value=value):
                out = self.run_pipeline("init", "scratch", "--title", "S", "--goal", "g", "--date", "2026-09-13",
                                        "--branch", "b", "--budget=" + value)
                self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                self.assertIn("argument --budget:", out.stderr)
                self.assertIn("at least $0.01", out.stderr)
                self.assertFalse(os.path.exists(os.path.join(self.root, "boards", "2026-09-13-scratch.json")))
        self.init_scratch("--budget", "0.01")
        self.assertEqual(self.board()["budget_usd"], 0.01, "a cent is a budget")
        for key in ("budget", "budget_usd"):
            for value in self.UNDER_A_CENT:
                with self.subTest(command="set", key=key, value=value):
                    out = self.run_pipeline("set", "scratch", key, value)
                    self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                    self.assertIn("at least $0.01", out.stderr)
                    self.assertEqual(self.board()["budget_usd"], 0.01, "a refused budget changes nothing")
        out = self.run_pipeline("set", "scratch", "budget", "5")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        out = self.run_pipeline("set", "scratch", "budget", "0.01")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.board()["budget_usd"], 0.01)

    def test_set_budget_sets_and_changes_it_and_refuses_what_is_not_an_amount(self):
        self.init_scratch()
        self.assertNotIn("budget_usd", self.board())
        out = self.run_pipeline("set", "scratch", "budget", "5")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.board()["budget_usd"], 5)
        out = self.run_pipeline("set", "scratch", "budget", "7.5")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.board()["budget_usd"], 7.5)
        self.assertIn("budget", self.events_text())
        for key in ("budget", "budget_usd"):
            for value in self.BAD:
                with self.subTest(key=key, value=value):
                    # `--` so argparse reads "-1" and "-inf" as the value, not as a flag.
                    out = self.run_pipeline("set", "scratch", key, "--", value)
                    self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
                    self.assertIn("at least $0.01", out.stderr)
                    self.assertEqual(self.board()["budget_usd"], 7.5, "a refused budget changes nothing")
        with Listener() as desk:
            out = self.run_pipeline("log", "scratch", "a line", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertEqual(self.beats(desk)[0]["budgetUsd"], 7.5)


    BUDGET_STOP = {"session": {"id": "s1"}, "directive": {"kind": "stop", "reason": "budget", "spentUsd": 5.12, "budgetUsd": 5}}
    OVER = "pipeline: over budget: spent $5.12 of $5.00."

    def test_a_note_answered_with_a_budget_stop_lands_then_exits_6_and_does_not_end_the_run(self):
        self.init_scratch("--budget", "5")
        with Listener(reply=self.BUDGET_STOP) as desk:
            env = {URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}
            out = self.run_pipeline("note", "scratch", "0", "--scene", "Framing it.", env=env)
            bodies = self.beats(desk)
        self.assertEqual(out.returncode, 6, out.stdout + out.stderr)
        self.assertEqual(self.board()["phases"][0]["scene"], "Framing it.", "the note landed before the exit")
        self.assertEqual(len(bodies), 1, "one beat, the note's: no ended beat follows")
        self.assertFalse(any(b["ended"] for b in bodies))
        self.assertIn(self.OVER, out.stdout)
        self.assertIn('"wait scratch"', out.stdout, "it names the command to run in the background")
        self.assertNotIn("the desk says stop", out.stdout)
        events = [json.loads(line) for line in self.events_text().splitlines()]
        escalated = [e for e in events if e.get("event") == "escalated"]
        self.assertEqual(len(escalated), 1, escalated)
        self.assertIn("$5.12", escalated[0]["note"])
        self.assertIn("$5.00", escalated[0]["note"])
        self.assertNotIn("stopped from the desk", self.events_text())

    def test_every_boundary_command_exits_6_on_a_budget_stop_after_its_write(self):
        self.init_scratch("--budget", "5", "--hold", "none")
        for args, landed in ((("phase", "scratch", "0", "start"), lambda b: b["phases"][0]["status"] == "active"),
                             (("step", "scratch", "0", "1", "doing"), lambda b: b["phases"][0]["steps"][0]["state"] == "doing"),
                             (("story", "scratch", "--stands", "Framing."), lambda b: b["story"]["stands"] == "Framing."),
                             (("log", "scratch", "a line"), lambda b: "a line" in self.events_text()),
                             (("phase", "scratch", "0", "done", "--force", "scratch"), lambda b: b["phases"][0]["status"] == "done")):
            with self.subTest(command=args[0]), Listener(reply=self.BUDGET_STOP) as desk:
                out = self.run_pipeline(*args, env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
                self.assertEqual(out.returncode, 6, out.stdout + out.stderr)
                self.assertTrue(landed(self.board()), "the write landed")
                self.assertIn(self.OVER, out.stdout)
                self.assertFalse(any(b["ended"] for b in self.beats(desk)))

    def test_a_budget_stop_on_the_beat_that_ended_the_run_changes_nothing(self):
        self.init_scratch("--budget", "5")
        with Listener(reply=self.BUDGET_STOP) as desk:
            out = self.run_pipeline("phase", "scratch", "9", "done", "--force", "a scratch run",
                                    env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            self.assertEqual(len(desk.requests), 1)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertNotIn("over budget", self.events_text())

    def assert_plain_stop_words(self, out):
        """Gate 8, ST-1 / correctness 4. The desk sends the same plain stop for
        Stop on the Now card and for Deny on the budget Ask, so the line cannot
        say which: it names both and blames neither."""
        line = next((l for l in out.splitlines() if "the desk says stop" in l), "")
        self.assertTrue(line.startswith("pipeline: the desk says stop."), out)
        self.assertNotIn("pressed Stop", line, "a Deny is not a press of Stop")
        self.assertIn("stopped this run, or declined to extend its budget", line)
        self.assertIn("stop working on it, and do not start the next phase.", line)

    def test_a_plain_stop_still_ends_the_run_and_exits_3_on_a_budgeted_run(self):
        self.init_scratch("--budget", "5")
        with Listener(reply={"session": {"id": "s1"}, "directive": {"kind": "stop"}}) as desk:
            out = self.run_pipeline("note", "scratch", "0", "--scene", "x", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
            bodies = self.beats(desk)
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assert_plain_stop_words(out.stdout)
        self.assertEqual([b["ended"] for b in bodies], [False, True])
        self.assertIn("stopped from the desk", self.events_text())


    # ---------- inside hold and wait, a budget stop is a wait ----------

    def escalated_over_budget(self):
        return [e for e in (json.loads(line) for line in self.events_text().splitlines())
                if e.get("event") == "escalated" and "over budget" in e.get("note", "")]

    def beats_after(self, desk, count):
        self.wait_for(f"{count} more beats", lambda: len(self.of(desk, "POST", self.BEAT)) >= count)

    def test_hold_answered_with_a_budget_stop_keeps_holding_and_exits_0_on_its_answer(self):
        self.phase_2_ready(init=("--budget", "5"))
        with self.asking_desk() as desk:
            desk.answer("POST", self.BEAT, 200, self.BUDGET_STOP)
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            self.beats_after(desk, len(self.of(desk, "POST", self.BEAT)) + 3)
            self.assertIsNone(proc.poll(), "a budget stop does not end the hold")
            desk.answer("GET", "/api/asks", 200, self.answered("approve", "Decided"))
            code, out, err = self.finish(proc)
            bodies = self.beats(desk)
        self.assertEqual(code, 0, out + err)
        self.assertIn("pipeline: answered: approve", out)
        self.assertEqual(self.board()["phases"][2]["status"], "done")
        self.assertFalse(any(b["ended"] for b in bodies), "nothing ended the run")
        self.assertNotIn("stopped from the desk", self.events_text())
        self.assertEqual(len(self.escalated_over_budget()), 1, "logged once, not once a beat")
        self.assertGreaterEqual(len(bodies), 5)

    def test_hold_still_exits_3_on_a_plain_stop_after_a_budget_stop(self):
        self.phase_2_ready(init=("--budget", "5"))
        with self.asking_desk() as desk:
            desk.answer("POST", self.BEAT, 200, self.BUDGET_STOP)
            proc = self.start_hold(self.desk_env(desk))
            self.reads_back(desk)
            desk.answer("POST", self.BEAT, 200, {"session": {"id": "s1"}, "directive": {"kind": "stop"}})
            code, out, err = self.finish(proc)
            bodies = self.beats(desk)
        self.assertEqual(code, 3, out + err)
        self.assertIs(bodies[-1]["ended"], True)
        self.assertIn("stopped from the desk", self.events_text())

    def start_wait(self, env, slug="scratch"):
        environment = dict(os.environ)
        for name in (URL_VARIABLE, TOKEN_VARIABLE, WORK_ITEM_VARIABLE, "CLAUDE_CODE_SESSION_ID") + PROXY_VARIABLES + RUN_VARIABLES:
            environment.pop(name, None)
        environment.update(env)
        proc = subprocess.Popen([sys.executable, PIPELINE, "--root", self.root, "wait", slug],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                cwd=self.tmp.name, env=environment)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        return proc

    def over_budget_run(self):
        """A budgeted run in Phase 0 whose note heard the budget stop and exited 6."""
        self.init_scratch("--budget", "5")
        with Listener(reply=self.BUDGET_STOP) as desk:
            out = self.run_pipeline("note", "scratch", "0", "--scene", "x", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
        self.assertEqual(out.returncode, 6, out.stdout + out.stderr)

    def test_wait_exits_0_when_the_desk_extends_the_budget_and_logs_the_decision(self):
        self.over_budget_run()
        with Listener(reply=self.BUDGET_STOP) as desk:
            proc = self.start_wait(self.desk_env(desk))
            self.beats_after(desk, 3)
            self.assertIsNone(proc.poll(), "a budget stop keeps it waiting")
            desk.answer("POST", self.BEAT, 200, {"session": {"id": "s1", "budgetUsd": 10}, "directive": {"kind": "continue"}})
            code, out, err = self.finish(proc)
            bodies = self.beats(desk)
        self.assertEqual(code, 0, out + err)
        self.assertFalse(any(b["ended"] for b in bodies))
        self.assertEqual({b["budgetUsd"] for b in bodies}, {5}, "every beat states the run's budget")
        self.assertIn("budget extended", out)
        self.assertIn("$10.00", out)
        decisions = [e for e in (json.loads(line) for line in self.events_text().splitlines()) if e.get("event") == "decision"]
        self.assertEqual(len(decisions), 1, decisions)
        self.assertIn("budget extended on the desk", decisions[0]["note"])
        self.assertIn("$10.00", decisions[0]["note"])
        self.assertLessEqual(len(self.escalated_over_budget()), 2, "the note's line, and at most one for the wait")

    def test_wait_exits_3_on_a_plain_stop_and_ends_the_run(self):
        self.over_budget_run()
        with Listener(reply=self.BUDGET_STOP) as desk:
            proc = self.start_wait(self.desk_env(desk))
            self.beats_after(desk, 2)
            desk.answer("POST", self.BEAT, 200, {"session": {"id": "s1"}, "directive": {"kind": "stop"}})
            code, out, err = self.finish(proc)
            bodies = self.beats(desk)
        self.assertEqual(code, 3, out + err)
        self.assertIn("pipeline: the desk says stop", out)
        self.assert_plain_stop_words(out)  # the stop a Deny sends, heard in `wait`
        self.assertIs(bodies[-1]["ended"], True)
        self.assertEqual(sum(1 for b in bodies if b["ended"]), 1)
        self.assertIn("stopped from the desk", self.events_text())

    def test_wait_waits_through_a_429_a_refused_beat_a_message_and_a_budget_stop(self):
        self.over_budget_run()
        with Listener(status=429, reply={"error": "busy"}) as desk:
            proc = self.start_wait(self.desk_env(desk))
            self.beats_after(desk, 2)
            self.assertIsNone(proc.poll(), "a 429 keeps it waiting")
            desk.answer("POST", self.BEAT, 503, {"error": "down"})
            self.beats_after(desk, len(desk.requests) + 2)
            self.assertIsNone(proc.poll(), "a 5xx keeps it waiting")
            desk.answer("POST", self.BEAT, 200, {"session": {"id": "s1"}, "directive": {"kind": "message", "text": "Looking now."}})
            self.beats_after(desk, len(desk.requests) + 2)
            self.assertIsNone(proc.poll(), "a message keeps it waiting")
            desk.answer("POST", self.BEAT, 200, self.BUDGET_STOP)
            self.beats_after(desk, len(desk.requests) + 3)
            self.assertIsNone(proc.poll(), "a budget stop keeps it waiting")
            desk.answer("POST", self.BEAT, 200, {"session": {"id": "s1"}, "directive": {"kind": "continue"}})
            code, out, err = self.finish(proc)
        self.assertEqual(code, 0, out + err)
        self.assertIn("pipeline: message from the desk: Looking now.", out)
        self.assertEqual(len(self.escalated_over_budget()), 2, "the note's line and one for the wait")
        self.assertNotIn("t0k3n", out + err)

    def test_wait_with_a_closed_port_keeps_waiting(self):
        self.over_budget_run()
        env = {URL_VARIABLE: f"http://127.0.0.1:{closed_port()}", TOKEN_VARIABLE: "t0k3n", "PIPELINE_HOLD_INTERVAL": self.INTERVAL}
        proc = self.start_wait(env)
        time.sleep(1.5)
        self.assertIsNone(proc.poll(), "a desk that cannot be reached keeps it waiting")
        proc.kill()
        proc.communicate()

    def test_wait_with_no_desk_exits_4_at_once_and_with_no_run_exits_1(self):
        self.over_budget_run()
        before = self.events_text()
        code, out, err = self.finish(self.start_wait({}))
        self.assertEqual(code, 4, out + err)
        self.assertIn("no desk is set", out)
        self.assertEqual(self.events_text(), before, "nothing written")
        code, out, err = self.finish(self.start_wait({}, slug="nothing"))
        self.assertEqual(code, 1, out + err)
        self.assertIn("no run named 'nothing'", err)

    # ---------- ALYDAS-17, Gate 6: residual guards ----------

    def test_the_exit_6_line_says_what_wait_exits_with(self):
        """G6, W12. The line the agent acts on names wait's own exit codes."""
        self.init_scratch("--budget", "5")
        with Listener(reply=self.BUDGET_STOP) as desk:
            out = self.run_pipeline("log", "scratch", "a line", env={URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"})
        self.assertEqual(out.returncode, 6, out.stdout + out.stderr)
        self.assertIn(f"it exits 0 when the budget is raised and {import_pipeline().STOP_EXIT} if the run is ended",
                      " ".join(out.stdout.split()))

    def test_wait_obeys_its_first_beat(self):
        """G6, T15. A run already ended on the desk ends at wait's first beat:
        one beat, then the ended beat, not a second beat first."""
        self.over_budget_run()
        with Listener(reply={"session": {"id": "s1"}, "directive": {"kind": "stop"}}) as desk:
            code, out, err = self.finish(self.start_wait(self.desk_env(desk)))
            bodies = self.beats(desk)
        self.assertEqual(code, 3, out + err)
        self.assertEqual([b["ended"] for b in bodies], [False, True])

    def test_hold_ends_0_on_a_send_back_or_an_unoffered_answer_while_over_budget(self):
        """G6, H3 and H4. Every way a hold ends on its answer waits through a
        budget stop (D9), not only Approve."""
        for answer, label, status in (("send-back", "Decided", "active"), ("maybe", "Maybe", "blocked")):
            with self.subTest(answer=answer):
                self.setUp()
                self.phase_2_ready(init=("--budget", "5"))
                with self.asking_desk() as desk:
                    desk.answer("POST", self.BEAT, 200, self.BUDGET_STOP)
                    proc = self.start_hold(self.desk_env(desk))
                    self.reads_back(desk)
                    desk.answer("GET", "/api/asks", 200, self.answered(answer, label))
                    code, out, err = self.finish(proc)
                self.assertEqual(code, 0, out + err)
                self.assertIn(f"pipeline: answered: {answer}", out)
                self.assertEqual(self.board()["phases"][2]["status"], status)
                self.assertEqual(len(self.escalated_over_budget()), 1, "logged once")

    def test_a_ledger_budget_a_beat_may_not_send_is_not_sent(self):
        """G6, B10. A hand-edited ledger value outside the desk's bound, or not
        a number, is no budget on the beat."""
        pipeline = import_pipeline()
        bound = pipeline.LEDGER_BOUNDS["costUsd"]
        for value in (bound * 10, 0, -5, True, "5", None, float("nan")):
            with self.subTest(value=value):
                self.assertIsNone(pipeline.run_budget({"budget_usd": value}))
        self.assertEqual(pipeline.run_budget({"budget_usd": bound}), bound)

    def test_the_stop_s_words_print_no_amount_that_is_not_one(self):
        """G6, B11. A negative, infinite or non-numeric amount from the desk is
        not printed as dollars."""
        words = import_pipeline().over_budget_words
        for spent in (-1, float("inf"), float("nan"), "5", True):
            with self.subTest(spent=spent):
                self.assertEqual(words({"spentUsd": spent, "budgetUsd": 5}), "spent more than its budget of $5.00")
        self.assertEqual(words({"spentUsd": 5.12, "budgetUsd": -5}), "spent more than its budget")


class RunStartCase(HoldHelpers, ScratchRunCase):
    """ALYDAS-127, S8. A session a runner started from the desk names its start
    (`ALYDASH_RUN_START`) on every beat, and its budget (`ALYDASH_RUN_BUDGET`)
    when the ledger states none. The desk honours `start` only on the beat that
    inserts the run, as it does `budgetUsd`, so every beat carries it."""

    BEAT = "/api/sessions/heartbeat"
    CALLS = (("phase", "scratch", "0", "start"), ("note", "scratch", "0", "--scene", "x"),
             ("log", "scratch", "a line"), ("phase", "scratch", "0", "done", "--force", "a scratch run"))

    def beats(self, desk):
        return [json.loads(r["raw"]) for r in self.of(desk, "POST", self.BEAT)]

    def bodies_with(self, extra, init=()):
        """Four beats (two phase events, a note, a log) with `extra` exported."""
        self.setUp()
        self.init_scratch(*init)
        with Listener() as desk:
            env = dict({URL_VARIABLE: desk.url, TOKEN_VARIABLE: "t0k3n"}, **extra)
            for args in self.CALLS:
                out = self.run_pipeline(*args, env=env)
                self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            bodies = self.beats(desk)
        self.assertEqual(len(bodies), len(self.CALLS))
        return bodies

    def test_the_start_is_sent_on_every_beat_while_the_variable_is_set(self):
        bodies = self.bodies_with({RUN_START_VARIABLE: "s1"})
        self.assertEqual([b.get("start") for b in bodies], ["s1"] * len(self.CALLS))
        # Beside the item the runner named, which is how the run reaches that item's screen.
        bodies = self.bodies_with({RUN_START_VARIABLE: "s1", WORK_ITEM_VARIABLE: "ALY-7"})
        for body in bodies:
            self.assertEqual(body["start"], "s1")
            self.assertEqual(body["subject"], {"kind": "work_item", "key": "ALY-7"})

    def test_unset_or_blank_no_beat_has_a_start(self):
        for name, extra in (("unset", {}), ("empty", {RUN_START_VARIABLE: ""}),
                            ("blank", {RUN_START_VARIABLE: "   "})):
            with self.subTest(name):
                for body in self.bodies_with(extra):
                    self.assertNotIn("start", body, "no start is no key, never null or empty")

    def test_the_runners_budget_is_sent_when_the_ledger_states_none(self):
        for name, value, expected in (("a whole number", "7", 7), ("a fraction", "2.5", 2.5),
                                      ("with a dollar sign", "$7", 7)):
            with self.subTest(name):
                bodies = self.bodies_with({RUN_BUDGET_VARIABLE: value})
                self.assertEqual([b.get("budgetUsd") for b in bodies], [expected] * len(self.CALLS))
                for body in bodies:
                    self.assertIsInstance(body["budgetUsd"], (int, float))
                    self.assertNotIsInstance(body["budgetUsd"], bool)

    def test_a_ledger_budget_wins_over_the_runners(self):
        bodies = self.bodies_with({RUN_BUDGET_VARIABLE: "7"}, init=("--budget", "5"))
        self.assertEqual({b["budgetUsd"] for b in bodies}, {5})

    MALFORMED = ("abc", "0", "-7", "nan", "inf", "-inf", "1e9", "0.001", "7 dollars", "", "   ")

    def test_a_malformed_runners_budget_sends_none(self):
        for value in self.MALFORMED:
            with self.subTest(value=value):
                for body in self.bodies_with({RUN_BUDGET_VARIABLE: value}):
                    self.assertNotIn("budgetUsd", body, "a budget that is not one is no key")
        # And it does not take a ledger budget away.
        bodies = self.bodies_with({RUN_BUDGET_VARIABLE: "abc"}, init=("--budget", "5"))
        self.assertEqual({b["budgetUsd"] for b in bodies}, {5})

    def test_the_desk_takes_a_beat_that_carries_a_start(self):
        """Gate 4 row 24, run rather than read: the desk's own heartbeatRefusal
        refuses nothing for the key, before S2 teaches it the key and after."""
        bodies = self.bodies_with({RUN_START_VARIABLE: "s1", RUN_BUDGET_VARIABLE: "7"})
        self.assertEqual((bodies[0]["start"], bodies[0]["budgetUsd"]), ("s1", 7), "the beat the desk is shown")
        self.assertEqual(desk_refusals(self, bodies[:1]), [None])


class Gate7BrowserSuiteCase(ScratchRunCase):
    """ALYDAS-553. Gate 7 runs the project's browser suite and pastes its
    totals in the evidence doc, under a line that names it, or says the
    project has none. `phase 7 done` refuses an evidence doc that does
    neither, because the instruction alone was skipped."""

    PICTURE = "![The page](screenshots/page.png)\n"
    LABELLED = "Browser suite (pnpm e2e, final tree at abc1234): 160 passed, 0 failed\n"

    def close_with(self, evidence):
        self.init_scratch("--hold", "none")
        self.assertEqual(self.run_pipeline("phase", "scratch", "7", "start").returncode, 0)
        self.assertEqual(self.run_pipeline("note", "scratch", "7", "--plain", "Said.").returncode, 0)
        for i in (1, 2, 3, 4):
            self.assertEqual(self.run_pipeline("step", "scratch", "7", str(i), "done").returncode, 0)
        os.makedirs(os.path.join(self.root, "measurements"), exist_ok=True)
        with open(os.path.join(self.root, "measurements", "2026-09-13-scratch-evidence.md"), "w",
                  encoding="utf-8") as f:
            f.write("# Evidence\n\n" + evidence)
        return self.run_pipeline("phase", "scratch", "7", "done")

    def assert_refused(self, out):
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("evidence doc has no browser suite totals", out.stderr)
        self.assertNotIn("is still to do", out.stderr)

    def test_an_evidence_doc_with_no_browser_suite_totals_is_refused(self):
        self.assert_refused(self.close_with(self.PICTURE))

    def test_a_labelled_totals_line_passes(self):
        out = self.close_with(self.PICTURE + "\n" + self.LABELLED)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_no_browser_suite_passes(self):
        out = self.close_with(self.PICTURE + "\nThis project has no browser suite.\n")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_totals_with_no_label_are_refused(self):
        """Across the other dimension: a count that names no suite is not one."""
        self.assert_refused(self.close_with(self.PICTURE + "\n160 passed, 0 failed\n"))

    def test_a_label_with_no_number_before_passed_is_refused(self):
        self.assert_refused(self.close_with(self.PICTURE + "\nBrowser suite (pnpm e2e): passed\n"))

    def test_a_label_with_a_count_that_is_not_passed_is_refused(self):
        self.assert_refused(self.close_with(self.PICTURE + "\nBrowser suite (pnpm e2e): not run, 160 tests\n"))

    def test_the_label_and_the_count_on_different_lines_are_refused(self):
        self.assert_refused(self.close_with(self.PICTURE + "\nBrowser suite (pnpm e2e):\n\n160 passed\n"))

    def test_no_browser_suite_inside_another_sentence_is_refused(self):
        self.assert_refused(self.close_with(self.PICTURE + "\nThe run left no browser suite failure unexplained.\n"))

    def test_a_totals_line_with_nought_passed_is_refused(self):
        self.assert_told(self.close_with(self.PICTURE + "\nBrowser suite (pnpm e2e, final tree at abc1234): 0 passed, 160 failed\n"), "0 passed")

    def test_failures_not_listed_as_unverified_are_refused(self):
        self.assert_told(self.close_with(self.PICTURE + "\nBrowser suite (pnpm e2e, final tree at abc1234): 150 passed, 10 failed\n"), "10 failed")

    def test_the_instructions_template_line_is_refused(self):
        self.assert_told(self.close_with(self.PICTURE + "\nThe gate asks for a line like:\n\n```\nBrowser suite (pnpm e2e, final tree at <sha>): 160 passed, 0 failed\n```\n"), "names no commit")

    def test_failures_listed_as_unverified_pass(self):
        out = self.close_with(self.PICTURE + "\nBrowser suite (pnpm e2e, final tree at abc1234): 150 passed, 10 failed\n\n"
                              "The 10 fail on main too and are listed as unverified.\n")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    # Gate 8 (alydas-545), W1 and W2. A doc that has a totals line is told
    # what is wrong with it, and never steered to "no browser suite"; words
    # elsewhere in the doc do not excuse a failing line.
    FAILING = "Browser suite (pnpm e2e, final tree at abc1234): 158 passed, 3 failed\n"

    def assert_told(self, out, reason):
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn(reason, out.stderr)
        self.assertNotIn("no browser suite totals", out.stderr)
        self.assertNotIn("say 'no browser suite'", out.stderr)

    def test_unlisted_failures_are_told_as_failures(self):
        self.assert_told(self.close_with(self.PICTURE + "\n" + self.FAILING), "3 failed")

    def test_nought_passed_is_told_as_nought(self):
        self.assert_told(self.close_with(self.PICTURE + "\nBrowser suite (pnpm e2e, final tree at abc1234): 0 passed, 0 failed\n"),
                         "0 passed")

    def test_the_template_is_told_it_names_no_commit(self):
        self.assert_told(self.close_with(self.PICTURE + "\nBrowser suite (pnpm e2e, final tree at <sha>): 160 passed, 0 failed\n"),
                         "names no commit")

    def test_no_browser_suite_beside_a_failing_line_is_refused(self):
        self.assert_told(self.close_with(self.PICTURE + "\n" + self.FAILING + "\nThis project has no browser suite.\n"), "3 failed")

    def test_unverified_about_something_else_does_not_excuse_failures(self):
        self.assert_told(self.close_with(self.PICTURE + "\n" + self.FAILING +
                                         "\nThe production check stays unverified until the deploy.\n"), "3 failed")

    def test_failures_under_an_unverified_heading_pass(self):
        out = self.close_with(self.PICTURE + "\n" + self.FAILING +
                              "\n## Unverified\n\n- The browser suite's 3 failures fail on main too.\n")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_the_screenshot_check_still_stands_beside_it(self):
        out = self.close_with(self.LABELLED)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("evidence doc references no screenshot", out.stderr)
        self.assertNotIn("browser suite totals", out.stderr)


if __name__ == "__main__":
    unittest.main()
