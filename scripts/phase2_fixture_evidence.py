"""Record one fixed, reviewed AMU-owned patch workflow with actual timings.

Run from an AMU checkout only after reviewing the bundled fixture.
The explicit flag authorizes only the four hash-pinned named fixture cases.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path

from amu import benchmark, fixture_verification
from amu.services.impact import PatchRequest, execute_patch
from amu.services.impact_report import PATCH_SECTIONS


def _wire_bytes(packet: dict) -> int:
    """Match the CLI's indented --json response, including its final LF."""
    return len((json.dumps(packet, indent=2) + "\n").encode("utf-8"))


def _write(path: Path, packet: dict) -> None:
    path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record(output: Path) -> dict:
    if not output.is_absolute() or output.exists():
        raise ValueError("output must be an absolute absent directory")
    output.mkdir(mode=0o700)
    analyses, verifications, measurements = {}, {}, {}
    with tempfile.TemporaryDirectory(prefix="amu-phase2-measure-") as temporary:
        root = Path(temporary) / "fixture"
        fixture_verification.export_fixture(root)
        for case in fixture_verification.CASES:
            patch = (root / "patches" / f"{case}.diff").read_text(encoding="utf-8")
            started = time.perf_counter()
            first = execute_patch(str(root), PatchRequest(patch, base="worktree", section="changes", page_size=100))
            initial_latency = time.perf_counter() - started
            analyses[case] = first
            retrieval_started = time.perf_counter()
            complete_bytes = 0
            for section in sorted(PATCH_SECTIONS):
                cursor = None
                page = 0
                while True:
                    packet = (first if section == "changes" and page == 0 else
                              execute_patch(str(root), PatchRequest(
                                  patch, base="worktree", section=section, cursor=cursor, page_size=100,
                              )))
                    if packet["report_hash"] != first["report_hash"]:
                        raise ValueError("source or evidence changed while paging the fixed fixture")
                    complete_bytes += _wire_bytes(packet)
                    _write(output / f"analysis-{case}-{section}-{page}.json", packet)
                    cursor = packet["page"]["next_cursor"]
                    if cursor is None:
                        break
                    page += 1
            retrieval_latency = time.perf_counter() - retrieval_started
            started = time.perf_counter()
            verification = fixture_verification.verify_fixture(case, execute_reviewed_fixture=True)
            verification_latency = time.perf_counter() - started
            verifications[case] = verification
            _write(output / f"verification-{case}.json", verification)
            measurements[case] = {
                "analysis_latency_seconds": initial_latency,
                "evidence_retrieval_seconds": retrieval_latency,
                "verification_latency_seconds": verification_latency,
                "initial_response_bytes": _wire_bytes(first),
                "complete_evidence_response_bytes": complete_bytes,
                "graph_provider": {"status": "not_requested", "reason": "Fixed fixture analysis did not request optional graph queries."},
                "basis": "perf_counter around one static service call, all section/page calls, and one pinned fixture verification; response bytes use CLI --json serialization",
            }
    scorecard = benchmark.fixture_workflow_record(analyses, verifications, measurements)
    _write(output / "workflow-scorecard.json", scorecard)
    summary = {
        "schema_version": 1,
        "fixture_version": fixture_verification.FIXTURE_VERSION,
        "trusted_manifest_sha256": fixture_verification.TRUSTED_MANIFEST_SHA256,
        "case_results": {case: {"status": verifications[case]["status"],
                                "matches_expected_outcome": verifications[case]["matches_expected_outcome"],
                                "analysis_report_hash": analyses[case]["report_hash"],
                                "finding_count": analyses[case]["assessment"]["counts"]["findings"],
                                "measurements": measurements[case]}
                         for case in fixture_verification.CASES},
        "engineering_correctness": scorecard["engineering_correctness"],
        "static_accuracy": scorecard["static_accuracy"],
        "agent_effectiveness": scorecard["agent_effectiveness"],
        "output": str(output),
        "limitation": "Timing varies by host; disposable repository paths and report hashes vary by run. All detailed evidence pages are saved here.",
    }
    _write(output / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="absolute absent directory for result files")
    parser.add_argument("--execute-reviewed-fixture", action="store_true",
                        help="run only the four pinned AMU-owned candidates and protected checks")
    arguments = parser.parse_args()
    if not arguments.execute_reviewed_fixture:
        parser.error("--execute-reviewed-fixture is required; review the bundled source, runner, contract, tests and diffs first")
    summary = record(arguments.output)
    print(json.dumps(summary, indent=2))
    engineering = summary["engineering_correctness"]
    return 0 if (engineering["matches_independent_labels"] == len(fixture_verification.CASES)
                 and engineering["runtime_transport_failures"] == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
