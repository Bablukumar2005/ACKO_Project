# Transcripts

AI-session exports, required by the assessment ("No transcripts means zero for the
AI-usage part").

| File | Tool | What |
|---|---|---|
| `opencode-session.json` | OpenCode v2.3.2 (`opencode session export`) | the full coding session that built this repository — planning, implementation, debugging, tests |

Everything in this repository was built in the single OpenCode session above, using the
model `vultr/glm-5.3-flash` through the Acko/LPU gateway (configured via `openai.py`).

How to re-export: `opencode session list`, then
`opencode session export <session-id> > transcripts/opencode-session.json`.
