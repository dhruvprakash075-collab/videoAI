---
paths:
  - "**/*.py"
  - "**/*.pyi"
---
# Python Patterns

> This file extends [common/patterns.md](../common/patterns.md) with Python-specific content.

## Protocol (Duck Typing)

```python
from typing import Protocol

class Repository(Protocol):
    def find_by_id(self, id: str) -> dict | None: ...
    def save(self, entity: dict) -> dict: ...
```

## Dataclasses as DTOs

```python
from dataclasses import dataclass

@dataclass
class CreateUserRequest:
    name: str
    email: str
    age: int | None = None
```

## Context Managers & Generators

- Use context managers (`with` statement) for resource management
- Use generators for lazy evaluation and memory-efficient iteration

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

## Error Handling Patterns

```python
# Specific exceptions
class BreakerOpen(Exception):
    def __init__(self, model: str, cooldown_s: float):
        self.model = model
        self.cooldown_s = cooldown_s
        super().__init__(f"Breaker open for {model}, cooldown {cooldown_s:.1f}s")

# Exception chaining
try:
    result = risky_operation()
except SpecificError as e:
    raise HigherLevelError("Context") from e
```

## Reference

See agent: `python-reviewer` for comprehensive Python patterns and idioms.
