---
paths:
  - "**/*.py"
---
# Advanced Python Patterns

> Extended patterns from ECC python-patterns skill.

## EAFP (Easier to Ask Forgiveness than Permission)

Python prefers exception handling over checking conditions.

```python
# Good: EAFP style
def get_value(dictionary: dict, key: str, default=None):
    try:
        return dictionary[key]
    except KeyError:
        return default

# Bad: LBYL (Look Before You Leap)
def get_value(dictionary: dict, key: str, default=None):
    if key in dictionary:
        return dictionary[key]
    else:
        return default
```

## Protocol-Based Duck Typing

```python
from typing import Protocol

class Renderable(Protocol):
    def render(self) -> str: ...

def render_all(items: list[Renderable]) -> str:
    return "\n".join(item.render() for item in items)
```

## Exception Chaining

```python
def process_data(data: str):
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse data: {data}") from e
```

## Custom Exception Hierarchy

```python
class AppError(Exception):
    """Base exception for all application errors."""
    pass

class ValidationError(AppError):
    """Raised when input validation fails."""
    pass

class NotFoundError(AppError):
    """Raised when a requested resource is not found."""
    pass

class BreakerOpen(AppError):
    """Raised when circuit breaker is open."""
    def __init__(self, model: str, cooldown_s: float):
        self.model = model
        self.cooldown_s = cooldown_s
        super().__init__(f"Breaker open for {model}, cooldown {cooldown_s:.1f}s")
```

## Context Managers

### Class-Based

```python
class ManagedResource:
    def __init__(self):
        self.resource = None

    def __enter__(self):
        self.resource = acquire_resource()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.resource:
            release_resource(self.resource)
        return False  # Don't suppress exceptions
```

### Function-Based

```python
from contextlib import contextmanager

@contextmanager
def timer(name: str):
    start = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start
    print(f"{name} took {elapsed:.4f}s")
```

## Generators for Memory Efficiency

```python
# Bad: Returns full list in memory
def read_lines(path: str) -> list[str]:
    with open(path) as f:
        return [line.strip() for line in f]

# Good: Yields lines one at a time
def read_lines(path: str):
    with open(path) as f:
        for line in f:
            yield line.strip()
```

## `__slots__` for Memory Efficiency

```python
# Bad: Regular class uses __dict__ (more memory)
class Point:
    def __init__(self, x: float, y: float):
        self.x = x
        self.y = y

# Good: __slots__ reduces memory usage
class Point:
    __slots__ = ['x', 'y']

    def __init__(self, x: float, y: float):
        self.x = x
        self.y = y
```

## Decorators

```python
import functools
import time

def timer(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        print(f"{func.__name__} took {elapsed:.4f}s")
        return result
    return wrapper

@timer
def slow_function():
    time.sleep(1)
```

## Anti-Patterns to Avoid

```python
# Bad: Mutable default arguments
def append_to(item, items=[]):
    items.append(item)
    return items

# Good: Use None and create new list
def append_to(item, items=None):
    if items is None:
        items = []
    items.append(item)
    return items

# Bad: Checking type with type()
if type(obj) == list:
    process(obj)

# Good: Use isinstance
if isinstance(obj, list):
    process(obj)

# Bad: Comparing to None with ==
if value == None:
    process()

# Good: Use is
if value is None:
    process()
```
