---
---
# Deployment Patterns

> From ECC deployment-patterns skill.

## Deployment Strategies

### Rolling Deployment (Default)
- Instances update gradually
- Old and new versions coexist
- Requires backward-compatible changes
- Zero downtime

### Blue-Green Deployment
- Two identical environments
- Switch traffic atomically
- Instant rollback
- Double infrastructure during deployment

### Canary Deployment
- Small traffic percentage to new version
- Catch issues with real traffic
- Needs traffic-splitting infrastructure

## Health Checks

```python
# FastAPI health check
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "checks": {
            "ollama": check_ollama(),
            "disk": check_disk(),
            "vram": check_vram(),
        }
    }
```

## Environment Configuration

Follow Twelve-Factor App:
- Use environment variables for config
- Validate at startup
- Fail fast on misconfiguration

```python
import os

required_vars = ["OLLAMA_HOST", "API_KEY"]
for var in required_vars:
    if not os.environ.get(var):
        raise ValueError(f"Missing required env var: {var}")
```

## Production Readiness Checklist

- [ ] Tests pass
- [ ] No hardcoded secrets
- [ ] Proper error handling
- [ ] Structured logging
- [ ] Health checks
- [ ] Resource limits defined
- [ ] Monitoring configured
- [ ] Rollback plan documented

## Rollback Strategy

```bash
# Keep previous version available
# Test rollback in staging first
# Ensure migrations are backward-compatible
# Have monitoring in place
```
