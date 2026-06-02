---
paths:
  - "**/*.py"
  - "**/*.pyi"
---
# Python Security

> This file extends [common/security.md](../common/security.md) with Python-specific content.

## Secret Management

```python
import os
from dotenv import load_dotenv

load_dotenv()

api_key = os.environ["OPENAI_API_KEY"]  # Raises KeyError if missing
```

## Security Scanning

- Use **bandit** for static security analysis:
  ```bash
  bandit -r .
  ```

## Dangerous Patterns to Avoid

```python
# BAD: Command injection
os.system(f"ffmpeg {user_input}")  # CRITICAL

# GOOD: Safe subprocess
import subprocess
subprocess.run(["ffmpeg", user_input], check=True)  # List args, no shell=True

# BAD: Path traversal
open(f"/data/{user_input}")  # CRITICAL

# GOOD: Validated path
from pathlib import Path
safe_path = Path("/data") / user_input
if not safe_path.resolve().startswith(Path("/data").resolve()):
    raise ValueError("Path traversal detected")
open(safe_path)

# BAD: eval/exec
eval(user_input)  # CRITICAL

# GOOD: Safe evaluation
import ast
ast.literal_eval(user_input)  # Only literals
```

## Video.AI Specific Security

- [ ] API keys in `.env` or environment variables
- [ ] File paths validated against traversal
- [ ] Ollama calls through `OllamaClient`, not raw urllib
- [ ] Subprocess calls use list arguments
- [ ] Config values not hardcoded
