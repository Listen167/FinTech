# SafeBank Agent

SafeBank Agent is a competition prototype for a secure AI banking agent. The repo is organized so frontend, backend, AI orchestration, interface contracts, and optional model assets can evolve independently.

## Directory Layout

```text
.
├── ai/                 # Agent orchestration, prompts, tool routing, safety checks
├── backend/            # FastAPI backend, mock bank services, policy engine, audit logic
├── frontend/           # Web app for chat, charts, confirmations, verification flows
├── interfaces/         # Shared API contracts, schemas, and generated client types
├── models/             # Optional small models, datasets, embeddings, checkpoints
├── docs/               # Product, architecture, and OpenAPI documentation
├── scripts/            # Local development and automation scripts
└── tests/              # Cross-service and end-to-end tests
```

## Development Boundaries

- Frontend collects user input, displays plans/results, and handles confirmation UI.
- Backend owns business state, permissions, verification, audit logs, and mock bank data.
- AI service parses intent, plans tasks, calls approved backend tools, and explains results.
- Interfaces are the shared contract between teams. Keep OpenAPI and generated types here.
- Models are optional. Prefer LLM output plus rules first, then add lightweight classifiers only when useful.

## Current Source Documents

- Technical document: `docs/AI_Banking_Agent_Technical_Document.md`
- OpenAPI contract: `docs/AI_Banking_Agent_OpenAPI.yaml`

