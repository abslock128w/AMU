"""Presentation/cache readers may not undo the graph transport bounds."""

import hashlib
from pathlib import Path

from amu import cli, entire, graph_refresh, state


def test_snapshot_hash_refuses_oversized_files_instead_of_reusing_them(tmp_path, monkeypatch):
    snapshot = state.amu_dir(str(tmp_path)) / "snapshot.ndjson"
    snapshot.write_bytes(b"old snapshot data")
    monkeypatch.setattr(entire, "MAX_SNAPSHOT_BYTES", 4)
    assert graph_refresh.snapshot_hash(str(tmp_path)) is None


def test_snapshot_hash_accepts_exact_byte_limit(tmp_path, monkeypatch):
    snapshot = state.amu_dir(str(tmp_path)) / "snapshot.ndjson"
    data = b"small snapshot"
    snapshot.write_bytes(data)
    monkeypatch.setattr(entire, "MAX_SNAPSHOT_BYTES", len(data))
    assert graph_refresh.snapshot_hash(str(tmp_path)) == hashlib.sha256(data).hexdigest()


def test_completion_reads_only_its_prefix_and_never_the_full_snapshot(tmp_path, monkeypatch):
    snapshot = state.amu_dir(str(tmp_path)) / "snapshot.ndjson"
    prefix = b'{"name":"first"}\n'
    snapshot.write_bytes(prefix + b'{"name":"must_not_read"}\n' + b"x" * 500_000)
    monkeypatch.setattr(cli, "COMPLETION_SNAPSHOT_BYTES", len(prefix), raising=False)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("whole snapshot reads are forbidden")

    monkeypatch.setattr(Path, "read_text", forbidden)
    completer = cli._completer(str(tmp_path))
    assert "first" in completer.words
    assert "must_not_read" not in completer.words


def test_completion_ignores_unsafe_snapshot_alias(tmp_path):
    source = tmp_path / "other.ndjson"
    source.write_text('{"name":"must_not_read"}\n')
    (state.amu_dir(str(tmp_path)) / "snapshot.ndjson").symlink_to(source)
    assert "must_not_read" not in cli._completer(str(tmp_path)).words
