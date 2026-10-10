"""Exercise real interactive input, not direct calls to Typer callbacks."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amu import cli, state
from tests.test_docs import _committed_repo, _sound_signature_diff, _write_doc_map
from tests.test_repl_key import _repl, _repo


def _session(monkeypatch, repo, commands):
    lines = iter([*commands, "/quit"])
    monkeypatch.setattr(cli, "_read_line", lambda *_: next(lines))
    monkeypatch.setattr(cli, "_prompt_session", lambda *_: None)
    monkeypatch.setattr(cli.keys, "available", lambda: False)
    cli._repl(str(repo))


def test_docs_listing_through_real_process(fake_entire, tmp_path):
    result = _repl("/docs\n/quit\n", _repo(tmp_path))
    assert result.returncode == 0
    assert "docs · mode draft" in result.stdout
    assert "use --prepare" not in result.stdout + result.stderr


def test_refresh_reaches_missing_map_guard_and_keeps_session_alive(fake_entire, tmp_path):
    result = _repl("/refresh api.py#public\n/help\n/quit\n", _repo(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "refresh · api.py#public" in result.stdout
    assert "failed:" in result.stdout
    assert "AMU command guide" in result.stderr
    assert "OptionInfo" not in result.stdout + result.stderr


@pytest.mark.parametrize("prefix", ["/", ""])
def test_docs_review_session_matches_cli(fake_entire, monkeypatch, tmp_path, prefix):
    repo = _committed_repo(tmp_path, {
        "api.py": "def public(value):\n    return value\n",
        "docs/api.md": "## public\n\nOld usage: def public(value).\n\n## Other\n\nKeep this.\n",
    })
    _write_doc_map(repo, {"api.py#public": [{"file": "docs/api.md", "section": "## public"}]})
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_a, **_k: _sound_signature_diff())
    # Build the index first: compare repeated listings, not initial-build deltas.
    cli.docgraph.sync(str(repo))
    expected = CliRunner().invoke(cli.app, ["docs", "--mode", "draft", "--repo", str(repo), "--json"])
    assert expected.exit_code == 0, expected.output
    outputs = []
    monkeypatch.setattr(cli, "_out", lambda out, *_: outputs.append(out))

    original = (repo / "docs/api.md").read_text()
    _session(monkeypatch, repo, [f"{prefix}docs --mode draft", f"{prefix}docs --prepare d1"])
    assert outputs[0] == json.loads(expected.stdout)
    assert outputs[1]["prepared"] is not None
    assert (repo / "docs/api.md").read_text() == original
    Path(outputs[1]["prepared"]["file"]).write_text("Reviewed usage: public(value, strict=True).\n")
    _session(monkeypatch, repo, [f"{prefix}docs --apply d1 --review"])
    assert outputs[-1]["applied"] == ["d1"]
    saved = (repo / "docs/api.md").read_text()
    assert "Reviewed usage" in saved and "## Other\n\nKeep this." in saved
    assert state.read_json("docs-graph.json", str(repo))["graph_hash"] == outputs[-1]["docs_graph"]["graph_hash"]


@pytest.mark.parametrize("arguments,cli_arguments", [
    ("api.py#public", ["--symbol", "api.py#public"]),
    ('"api space.py#public" --depth 3', ["--symbol", "api space.py#public", "--depth", "3"]),
    ("api.py#public --change signature", ["--symbol", "api.py#public", "--change", "signature"]),
    ("api.py#public --change rename --new-symbol api.py#renamed",
     ["--symbol", "api.py#public", "--change", "rename", "--new-symbol", "api.py#renamed"]),
    ("--symbol api.py#public --change=move --new-symbol other.py#public",
     ["--symbol", "api.py#public", "--change=move", "--new-symbol", "other.py#public"]),
])
def test_refresh_session_uses_cli_validation_and_values(fake_entire, monkeypatch, tmp_path, arguments, cli_arguments):
    repo = _repo(tmp_path)
    calls = []

    def payload(*args):
        calls.append(args)
        return {"edge_delta": {"added": [], "removed": []}, "complete": True, "next": "run amu check"}, 0

    monkeypatch.setattr(cli, "_refresh_payload", payload)
    expected = CliRunner().invoke(cli.app, ["refresh", *cli_arguments, "--repo", str(repo)])
    assert expected.exit_code == 0, expected.output
    _session(monkeypatch, repo, [f"/refresh {arguments}"])
    assert len(calls) == 2
    assert calls[0] == calls[1]


@pytest.mark.parametrize("command,error", [
    ("/docs --review", "use --prepare"),
    ("/docs --prepare d1 --apply d1", "use --prepare"),
    ("/docs --bogus", "No such option"),
    ('/docs --mode "', "No closing quotation"),
    ("/refresh", "Missing option"),
    ("/refresh api.py#public --depth nope", "not a valid int"),
    ("/refresh api.py#public --change rename", "requires --new-symbol"),
    ("/refresh api.py#public --new-symbol api.py#renamed", "only valid with"),
])
def test_invalid_options_do_not_exit_session_or_apply_changes(fake_entire, monkeypatch, tmp_path, capsys, command, error):
    def unexpected(*_a, **_k):
        pytest.fail("invalid input must not reach refresh or docs writes")

    monkeypatch.setattr(cli, "_refresh_payload", unexpected)
    monkeypatch.setattr(cli.docreview, "apply", unexpected)
    monkeypatch.setattr(cli.docsmod, "apply", unexpected)
    _session(monkeypatch, _repo(tmp_path), [command, "/help"])
    captured = capsys.readouterr()
    assert error in captured.out + captured.err
    assert "AMU command guide" in captured.err
