# DocSeek

[中文 README](README.md)

## Introduction

DocSeek is a self-hosted knowledge workbench. Upload documents or other properties, extract their content, build Property and Entity Graphs, and search or ask grounded AI questions with traceable citations.

## Start the project

Requirements: `uv`, Node.js 20+, npm, and Git.

```bash
git clone https://github.com/ATreep/DocSeek.git
cd DocSeek
uv python install 3.12
uv sync --group dev
npm --prefix frontend install
./start.sh
```

When startup finishes, open <http://localhost:5173> and sign in with the local development account `admin` / `admin`. Change the password immediately after the first login; do not expose the development services to the public internet without hardening them.

`start.sh` starts the API (default: `http://127.0.0.1:8000`) and frontend, and stores runtime data in `data/`. To use Neo4j, configure `DOCSEEK_NEO4J_*` variables before starting. Without Neo4j, DocSeek uses its local JSON graph fallback.

## Main features

- **Property import and parsing**: Supports common text, PDF, and Office formats while preserving project and property structure.
- **Property Graph**: Explore property-to-property relations with filtering, search, zoom, focus, and layout controls.
- **Entity Graph**: Extract entities, definitions, and relations from property content while retaining source properties.
- **Search and AI Query**: Search properties and entities; AI Query answers from graph evidence with citations and relation paths.
- **Project-scoped MCP**: Enable or close MCP endpoints per project for compatible clients.
- **Permissions and localization**: Manage users, groups, roles, and capabilities; switch the interface between Chinese and English.
- **Local-first deployment**: Neo4j is optional, so the local fallback provides a quick way to try the workbench.

## Screenshots

Full image directory: [screenshots/](screenshots/)

### AI Query

![AI Query](screenshots/ai-query.png)

### Entity Graph overview

![Entity Graph overview](screenshots/entity-graph-overview.png)

### Entity relation preview

![Entity relation preview](screenshots/entity-relation-preview.png)

## Development checks

```bash
uv run pytest -q
npm --prefix frontend test
npm --prefix frontend run build
```

## License

This project is licensed under [GPL-3.0](LICENSE).
