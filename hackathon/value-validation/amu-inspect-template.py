"""Local frozen AMU MCP adapter for one exact synthetic treatment root."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

from amu import mcp_manifest, mcp_verification

CONDITION = Path(__file__).resolve().parent.parent
REPO = CONDITION / 'repo'
ARTIFACTS = CONDITION / 'artifacts'
SOURCE = Path('/Users/Abslock128/Documents/ChatGPT/AMU/phase1-continuation-2026-10-10/final-frozen-source/amu-0.2.0')
spec = importlib.util.spec_from_file_location('accepted_raw_mcp', SOURCE / 'scripts/smoke_mcp_wire.py')
wire = importlib.util.module_from_spec(spec); spec.loader.exec_module(wire)
CONTROL = ARTIFACTS / 'local-mcp-control'; CONTROL.mkdir(exist_ok=True)
allowed = ['amu_search', 'amu_graph', 'amu_impact', 'amu_docs_reviews']
assert len(sys.argv) in [2, 3], 'Usage: amu_inspect.py TOOL JSON_ARGUMENTS (repo injected); or --schema'
name = sys.argv[1]
assert name in allowed or name == '--schema', 'Inspection tools only'
arguments = json.loads(sys.argv[2]) if len(sys.argv) == 3 else {}
assert 'repo' not in arguments or Path(arguments['repo']).resolve() == REPO.resolve()
arguments['repo'] = str(REPO.resolve())
launcher = f'''import os
os.environ.update(PATH="/opt/homebrew/Caskroom/entire/0.10.5:/Users/Abslock128/go/bin:/opt/homebrew/bin:/usr/bin:/bin", AMU_DISABLE_MODELS="1", ENTIRE_PLUGIN_DATA_DIR={str(CONDITION / 'cache')!r})
from amu import mcp_server
mcp_server.main(["--allow-repo", {str(REPO.resolve())!r}])
'''
started = time.perf_counter(); stamp = time.time_ns()
receipt = {'started_at_utc': datetime.now(timezone.utc).isoformat(),
           'tool': name, 'arguments': arguments, 'local_only': True,
           'live_configuration_changed': False, 'models_used_by_amu': False}
with wire.RawSession(sys.executable, CONTROL, code=launcher, timeout=45) as session:
    initialized = time.perf_counter()
    receipt['build_identity'] = mcp_verification._initialize_identity(session.initialize())
    assert receipt['build_identity']['build_sha256'] == '880131726cdfd53d8d572a4ebb2ccfcba278179a7ceee5136a2c33c19d415f0e'
    receipt['initialization_seconds'] = time.perf_counter() - initialized
    frame, tools = session.call('case-tools', 'tools/list', {})
    wire.assert_bounded(frame)
    receipt['manifest'] = mcp_manifest.compare_tools(tools['result']['tools'])
    assert receipt['manifest']['match']
    (ARTIFACTS / f'amu-{stamp}-tools.jsonl').write_bytes(frame)
    if name == '--schema':
        payload = tools['result']['tools']
        receipt['analysis_seconds'] = 0
    else:
        analysis_started = time.perf_counter()
        frame, response = session.call('case-call', 'tools/call', {'name': name, 'arguments': arguments})
        receipt['analysis_seconds'] = time.perf_counter() - analysis_started
        wire.assert_bounded(frame)
        (ARTIFACTS / f'amu-{stamp}-result.jsonl').write_bytes(frame)
        receipt['response'] = response
        if 'error' in response or response.get('result', {}).get('isError'):
            payload = response
        else:
            text = '\n'.join(x['text'] for x in response['result']['content'] if x.get('type') == 'text')
            payload = json.loads(text)
    receipt['frames'] = session.frames
    receipt['maximum_frame_bytes'] = max(row['bytes'] for row in session.frames)
receipt['total_elapsed_seconds'] = time.perf_counter() - started
receipt['payload'] = payload
(ARTIFACTS / f'amu-{stamp}.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps({'tool': name, 'result': payload,
                  'analysis_seconds': receipt['analysis_seconds'],
                  'total_elapsed_seconds': receipt['total_elapsed_seconds']}, indent=2))
