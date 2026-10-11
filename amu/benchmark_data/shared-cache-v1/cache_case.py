"""Small AMU-owned fixture with a type-aware class-level shared cache."""


class ValueRecord:
    def __init__(self, kind, value):
        self.kind = kind
        self.value = value


SUPPORTED_TYPES = (int, str)


class TypedCache:
    _cache = {}

    @classmethod
    def lookup(cls, value):
        if type(value) not in SUPPORTED_TYPES:
            raise ValueError("only integers and strings are supported")
        key = (type(value).__name__, value)
        record = cls._cache.get(key)
        if record is None:
            record = ValueRecord(type(value).__name__, value)
            cls._cache[key] = record
        return record
