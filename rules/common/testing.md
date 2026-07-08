# Testing Requirements

## Minimum Test Coverage: 80%

Test types to consider when risk justifies them:
1. **Unit Tests** — Individual functions, utilities, components
2. **Integration Tests** — Module interactions, API endpoints
3. **Regression Tests** — Specific bug fixes

## Test-Driven Development

Preferred workflow for non-trivial behavior changes:
1. Write test first (RED)
2. Run test — it should FAIL
3. Write minimal implementation (GREEN)
4. Run test — it should PASS
5. Refactor (IMPROVE)
6. Verify coverage (80%+)

## Troubleshooting Test Failures

1. Use **tdd-guide** agent
2. Check test isolation
3. Verify mocks are correct
4. Fix implementation, not tests (unless tests are wrong)

## Test Structure (AAA Pattern)

```python
def test_calculates_total():
    # Arrange
    items = [Item(price=10), Item(price=20)]
    
    # Act
    total = calculate_total(items)
    
    # Assert
    assert total == 30
```

### Test Naming

Use descriptive names that explain the behavior under test:

```python
def test_returns_empty_list_when_no_segments_match():
    ...

def test_raises_error_when_api_key_is_missing():
    ...

def test_falls_back_to_default_when_redis_unavailable():
    ...
```
