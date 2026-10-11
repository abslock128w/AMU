"""Credential store: ~/.amu/credentials.json (mode 0600, AMU_HOME-aware). Bring your own provider.
Secrets are never printed or logged; `source()`/`masked()` are the only things callers may show."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from amu.workspace import home

ENV = "ANTHROPIC_API_KEY"
GEMINI_ENV = "GEMINI_API_KEY"

PROVIDERS = {
    # name: (family, default model, default base_url, needs_key)
    "anthropic": ("anthropic", "claude-opus-5", None, True),
    "bedrock": ("anthropic", "anthropic.claude-opus-5", None, False),      # AWS credentials from the environment
    "vertex": ("anthropic", "claude-opus-5", None, False),                 # GCP ADC
    "foundry": ("anthropic", "claude-opus-5", None, True),                 # resource + key
    "openai-compatible": ("openai", None, None, False),                    # any /v1/chat/completions endpoint
    "ollama": ("openai", "llama3.1", "http://localhost:11434/v1", False),
    "lmstudio": ("openai", None, "http://localhost:1234/v1", False),
    "openrouter": ("openai", None, "https://openrouter.ai/api/v1", True),
    # Native Gemini Developer API. This matches the documented
    # ``models/<model>:generateContent`` route and ``X-goog-api-key`` header.
    "gemini": ("gemini", "gemini-flash-latest", "https://generativelanguage.googleapis.com/v1beta/models", True),
}


def _path() -> Path:
    return home() / "credentials.json"


def _read() -> dict:
    try:
        with _directory() as (_, directory):
            fd = os.open('credentials.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, 'r') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    return {}
                value = json.load(stream)
                return value if isinstance(value, dict) else {}
    except (ValueError, OSError):
        return {}


@contextmanager
def _directory():
    p = _path()
    fd = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError('credential directory must be owned by you and not writable by others')
        yield p, fd
    finally:
        os.close(fd)


def _check_target(directory):
    try:
        info = os.stat('credentials.json', dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('credential file must be a regular, unlinked file')


def _write(d: dict) -> str:
    with _directory() as (p, directory):
        _check_target(directory)
        name = f'.credentials-{uuid.uuid4().hex}.tmp'
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'w') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(json.dumps(d) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            _check_target(directory)
            os.replace(name, p.name, src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass
        return str(p)


def validate_endpoint(url: str | None) -> None:
    if url is None:
        return
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme in ('http', 'https') and parsed.hostname and
                 parsed.username is None and parsed.password is None and
                 not parsed.query and not parsed.fragment and
                 not any(c.isspace() or ord(c) < 32 for c in url))
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('base URL must be HTTP(S), with a host and without user info, query, or fragment')


def set_credential(provider: str = "anthropic", api_key: str | None = None, base_url: str | None = None, model: str | None = None,
                   region: str | None = None, project_id: str | None = None, resource: str | None = None) -> str:
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider}; choose one of {', '.join(PROVIDERS)}")
    family, default_model, default_url, needs_key = PROVIDERS[provider]
    validate_endpoint(base_url or default_url)
    api_key = (api_key or "").strip() or None
    if provider == "anthropic" and api_key and not api_key.startswith("sk-ant-"):
        raise ValueError("that does not look like an Anthropic API key (expected sk-ant-…)")
    if needs_key and not api_key:
        raise ValueError(f"provider {provider} needs an API key")
    if provider == "vertex" and not (project_id and region):
        raise ValueError("vertex needs --project and --region")
    if provider == "bedrock" and not region:
        raise ValueError("bedrock needs --region")
    if provider == "foundry" and not resource:
        raise ValueError("foundry needs --resource")
    if family in ("openai", "gemini") and not (base_url or default_url):
        raise ValueError(f"provider {provider} needs --base-url")
    if family in ("openai", "gemini") and not (model or default_model):
        raise ValueError(f"provider {provider} needs --model")
    if provider == "openrouter" and "/" not in (model or ""):
        raise ValueError("openrouter models are namespaced, e.g. anthropic/claude-opus-5 or openai/gpt-4o — see openrouter.ai/models")
    rec = {"provider": provider, "family": family, "api_key": api_key, "base_url": base_url or default_url, "model": model or default_model,
           "region": region, "project_id": project_id, "resource": resource}
    return _write({k: v for k, v in rec.items() if v is not None})


def set_key(value: str) -> str:
    """Back-compat: plain Anthropic key."""
    return set_credential("anthropic", api_key=value)


def remove_key() -> bool:
    with _directory() as (_, directory):
        _check_target(directory)
        try:
            os.unlink('credentials.json', dir_fd=directory)
        except FileNotFoundError:
            return False
        return True


def _ant_profile() -> bool:
    if not shutil.which("ant"):
        return False
    try:
        r = subprocess.run(["ant", "auth", "status"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and "no " not in r.stdout.lower()[:200]


def models_disabled() -> bool:
    """Trusted process switch; only the exact value ``1`` disables models."""
    return os.environ.get("AMU_DISABLE_MODELS") == "1"


def credential() -> dict:
    """Effective credential record.

    ``AMU_DISABLE_MODELS=1`` stops discovery before environment credentials,
    stored credentials or an ambient profile can enable model access.
    Environment credentials win in this order: ``ANTHROPIC_API_KEY``, then
    ``GEMINI_API_KEY``, then an OpenAI-compatible endpoint. ``GOOGLE_API_KEY``
    is accepted only when ``AMU_PROVIDER=gemini`` was explicit, so an unrelated
    Google credential cannot silently turn the model on.
    """
    if models_disabled():
        return {"provider": None, "family": None, "source": "absent"}
    if os.environ.get(ENV):
        return {"provider": "anthropic", "family": "anthropic", "api_key": os.environ[ENV], "base_url": os.environ.get("ANTHROPIC_BASE_URL"),
                "model": os.environ.get("AMU_MODEL") or "claude-opus-5", "source": "env"}
    gemini_key = os.environ.get(GEMINI_ENV)
    if not gemini_key and os.environ.get("AMU_PROVIDER") == "gemini":
        gemini_key = os.environ.get("GOOGLE_API_KEY")
    if gemini_key:
        _, default_model, default_url, _ = PROVIDERS["gemini"]
        return {"provider": "gemini", "family": "gemini", "api_key": gemini_key, "base_url": default_url,
                "model": os.environ.get("AMU_MODEL") or default_model, "source": "env"}
    if os.environ.get("OPENAI_BASE_URL") or os.environ.get("AMU_PROVIDER") in ("openai-compatible", "ollama", "lmstudio", "openrouter"):
        prov = os.environ.get("AMU_PROVIDER") or "openai-compatible"
        return {"provider": prov, "family": "openai", "api_key": os.environ.get("OPENAI_API_KEY"), "base_url": os.environ.get("OPENAI_BASE_URL") or PROVIDERS[prov][2],
                "model": os.environ.get("AMU_MODEL") or PROVIDERS[prov][1], "source": "env"}
    f = _read()
    if f.get("provider") == "gemini":
        # Migrate the short-lived OpenAI-compatible Gemini record in place at
        # read time. It used a model name that is not available to all AI
        # Studio/free-tier keys, while the native API user's verified command
        # uses the stable ``gemini-flash-latest`` alias.
        _, default_model, default_url, _ = PROVIDERS["gemini"]
        legacy_base = "https://generativelanguage.googleapis.com/v1beta/openai"
        legacy_model = "gemini-3.8-flash"
        return {
            **f,
            "family": "gemini",
            "base_url": default_url if f.get("base_url") in (None, legacy_base) else f["base_url"],
            "model": default_model if f.get("model") in (None, legacy_model) else f["model"],
            "source": "file",
        }
    if f.get("provider"):
        return {**f, "source": "file"}
    if f.get("anthropic_api_key"):  # pre-provider file shape
        return {"provider": "anthropic", "family": "anthropic", "api_key": f["anthropic_api_key"], "model": "claude-opus-5", "source": "file"}
    if _ant_profile():
        return {"provider": "anthropic", "family": "anthropic", "api_key": None, "model": "claude-opus-5", "source": "ant-profile"}
    return {"provider": None, "family": None, "source": "absent"}


def source() -> str:
    return credential()["source"]


def available() -> bool:
    return source() != "absent"


def resolve() -> str | None:
    return credential().get("api_key")


def masked() -> str:
    c = credential()
    k = c.get("api_key")
    tail = f"…{k[-4:]}" if k else "(no key: ambient credentials)"
    return f"{c.get('provider')} · {c.get('model')} · {tail}" + (f" · {c['base_url']}" if c.get("base_url") else "")
