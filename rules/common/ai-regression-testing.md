---
---
# AI Regression Testing

> From ECC ai-regression-testing skill.

## The Core Problem

When an AI writes code and then reviews its own work, it carries the same assumptions into both steps. This creates predictable failure patterns that only automated tests can catch.

```
AI writes fix → AI reviews fix → AI says "looks correct" → Bug still exists
```

## Common AI Regression Patterns

### Pattern 1: Path Mismatch

**Most common**: AI adds field to one code path but forgets another.

```python
# FAIL: AI adds field to production path only
if sandbox_mode:
    return {"data": {"id": email, "name"}}  # Missing new field
# Production path
return {"data": {"id": email, "name", "new_field"}}
```

**Test to catch it**:

```python
def test_sandbox_and_production_return_same_fields():
    # In test env, sandbox mode is forced ON
    result = get_profile()
    for field in REQUIRED_FIELDS:
        assert field in result["data"]
```

### Pattern 2: SELECT Clause Omission

When adding new columns, AI often forgets to update the SELECT clause.

```python
# FAIL: New column added to response but not to SELECT
data = db.query("SELECT id, email, name FROM users")  # new_field not here
return {**data, "new_field": data.get("new_field")}  # Always None
```

### Pattern 3: Error State Leakage

```python
# FAIL: Error state set but old data not cleared
except Exception as e:
    error = "Failed to load"
    # reservations still shows data from previous tab!
```

### Pattern 4: Missing Rollback

```python
# FAIL: No rollback on failure
async def remove_item(id):
    items.remove(id)
    await api.delete(id)  # If API fails, item is gone from UI but still in DB
```

## Strategy: Test Where Bugs Were Found

Don't aim for 100% coverage. Instead:

```
Bug found in segment_runner.py     → Write test for segment_runner
Bug found in ollama_client.py      → Write test for ollama_client
Bug found in audio_proxy.py        → Write test for audio_proxy
No bug in image_gen.py             → Don't write test (yet)
```

**Why this works with AI development:**

1. AI tends to make the **same category of mistake** repeatedly
2. Bugs cluster in complex areas (auth, multi-path logic, state management)
3. Once tested, that exact regression **cannot happen again**
4. Test count grows organically with bug fixes — no wasted effort

## Quick Reference

| AI Regression Pattern | Test Strategy | Priority |
|---|---|---|
| Path mismatch | Assert same response shape in both paths | High |
| SELECT clause omission | Assert all required fields in response | High |
| Error state leakage | Assert state cleanup on error | Medium |
| Missing rollback | Assert state restored on API failure | Medium |

## DO / DON'T

**DO:**
- Write tests immediately after finding a bug (before fixing it if possible)
- Test the API response shape, not the implementation
- Run tests as the first step of every bug-check
- Keep tests fast (< 1 second total)
- Name tests after the bug they prevent (e.g., "test_bug_r1_regression")

**DON'T:**
- Write tests for code that has never had a bug
- Trust AI self-review as a substitute for automated tests
- Skip path testing because "it's just mock data"
- Aim for coverage percentage — aim for regression prevention
