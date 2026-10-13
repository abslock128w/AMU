"""Collect complete static packets from frozen source cases, without importing them.

Labels are deliberately not accepted by this collection interface. Invoke once
per trusted AMU source version, with an absent output directory.
"""

import argparse
import base64
import hashlib
import json
import sys
import time
from dataclasses import replace
from pathlib import Path


def reconstruct(items):
    result, fragments = [], {}
    for item in items:
        if "evidence_fragment" not in item:
            result.append(item)
            continue
        fragment = item["evidence_fragment"]
        fragments.setdefault(fragment["item_sha256"], []).append(fragment)
    for digest, parts in fragments.items():
        parts.sort(key=lambda part: part["fragment_index"])
        assert len(parts) == parts[0]["fragment_count"]
        assert [part["fragment_index"] for part in parts] == list(range(len(parts)))
        body = b"".join(base64.b64decode(part["data"], validate=True) for part in parts)
        assert hashlib.sha256(body).hexdigest() == digest
        result.append(json.loads(body))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--inputs", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root.resolve()))
    from amu.services import impact

    args.output.mkdir(parents=True, exist_ok=False)
    measurements = []
    for case in sorted(args.inputs.iterdir()):
        if not case.is_dir():
            continue
        output = args.output / case.name
        repo = output / "repository"
        repo.mkdir(parents=True)
        (repo / "subject.py").write_bytes((case / "before.py").read_bytes())
        (repo / "test_case.py").write_bytes((case / "test_case.py").read_bytes())
        request = impact.PatchRequest((case / "patch.diff").read_text(), base="worktree")
        start = time.perf_counter()
        initial = impact.execute_patch(str(repo.resolve()), request)
        elapsed = time.perf_counter() - start
        def save(value, path):
            path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")

        save(initial, output / "initial.json")
        groups = {}
        total_bytes = 0
        for section in sorted(initial["retrieval"]):
            cursor, items, count = None, [], 0
            while True:
                page = impact.execute_patch(str(repo.resolve()), replace(request, section=section, cursor=cursor, page_size=100))
                assert page["report_hash"] == initial["report_hash"]
                save(page, output / f"{section}-{count}.json")
                total_bytes += len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode())
                items.extend(page["details"]["items"])
                cursor = page["page"]["next_cursor"]
                count += 1
                if cursor is None:
                    break
            groups[section] = reconstruct(items)
            assert len(groups[section]) == initial["retrieval"][section]["logical_items"]
        save(groups, output / "complete.json")
        measurements.append({"case": case.name, "seconds": elapsed,
                             "initial_bytes": len(json.dumps(initial, ensure_ascii=False, separators=(",", ":")).encode()),
                             "complete_packet_bytes": total_bytes,
                             "counts": {key: len(rows) for key, rows in groups.items()}})
    (args.output / "measurements.json").write_text(json.dumps(measurements, indent=2) + "\n")
    print(json.dumps({"cases": len(measurements), "output": str(args.output)}))


if __name__ == "__main__":
    main()
