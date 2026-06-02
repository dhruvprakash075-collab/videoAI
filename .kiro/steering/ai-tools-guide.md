# AI Tools & Capabilities Guide
> Every AI working in this project should read this first. It tells you what tools exist and how to use them properly.

---

## 1. The Golden Rules

- **Full permission granted — act without asking.** The user has explicitly granted permission for all work. NEVER say "shall I proceed?", "do you want me to?", "should I go ahead?" or any similar phrase. Just do it. This applies to ALL agents including sub-agents.
- **Only exception:** actions that are completely irreversible and catastrophic (wiping an entire database, deleting the whole repo) — state what you are about to do in one sentence, then do it immediately.
- **Use the right tool.** Never use a shell command when a dedicated tool exists (e.g. use `read_file` not `cat`, use `grep_search` not `grep`).
- **Run independent calls in parallel.** If two tool calls don't depend on each other, fire them at the same time.
- **Save what you learn.** Any useful fact (file location, bug fix, convention, decision) goes into MCP Memory so the next AI session can reuse it.
- **Speak less, work more.** Skip narrating steps. Give a short summary at the end.
- **Plain language.** Short sentences. No jargon. If a tech term is needed, explain it in one phrase.

### Sub-agent permission note
When invoking sub-agents via `invoke_sub_agent`, always prepend this to the prompt:
```
The user has granted full permission. Act without asking for confirmation on any reversible work. Never say "shall I proceed?" or similar. Just do the work.
```

---

## 2. Tool Categories & When to Use Each

### File & Code Tools
| Tool | Use it when... |
|------|---------------|
| `read_file` / `read_files` | Reading one or more files (use `read_files` for several at once, in parallel) |
| `fs_write` | Creating a new file or fully overwriting one (keep each call ≤50 lines, then `fs_append`) |
| `fs_append` | Adding content to the end of an existing file |
| `str_replace` | Editing a specific part of a file (surgical change) |
| `delete_file` | Deleting a file |
| `smartRelocate` | Moving/renaming a file AND auto-updating all imports |
| `semanticRename` | Renaming a variable/function/class everywhere in the codebase |
| `list_directory` | Listing what's in a folder |
| `file_search` | Finding a file when you know part of its name |
| `grep_search` | Searching for text/patterns inside files |
| `getDiagnostics` | Checking a file for compile errors, type errors, lint issues |

### Terminal Tools
| Tool | Use it when... |
|------|---------------|
| `execute_pwsh` | Running a quick command that finishes fast |
| `control_pwsh_process` (start) | Starting a long-running process (dev server, watcher) in the background |
| `control_pwsh_process` (stop) | Stopping a background process |
| `list_processes` | Seeing what background processes are running |
| `get_process_output` | Reading the output/logs of a background process |

> **Windows note:** This project runs on Windows CMD. Use `&` not `&&`, use `dir` not `ls`, use `del` not `rm`.

### Browser / Web Tools
| Tool | Use it when... |
|------|---------------|
| `mcp_playwright_playwright_navigate` | Opening a URL in the browser |
| `mcp_playwright_playwright_screenshot` | Taking a screenshot of the page |
| `mcp_playwright_playwright_click` | Clicking a button or link |
| `mcp_playwright_playwright_fill` | Typing into an input field |
| `mcp_playwright_playwright_get_visible_text` | Reading all text on the page |
| `mcp_playwright_playwright_get` / `_post` / `_put` / `_patch` / `_delete` | Making HTTP API calls |
| `mcp_playwright_playwright_console_logs` | Reading browser console errors/logs |
| `remote_web_search` | Searching the web for current info |
| `web_fetch` | Fetching content from a specific URL |

### Memory Tools (MCP Memory)
Think of this as a shared notebook that persists across sessions.

| Tool | Use it when... |
|------|---------------|
| `mcp_memory_search_nodes` | **Start every task here** — check if prior work is already saved |
| `mcp_memory_create_entities` | Saving a new fact/finding/decision |
| `mcp_memory_add_observations` | Adding more details to something already saved |
| `mcp_memory_create_relations` | Linking two saved facts together |
| `mcp_memory_read_graph` | Reading everything saved (full picture) |
| `mcp_memory_open_nodes` | Reading specific saved items by name |
| `mcp_memory_delete_entities` | Removing outdated/wrong saved facts |

**What to save:** bug fixes, file locations, architecture decisions, gotchas, conventions, test results, design choices.

### Thinking Tool
| Tool | Use it when... |
|------|---------------|
| `mcp_sequential_thinking_sequentialthinking` | Breaking down a complex problem step by step before acting |

### Terminal UI (TUI) Tools
For running and interacting with terminal-based apps (like `studio_tui.py`).

| Tool | Use it when... |
|------|---------------|
| `mcp_tui_mcp_launch` | Starting a TUI app in a virtual terminal |
| `mcp_tui_mcp_take_snapshot` | Capturing what's on screen |
| `mcp_tui_mcp_type_text` / `press_key` | Sending input to the TUI |
| `mcp_tui_mcp_wait_for_text` / `wait_for_stable` | Waiting for the TUI to show something |
| `mcp_tui_mcp_find_text` | Finding text on screen and getting its position |
| `mcp_tui_mcp_kill_session` | Closing the TUI session |

---

## 3. Kiro Powers (activate before use)

Powers are like plugins. You MUST activate them first to get their tool list, then call `use`.

```
Step 1: kiroPowers(action="activate", powerName="context7")
Step 2: kiroPowers(action="use", powerName="context7", serverName=<from step1>, toolName=<from step1>, arguments={...})
```

| Power | Use it for... |
|-------|--------------|
| `context7` | Getting up-to-date docs for any library (React, FastAPI, Textual, etc.) |
| `firebase` | Firebase backend work (auth, Firestore, storage, functions) |

---

## 4. codingbuddy Tools

### Skills (domain knowledge packs)
```python
# Find the right skill for your task
mcp_codingbuddy_recommend_skills(prompt="your task description")

# Load and follow a skill's instructions
mcp_codingbuddy_get_skill(skillName="systematic-debugging")
```

Key skills for this project:
- `systematic-debugging` — when something is broken
- `build-fix` — when build/compile fails
- `writing-plans` — before a big multi-step task
- `executing-plans` — when following a written plan
- `frontend-design` — React dashboard work
- `security-audit` — before shipping anything security-related
- `test-driven-development` — when adding features
- `verification-before-completion` — before claiming work is done
- `brainstorming` — before creating anything new

### Agents (specialist AIs)
```python
# Get the right agent for a task
mcp_codingbuddy_dispatch_agents(mode="ACT", taskDescription="fix the TUI progress bar")
```

Key agents: `context-gatherer` (explore unfamiliar code), `architect` (design decisions), `code-reviewer` (review changes), `python-reviewer`, `security-reviewer`, `performance-optimizer`, `build-error-resolver`.

### Project Config
```python
mcp_codingbuddy_get_project_config()       # tech stack, architecture
mcp_codingbuddy_get_code_conventions()     # naming, style rules
```

### Workflow Modes
- `PLAN` — figure out what to do and how
- `ACT` — do the work
- `EVAL` — review/verify what was done

```python
mcp_codingbuddy_parse_mode(prompt="PLAN: add a progress bar to the TUI")
```

---

## 5. Sub-Agents (invoke_sub_agent)

Delegate to a specialist when the task is complex or unfamiliar.

| Agent | Use it for... |
|-------|--------------|
| `context-gatherer` | Understanding an unfamiliar part of the codebase |
| `architect` | Designing a new feature or system |
| `planner` | Breaking a big task into steps |
| `code-reviewer` | Reviewing code changes |
| `python-reviewer` | Python-specific code review |
| `react-reviewer` | React/JSX code review |
| `security-reviewer` | Security vulnerability check |
| `performance-optimizer` | Making slow code faster |
| `build-error-resolver` | Fixing build failures |
| `tdd-guide` | Test-driven development guidance |
| `general-task-execution` | Any well-defined subtask |

---

## 6. This Project's Stack (quick reference)

- **Language:** Python 3.10–3.13 in `venv/`
- **Entry point:** `bootstrap_pipeline.py` (always run through this)
- **LLM:** Ollama at `http://localhost:11434`
- **Image gen:** Stable Diffusion (local, 6GB VRAM — be conservative)
- **Audio:** edge-tts, OmniVoice, pydub, FFmpeg (bundled in `ffmpeg-8.1.1-essentials_build/`)
- **Frontend:** React 19 + Vite in `dashboard/`
- **API:** FastAPI at `http://127.0.0.1:8000` (`utils/local_ui.py`)
- **Config:** `config/config.yaml` — always read from here, never hardcode
- **Platform:** Windows only — use `pathlib.Path`, CMD commands, PowerShell scripts

---

## 7. Standard Workflow for Any Task

```
1. Search MCP memory for prior findings on this topic
2. Read relevant files (use read_code for large ones)
3. If complex → use mcp_sequential_thinking or dispatch context-gatherer
4. Check codingbuddy skills for a matching skill → activate it
5. Do the work using the right tools
6. Run getDiagnostics on edited files
7. Save new findings to MCP memory
8. Give a short plain-language summary
```

---

## 8. What's Already in Memory

Search MCP memory before starting any work — the full project is documented there:

**Project knowledge:**
- `Video.AI-project` — purpose, stack, entry points, CLI flags, GPU constraints
- `Video.AI-file-map` — every key file and what it does
- `Video.AI-conventions` — coding rules (venv, pathlib, logger, additive UIState, etc.)
- `Video.AI-UIState` — shared state fields, threading model, race fixes
- `Video.AI-known-bugs-fixed` — bugs already fixed with exact details

**Studio TUI work (Phase 1+2+3 complete):**
- `studio-tui-implementation-status` — all phases done, keybindings, verification
- `pipeline_long-tui-hooks` — exact pipeline_long.py changes
- `studio-tui-enhancements` — full spec (25 tasks, 18 requirements)
- `kiro-hooks-config` — hook + steering file locations

```python
mcp_memory_search_nodes(query="Video.AI")   # project detail
mcp_memory_search_nodes(query="TUI")        # TUI findings
mcp_memory_read_graph()                     # everything at once
```
