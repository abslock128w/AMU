import json
import subprocess
import sys
import threading
import time

from amu import state, watch

CONTRACT = {"phases": [{"n": 1, "kind": "additive", "nodes": [], "files": ["a.py"], "tests": []},
                       {"n": 3, "kind": "interior", "nodes": ["n1"], "files": ["b.py"], "tests": []}],
            "allowed_files": ["a.py", "b.py"], "targets": [], "frozen_signatures": [], "base_sha": "x", "map_hash": "h", "created_at": "n"}
MAP = {"nodes": [{"id": "n1", "path": "b.py", "confidence": "sound", "verify": "v"}, {"id": "n2", "path": "u.py", "confidence": "unknown", "verify": "amu verify --node n2"}]}


def test_file_in_phase_is_editing():
    r = watch.resolve_change("a.py", CONTRACT, MAP, phase=1)
    assert r["nodes"] == {} and any("phase file" in w for w in r["warnings"])


def test_later_phase_file_is_drift():
    r = watch.resolve_change("b.py", CONTRACT, MAP, phase=1)
    assert r["nodes"] == {"n1": "drift"} and any("PHASE_DRIFT" in w for w in r["warnings"])


def test_outside_contract_warns_and_unknown_touched():
    r = watch.resolve_change("u.py", CONTRACT, MAP, phase=1)
    assert any("outside the contract" in w for w in r["warnings"]) and any("UNKNOWN_TOUCHED" in w for w in r["warnings"])


def test_amu_and_git_paths_ignored():
    assert watch.resolve_change(".amu/state.json", CONTRACT, MAP, 1)["ignored"]


def test_watch_cannot_write_a_green_verification_state(tmp_path):
    state.write_json("state.json", {"nodes": {"n1": "planned"}, "violations": [], "history": []}, str(tmp_path))

    updated = watch.apply(str(tmp_path), {"nodes": {"n1": "green", "n2": "editing"}})

    assert updated["nodes"] == {"n1": "planned", "n2": "editing"}
    assert updated["history"] == []


def test_live_watch_updates_state_json(fake_entire, tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path)
    (tmp_path / "b.py").write_text("x = 1\n")
    state.write_json("contract.json", CONTRACT, str(tmp_path))
    state.write_json("map.json", {**MAP, "root": {"file": "a.py", "exports": 1, "internal": 0, "feature": "F", "feature_source": "derived"},
                                  "ripples": [], "features": {}, "radius": {}, "resolution": {}, "next": "", "map_hash": "h",
                                  "nodes": [{**n, "symbol": None, "risk": "unknown", "feature": "F", "order": 1, "action": "edit", "via": None, "reason": "r"} for n in MAP["nodes"]]}, str(tmp_path))

    def edit():
        # The watcher snapshots the tree once it has started, and a slow runner can take seconds to import it; an edit
        # before that snapshot is never seen. Keep editing through the window so at least one lands after it.
        for value in range(2, 8):
            time.sleep(1.0)
            (tmp_path / "b.py").write_text(f"x = {value}\n")
    threading.Thread(target=edit, daemon=True).start()
    r = subprocess.run([sys.executable, "-m", "amu.cli", "watch", "--phase", "1", "--no-live", "--seconds", "8"], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    st = json.loads((tmp_path / ".amu" / "state.json").read_text())
    assert st["nodes"].get("n1") == "drift", r.stdout + r.stderr
