"""Bounded, descriptor-based I/O for critical local JSON state.

Only the workspace prefix is resolved. The managed root (normally ``.amu``),
its descendants, the JSON file, and its lock are opened without following links.
Callers must hold ``lock(path)`` around a complete read/modify/write transaction;
``write_atomic`` alone prevents partial files, but cannot prevent lost updates.
"""

from __future__ import annotations

import json
import math
import os
import stat
import threading
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on non-POSIX hosts
    fcntl = None

MAX_JSON_BYTES = 16 * 1024 * 1024
_THREAD_LOCK = threading.RLock()
_DESCRIPTOR_APIS = (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
                    and os.open in os.supports_dir_fd
                    and os.stat in os.supports_dir_fd
                    and os.stat in os.supports_follow_symlinks)


class StateIOError(ValueError):
    """Saved state cannot safely be read, replaced, or locked."""


def _error(path: Path, reason: str) -> StateIOError:
    # Do not include JSON values, decoder context, or OS exception messages.
    return StateIOError(f"{path.name}: {reason}")


def _limits(path: Path, max_bytes: int) -> None:
    if type(max_bytes) is not int or max_bytes <= 0:
        raise _error(path, "JSON byte limit must be a positive integer")


def _location(path: Path, managed_root: Path | None) -> tuple[Path, tuple[str, ...]]:
    path = path.absolute()
    if managed_root is None:
        indices = [i for i, name in enumerate(path.parts[:-1]) if name == ".amu"]
        root = Path(*path.parts[:indices[-1] + 1]) if indices else path.parent
    else:
        root = Path(managed_root).absolute()
    try:
        tail = path.relative_to(root).parts
    except ValueError as exc:
        raise _error(path, "state path is outside its managed directory") from exc
    if not root.name or not tail or any(name in ("", ".", "..") for name in tail):
        raise _error(path, "invalid managed state path")
    # Repo paths may themselves be aliases, and /tmp is an alias on macOS.
    # Preserve those while refusing aliases at or below the managed root.
    return root.parent.resolve(), (root.name, *tail)


def _same_file(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _regular(path: Path, info: os.stat_result, *, lock_file: bool = False) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise _error(path, "lock must be a regular file without links" if lock_file
                     else "state must be a regular file without links")
    if info.st_uid != os.getuid():
        raise _error(path, "state and lock files must be owned by the current user")


@dataclass
class _Directory:
    descriptor: int
    path: Path
    links: list[tuple[int, str, os.stat_result]]

    def verify(self) -> None:
        for parent, name, original in self.links:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(current.st_mode) or not _same_file(original, current):
                raise _error(self.path, "managed directory changed during state access")

    def target(self, name: str, *, missing_ok: bool = False) -> os.stat_result | None:
        try:
            result = os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise
        _regular(self.path, result)
        return result


@contextmanager
def _directory(path: Path, managed_root: Path | None, *, create: bool) -> Iterator[_Directory]:
    if not _DESCRIPTOR_APIS:
        raise _error(path, "secure descriptor-based state access is unavailable on this host")
    prefix, names = _location(path, managed_root)
    descriptors: list[int] = []
    links: list[tuple[int, str, os.stat_result]] = []
    exception_in_progress = False
    try:
        parent = os.open(prefix, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(parent)
        for name in names[:-1]:
            if create:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=parent)
                except FileExistsError:
                    pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            descriptors.append(child)
            info = os.fstat(child)
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise _error(path, "managed directory must be owned by you and not writable by others")
            links.append((parent, name, info))
            parent = child
        directory = _Directory(parent, path, links)
        directory.verify()
        yield directory
    except BaseException:
        exception_in_progress = True
        raise
    finally:
        cleanup_error = None
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError as exc:
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None and not exception_in_progress:
            raise cleanup_error


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate JSON object keys")
        result[key] = value
    return result


def _float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("nonfinite JSON number")
    return value


def _constant(raw: str) -> Any:
    raise ValueError("nonfinite JSON number")


def read(
    path: str | Path,
    default: Any = None,
    validate: Callable[[Any], Any] | None = None,
    *,
    max_bytes: int = MAX_JSON_BYTES,
    managed_root: str | Path | None = None,
) -> Any:
    """Read strict JSON; return ``default`` only when its path is absent.

    A validation callback receives the decoded value and returns the desired
    result. Its errors propagate unchanged so callers can preserve schema errors.
    """
    path = Path(path)
    _limits(path, max_bytes)
    root = Path(managed_root) if managed_root is not None else None
    opened = False
    try:
        with _directory(path, root, create=False) as directory:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory.descriptor)
            opened = True
            try:
                original = os.fstat(descriptor)
                _regular(path, original)
                if original.st_size > max_bytes:
                    raise _error(path, f"state exceeds JSON byte limit ({max_bytes})")
                chunks = bytearray()
                while len(chunks) <= max_bytes:
                    chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - len(chunks)))
                    if not chunk:
                        break
                    chunks.extend(chunk)
                if len(chunks) > max_bytes:
                    raise _error(path, f"state exceeds JSON byte limit ({max_bytes})")
                final = os.fstat(descriptor)
                if (original.st_size, original.st_mtime_ns, original.st_ctime_ns) != (
                    final.st_size, final.st_mtime_ns, final.st_ctime_ns
                ):
                    raise _error(path, "state changed during read; retry this operation")
                current = directory.target(path.name)
                if not _same_file(original, current):
                    raise _error(path, "state changed during read; retry this operation")
                directory.verify()
            finally:
                os.close(descriptor)
    except FileNotFoundError as exc:
        if not opened:
            return default
        raise _error(path, "state changed during read; retry this operation") from exc
    except OSError as exc:
        raise _error(path, "unable to safely read state; restore or repair this state file") from exc
    try:
        value = json.loads(chunks.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_float=_float, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _error(path, "invalid or ambiguous JSON; restore or repair this state file") from exc
    return validate(value) if validate is not None else value


@contextmanager
def lock(path: str | Path, *, managed_root: str | Path | None = None) -> Iterator[None]:
    """Serialize a whole transaction across threads and processes.

    Lock files persist so waiting processes continue to use the same inode.
    Hosts without a process lock fail explicitly instead of silently losing it.
    """
    path = Path(path)
    if fcntl is None:
        raise _error(path, "cross-process state locking is unavailable on this host")
    root = Path(managed_root) if managed_root is not None else None
    descriptor = None
    acquired = False
    with _THREAD_LOCK:
        resources = ExitStack()
        exception_in_progress = False
        try:
            try:
                directory = resources.enter_context(_directory(path, root, create=True))
                name = f".{path.name}.lock"
                flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
                try:
                    descriptor = os.open(name, flags, dir_fd=directory.descriptor)
                except FileNotFoundError:
                    # Initialize exclusively; concurrent creators then open
                    # the same existing inode. Simultaneous O_CREAT combined
                    # with O_NOFOLLOW can fail with ENOENT on macOS.
                    try:
                        descriptor = os.open(name, flags | os.O_CREAT | os.O_EXCL,
                                             mode=0o600, dir_fd=directory.descriptor)
                    except FileExistsError:
                        descriptor = os.open(name, flags, dir_fd=directory.descriptor)
                original = os.fstat(descriptor)
                _regular(path, original, lock_file=True)
                os.fchmod(descriptor, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                acquired = True
                directory.verify()
                current = directory.target(name)
                if not _same_file(original, current):
                    raise _error(path, "state lock changed while waiting; retry this operation")
            except OSError as exc:
                raise _error(path, "unable to safely lock state; restore or repair this state directory") from exc

            # The body owns its exceptions. In particular, document-save and
            # workspace callers depend on distinguishing their OSError from
            # a failure to acquire or verify a state lock.
            yield
            try:
                directory.verify()
                current = directory.target(name)
                if not _same_file(original, current):
                    raise _error(path, "state lock changed during transaction; retry this operation")
            except OSError as exc:
                raise _error(path, "unable to safely verify state lock; restore or repair this state directory") from exc
        except BaseException:
            exception_in_progress = True
            raise
        finally:
            cleanup_error = None
            if descriptor is not None:
                try:
                    if acquired:
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError as exc:
                    cleanup_error = exc
                finally:
                    try:
                        os.close(descriptor)
                    except OSError as exc:
                        cleanup_error = cleanup_error or exc
            try:
                resources.close()
            except OSError as exc:
                cleanup_error = cleanup_error or exc
            # Always attempt every release; an existing failure remains the
            # primary diagnostic, including KeyboardInterrupt/SystemExit.
            if cleanup_error is not None and not exception_in_progress:
                raise _error(path, "unable to safely release state lock; retry this operation") from cleanup_error


def write_atomic(
    path: str | Path,
    value: Any,
    *,
    max_bytes: int = MAX_JSON_BYTES,
    managed_root: str | Path | None = None,
) -> Path:
    """Replace JSON through a private, fsynced file in the pinned parent.

    This primitive does not take a lock; callers choose whether the write is an
    explicit snapshot replacement or part of a locked read/modify/write operation.
    """
    path = Path(path)
    _limits(path, max_bytes)
    try:
        content = (json.dumps(value, indent=2, sort_keys=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise _error(path, "value cannot be saved as valid JSON") from exc
    if len(content) > max_bytes:
        raise _error(path, f"state exceeds JSON byte limit ({max_bytes})")
    try:
        # JSON serializes integer keys as strings. Detect collisions such as
        # {1: ..., "1": ...} before replacing a previously readable artifact.
        json.loads(content, object_pairs_hook=_pairs, parse_float=_float,
                   parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise _error(path, "value cannot be saved as unambiguous JSON") from exc
    root = Path(managed_root) if managed_root is not None else None
    try:
        with _directory(path, root, create=True) as directory:
            directory.target(path.name, missing_ok=True)
            name = f".{path.name}.{uuid.uuid4().hex}.tmp"
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 mode=0o600, dir_fd=directory.descriptor)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                directory.verify()
                directory.target(path.name, missing_ok=True)
                os.replace(name, path.name, src_dir_fd=directory.descriptor, dst_dir_fd=directory.descriptor)
                os.fsync(directory.descriptor)
                directory.verify()
            finally:
                try:
                    os.unlink(name, dir_fd=directory.descriptor)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        raise _error(path, "unable to safely replace state; restore or repair this state directory") from exc
    return path
