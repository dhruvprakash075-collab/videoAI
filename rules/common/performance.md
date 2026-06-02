# Performance Optimization

## GPU Memory Management (6GB Constraint)

- Only ONE model in VRAM at a time
- Force-evict Ollama models before GPU tasks (`keep_alive=0`)
- Use `global_scheduler.task("heavy", ...)` for all GPU work
- HEAVY slot = 1 (1800s wait), LIGHT slot = 16 (60s wait)

## Caching Strategies

- Cache expensive computations (vision cache, story memory)
- Use `functools.lru_cache` for pure function results
- Invalidate caches when inputs change

## Concurrency

- Serialize CrewAI calls through `crewai_lock` (RLock)
- Use circuit breaker pattern for external services
- Parallelize independent operations where possible

## Profiling

```bash
# Python profiling
python -m cProfile -s cumulative script.py

# GPU memory monitoring
nvidia-smi
```

## Performance Checklist

- [ ] GPU work through `global_scheduler.task("heavy", ...)`
- [ ] Ollama models evicted before GPU tasks
- [ ] Expensive computations cached
- [ ] Circuit breaker for external services
- [ ] No unnecessary memory copies
