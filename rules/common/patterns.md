# Common Patterns

## Repository Pattern

Encapsulate data access behind a consistent interface:
- Define standard operations: find, create, update, delete
- Concrete implementations handle storage details
- Business logic depends on the abstract interface

## Circuit Breaker Pattern

Prevent cascade failures when external services are down:
- Track failure counts per service
- Open circuit after threshold failures
- Cool down before retrying
- Provide fallback behavior

```python
from utils.crewai_breaker import guarded_crewai_kickoff, BreakerOpen

try:
    result = guarded_crewai_kickoff(crew, model_name="my-model", timeout_s=240)
except BreakerOpen as e:
    # e.cooldown_s is the REAL remaining cooldown
    log.warning(f"Breaker open for {e.cooldown_s:.1f}s — falling back")
    # ... fall back to a different model or skip
```

## Config-Driven Pattern

All tunables in config, not hardcoded:
- `config/config.yaml` for values
- `config/config_schemas.py` for validation
- Access via `config.get("section", {}).get("key", default)`

## Atomic Write Pattern

Prevent corruption on crash:
```python
import tempfile
import os

def atomic_write(path, content):
    """Write to temp file, then replace."""
    dir_name = os.path.dirname(path)
    with tempfile.NamedTemporaryFile(mode='w', dir=dir_name, delete=False) as f:
        f.write(content)
        temp_path = f.name
    os.replace(temp_path, path)
```

## Context Manager Pattern

Resource management with `with` statement:
```python
from contextlib import contextmanager

@contextmanager
def managed_resource():
    resource = acquire_resource()
    try:
        yield resource
    finally:
        release_resource(resource)
```

## API Response Format

Use a consistent envelope for all API responses:
```python
{
    "success": True,
    "data": {...},
    "error": None,
    "metadata": {"total": 100, "page": 1}
}
```
