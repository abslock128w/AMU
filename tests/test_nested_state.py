import json

import pytest

from amu import state


@pytest.mark.parametrize("filename,value", [
    ("map.json", {"nodes": [None]}),
    ("map.json", {"nodes": [{"confidence": True}]}),
    ("map.json", {"root": []}),
    ("contract.json", {"phases": ["invalid"]}),
    ("contract.json", {"phases": [{"n": True}]}),
    ("contract.json", {"targets": [{"symbol": []}]}),
    ("contract.json", {"allowed_files": [None]}),
    ("state.json", {"nodes": {"n1": True}}),
    ("state.json", {"history": [False]}),
    ("state.json", {"violations": [{"severity": "SUCCESS"}]}),
    ("memory.json", {"entries": [{"pinned": "false"}]}),
    ("memory.json", {"entries": ["invalid"]}),
    ("refresh-state.json", {"targets": []}),
    ("refresh-state.json", {"targets": {"api.py#f": {"complete": "true"}}}),
    ("sync-state.json", {"readiness": []}),
    ("config.json", {"docs": []}),
    ("config.json", {"test_cmd": ["pytest"]}),
    ("unknown-answers.json", {"answers": [{"confirmed": "true"}]}),
])
def test_nested_corruption_is_reported_and_preserved(tmp_path, filename, value):
    path = state.amu_dir(str(tmp_path)) / filename
    payload = json.dumps(value)
    path.write_text(payload)
    with pytest.raises(state.StateReadError):
        state.read_json(filename, str(tmp_path), default={})
    with pytest.raises(state.StateReadError):
        state.update_json(filename, str(tmp_path), {}, lambda _: {})
    assert path.read_text() == payload


def test_unversioned_partial_artifacts_stay_inspectable(tmp_path):
    for filename, value in (("map.json", {"nodes": [{"id": "n1"}]}),
                            ("contract.json", {"base_sha": "HEAD"}),
                            ("state.json", {"history": [{}]})):
        state.write_json(filename, value, str(tmp_path))
        assert state.read_json(filename, str(tmp_path)) == value


def test_invalid_generated_contract_is_refused_before_replacement(tmp_path):
    contract = {"targets": [], "base_sha": "HEAD", "map_hash": "m", "phases": [],
                "frozen_signatures": [], "allowed_files": [], "created_at": "now"}
    path = state.write_json("contract.json", contract, str(tmp_path), kind="contract")
    before = path.read_bytes()
    with pytest.raises(state.SchemaError):
        state.write_json("contract.json", {**contract, "phases": [False]}, str(tmp_path), kind="contract")
    assert path.read_bytes() == before


def test_public_writes_reject_malformed_nested_state_without_replacing_it(tmp_path):
    path = state.write_json("state.json", {"nodes": {"n1": "planned"}}, str(tmp_path))
    before = path.read_bytes()
    with pytest.raises(state.StateReadError):
        state.write_json("state.json", {"nodes": {"n1": True}}, str(tmp_path))
    assert path.read_bytes() == before


def test_managed_path_cannot_escape_or_shift_boundary_through_nested_amu(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "state.json").write_text('{"private":true}')
    root = state.amu_dir(str(tmp_path))
    (root / "alias").symlink_to(outside, target_is_directory=True)
    for name in ("../outside/state.json", "alias/.amu/state.json"):
        with pytest.raises(state.StateReadError):
            state.write_json(name, {}, str(tmp_path))
    assert not (outside / ".amu").exists()
    assert (outside / "state.json").read_text() == '{"private":true}'
