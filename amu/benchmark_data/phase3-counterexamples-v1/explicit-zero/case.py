"""Resolve a requested limit while preserving explicit zero."""


def effective_limit(requested, default=5):
    if requested is None:
        return default
    if type(requested) is not int or requested < 0:
        raise ValueError("requested limit must be a nonnegative integer")
    return requested
