"""Present saved, actual demo evidence; does not rerun analysis or alter source."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
HACKATHON = HERE.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screen", choices=("bug", "graph", "impact", "patch", "tests", "comparison", "limits"))
    args = parser.parse_args()
    replay = json.loads((HERE / "runs/verified-replay.json").read_text())
    comparison = json.loads((HACKATHON / "value-validation/comparison-results.json").read_text())
    if replay["status"] != "passed":
        raise SystemExit("The archived replay is not verified.")
    print("AMU | saved actual local evidence | synthetic checkout case\n")

    if args.screen == "bug":
        print("5 cents, 50% discount; nearest cent, exact halves up.")
        print(f"Observed original: {replay['original_observed_total_cents']} cents. Required: 3 cents.")
        print("The original eight available tests passed despite this bug.\n")
        source = HACKATHON / "fixtures/checkout/shop/pricing.py"
        for number, line in enumerate(source.read_text().splitlines(), 1):
            print(f"shop/pricing.py:{number:2}  {line}")
    elif args.screen == "graph":
        graph = next(c["payload"] for c in replay["calls"] if c["tool"] == "amu_graph")
        print(f"Actual amu_graph result: {len(graph['nodes'])} nodes, {len(graph['edges'])} edges.")
        for node in graph["nodes"]:
            if node["path"].startswith("shop/"):
                print(f"  {node['path']}#{node['name']} | {node['section']} | depth {node.get('depth', 0)}")
        print("\nGraph caller depth is relative to the shared pricing function.")
        print("Source corroboration of the indirect receipt path:")
        for relative in ("shop/receipts.py", "shop/quote.py", "shop/checkout.py"):
            for number, line in enumerate((HACKATHON / "fixtures/checkout" / relative).read_text().splitlines(), 1):
                if "quote(" in line or "discounted_total_cents(" in line:
                    print(f"  {relative}:{number}  {line.strip()}")
        print("\nRaw edge provenance labels: " + ", ".join(sorted({e.get('provenance', 'unknown') for e in graph['edges']})))
        print("Static relationships identify places to investigate; they do not prove breakage.")
    elif args.screen == "impact":
        impact = next(c["payload"] for c in replay["calls"] if c["tool"] == "amu_impact")
        print(f"Actual intent-impact status: {impact['status']}; behavioral safety remains unknown.")
        print("Every evidence section was retrieved from the same versioned report:")
        for name, section in replay["sections"].items():
            print(f"  {name}: {section['entries']} entries, {section['pages']} page(s), complete={section['complete']}")
        print(f"\nOld cursor refused after source edit: {replay['old_cursor_refused_after_source_change']}")
        print("The genuine empty review queue and an ungranted-root denial also passed.")
        print("Intent analysis does not execute or semantically verify the proposed patch.")
    elif args.screen == "patch":
        print("Actual captured treatment patch (pricing hunk):")
        patch = (HACKATHON / "value-validation/treatment.patch").read_text()
        print(patch.split("diff --git a/tests/", 1)[0].rstrip())
        print("The full saved patch also adds seven regression tests.")
        print(f"Disposable replay observed the corrected total: {replay['fixed_observed_total_cents']} cents.")
    elif args.screen == "tests":
        command = next(c for c in replay["commands"] if c["label"] == "captured final regression suite replay")
        print("Actual replayed agent-selected suite:")
        print((command["stdout"] + command["stderr"]).rstrip())
        print("\nSame frozen independent evaluator:")
        for check in replay["independent_evaluation"]["checks"]:
            print(f"  {'PASS' if check['passed'] else 'FAIL'} [{check['category']}] {check['name']}")
        print(f"\nWire frames: {len(replay['frames'])}; largest {replay['maximum_frame_bytes']:,} / 131,072 bytes.")
    elif args.screen == "comparison":
        metrics = comparison["metrics"]
        print("One illustrative case, two fresh agent contexts, identical starting source.")
        print("Condition                  Independent checks   Selected tests   Task clock")
        for condition, label in (("baseline", "Ordinary exploration"), ("treatment", "+ AMU")):
            run = comparison["runs"][condition]
            print(f"{label:26} {run['independent_result']['passed']:2}/10                15 passed        {run['task_elapsed_seconds']:.2f} s")
        print(f"\nAMU index preparation, separate: {metrics['index_setup_seconds']:.3f} s.")
        print(f"AMU process-command time during task: {metrics['treatment_amu_process_command_wall_seconds']:.3f} s.")
        print("Both found quote, checkout and the indirect receipt consumer.")
        print("AMU corroborated them with relationship/coverage packets. Baseline was faster.")
        print("No correctness or speed advantage was observed; causal attribution is unavailable.")
    else:
        print("Evidence: graph relationships; agent reasoning: patch and test selection;")
        print("verification: executed tests for the behaviors they cover.")
        print("Static coverage is bounded; raw graph edge provenance is unknown here.")
        print("Treatment-only skill read and unsuccessful queries are retained and disclosed.")
        print("One case, imperfect controls; not a general benchmark or guarantee.")
        print("EHpaIc is frozen. Authenticated ChatGPT/NumPy acceptance remains open.")
        print("This saved local replay is not a live agent run or a completed screen recording.")


if __name__ == "__main__":
    main()
