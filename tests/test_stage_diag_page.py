"""Checks on the operator diagnostic page.

String assertions pin the page's contract — the panes, the API routes it
calls, and its token handling. The node-based tests run the page's script
and its pure helpers for real, so a syntax error cannot hide behind green
greps.
"""

import json
import re
import shutil
import subprocess

import pytest

from stage import diag_page

NODE = shutil.which("node") or shutil.which("nodejs")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _script():
    blocks = re.findall(r"<script>(.*?)</script>", diag_page.DIAG_PAGE_HTML, re.S)
    assert len(blocks) == 1, "the diagnostic page must keep a single script block"
    return blocks[0]


def _helpers_block():
    """The pure formatting helpers alone, so they run without a document."""
    text = _script()
    start = text.index("/* ---------- helpers ---------- */")
    end = text.index("/* ---------- api ---------- */")
    return text[start:end]


def _run(source, tmp_path):
    path = tmp_path / "harness.js"
    path.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(path)], capture_output=True, text=True, timeout=30, cwd=str(tmp_path)
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_page_names_its_panes_and_routes():
    html = diag_page.DIAG_PAGE_HTML
    assert "aurora diagnostics" in html
    for pane in ('id="lives"', 'id="lanes"', 'id="turns"', 'id="requests"'):
        assert pane in html
    for route in (
        "/api/diag/incarnations",
        "/api/diag/incarnation",
        "/api/diag/streams",
        "/api/diag/stream",
        "/api/diag/entry",
    ):
        assert route in html
    assert "X-Console-Token" in html


def test_page_pins_its_grid_row_to_the_viewport():
    """Without an explicit row height the implicit grid row sizes to content,
    pushing the second rail section and both panes' scrollbars off screen."""
    assert "grid-template-rows: 100%" in diag_page.DIAG_PAGE_HTML


def test_page_strips_the_token_from_the_url():
    assert "history.replaceState" in _script()


@needs_node
def test_page_script_parses(tmp_path):
    harness = "var script = " + json.dumps(_script()) + ";\nnew Function(script);\n"
    harness += "console.log(JSON.stringify({ok: true}));\n"
    out = _run(harness, tmp_path)
    assert out["ok"] is True


@needs_node
def test_format_helpers(tmp_path):
    harness = _helpers_block()
    harness += """
var out = {};
out.when_none = fmtWhen(null);
out.when_real = fmtWhen(1755302400);
out.ago_s = fmtAgo(5);
out.ago_m = fmtAgo(65);
out.ago_h = fmtAgo(7200);
out.ago_d = fmtAgo(200000);
out.tokens_none = fmtTokens(null);
out.tokens_small = fmtTokens(999);
out.tokens_k = fmtTokens(12345);
out.tokens_m = fmtTokens(2500000);
out.span = fmtSpan(7980);
out.span_short = fmtSpan(42);
out.life_current = lifeTitle({ordinal: 3, current: true, ending_kind: null});
out.life_declared = lifeTitle({ordinal: 2, current: false, ending_kind: "declared"});
out.life_harness = lifeTitle({ordinal: 1, current: false, ending_kind: "harness"});
console.log(JSON.stringify(out));
"""
    out = _run(harness, tmp_path)
    assert out["when_none"] == "—"
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}", out["when_real"])
    assert out["ago_s"] == "5s"
    assert out["ago_m"] == "1m"
    assert out["ago_h"] == "2h"
    assert out["ago_d"] == "2d"
    assert out["tokens_none"] == "—"
    assert out["tokens_small"] == "999"
    assert out["tokens_k"] == "12.3k"
    assert out["tokens_m"] == "2.5M"
    assert out["span"] == "2h 13m"
    assert out["span_short"] == "42s"
    assert "life 3" in out["life_current"] and "current" in out["life_current"]
    assert "life 2" in out["life_declared"] and "declared" in out["life_declared"]
    assert "life 1" in out["life_harness"] and "harness" in out["life_harness"]


@needs_node
def test_page_ranges_label_pagination(tmp_path):
    harness = _helpers_block()
    harness += """
var out = {};
out.empty = pageLabel(0, 30, 0);
out.first = pageLabel(0, 30, 90);
out.middle = pageLabel(30, 30, 90);
out.short_tail = pageLabel(60, 30, 75);
console.log(JSON.stringify(out));
"""
    out = _run(harness, tmp_path)
    assert out["empty"] == "0 of 0"
    assert out["first"] == "1–30 of 90"
    assert out["middle"] == "31–60 of 90"
    assert out["short_tail"] == "61–75 of 75"
