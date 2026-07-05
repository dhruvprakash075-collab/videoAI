"""Recursive dict merge utility."""
from __future__ import annotations


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Returns merged dict."""
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        elif key in result and isinstance(result[key], list) and isinstance(value, list):
            seen = {str(v) for v in result[key]}
            for v in value:
                if str(v) not in seen:
                    result[key].append(v)
                    seen.add(str(v))
        else:
            result[key] = value
    return result
