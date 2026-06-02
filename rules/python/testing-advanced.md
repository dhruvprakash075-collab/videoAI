---
paths:
  - "**/*.py"
---
# Advanced Python Testing

> Extended testing patterns from ECC python-testing skill.

## Fixture Scopes

```python
# Function scope (default) - runs for each test
@pytest.fixture
def temp_file():
    with open("temp.txt", "w") as f:
        yield f
    os.remove("temp.txt")

# Module scope - runs once per module
@pytest.fixture(scope="module")
def module_db():
    db = Database(":memory:")
    db.create_tables()
    yield db
    db.close()

# Session scope - runs once per test session
@pytest.fixture(scope="session")
def shared_resource():
    resource = ExpensiveResource()
    yield resource
    resource.cleanup()
```

## Parametrized Fixtures

```python
@pytest.fixture(params=["sqlite", "postgresql", "mysql"])
def db(request):
    if request.param == "sqlite":
        return Database(":memory:")
    elif request.param == "postgresql":
        return Database("postgresql://localhost/test")

def test_database_operations(db):
    result = db.query("SELECT 1")
    assert result is not None
```

## Autouse Fixtures

```python
@pytest.fixture(autouse=True)
def reset_config():
    """Automatically runs before every test."""
    Config.reset()
    yield
    Config.cleanup()

def test_without_fixture_call():
    # reset_config runs automatically
    assert Config.get_setting("debug") is False
```

## Parametrization

```python
@pytest.mark.parametrize("input,expected", [
    ("hello", "HELLO"),
    ("world", "WORLD"),
    ("PyThOn", "PYTHON"),
])
def test_uppercase(input, expected):
    assert input.upper() == expected

# With IDs for readable output
@pytest.mark.parametrize("input,expected", [
    ("valid@email.com", True),
    ("invalid", False),
], ids=["valid-email", "invalid-format"])
def test_email_validation(input, expected):
    assert is_valid_email(input) is expected
```

## Mocking Patterns

### Mocking Exceptions

```python
@patch("mypackage.api_call")
def test_api_error_handling(api_call_mock):
    api_call_mock.side_effect = ConnectionError("Network error")

    with pytest.raises(ConnectionError):
        api_call()
```

### Mocking Context Managers

```python
@patch("builtins.open", new_callable=mock_open)
def test_file_reading(mock_file):
    mock_file.return_value.read.return_value = "file content"
    result = read_file("test.txt")
    assert result == "file content"
```

### Using Autospec

```python
@patch("mypackage.DBConnection", autospec=True)
def test_autospec(db_mock):
    db = db_mock.return_value
    db.query("SELECT * FROM users")
    # This would fail if DBConnection doesn't have query method
    db_mock.assert_called_once()
```

## Testing Async Code

```python
@pytest.mark.asyncio
async def test_async_function():
    result = await async_add(2, 3)
    assert result == 5

@pytest.mark.asyncio
@patch("mypackage.async_api_call")
async def test_async_mock(api_call_mock):
    api_call_mock.return_value = {"status": "ok"}
    result = await my_async_function()
    api_call_mock.assert_awaited_once()
```

## Testing Exceptions

```python
def test_divide_by_zero():
    with pytest.raises(ZeroDivisionError):
        divide(10, 0)

def test_custom_exception():
    with pytest.raises(ValueError, match="invalid input"):
        validate_input("invalid")

def test_exception_with_details():
    with pytest.raises(CustomError) as exc_info:
        raise CustomError("error", code=400)
    assert exc_info.value.code == 400
```

## Test Organization

```
tests/
├── conftest.py                 # Shared fixtures
├── __init__.py
├── unit/                       # Unit tests
│   ├── test_models.py
│   └── test_utils.py
├── integration/                # Integration tests
│   ├── test_api.py
│   └── test_database.py
└── e2e/                        # End-to-end tests
    └── test_user_flow.py
```

## pytest Configuration

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts =
    --strict-markers
    --disable-warnings
    --cov=mypackage
    --cov-report=term-missing
markers =
    slow: marks tests as slow
    integration: marks tests as integration tests
    unit: marks tests as unit tests
```

## Running Tests

```bash
# Run all tests
pytest

# Run specific file
pytest tests/test_utils.py

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov=mypackage --cov-report=html

# Run only fast tests
pytest -m "not slow"

# Run until first failure
pytest -x

# Run last failed tests
pytest --lf
```
