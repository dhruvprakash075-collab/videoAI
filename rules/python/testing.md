---
paths:
  - "**/*.py"
  - "**/*.pyi"
---
# Python Testing

> This file extends [common/testing.md](../common/testing.md) with Python-specific content.

## Framework

Use **pytest** as the testing framework.

## Coverage

```bash
venv\Scripts\python.exe -m coverage run -m pytest tests/ -q
venv\Scripts\python.exe -m coverage report
```

## Test Organization

This repo organizes tests by **directory** (`tests/` flat + `tests/unit/`,
`tests/integration/`), not pytest markers — `pyproject.toml` registers no
markers, so `@pytest.mark.unit` etc. would fail as unknown markers. Follow
the existing layout instead:

```
tests/unit/            — pure unit tests (no external services)
tests/integration/     — cross-module / fixture-based tests
tests/                 — everything else, one file per module under test
```

## Mocking External Services

```python
from unittest.mock import patch, MagicMock

# Mock Ollama
@patch('utils.ollama_client.OllamaClient.generate')
def test_with_mocked_ollama(mock_generate):
    mock_generate.return_value = "mocked response"
    result = function_using_ollama()
    assert result == expected

# Mock CrewAI
@patch('utils.crewai_breaker.guarded_crewai_kickoff')
def test_with_mocked_crewai(mock_kickoff):
    mock_kickoff.return_value = "mocked crew result"
    result = function_using_crewai()
    assert result == expected
```

## Reference

See agent: `tdd-guide` for detailed TDD workflow and patterns.
