"""Score reachable tests per changed symbol, then pick a diverse top list.

Signals are deliberately few: call distance, how specific the reference is,
whether a receiver was guessed, and name affinity between test and symbol.
Coverage observations, when supplied, are a boost and never a filter.
"""
from __future__ import annotations

import math
import re

DEPTH = {0: 1.0, 1: 1.0, 2: 0.6, 3: 0.36, 4: 0.22, 5: 0.13}
SPECIFICITY = {0: 1.0, 1: 0.7, 2: 0.5}
FLOOR = 0.25
OBSERVED = 3.0  # a test recorded executing the symbol outranks any static path
STOP = {'test', 'tests', 'py', 'init', 'self', 'the', 'and', 'for', 'with', 'def', 'return', 'if', 'else', 'elif', 'not', 'is',
        'in', 'or', 'none', 'true', 'false', 'import', 'from', 'as', 'class', 'raise', 'try', 'except', 'assert', 'str', 'int'}
WORD = re.compile(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+')


def tokens(text):
    found = set()
    for part in WORD.findall(text):
        part = part.lower()
        if len(part) < 2 or part in STOP:
            continue
        if len(part) > 3 and part.endswith('s'):
            part = part[:-1]
        found.add(part)
    return found


def test_tokens(record):
    return tokens(record['qual']) | tokens(record['path'].rsplit('/', 1)[-1][:-3])


def symbol_tokens(record):
    stem = tokens(record['path'].rsplit('/', 1)[-1].rsplit('.', 1)[0])
    return tokens(' '.join(record['qual'].split('.')[-2:])) | stem


class Affinity:
    """IDF-weighted share of a symbol's name found in a test's name."""

    def __init__(self, tests):
        self.names = {ident: test_tokens(record) for ident, record in tests.items()}
        frequency = {}
        for names in self.names.values():
            for token in names:
                frequency[token] = frequency.get(token, 0) + 1
        total = max(1, len(self.names))
        self.weight = {token: math.log(1 + total / count) for token, count in frequency.items()}

    def score(self, test, symbol_names):
        known = [t for t in symbol_names if t in self.weight]
        if not known:
            return 0.0, []
        shared = sorted(self.names.get(test, set()) & set(known))
        return sum(self.weight[t] for t in shared) / sum(self.weight[t] for t in known), shared


class Content:
    """IDF-weighted share of the changed lines' vocabulary found in a test's name and body."""

    def __init__(self, tests):
        self.words = {ident: test_tokens(record) | set(record.get('words', ())) for ident, record in tests.items()}
        frequency = {}
        for words in self.words.values():
            for token in words:
                frequency[token] = frequency.get(token, 0) + 1
        total = max(1, len(self.words))
        self.weight = {token: math.log(1 + total / count) for token, count in frequency.items()}

    def query(self, text):
        known = {t for t in tokens(text) if t in self.weight}
        return known, sum(self.weight[t] for t in known)

    def score(self, test, query):
        known, total = query
        if not total:
            return 0.0
        return sum(self.weight[t] for t in self.words.get(test, ()) & known) / total


def structural(rank):
    depth, specificity, approximate = rank
    return DEPTH.get(depth, 0.2) * SPECIFICITY.get(specificity, 0.5) * (0.5 if approximate else 1.0)


def diverse(scores, limit):
    """Greedy pick. A chosen test discounts each symbol in proportion to how well it covers it,
    so a weak transitive touch does not use up a symbol that another test exercises directly."""
    remaining, strongest = {}, {}
    for per_test in scores.values():
        for symbol, value in per_test.items():
            remaining[symbol] = 1.0
            strongest[symbol] = max(strongest.get(symbol, 0.0), value)
    left = dict(scores)
    order = []

    def gain(test):
        # Coverage of a symbol is discounted, never erased: relevance keeps ordering the tail.
        return sum((FLOOR + (1 - FLOOR) * remaining[s]) * v for s, v in left[test].items())

    # A test's gain never exceeds the sum of its positive values (each weight is at most 1). Scanning in that bound's order and
    # stopping once the bound falls below the best gain found gives the same pick as scanning every test.
    bound = sorted(((sum(v for v in per.values() if v > 0), t) for t, per in scores.items()), key=lambda item: (-item[0], item[1]))
    while left and len(order) < limit:
        best, best_gain = None, None
        for most, test in bound:
            if test not in left:
                continue
            if best is not None and most < best_gain:
                break
            value = gain(test)
            if best is None or (-value, test) < (-best_gain, best):
                best, best_gain = test, value
        order.append((best, best_gain))
        for symbol, value in left.pop(best).items():
            if strongest[symbol] > 0:
                remaining[symbol] *= 1 - 0.5 * value / strongest[symbol]
    return order
