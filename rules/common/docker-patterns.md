---
---
# Docker Patterns

> From ECC docker-patterns skill.

## Multi-Stage Dockerfile

```dockerfile
# Stage 1: Dependencies
FROM python:3.12-slim AS deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2: Development
FROM deps AS dev
COPY . .
CMD ["python", "bootstrap_pipeline.py"]

# Stage 3: Production
FROM deps AS production
COPY . .
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser
CMD ["python", "bootstrap_pipeline.py"]
```

## Docker Compose for Local Development

```yaml
version: '3.8'

services:
  app:
    build:
      context: .
      target: dev
    volumes:
      - .:/app
    environment:
      - OLLAMA_HOST=http://ollama:11434
    depends_on:
      - ollama

  ollama:
    image: ollama/ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama

  dashboard:
    build:
      context: ./dashboard
    ports:
      - "5173:5173"
    volumes:
      - ./dashboard:/app
      - /app/node_modules

volumes:
  ollama_data:
```

## .dockerignore

```
node_modules/
.git/
.env*
dist/
build/
__pycache__/
*.pyc
venv/
.cache/
coverage/
logs/
studio_outputs/
studio_checkpoints/
```

## Security Best Practices

- Use specific image tags (not `:latest`)
- Run as non-root user
- Drop capabilities: `cap_drop: ALL`
- No secrets in image layers
- Use `.env` files for secrets

## Debugging

```bash
# Logs
docker compose logs -f app

# Exec into container
docker compose exec app bash

# Check container stats
docker stats

# Rebuild
docker compose build --no-cache

# Cleanup
docker compose down -v
docker system prune
```

## Anti-Patterns

- Using `:latest` tags
- Running as root
- Storing data without volumes
- Embedding secrets in compose files
- Multiple processes in one container
