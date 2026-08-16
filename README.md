# DocSeek

DocSeek is a self-hosted knowledge workbench for turning uploaded properties into a Property Graph, an Entity Graph, graph-only Search results, and grounded AI Query answers.

Release 1 is a modular monolith:

- `backend/` contains the FastAPI API, local auth/roles, project and property lifecycle, LangGraph processing workflow, Neo4j adapters, retrieval, AI Query, and project-scoped MCP lifecycle.
- `frontend/` contains the React/Vite workbench with the property tree, graph tabs, Search, AI Query, property inspection overlay, and MCP controls.
- SQLite under `data/conf/` stores users, groups, roles, sessions, projects, and operational locks/jobs only. Graph/property/entity canonical data is written through the graph adapter.
- Neo4j uses the named `property_graph` and `entity_graph` databases when reachable. The GraphRAG adapter exposes Neo4j GraphRAG's `SimpleKGPipeline` seam for configured provider objects and preserves source property IDs in document metadata. With `DOCSEEK_ALLOW_LOCAL_FALLBACK=true` (the default), development uses JSON graph snapshots under `data/graph-fallback/` while preserving the same adapter contract.

## Fresh setup with uv

Prerequisites:

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Node.js 20 or newer with npm
- Git

Clone the repository and let uv install the required Python version and all locked Python dependencies:

```bash
git clone https://github.com/ATreep/DocSeek.git
cd DocSeek
uv python install 3.12
uv sync --group dev
npm --prefix frontend install
```

Start the API and frontend together:

```bash
./start.sh
```

The script stops prior DocSeek API/frontend processes, uses `uv run` for the Python API, starts both services, and prints the frontend URL: `http://localhost:5173`.

The seeded local development account is `admin` / `admin`. Change local credentials before exposing the deployment.

To update dependencies, edit `pyproject.toml`, run `uv lock`, and commit both `pyproject.toml` and `uv.lock`. Use `uv sync --locked --group dev` in reproducible development or CI environments.

## Configuration

The application runs without an environment file by using its deterministic local fallback. Configure model providers from the Settings panel, or create a local `.env` file with `DOCSEEK_LLM_*`, `DOCSEEK_EMBEDDING_*`, and optional `DOCSEEK_NEO4J_*` values. Hidden files and the runtime `data/` directory are intentionally excluded from Git. Provider failures are surfaced as failed candidate jobs and never activate a partial graph.

The System settings panel also manages provider profiles, route selection, Neo4j/storage checks, users, groups, roles, and the current profile. Provider secrets are accepted only as configuration metadata by the local control plane; runtime API secrets should be supplied through environment variables and are never returned by the API.

## Verification

```bash
uv sync --locked --group dev
uv run pytest -q
npm --prefix frontend test
npm --prefix frontend run build
```

The backend tests cover capability resolution, project/property naming and lifecycle, exclusive locks, image exclusion from Entity Graph input, graph database separation, GraphRAG adapter contracts, Search without an LLM call, AI Query citations, recovery, and MCP manual lifecycle. Frontend Vitest tests cover graph filtering controls, image preview rendering, and lock-aware property actions.
