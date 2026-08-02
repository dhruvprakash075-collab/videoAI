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

Live contract: `utils/local_ui.py` serves a single FastAPI app on
`127.0.0.1:8000` (start with `python -m utils.local_ui`). All JSON routes are
under `/api/*` — there is no `/api/v1` prefix and no `/health` route; use the
decorator list in local_ui.py as the single source of truth.

```python
# Jobs (run the pipeline from the UI)
POST   /api/jobs                      # Start a job
GET    /api/jobs                      # List jobs
GET    /api/jobs/{job_id}             # Job detail
GET    /api/jobs/{job_id}/events      # SSE progress stream
GET    /api/jobs/{job_id}/artifacts   # Job artifacts
POST   /api/jobs/{job_id}/cancel      # Cancel job
POST   /api/jobs/{job_id}/retry       # Retry failed job

# Pipeline control
POST   /api/upload_script             # Script as source
POST   /api/consultation_reply        # Director consultation answer
POST   /api/manual_pause              # Pause/abort the running run

# Status / config
GET    /api/status                    # Run status (UIState)
GET    /api/voices                    # Character voice list
GET    /api/preflight                 # Preflight check results
GET    /api/config                    # Get config
POST   /api/config                    # Update config

# Audio / A-B tests
GET    /api/audio/preview/{character} # Voice preview
POST   /api/ab/generate               # Start A/B generation
GET    /api/ab/status/{job_id}        # A/B status
POST   /api/ab/pick                   # Pick A/B winner

# Chat (director memory)
POST   /api/chat                      # Chat message
GET    /api/chat/sessions/{session_id}# Session history
DELETE /api/chat/sessions/{session_id}# Delete session

# Artifacts / memory
GET    /api/artifacts                 # List runs
GET    /api/artifacts/{run_id}        # Artifacts for a run
GET    /api/memory                    # Story memory summary
GET    /api/characters                # Character list
```

## API Design Checklist

- [ ] Resource URLs follow naming conventions
- [ ] Correct HTTP methods used
- [ ] Appropriate status codes returned
- [ ] Input validated with Pydantic
- [ ] Error responses follow standard format
- [ ] Authentication required (or explicitly public)
- [ ] Response doesn't leak internal details
