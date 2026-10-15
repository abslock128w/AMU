"""Build AMU's index (facts and graph) of a workspace into the directory the hook's checks use.

Usage: prebuild_index.py WORKSPACE INDEX_DIR, with PYTHONPATH naming the AMU checkout. Run before the session starts;
not charged to the session's test-time budget.
"""
import sys

from amu.core.index import state

if __name__ == '__main__':
    facts, hashes, gaps, parsed, graph = state(sys.argv[1], sys.argv[2])
    print(len(hashes), 'files', len(graph.records), 'records')
