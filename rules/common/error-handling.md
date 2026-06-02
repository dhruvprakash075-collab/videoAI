---
---
# Error Handling Patterns

> From ECC error-handling skill.

## Core Principles

1. **Fail fast and loudly** — surface errors at the boundary where they occur
2. **Typed errors over string messages** — errors are first-class values with structure
3. **User messages ≠ developer messages** — friendly text to users, full context server-side
4. **Never swallow errors silently** — every except block must handle, re-throw, or log
5. **Errors are part of your API contract** — document every error code

## Custom Exception Hierarchy

```python
class AppError(Exception):
    """Base application error."""
    def __init__(self, message: str, code: str, status_code: int = 500):
        super().__init__(message)
        self.code = code
        self.status_code = status_code

class NotFoundError(AppError):
    def __init__(self, resource: str, id: str):
        super().__init__(f"{resource} not found: {id}", "NOT_FOUND", 404)

class ValidationError(AppError):
    def __init__(self, message: str, details: list[dict] | None = None):
        super().__init__(message, "VALIDATION_ERROR", 422)
        self.details = details or []
```

## Exception Chaining

```python
def process_data(data: str):
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse data: {data}") from e
```

## Retry with Exponential Backoff

```python
import time
import random

def with_retry(fn, max_attempts=3, base_delay=0.5, max_delay=10, retry_if=None):
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt == max_attempts or (retry_if and not retry_if(e)):
                raise
            jitter = random.random() * base_delay
            delay = min(base_delay * 2 ** (attempt - 1) + jitter, max_delay)
            time.sleep(delay)
    raise last_error
```

## User-Facing Error Messages

```python
USER_ERROR_MESSAGES = {
    "NOT_FOUND": "The requested item could not be found.",
    "UNAUTHORIZED": "Please sign in to continue.",
    "FORBIDDEN": "You don't have permission to do that.",
    "VALIDATION_ERROR": "Please check your input and try again.",
    "INTERNAL_ERROR": "Something went wrong on our end. Please try again later.",
}

def get_user_message(code: str) -> str:
    return USER_ERROR_MESSAGES.get(code, USER_ERROR_MESSAGES["INTERNAL_ERROR"])
```

## Error Handling Checklist

- [ ] Every except block handles, re-throws, or logs — no silent swallowing
- [ ] API errors follow standard envelope `{ error: { code, message } }`
- [ ] User-facing messages contain no stack traces or internal details
- [ ] Full error context is logged server-side
- [ ] Custom exception classes extend base `AppError` with `code` field
- [ ] Retry logic only retries retriable errors (not 4xx client errors)
