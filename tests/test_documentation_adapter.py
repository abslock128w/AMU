"""CLI error handling must retain critical state errors through the service."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amu import cli
from tests.test_docs import _committed_repo, _sound_signature_diff, _write_doc_map


def test_corrupt_review_metadata_is_a_state_failure_not_a_successful_skip(fake_entire, tmp_path, monkeypatch):
    repo = _committed_repo(tmp_path, {"api.py": "def public(value): return value\n",
                                    "docs/api.md": "# API\n\nOld usage: def public(value).\n"})
    _write_doc_map(repo, {"api.py#public": [{"file": "docs/api.md", "section": "# API"}]})
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: _sound_signature_diff())
    runner = CliRunner()

    def run(*args):
        return runner.invoke(cli.app, ["docs", *args, "--json", "--repo", str(repo)])

    assert run("--mode", "draft").exit_code == 0
    prepared = run("--prepare", "d1")
    assert prepared.exit_code == 0, prepared.output
    draft = json.loads(prepared.stdout)["prepared"]
    Path(draft["file"]).with_suffix(".json").write_text("{corrupt")
    before = (repo / "docs/api.md").read_bytes()
    result = run("--apply", "d1", "--review")
    assert result.exit_code != 0
    out = json.loads(result.stdout)
    assert out["error"] and "not_applied" not in out
    assert (repo / "docs/api.md").read_bytes() == before


@pytest.mark.parametrize("command", ["docs", "docs-reviews", "docs-saves", "docs-graph"])
def test_relative_cli_repository_remains_supported(fake_entire, tmp_path, monkeypatch, command):
    repo = _committed_repo(tmp_path, {"api.py": "def public(value): return value\n",
                                    "docs/api.md": "# API\n\nOld usage.\n"})
    monkeypatch.chdir(repo.parent)
    monkeypatch.setattr(cli.entire, "diff_json", lambda *_args, **_kwargs: _sound_signature_diff())
    result = CliRunner().invoke(cli.app, [command, "--json", "--repo", repo.name])
    assert result.exit_code == 0, result.output
    assert "error" not in json.loads(result.stdout)
