import pytest

from amu import cli
from tests.test_repl_dispatch import _session


def test_failed_lookup_does_not_cache_an_empty_inventory(monkeypatch):
    results = [([], "graph symbols exceeded the byte limit"),
               ([{"name": "target", "qualified_name": "target", "file_path": "api.py"}], None)]
    monkeypatch.setattr(cli.entire, "available", lambda: True)
    monkeypatch.setattr(cli.entire, "symbols_result", lambda _repo, *_args: results.pop(0))
    lookup = cli._lookup_factory("repo")
    with pytest.raises(cli.GraphDataError, match="limit"):
        lookup("target")
    assert lookup("target") == [{"symbol": "target", "path": "api.py"}]


def test_graph_limit_error_keeps_repl_open_without_not_found_claim(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli.entire, "available", lambda: True)
    monkeypatch.setattr(cli.entire, "symbols_result", lambda _repo, *_args: ([], "graph symbols exceeded the byte limit"))
    _session(monkeypatch, tmp_path, ["change target", "/help"])
    output = capsys.readouterr()
    assert "byte limit" in output.err
    assert "AMU command guide" in output.err
    assert "no symbol found" not in (output.out + output.err).lower()
