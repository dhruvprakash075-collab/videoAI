---
paths:
  - "**/*.py"
  - "**/*.pyi"
---
# Python Coding Style

> This file extends [common/coding-style.md](../common/coding-style.md) with Python-specific content.

## Standards

- Follow **PEP 8** conventions
- Use **type annotations** on all function signatures
- Use **ruff** for linting (`ruff check .`)

## Immutability

Prefer immutable data structures:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class User:
    name: str
    email: str

from typing import NamedTuple

class Point(NamedTuple):
    x: float
    y: float
```

## Formatting

- **ruff** for linting and formatting
- Use `pathlib.Path` for all file paths, not `os.path`
- Use `snake_case` for functions and variables
- Use `PascalCase` for classes
- Use `UPPER_SNAKE_CASE` for constants

## Reference

See agent: `python-reviewer` for comprehensive Python review guidelines.
