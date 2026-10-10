"""Snapshots are validated incrementally and never publish partial output."""

from types import SimpleNamespace

import pytest

from amu import entire


def _provider(monkeypatch, chunks, *, error=None, code=0):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        assert kwargs["capture_stdout"] is False
        for chunk in chunks:
            try:
                kwargs["stdout_sink"](chunk)
            except (ValueError, OSError):
                return SimpleNamespace(returncode=126, stdout=b"", stderr=b"", error="io", stdout_bytes=0, stderr_bytes=0)
        return SimpleNamespace(returncode=code, stdout=b"", stderr=b"", error=error,
                               stdout_bytes=sum(map(len, chunks)), stderr_bytes=0)

    monkeypatch.setattr(entire.process, "run", run)
    return calls


def test_snapshot_accepts_split_utf8_and_ndjson_without_capturing_whole_output(tmp_path, monkeypatch):
    output = '["h",1,{}]\n["d",1,["café"]]\n["f",1,2,3]\n'.encode()
    at = output.index(b"\xc3")
    calls = _provider(monkeypatch, [output[:at+1], output[at+1:]])
    result = entire.snapshot("repo", str(tmp_path / "snapshot.ndjson"), worktree=True)
    assert result == {"lines": 3, "path": str(tmp_path / "snapshot.ndjson")}
    assert (tmp_path / "snapshot.ndjson").read_bytes() == output
    assert "--worktree" in calls[0][0]


@pytest.mark.parametrize("bad", [b"not-json\n", b'["h",true,{}]\n', b'["h",1,{}]\n{}\n',
                                 b'{"record_type":"header","value":NaN}\n',
                                 b'{"record_type":"header","value":1e999}\n',
                                 b'{"record_type":"header","record_type":"file"}\n',
                                 b'{"record_type":"header","value":"\xff"}\n'])
def test_malformed_snapshot_preserves_prior_bytes_and_cleans_temporary(tmp_path, monkeypatch, bad):
    destination = tmp_path / "snapshot.ndjson"
    destination.write_bytes(b"old evidence")
    _provider(monkeypatch, [bad])
    result = entire.snapshot("repo", str(destination))
    assert result["error"]
    assert destination.read_bytes() == b"old evidence"
    assert not list(tmp_path.glob(".snapshot.ndjson.*"))


def test_oversized_line_and_record_count_fail_closed(tmp_path, monkeypatch):
    destination = tmp_path / "snapshot.ndjson"
    destination.write_bytes(b"old evidence")
    monkeypatch.setattr(entire, "MAX_RECORD_BYTES", 20)
    _provider(monkeypatch, [b'["h",1,{}]\n["f",1,"' + b"x" * 30 + b'"]\n'])
    assert "limit" in entire.snapshot("repo", str(destination))["error"]
    monkeypatch.setattr(entire, "MAX_SNAPSHOT_RECORDS", 1)
    _provider(monkeypatch, [b'["h",1,{}]\n["f",1]\n'])
    assert "limit" in entire.snapshot("repo", str(destination))["error"]
    assert destination.read_bytes() == b"old evidence"


def test_process_limit_failure_keeps_prior_snapshot_even_after_valid_prefix(tmp_path, monkeypatch):
    destination = tmp_path / "snapshot.ndjson"
    destination.write_bytes(b"old evidence")
    _provider(monkeypatch, [b'["h",1,{}]\n'], error="output_limit", code=125)
    assert "limit" in entire.snapshot("repo", str(destination))["error"]
    assert destination.read_bytes() == b"old evidence"
    assert not list(tmp_path.glob(".snapshot.ndjson.*"))
