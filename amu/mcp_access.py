"""Operator-selected authority for the local stdio server, not an OS sandbox."""

from __future__ import annotations

import json
import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

INSPECTION_TOOLS = frozenset({"amu_search", "amu_graph", "amu_impact", "amu_docs_reviews"})
PATCH_INSPECTION_TOOLS = INSPECTION_TOOLS | {"amu_analyze_patch"}
MAX_ARGUMENT_BYTES = 64 * 1024
MAX_REPOSITORIES = 16


class AccessError(ValueError):
    def __init__(self, kind: str, message: str, *, category: str | None = None, hint: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.category = category or {
            "repository_not_allowed": "REPOSITORY_NOT_AUTHORIZED",
            "repository_changed": "REPOSITORY_NOT_AUTHORIZED",
            "invalid_repository": "INVALID_REPOSITORY",
        }.get(kind, "INVALID_REQUEST")
        self.hint = hint or {
            "REPOSITORY_NOT_AUTHORIZED": (
                "Choose an explicitly authorized repository. Only the operator can review and change "
                "the server's exact-root grants; a tool request cannot expand them."
            ),
            "INVALID_REPOSITORY": "Provide an absolute existing repository directory in the MCP server's filesystem.",
        }.get(self.category, "Review the request and the operator-selected server configuration.")


@dataclass(frozen=True)
class RepositoryGrant:
    path: Path
    device: int
    inode: int


def _directory(value: object) -> Path:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise AccessError("invalid_repository", "repo is required: provide an absolute existing directory")
    path = Path(value.strip())
    if not path.is_absolute():
        raise AccessError("invalid_repository", "repo must be an absolute existing directory")
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise AccessError("invalid_repository", "repo must be an existing directory")
        return resolved
    except (FileNotFoundError, PermissionError):
        raise AccessError(
            "invalid_repository", "repo must be an existing directory accessible to the AMU MCP server",
            category="REPOSITORY_NOT_FOUND",
            hint=("The AI client and AMU MCP server may use different filesystems. A repository uploaded to "
                  "the client is not automatically available to this server. Choose an authorized local checkout "
                  "or use the operator-controlled onboarding workflow."),
        ) from None
    except AccessError:
        raise
    except (OSError, ValueError, RuntimeError):
        raise AccessError("invalid_repository", "repo must be an existing directory") from None


class MCPAccessPolicy:
    """Exact-root grants, conservative discovery and one bounded active call.

    Input is trusted operator startup configuration, never model-provided tool
    arguments. Directory identity is rechecked for every call; this does not
    sandbox native providers or arbitrary project commands against hostile OS
    filesystem races. Use only trusted repositories and clients.
    """

    def __init__(self, repositories: list[str], *, enable_actions: bool = False,
                 enable_patch_analysis: bool = False):
        if (not isinstance(repositories, list) or not repositories or len(repositories) > MAX_REPOSITORIES
                or type(enable_actions) is not bool or type(enable_patch_analysis) is not bool):
            raise AccessError("invalid_authority", "select 1–16 explicit --allow-repo roots")
        roots = {_directory(value) for value in repositories}
        grants = []
        for path in sorted(roots):
            try:
                info = path.stat(follow_symlinks=False)
            except OSError:
                raise AccessError("invalid_authority", "allowed repository is unavailable") from None
            if not stat.S_ISDIR(info.st_mode):
                raise AccessError("invalid_authority", "allowed repository must be a directory")
            grants.append(RepositoryGrant(path, info.st_dev, info.st_ino))
        self.grants = tuple(grants)
        self.enable_actions = enable_actions
        self.enable_patch_analysis = enable_patch_analysis
        self.inspection_tools = PATCH_INSPECTION_TOOLS if enable_patch_analysis else INSPECTION_TOOLS
        self._slot = threading.BoundedSemaphore(1)

    def allows_tool(self, name: str) -> bool:
        if name == "amu_analyze_patch":
            return self.enable_patch_analysis
        return self.enable_actions or name in self.inspection_tools

    def repository(self, value: object) -> str:
        selected = _directory(value)
        grant = next((grant for grant in self.grants if grant.path == selected), None)
        if grant is None:
            raise AccessError("repository_not_allowed", "repository is not authorized for this MCP server")
        try:
            current = grant.path.stat(follow_symlinks=False)
            if (not stat.S_ISDIR(current.st_mode)
                    or (current.st_dev, current.st_ino) != (grant.device, grant.inode)):
                raise ValueError()
        except (OSError, ValueError):
            raise AccessError("repository_changed", "authorized repository identity changed; restart after review") from None
        return str(selected)

    def acquire(self) -> bool:
        return self._slot.acquire(blocking=False)

    def release(self) -> None:
        self._slot.release()


def check_arguments(arguments: dict) -> None:
    try:
        size = len(json.dumps(arguments, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise AccessError("invalid_arguments", "tool arguments are not valid bounded JSON") from None
    if size > MAX_ARGUMENT_BYTES:
        raise AccessError("argument_limit", "tool arguments exceed 64 KiB; narrow the request")


def child_environment() -> dict[str, str]:
    """Do not pass client API keys, Python/Git injection or model settings to CLI.

    Keep required OS/PATH/home environment for the operator-selected local
    provider and state, but independently disable built-in model discovery.
    This is not secret removal from source/test commands or a process sandbox.
    """
    environment = {
        key: value for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))
        and not key.upper().startswith(("ANTHROPIC_", "GEMINI_", "OPENAI_", "PYTHON", "GIT_", "LD_", "DYLD_"))
        and key not in {"AMU_PROVIDER", "AMU_MODEL", "CONTROL_PLANE_API_KEY", "SSH_AUTH_SOCK", "GIT_ASKPASS"}
    }
    environment["AMU_DISABLE_MODELS"] = "1"
    return environment
