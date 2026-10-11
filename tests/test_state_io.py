"""Critical state must not follow links or silently accept ambiguous JSON."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from amu import state_io


def _path(tmp_path, name="state.json"):
    directory = tmp_path / ".amu"
    directory.mkdir(exist_ok=True)
    return directory / name


def test_absence_returns_default_without_creating_state(tmp_path):
    fallback = {"nodes": {}}
    assert state_io.read(tmp_path / ".amu" / "state.json", fallback) is fallback
    assert not (tmp_path / ".amu").exists()


@pytest.mark.parametrize("payload", [
    '{"ok": 1, "ok": 2}', '{"inner": {"x": 1, "x": 2}}',
    '{"value": NaN}', '{"value": Infinity}', '{"value": -Infinity}',
    '{"value": 1e309}', '{"secret": "sensitive-value", broken}',
])
def test_invalid_or_ambiguous_json_never_becomes_defaults(tmp_path, payload):
    path = _path(tmp_path)
    path.write_text(payload)
    with pytest.raises(state_io.StateIOError) as captured:
        state_io.read(path, {})
    assert "sensitive-value" not in str(captured.value)
    assert path.read_text() == payload


def test_read_bound_rejects_large_file_and_invalid_encoding(tmp_path):
    path = _path(tmp_path)
    path.write_bytes(b" " * 65)
    with pytest.raises(state_io.StateIOError, match="limit"):
        state_io.read(path, {}, max_bytes=64)
    path.write_bytes(b"\xff")
    with pytest.raises(state_io.StateIOError):
        state_io.read(path, {})


def test_validation_callback_receives_valid_value(tmp_path):
    path = _path(tmp_path)
    path.write_text('{"n": 2}')
    assert state_io.read(path, validate=lambda value: {"n": value["n"] + 1}) == {"n": 3}


@pytest.mark.parametrize("kind", ["directory", "fifo", "link", "hardlink"])
def test_nonregular_and_linked_state_is_rejected(tmp_path, kind):
    path = _path(tmp_path)
    outside = tmp_path / "external.json"
    outside.write_text('{"private": true}')
    if kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "link":
        path.symlink_to(outside)
    else:
        os.link(outside, path)
    with pytest.raises(state_io.StateIOError):
        state_io.read(path, {})


@pytest.mark.parametrize("nested", [False, True])
def test_linked_managed_parent_never_redirects_reads_or_writes(tmp_path, nested):
    outside = tmp_path / "external"
    outside.mkdir()
    external = outside / "state.json"
    external.write_text('{"untouched": true}')
    if nested:
        managed = tmp_path / ".amu"
        managed.mkdir()
        (managed / "runs").symlink_to(outside, target_is_directory=True)
        path = managed / "runs" / "state.json"
    else:
        (tmp_path / ".amu").symlink_to(outside, target_is_directory=True)
        path = tmp_path / ".amu" / "state.json"
    with pytest.raises(state_io.StateIOError):
        state_io.read(path, {})
    with pytest.raises(state_io.StateIOError):
        state_io.write_atomic(path, {"changed": True})
    assert json.loads(external.read_text()) == {"untouched": True}
    assert list(outside.iterdir()) == [external]


def test_workspace_alias_before_managed_root_is_supported(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    alias = tmp_path / "repo-alias"
    alias.symlink_to(repo, target_is_directory=True)
    path = alias / ".amu" / "state.json"
    state_io.write_atomic(path, {"n": 1})
    assert state_io.read(path) == {"n": 1}
    assert (repo / ".amu" / "state.json").is_file()


def test_arbitrary_home_root_cannot_be_a_symbolic_link(tmp_path):
    outside = tmp_path / "external"
    outside.mkdir()
    root = tmp_path / "custom-home"
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(state_io.StateIOError):
        state_io.read(root / "workspace.json", {}, managed_root=root)


def test_final_file_swap_is_not_followed(tmp_path, monkeypatch):
    path = _path(tmp_path)
    path.write_text('{"safe": true}')
    external = tmp_path / "external.json"
    external.write_text('{"private": true}')
    original_open = os.open
    swapped = False

    def swapping_open(name, flags, *args, **kwargs):
        nonlocal swapped
        if name == path.name and not swapped:
            swapped = True
            path.unlink()
            path.symlink_to(external)
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(state_io.os, "open", swapping_open)
    with pytest.raises(state_io.StateIOError):
        state_io.read(path, {})
    assert swapped
    assert json.loads(external.read_text()) == {"private": True}


def test_file_growth_during_read_is_bounded(tmp_path, monkeypatch):
    path = _path(tmp_path)
    path.write_text("{}")
    original_read = os.read
    changed = False

    def growing_read(descriptor, count):
        nonlocal changed
        if not changed:
            changed = True
            path.write_bytes(b" " * 65)
        return original_read(descriptor, count)

    monkeypatch.setattr(state_io.os, "read", growing_read)
    with pytest.raises(state_io.StateIOError, match="limit"):
        state_io.read(path, {}, max_bytes=64)


def test_removed_file_during_read_is_not_treated_as_missing(tmp_path, monkeypatch):
    path = _path(tmp_path)
    path.write_text("{}")
    original_read = os.read

    def removing_read(descriptor, count):
        chunk = original_read(descriptor, count)
        path.unlink(missing_ok=True)
        return chunk

    monkeypatch.setattr(state_io.os, "read", removing_read)
    with pytest.raises(state_io.StateIOError, match="changed"):
        state_io.read(path, {})


def test_managed_directory_swap_during_read_is_detected(tmp_path, monkeypatch):
    path = _path(tmp_path)
    path.write_text('{"safe": true}')
    external = tmp_path / "external"
    external.mkdir()
    (external / path.name).write_text('{"private": true}')
    original_open = os.open
    swapped = False

    def swapping_open(name, flags, *args, **kwargs):
        nonlocal swapped
        if name == path.name and not swapped:
            swapped = True
            path.parent.rename(tmp_path / "saved-state")
            path.parent.symlink_to(external, target_is_directory=True)
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(state_io.os, "open", swapping_open)
    with pytest.raises(state_io.StateIOError, match="changed"):
        state_io.read(path, {})
    assert swapped
    assert json.loads((external / path.name).read_text()) == {"private": True}


def test_unreadable_existing_state_is_not_treated_as_missing(tmp_path, monkeypatch):
    path = _path(tmp_path)
    path.write_text('{"secret": "sensitive-value"}')
    original_open = os.open

    def unreadable_open(name, flags, *args, **kwargs):
        if name == path.name:
            raise PermissionError("sensitive-value")
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(state_io.os, "open", unreadable_open)
    with pytest.raises(state_io.StateIOError) as captured:
        state_io.read(path, {})
    assert "sensitive-value" not in str(captured.value)


def test_write_rejects_existing_links_and_preserves_previous_on_invalid_json(tmp_path):
    path = _path(tmp_path)
    state_io.write_atomic(path, {"n": 1})
    before = path.read_bytes()
    with pytest.raises(state_io.StateIOError):
        state_io.write_atomic(path, {"n": float("nan")})
    assert path.read_bytes() == before
    external = tmp_path / "external.json"
    external.write_text("{}")
    path.unlink()
    path.symlink_to(external)
    with pytest.raises(state_io.StateIOError):
        state_io.write_atomic(path, {"n": 2})
    assert external.read_text() == "{}"


def test_ambiguous_serialized_keys_do_not_replace_saved_state(tmp_path):
    path = _path(tmp_path)
    state_io.write_atomic(path, {"n": 1})
    before = path.read_bytes()
    with pytest.raises(state_io.StateIOError, match="unambiguous"):
        state_io.write_atomic(path, {1: "first", "1": "second"})
    assert path.read_bytes() == before


def test_managed_directory_writable_by_others_is_rejected(tmp_path):
    path = _path(tmp_path)
    path.parent.chmod(0o777)
    with pytest.raises(state_io.StateIOError, match="writable"):
        state_io.read(path, {})
    with pytest.raises(state_io.StateIOError, match="writable"):
        with state_io.lock(path):
            pytest.fail("untrusted directory was used")
    assert not path.exists()


def test_atomic_replace_failure_preserves_existing_bytes_and_removes_temp(tmp_path, monkeypatch):
    path = _path(tmp_path)
    state_io.write_atomic(path, {"n": 1})
    before = path.read_bytes()

    def failed_replace(*args, **kwargs):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(state_io.os, "replace", failed_replace)
    with pytest.raises(state_io.StateIOError):
        state_io.write_atomic(path, {"n": 2})
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("kind", ["link", "directory", "fifo", "hardlink"])
def test_unsafe_lock_paths_cannot_be_used(tmp_path, kind):
    path = _path(tmp_path)
    lock = path.with_name(f".{path.name}.lock")
    outside = tmp_path / "external"
    outside.write_text("private")
    if kind == "link":
        lock.symlink_to(outside)
    elif kind == "directory":
        lock.mkdir()
    elif kind == "fifo":
        os.mkfifo(lock)
    else:
        os.link(outside, lock)
    with pytest.raises(state_io.StateIOError):
        with state_io.lock(path):
            pytest.fail("unsafe lock was acquired")
    assert outside.read_text() == "private"


def test_state_and_lock_creation_is_private(tmp_path):
    path = tmp_path / ".amu" / "runs" / "state.json"
    with state_io.lock(path):
        state_io.write_atomic(path, {"n": 1})
    lock = path.with_name(f".{path.name}.lock")
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_legacy_lock_permissions_become_private_and_lock_inode_is_retained(tmp_path):
    path = _path(tmp_path)
    lock = path.with_name(f".{path.name}.lock")
    lock.write_text("")
    lock.chmod(0o644)
    original = lock.stat().st_ino
    with pytest.raises(RuntimeError):
        with state_io.lock(path):
            assert stat.S_IMODE(lock.stat().st_mode) == 0o600
            raise RuntimeError("transaction callback failure")
    with state_io.lock(path):
        assert lock.stat().st_ino == original


def test_transaction_oserror_is_preserved_and_lock_is_released(tmp_path):
    path = _path(tmp_path)
    failure = OSError("document replacement failed")
    with pytest.raises(OSError) as captured:
        with state_io.lock(path):
            raise failure
    assert captured.value is failure
    with state_io.lock(path):
        state_io.write_atomic(path, {"n": 1})
    assert state_io.read(path) == {"n": 1}


def test_cleanup_failure_does_not_mask_transaction_failure(tmp_path, monkeypatch):
    path = _path(tmp_path)
    failure = OSError("document replacement failed")
    original_flock = state_io.fcntl.flock

    def interrupted_unlock(descriptor, operation):
        if operation == state_io.fcntl.LOCK_UN:
            raise OSError("unlock failed")
        return original_flock(descriptor, operation)

    with monkeypatch.context() as patch:
        patch.setattr(state_io.fcntl, "flock", interrupted_unlock)
        with pytest.raises(OSError) as captured:
            with state_io.lock(path):
                raise failure
        assert captured.value is failure
    # Closing the lock descriptor releases the process lock even if explicit
    # unlock failed; the next transaction remains usable.
    with state_io.lock(path):
        state_io.write_atomic(path, {"n": 1})


def test_unlock_failure_without_body_failure_is_reported(tmp_path, monkeypatch):
    path = _path(tmp_path)
    original_flock = state_io.fcntl.flock

    def failed_unlock(descriptor, operation):
        if operation == state_io.fcntl.LOCK_UN:
            raise OSError("private-system-diagnostic")
        return original_flock(descriptor, operation)

    monkeypatch.setattr(state_io.fcntl, "flock", failed_unlock)
    with pytest.raises(state_io.StateIOError) as captured:
        with state_io.lock(path):
            pass
    assert "private-system-diagnostic" not in str(captured.value)


def test_replaced_lock_during_transaction_is_rejected(tmp_path):
    path = _path(tmp_path)
    lock = path.with_name(f".{path.name}.lock")
    with pytest.raises(state_io.StateIOError, match="changed"):
        with state_io.lock(path):
            replacement = path.parent / "replacement"
            replacement.write_text("")
            replacement.replace(lock)


def test_lock_fails_closed_without_process_locking(tmp_path, monkeypatch):
    path = _path(tmp_path)
    monkeypatch.setattr(state_io, "fcntl", None)
    with pytest.raises(state_io.StateIOError, match="process"):
        with state_io.lock(path):
            pytest.fail("in-process locking alone is insufficient")


def test_parallel_process_transactions_keep_all_updates(tmp_path):
    path = _path(tmp_path)
    source = Path(__file__).resolve().parents[1]
    script = """
import sys
from pathlib import Path
from amu import state_io
path = Path(sys.argv[1])
for _ in range(12):
    with state_io.lock(path):
        value = state_io.read(path, {"n": 0})
        state_io.write_atomic(path, {"n": value["n"] + 1})
"""
    processes = [subprocess.Popen([sys.executable, "-c", script, str(path)], cwd=source,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(4)]
    results = [(process, process.communicate(timeout=30)) for process in processes]
    failures = [stdout + stderr for process, (stdout, stderr) in results if process.returncode]
    assert not failures, "\n".join(failures)
    assert state_io.read(path) == {"n": 48}
