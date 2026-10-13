"""Read-only ingestion of operator-supplied pytest-cov execution evidence.

No target imports or collection. Observed tests are prioritized, never used to
exclude other tests. A source binding checks freshness, not artifact authenticity.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

VERSION = 1
MAX_OBSERVATIONS = 100_000
MAX_CONTEXT_BYTES = 2048
CONFIG_FILES = ('pyproject.toml', 'pytest.ini', 'setup.cfg', 'setup.py', 'tox.ini',
                '.coveragerc', 'requirements.txt', 'requirements-dev.txt', 'uv.lock',
                'poetry.lock', 'Pipfile.lock')
LIMITATION = ('Recorded baseline execution does not establish assertion relevance, changed-path execution, '
              'or regression protection. Unobserved tests must not be excluded. External data, plugins, '
              'native code and environment changes are not authenticated by source hashes.')


def error(message):
    from amu.services.impact import ImpactInputError
    return ImpactInputError(message)


def _text_bytes(value):
    try:
        return len(value.encode('utf-8'))
    except UnicodeError as exc:
        raise error('coverage text must be valid UTF-8') from exc


def relative_path(value):
    if not isinstance(value, str) or not value or _text_bytes(value) > 4096:
        raise error('coverage path must be bounded repository-relative text')
    path = PurePosixPath(value)
    if (path.is_absolute() or str(path) != value or value == '.' or
            any(p in {'..', '.git', '.entire'} for p in path.parts) or '\\' in value or
            any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise error('coverage path must be normalized inside the repository')
    return value


def read_json(root, path):
    from amu.services.impact import _source
    payload, _ = _source(root, relative_path(path))

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result

    def constant(value):
        raise ValueError('non-JSON constant')

    try:
        value = json.loads(payload, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise error('coverage artifact must be bounded JSON without duplicate keys') from exc
    if not isinstance(value, dict):
        raise error('coverage artifact must be an object')
    return value, hashlib.sha256(payload).hexdigest()


def binding(root):
    from amu.services.impact import _python_inventory, _source, _source_path_present_or_unsafe
    names = set()
    for production in (False, True):
        inventory, complete = _python_inventory(root, production=production)
        if not complete:
            raise error('coverage source snapshot exceeds the complete Python inventory limit')
        names.update(inventory)
    names.update(CONFIG_FILES)
    hashes = {}
    total = 0
    for name in sorted(names):
        if _source_path_present_or_unsafe(root, name):
            payload, _ = _source(root, name)
            total += len(payload)
            if total > 8 * 1024 * 1024:
                raise error('coverage source snapshot exceeds 8 MiB')
            hashes[name] = hashlib.sha256(payload).hexdigest()
        else:
            hashes[name] = None
    return hashes


def snapshot(root, *, environment, command):
    """Capture before the operator's separately authorized baseline test run."""
    for name, value in [('environment', environment), ('command', command)]:
        if not isinstance(value, str) or not value.strip() or _text_bytes(value) > 2048:
            raise error(f'coverage {name} must be nonempty bounded text')
    hashes = binding(root)
    if hashes != binding(root):
        raise error('source changed during coverage snapshot')
    return {'format': 'amu-coverage-snapshot', 'version': VERSION,
            'created_at': datetime.now(timezone.utc).isoformat(), 'root': str(root),
            'environment': environment, 'collection_command': command,
            'source_hashes': hashes, 'collection_authorship': 'operator-supplied; not executed by AMU'}


def _validate_snapshot(value):
    if (value.get('format') != 'amu-coverage-snapshot' or value.get('version') != VERSION or
            not isinstance(value.get('source_hashes'), dict) or not value['source_hashes'] or
            not isinstance(value.get('root'), str) or not Path(value['root']).is_absolute()):
        raise error('invalid coverage source snapshot')
    for key in ('created_at', 'environment', 'collection_command'):
        if not isinstance(value.get(key), str) or not value[key] or _text_bytes(value[key]) > 2048:
            raise error('invalid coverage collection metadata')
    for path, digest in value['source_hashes'].items():
        relative_path(path)
        if digest is not None and (not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
            raise error('invalid coverage source hash')


def _file_path(path, source):
    if not isinstance(path, str):
        raise error('invalid coverage source path')
    if Path(path).is_absolute():
        try:
            path = Path(path).relative_to(source['root']).as_posix()
        except ValueError as exc:
            raise error('coverage source is outside the recorded repository') from exc
    return relative_path(path)


def _context(value, source):
    if not isinstance(value, str) or _text_bytes(value) > MAX_CONTEXT_BYTES:
        raise error('coverage context exceeds its text limit')
    if not value:
        return None  # Imports/session setup have no attributable test.
    nodeid, sep, phase = value.rpartition('|')
    if not sep or phase not in {'setup', 'run', 'teardown'} or '::' not in nodeid:
        return None  # Other context plugins/static contexts are not pytest-cov test IDs.
    path, name = nodeid.split('::', 1)
    path = relative_path(path)
    if not source['source_hashes'].get(path) or not name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise error('coverage context refers to an unbound or invalid test')
    return nodeid, phase


def _observations(files, source, *, imported=False):
    if not isinstance(files, dict):
        raise error('coverage files must be an object')
    result, count, unknown = {}, 0, 0
    for path, record in files.items():
        path = _file_path(path, source)
        if path in result:
            raise error('duplicate normalized coverage path')
        if not source['source_hashes'].get(path):
            raise error('coverage file is absent from source snapshot')
        contexts = record if imported else record.get('contexts') if isinstance(record, dict) else None
        if not isinstance(contexts, dict):
            raise error('coverage JSON needs per-line contexts; export with --show-contexts')
        rows = {}
        for line, values in contexts.items():
            if not isinstance(line, str) or not re.fullmatch('[1-9][0-9]{0,6}', line) or not isinstance(values, list):
                raise error('invalid coverage line/context list')
            count += len(values) + 1
            if count > MAX_OBSERVATIONS:
                raise error('coverage context observation limit exceeded')
            for value in values:
                if _context(value, source) is None:
                    unknown += 1
            rows[line] = sorted(set(values))
        result[path] = rows
    return result, unknown


def import_report(root, snapshot_path, coverage_path=None, *, data_path=None):
    source, _ = read_json(root, snapshot_path)
    _validate_snapshot(source)
    if source['source_hashes'] != binding(root):
        raise error('source changed since coverage snapshot; recollect baseline evidence')
    if (coverage_path is None) == (data_path is None):
        raise error('provide exactly one coverage JSON or coverage data file')
    raw, digest = read_data(root, data_path) if data_path is not None else read_json(root, coverage_path)
    if not isinstance(raw.get('meta'), dict) or raw['meta'].get('show_contexts') is not True:
        raise error('coverage JSON needs per-test contexts; use pytest-cov --cov-context=test and coverage json --show-contexts')
    observations, unknown = _observations(raw.get('files'), source)
    if source['source_hashes'] != binding(root):
        raise error('source changed during coverage import')
    table = sorted({context for rows in observations.values() for values in rows.values() for context in values})
    indexes = {value: index for index, value in enumerate(table)}
    compact = {path: {line: [indexes[v] for v in values] for line, values in rows.items()}
               for path, rows in observations.items()}
    bundle = {'format': 'amu-observed-tests', 'version': VERSION, 'snapshot': source,
            'coverage_sha256': digest, 'coverage_version': str(raw['meta'].get('version', 'unknown'))[:80],
            'observations': compact, 'context_table': table, 'unattributed_contexts': unknown,
            'limitation': LIMITATION}
    if len(json.dumps(bundle, ensure_ascii=False, separators=(',', ':')).encode()) > 2 * 1024 * 1024:
        raise error('imported coverage artifact exceeds 2 MiB; use a narrower collection or export')
    return bundle


def load(root, path):
    bundle, digest = read_json(root, path)
    if bundle.get('format') != 'amu-observed-tests' or bundle.get('version') != VERSION:
        raise error('invalid observed-test artifact format')
    source = bundle.get('snapshot')
    if not isinstance(source, dict):
        raise error('missing coverage snapshot')
    _validate_snapshot(source)
    table = bundle.get('context_table')
    compact = bundle.get('observations')
    if not isinstance(table, list) or len(table) > MAX_OBSERVATIONS or not isinstance(compact, dict):
        raise error('invalid compact coverage contexts')
    for value in table:
        _context(value, source)
    expanded, count = {}, 0
    for path, rows in compact.items():
        if not isinstance(rows, dict):
            raise error('invalid coverage observation rows')
        expanded[path] = {}
        for line, indexes in rows.items():
            if not isinstance(indexes, list):
                raise error('invalid coverage context indexes')
            count += len(indexes) + 1
            if count > MAX_OBSERVATIONS:
                raise error('coverage context observation limit exceeded')
            if any(type(i) is not int or not 0 <= i < len(table) for i in indexes):
                raise error('invalid coverage context index')
            expanded[path][line] = [table[i] for i in indexes]
    observations, unknown = _observations(expanded, source, imported=True)
    try:
        fresh = source['source_hashes'] == binding(root)
    except ValueError:
        fresh = False
    status = {'status': 'source-matched' if fresh else 'stale', 'artifact_sha256': digest,
              'environment': source['environment'], 'collection_command': source['collection_command'],
              'collection_authorship': 'operator-supplied; not authenticated by AMU',
              'environment_verified': False, 'automatic_deselection': False,
              'unattributed_contexts': unknown, 'limitation': LIMITATION,
              'prioritized_tests': 0, 'unobserved_entities': []}
    return observations if fresh else {}, source, status


def recommendations(observations, source, changes, *, aggregate_classes=frozenset()):
    """Select observed baseline entity spans, including unexecuted branch edits.

    This deliberately uses complete enclosing entity spans, not intersections
    with edited lines. Added entities have no baseline span and stay unknown.
    """
    checks, unobserved = {}, []
    for change in changes:
        if change.get('source_role', {}).get('role') != 'production':
            continue
        entity = change['id']
        if entity in aggregate_classes:
            continue
        path = entity.split('#', 1)[0]
        loc = change.get('before_location')
        seen = False
        if loc:
            per_test = {}
            for line, contexts in observations.get(path, {}).items():
                if not loc['line'] <= int(line) <= loc['end_line']:
                    continue
                for value in contexts:
                    context = _context(value, source)
                    if context:
                        nodeid, phase = context
                        entry = per_test.setdefault(nodeid, {'lines': set(), 'phases': set()})
                        entry['lines'].add(int(line))
                        entry['phases'].add(phase)
            for nodeid, entry in per_test.items():
                seen = True
                check = checks.setdefault(nodeid, {'test_id': nodeid, 'path': nodeid.split('::')[0],
                    'entities': [], 'associations': [], 'observed_execution': [],
                    'behavioral_rank': 4, 'verification_category': 'observed-baseline-execution',
                    'test_provenance': {'status': 'baseline-recorded', 'evidence_phase': 'before'}})
                check['entities'].append(entity)
                check['associations'].append({'entity_id': entity, 'relationship': 'observed-baseline-execution',
                    'resolution': 'recorded', 'obligation_evidence': [], 'changed_behavior_established': False})
                check['observed_execution'].append({'entity_id': entity, 'path': path,
                    'lines': sorted(entry['lines']), 'phases': sorted(entry['phases']),
                    'basis': 'baseline-entity-span', 'assertion_relevance': 'unknown'})
        if not seen:
            unobserved.append(entity)
    return [checks[k] for k in sorted(checks)], sorted(unobserved)


def merge(checks, observed):
    indexed = {row['test_id']: row for row in checks}
    for row in observed:
        prior = indexed.get(row['test_id'])
        if prior is None:
            checks.append(row)
        else:
            prior['observed_execution'] = row['observed_execution']
            prior['associations'].extend(row['associations'])
            prior['entities'] = sorted(set(prior['entities']) | set(row['entities']))
    checks.sort(key=lambda row: (not bool(row.get('observed_execution')), row['test_id']))


def read_data(root, path):
    """Read a private copy through Coverage.py's public API, never its plugins.

    Avoid JSON report function/class duplication and source analysis. No coverage
    configuration, target modules, or coverage plugins are loaded by CoverageData.
    """
    import tempfile

    from amu.services.impact import _source
    try:
        import coverage
    except ImportError as exc:
        raise error("coverage data import requires AMU's coverage extra; JSON import needs no extra") from exc
    payload, _ = _source(root, relative_path(path))
    if not payload.startswith(b'SQLite format 3\x00'):
        raise error('coverage data must be a Coverage.py SQLite file')
    files = {}
    try:
        with tempfile.TemporaryDirectory(prefix='amu-coverage-') as temporary:
            copy = Path(temporary) / 'coverage-data'
            copy.write_bytes(payload)
            data = coverage.CoverageData(basename=str(copy))
            try:
                data.read()
                measured = sorted(data.measured_files())
                if len(measured) > 1024:
                    raise error('coverage measured-file limit exceeded')
                count = 0
                for filename in measured:
                    if data.file_tracer(filename):
                        raise error('custom coverage file tracers are unsupported')
                    contexts = data.contexts_by_lineno(filename)
                    count += sum(len(values) + 1 for values in contexts.values())
                    if count > MAX_OBSERVATIONS:
                        raise error('coverage context observation limit exceeded')
                    files[filename] = {'contexts': {str(line): values for line, values in contexts.items()}}
            finally:
                data.close()
    except (coverage.exceptions.CoverageException, OSError, ValueError) as exc:
        raise error(f'coverage data import failed: {exc}') from exc
    return {'meta': {'version': coverage.__version__, 'show_contexts': True}, 'files': files}, hashlib.sha256(payload).hexdigest()


def aggregate_class_changes(before, after, changes):
    """Class AST hashes include methods; do not expand method-only edits to peers.

    Retain class-wide candidates for structural changes (bases, decorators,
    attributes, method signatures/additions/removals). No runtime claim is made.
    """
    import ast
    import copy

    class WithoutMethodBodies(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            node.body = [ast.Pass()]
            return node

        visit_AsyncFunctionDef = visit_FunctionDef

    aggregates = set()
    for row in changes:
        if row.get('kind') != 'class' or not row.get('before_location') or not row.get('after_location'):
            continue
        path = row['id'].split('#', 1)[0]
        old = before.get(path, {}).get('tree')
        new = after.get(path)
        if old is None or new is None:
            continue
        nodes = []
        for tree, location in [(old, row['before_location']), (new, row['after_location'])]:
            node = next((n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.lineno == location['line']), None)
            nodes.append(ast.dump(WithoutMethodBodies().visit(copy.deepcopy(node)), include_attributes=False) if node else None)
        if nodes[0] is not None and nodes[0] == nodes[1]:
            aggregates.add(row['id'])
    return aggregates
