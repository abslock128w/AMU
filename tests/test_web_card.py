"""Regression checks for the static map-card renderer's hostile-input boundary."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from amu import graphview

PUBLIC = Path(__file__).resolve().parents[1] / "web" / "public"


@pytest.mark.parametrize("impact", ["recorded", "unresolved"])
def test_production_graph_export_is_accepted_by_local_viewer(impact):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    data = json.loads((Path(__file__).parent / "fixtures" / "impact.json").read_text()) if impact == "recorded" else {}
    payload = graphview.payload_from_impact("amu/classify.py#classify_consumers", data)
    script = """
const assert = require('node:assert/strict');
const fs = require('node:fs');
const boundary = require(process.argv[1]);
const text = fs.readFileSync(0, 'utf8');
assert.deepEqual(boundary.parse(text, 'graph'), JSON.parse(text));
"""
    result = subprocess.run([node, "-e", script, str(PUBLIC / "viewer-input.js")],
                            input=json.dumps(payload), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_viewer_boundary_rejects_invalid_graphs_before_rendering():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = r"""
const assert = require('node:assert/strict');
const v = require(process.argv[1]);
const graph = {nodes: [{id: 'a'}], edges: []};
assert.deepEqual(v.parse(JSON.stringify(graph), 'graph'), graph);
assert.deepEqual(v.parse('{"nodes":[],"edges":[]}', 'graph'), {nodes: [], edges: []});
for (const bad of [
  {nodes: null, edges: []}, {nodes: [null], edges: []},
  {nodes: [{id: ''}], edges: []}, {nodes: [{id: 'a'}, {id: 'a'}], edges: []},
  {nodes: [{id: 'a'}], edges: [{from: 'a', to: 'missing'}]},
  {nodes: Array.from({length: 1001}, (_, i) => ({id: String(i)})), edges: []},
  {nodes: [{id: 'a'}], edges: Array.from({length: 5001}, () => ({from: 'a', to: 'a'}))},
  {nodes: [], edges: [], error: 'provider failed'},
]) assert.throws(() => v.parse(JSON.stringify(bad), 'graph'));
assert.throws(() => v.parse('{"nodes":[],"edges":[],"metadata":1e999}', 'graph'), /finite/);
assert.throws(() => v.parse(' '.repeat(v.LIMITS.bytes + 1), 'graph'), /4 MiB/);
assert.throws(() => v.parse('{"nodes":[],"edges":[],"extra":' + '['.repeat(65) + '0' + ']'.repeat(65) + '}', 'graph'), /nesting/);
assert.throws(() => v.parse('{"nodes":[],"edges":[],"extra":"' + '😀'.repeat(v.LIMITS.bytes / 4) + '"}', 'graph'), /4 MiB/);
assert.deepEqual(v.parse('{"nodes":[],"root":null,"resolution":{}}', 'card').nodes, []);
assert.deepEqual(v.parse('{"nodes":[],"root":"a","ripples":[]}', 'card').nodes, []);
"""
    result = subprocess.run(
        [node, "-e", script, str(PUBLIC / "viewer-input.js")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_card_uses_an_external_script_and_a_restrictive_csp():
    html = (PUBLIC / "card.html").read_text()

    assert 'src="card.js" defer' in html
    assert "Content-Security-Policy" in html
    assert "script-src 'self'" in html
    assert "onclick=" not in html and "onerror=" not in html


def test_card_renderer_does_not_interpolate_untrusted_map_values_as_html():
    html = (PUBLIC / "card.html").read_text()
    script = (PUBLIC / "card.js").read_text()

    assert "innerHTML" not in html + script
    assert "outerHTML" not in html + script
    assert "insertAdjacentHTML" not in html + script
    assert "textContent" in script
    assert "classList.add" in script


@pytest.mark.parametrize("filename", ["card.js", "graph.js", "viewer-input.js"])
def test_card_script_has_valid_javascript_syntax_when_node_is_available(filename):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    result = subprocess.run([node, "--check", str(PUBLIC / filename)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


def test_static_viewers_never_treat_pasted_claims_as_approval_evidence():
    graph = (PUBLIC / "graph.html").read_text() + (PUBLIC / "graph.js").read_text()
    card = (PUBLIC / "card.js").read_text()
    card_html = (PUBLIC / "card.html").read_text()

    assert "APPROVAL-GRADE GRAPH EVIDENCE" not in graph
    assert "CANDIDATE GRAPH ONLY" in graph
    assert "const approvable = false" in graph
    assert "approve.disabled = true" in card
    assert "plannerProceed" not in card
    assert "Graph evidence is approval-grade" not in card
    assert "Approval happens in CLI" in card_html


def test_graph_is_self_hosted_and_has_no_inline_script_handlers():
    html = (PUBLIC / "graph.html").read_text()
    script = (PUBLIC / "graph.js").read_text()
    assert "https://cdnjs.cloudflare.com" not in html
    assert 'src="vendor/d3.min.js" defer' in html
    assert 'src="viewer-input.js" defer' in html
    assert 'src="graph.js" defer' in html
    assert "script-src 'self'" in html and "connect-src 'none'" in html
    assert "onclick=" not in html and "<script>" not in html
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script
    assert (PUBLIC / "vendor/d3.LICENSE").is_file()
