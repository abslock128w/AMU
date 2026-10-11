import json
import re
from pathlib import Path

import pytest

from amu import memory, state


def _repo(tmp_path):
    (tmp_path / ".amu").mkdir()
    (tmp_path / ".amu" / "feature_map.json").write_text(json.dumps({"Core": ["amu/**"]}))
    (tmp_path / "CLAUDE.md").write_text("# human notes\nkeep me\n")
    return str(tmp_path)


def test_markers_created_once_and_replaced_in_place(tmp_path):
    repo = _repo(tmp_path)
    memory.refresh(repo, files=("CLAUDE.md",))
    memory.refresh(repo, files=("CLAUDE.md",))
    txt = (tmp_path / "CLAUDE.md").read_text()
    assert txt.count(memory.START) == 1 and txt.startswith("# human notes\nkeep me\n")


def test_human_text_outside_markers_is_byte_identical(tmp_path):
    repo = _repo(tmp_path)
    memory.refresh(repo, files=("CLAUDE.md",))
    before = (tmp_path / "CLAUDE.md").read_text()
    (tmp_path / "CLAUDE.md").write_text(before + "\nafter block\n")
    memory.refresh(repo, files=("CLAUDE.md",))
    after = (tmp_path / "CLAUDE.md").read_text()
    pre_b, _, rest_b = before.partition(memory.START)
    pre_a, _, rest_a = after.partition(memory.START)
    assert pre_a == pre_b and rest_a.partition(memory.END)[2] == rest_b.partition(memory.END)[2] + "\nafter block\n"


def test_stale_flips_when_cited_file_hash_changes(tmp_path):
    repo = _repo(tmp_path)
    (tmp_path / ".amu" / "map.json").write_text(json.dumps({"nodes": [{"id": "n1", "path": "x.py", "confidence": "unknown", "reason": "r", "verify": "v"}]}))
    (tmp_path / "x.py").write_text("a")
    memory.refresh(repo, files=("CLAUDE.md",))
    (tmp_path / "x.py").write_text("b")
    assert any(e.get("stale") for e in memory.collect(repo))


def test_caps_enforced_with_overflow_note(tmp_path):
    repo = _repo(tmp_path)
    fm = {f"F{i}": [f"d{i}/**"] for i in range(20)}
    (tmp_path / ".amu" / "feature_map.json").write_text(json.dumps(fm))
    memory.refresh(repo, files=("CLAUDE.md",))
    block = (tmp_path / "CLAUDE.md").read_text().split(memory.START)[1].split(memory.END)[0]
    assert block.count("\n") <= memory.MAX_LINES and re.search(r"\+\d+ more in \.amu/memory\.md", block)


def test_secret_pattern_rejected(tmp_path):
    with pytest.raises(memory.MemoryRejected):
        memory._entry("answered_unknown", "key is sk-ant-abc123", [], str(tmp_path))


@pytest.mark.parametrize(
    "secret",
    [
        "GEMINI_API_KEY=AIza-gemini-memory-secret-123456",
        "GOOGLE_API_KEY=AIza-google-memory-secret-123456",
        "AIza1234567890abcdefghij",
        "AQ.gemini-key-format-1234567890",
    ],
)
def test_gemini_secret_patterns_are_rejected_from_memory(tmp_path, secret):
    with pytest.raises(memory.MemoryRejected):
        memory._entry("answered_unknown", f"do not retain {secret}", [], str(tmp_path))


def test_no_change_no_write(tmp_path):
    repo = _repo(tmp_path)
    memory.refresh(repo, files=("CLAUDE.md",))
    memory.render_block  # block header carries a timestamp; the write is skipped only when bytes match
    p = Path(repo) / "CLAUDE.md"
    block = memory.render_block(memory.collect(repo), repo)
    assert memory.write_block(p, block) in (True, False)
    assert memory.write_block(p, block) is False


def test_show_entries_validate_schema(tmp_path):
    repo = _repo(tmp_path)
    memory.refresh(repo, files=("CLAUDE.md",))
    for e in state.read_json("memory.json", repo)["entries"]:
        state.validate("memory_entry", e)
