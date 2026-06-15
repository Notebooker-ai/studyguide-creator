# studyguide-creator

An [Open Notebook](https://open-notebook.ai) **creator** plugin: turns notebook
content into a **study guide**, rendered by Quarto to a self-contained HTML page plus
a downloadable PDF.

Each topic has a **key concept** (1–2 sentences), **"what you need to know"** bullets,
and **common traps & misconceptions**; a **glossary** is appended at the bottom.

- Emits the `studyguide.v1` artifact schema (substance in `CreationResult.files`; PDF via `tectonic`).
- Implements the [`open-notebook-creator-sdk`](https://github.com/Notebooker-ai/open-notebook-creator-sdk) `BaseCreator` contract; registers under `open_notebook.creators`.

## Requirements

- The [`quarto`](https://quarto.org) CLI must be installed on the server.

## Model roles

| role | kind | requires |
|------|------|----------|
| `text` | language | `structured_json` |

## Config

| field | default | notes |
|-------|---------|-------|
| `num_topics` | 6 | 1–20 |
| `audience` | "undergraduate" | high_school / undergraduate / graduate / general |
| `formats` | ["html","pdf"] | output formats |

## Dev

```bash
uv sync --extra dev
uv run pytest
```

MIT licensed.
