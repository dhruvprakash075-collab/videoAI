# Video.AI v6 Pipeline Architecture

Visual flow of the unified pipeline, mapped to your design requirements.

> **Req #1:** Two entry options — Upload Source OR Fresh Idea.
> **Req #2:** Upload Source path → hybrid only splits, does not write.
> **Req #3:** Production node is a Writer+Director Hybrid, not separate nodes.
> **Req #4:** Both paths merge into the same production loop.

```mermaid
graph TD
    Start([User starts pipeline]) --> Choice{Entry choice}

    %% ── Path A: Upload Source (Req #1) ──────────────────────────────
    Choice -->|Upload Source|     SrcLoad[Source Loader<br/>.txt .md .pdf .docx URL + paste]
    SrcLoad --> SrcSplit[Source Splitter<br/>chunks source into N parts<br/>uses writer model with split prompt]
    SrcSplit --> SrcPlan[Story Plan:<br/>N segments from source]

    %% ── Path B: Fresh Idea (Req #1) ─────────────────────────────────
    Choice -->|Fresh Idea + topic| Director[Pre-Production:<br/>AI Director plans story]
    Choice -->|Fresh Idea, no topic|     Researcher[Web Researcher<br/>Wikipedia + RSS]
    Researcher --> Director
    Director --> FreshPlan[Story Plan:<br/>N segments from idea]

    %% ── Both paths converge (Req #4) ────────────────────────────────
    SrcPlan --> Hybrid
    FreshPlan --> Hybrid

    %% ── Production loop, per segment ────────────────────────────────
    subgraph ProdLoop [Production Loop - one pass per segment]
        direction TB
        Hybrid[Writer+Director Hybrid Node<br/>writer writes → self-critiques via prompt swap<br/>Req #3: same model = single node is correct]
        Translate[Translate to Devanagari<br/>Sarvam]
        TTS[OmniVoice TTS<br/>Hindi voice clone]
        SD[SD Image Gen<br/>LoRA face-lock]
        Render[FFmpeg Render<br/>Ken Burns + SRT]

        Hybrid -->|critic_approved| Translate
        Hybrid -.->|rejected, rewrites<max| Hybrid
        Translate --> TTS
        TTS --> SD
        SD --> Render
    end

    %% ── Post-production, shared ─────────────────────────────────────
    Render --> Post[Post-Production:<br/>Concat + Thumbnail + Manifest]
    Post --> SEO[SEO Generator<br/>Title + Description + Tags]
    SEO --> YT{Upload enabled?}
    YT -->|yes| Uploader[Playwright<br/>YouTube Upload]
    YT -->|no| Done
    Uploader --> Done([Final MP4])

    %% ── Styling ─────────────────────────────────────────────────────
    classDef entry fill:#1e3a5f,stroke:#4a90e2,color:#fff;
    classDef hybrid fill:#2d5a27,stroke:#4CAF50,color:#fff;
    classDef gpu fill:#5a3a1e,stroke:#ff9800,color:#fff;
    classDef post fill:#3d2f5b,stroke:#9c27b0,color:#fff;
    classDef choice fill:#5a1e1e,stroke:#f44336,color:#fff;

    class Choice,YT choice
    class SrcLoad,SrcSplit,Researcher,Director entry
    class Hybrid hybrid
    class TTS,SD,Render,Uploader gpu
    class Post,SEO,Translate post
```

## Node Inventory (Req #3: hybrid is ONE node)

| Node | Lives in | Touches GPU? | LLM? |
|---|---|---|---|
| Source Loader | `utils/source_loader.py` (Phase 1) | No | No |
| Source Splitter | `utils/source_splitter.py` (Phase 2) | No | Yes (1 call, writer model, split prompt) |
| Web Researcher | `utils/web_researcher.py` (Phase 3) | No | No (HTTP only) |
| Pre-Production Director | `core/pre_production.py` (existing) | No | Yes |
| **Writer+Director Hybrid** | `core/pipeline_graph.py` (Phase 4) | No | **Yes (2–6 calls)** |
| Translate (Sarvam) | `core/pipeline_graph.py` (existing `translate_node`) | No | Yes |
| TTS (OmniVoice) | `core/segment_runner.py` (existing) | **Yes** | No |
| SD Image Gen (LoRA) | `core/segment_runner.py` (existing) | **Yes** | No |
| FFmpeg Render | `core/segment_runner.py` (existing) | No | No |
| Post-Production | `core/post_production.py` (existing) | No | No |
| SEO Generator | `utils/seo_generator.py` (extend Phase 5) | No | Yes |
| YouTube Upload | `utils/youtube_uploader.py` (Phase 6 tests) | No | No |

**VRAM rule (from AGENTS.md):** every GPU-touching node (TTS, SD) MUST go
through `global_scheduler.task("heavy", ...)` so only one model is in VRAM
at a time on the 6GB RTX 4050.

## Hybrid Node Internals (Req #3 detail)

The Writer+Director Hybrid is **one graph node** that internally does:

```
hybrid_node(state):
    if state.source_chunks is not None:
        # Source-upload path (Req #2) — no writing, no critique
        script = state.source_chunks[state.i]
        return { script, critic_score: 100, critic_approved: True, rewrites_attempted: 0 }
    else:
        # Fresh-idea path — full creative loop, SAME model, prompt-swap
        for attempt in range(max_rewrites + 1):
            script = call_llm(WRITER_PROMPT, plan, context)        # zephyr-writer
            verdict = call_llm(CRITIC_PROMPT, script)              # zephyr-writer, different prompt
            if verdict.score >= threshold:
                return { script, verdict, rewrites_attempted: attempt }
        return { script, verdict, rewrites_attempted: max_rewrites }   # forgiving escape hatch
```

**One node, justified by v6.1 model roster:** the writer and critic are the
**same model** (`zephyr-writer`) with different system prompts. Ollama keeps
the model loaded between calls (`keep_alive: 3m` per `config.yaml:25`), so
the prompt swap is essentially free. Splitting them into separate graph
nodes would force a `route_after_critic` conditional edge and lose the
"always-on" model residency. **One node is the correct architecture for the
2-model roster.**
