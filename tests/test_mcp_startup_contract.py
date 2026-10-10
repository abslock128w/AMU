"""Public SDK registration must enforce the reviewed inspection contract."""

import inspect

import pytest

from amu import mcp_manifest, mcp_server, mcp_transport


def _sdk():
    pytest.importorskip("mcp")
    try:
        from mcp.server import fastmcp as sdk
        return sdk, "FastMCP"
    except ImportError:
        from mcp.server import mcpserver as sdk
        return sdk, "MCPServer"


def test_startup_refuses_sdk_without_public_nonempty_server_version(tmp_path, monkeypatch):
    sdk, name = _sdk()

    class UnsupportedServer:
        def __init__(self, name, instructions):
            pytest.fail("an SDK with no public server version must not start")

    monkeypatch.setattr(sdk, name, UnsupportedServer)
    with pytest.raises(SystemExit) as failure:
        mcp_server.main(["--allow-repo", str(tmp_path)])
    assert failure.value.code == 3


def test_startup_refuses_unavailable_payload_identity(tmp_path, monkeypatch):
    sdk, name = _sdk()
    ran = []
    identity = {**mcp_manifest.public_identity(), "build_sha256": None}
    monkeypatch.setattr(mcp_manifest, "public_identity", lambda: identity)
    monkeypatch.setattr(mcp_transport, "run_stdio", lambda *args, **kwargs: ran.append(True))
    with pytest.raises(SystemExit) as failure:
        mcp_server.main(["--allow-repo", str(tmp_path)])
    assert failure.value.code == 3 and not ran


def test_startup_refuses_registered_schema_drift_before_running(tmp_path, monkeypatch):
    _sdk()
    real_tool = mcp_server._strict_mcp_tool
    ran = []

    def changed_schema(fn, policy):
        tool = real_tool(fn, policy)
        if fn.__name__ == "amu_impact":
            signature = inspect.signature(tool)
            tool.__signature__ = signature.replace(parameters=[
                parameter.replace(default=None) if parameter.name == "repo" else parameter
                for parameter in signature.parameters.values()
            ])
        return tool

    monkeypatch.setattr(mcp_server, "_strict_mcp_tool", changed_schema)
    monkeypatch.setattr(mcp_transport, "run_stdio", lambda *args, **kwargs: ran.append(True))
    with pytest.raises(SystemExit) as failure:
        mcp_server.main(["--allow-repo", str(tmp_path)])
    assert failure.value.code == 2 and not ran


def test_startup_preserves_actions_mode_but_checks_inspection_schema_subset(tmp_path, monkeypatch):
    sdk, name = _sdk()
    ran = []
    monkeypatch.setattr(mcp_transport, "run_stdio", lambda server, **kwargs: ran.append((server, kwargs)))
    mcp_server.main(["--allow-repo", str(tmp_path), "--enable-actions"])
    assert len(ran) == 1
    server, options = ran[0]
    assert isinstance(server, getattr(sdk, name))
    assert options["name"] == "TWC Thugs · amu"
    assert options["version"] == mcp_manifest.server_version(mcp_manifest.public_identity())
    assert "Actions are enabled for trusted projects" in options["instructions"]
