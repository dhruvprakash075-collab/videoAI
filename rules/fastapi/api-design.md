---
paths:
  - "utils/local_ui.py"
---
# API Design Patterns

> From ECC api-design skill. Applicable to `utils/local_ui.py` (FastAPI local control API).

## Resource Design

```
# Resources are nouns, plural, lowercase
GET    /api/v1/pipeline/status
GET    /api/v1/pipeline/runs
POST   /api/v1/pipeline/start
GET    /api/v1/projects
GET    /api/v1/projects/:id
```

## HTTP Methods and Status Codes

| Method | Status Code | Use For |
|--------|-------------|---------|
| GET | 200 OK | Retrieve resources |
| POST | 201 Created | Create/start resources |
| POST | 202 Accepted | Async operations (pipeline start) |
| PUT | 200 OK | Full update |
| PATCH | 200 OK | Partial update |
| DELETE | 204 No Content | Remove resource |

## Response Format

### Success

```json
{
  "data": {
    "status": "running",
    "progress": 0.45
  }
}
```

### Collection

```json
{
  "data": [...],
  "meta": {
    "total": 10,
    "page": 1,
    "per_page": 20
  }
}
```

### Error

```json
{
  "error": {
    "code": "pipeline_busy",
    "message": "Pipeline is already running"
  }
}
```

## Status Codes Reference

```
# Success
200 OK                    — GET, PUT, PATCH
201 Created               — POST (include Location header)
202 Accepted              — Async operation started
204 No Content            — DELETE

# Client Errors
400 Bad Request           — Validation failure
401 Unauthorized          — Missing auth
404 Not Found             — Resource doesn't exist
409 Conflict              — Pipeline already running
422 Unprocessable Entity  — Valid JSON, bad data
429 Too Many Requests     — Rate limit exceeded

# Server Errors
500 Internal Server Error — Unexpected failure
503 Service Unavailable   — Ollama down, breaker open
```

## Video.AI API Endpoints

```python
# Pipeline control
POST   /api/v1/pipeline/start     # Start pipeline
POST   /api/v1/pipeline/stop      # Stop pipeline
GET    /api/v1/pipeline/status    # Get pipeline status
GET    /api/v1/pipeline/progress  # Get progress

# Projects
GET    /api/v1/projects           # List projects
GET    /api/v1/projects/:id       # Get project details

# Health
GET    /health                    # Health check
GET    /health/detailed           # Detailed health (Ollama, VRAM, disk)
```

## API Design Checklist

- [ ] Resource URLs follow naming conventions
- [ ] Correct HTTP methods used
- [ ] Appropriate status codes returned
- [ ] Input validated with Pydantic
- [ ] Error responses follow standard format
- [ ] Authentication required (or explicitly public)
- [ ] Response doesn't leak internal details
