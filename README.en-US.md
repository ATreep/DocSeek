[中文](README.md)

# DocSeek

## Introduction

DocSeek is a local knowledge base management system for visual relationship graph generation and AI-powered knowledge Q&A.
Powered by LLMs and GraphRAG, it automatically groups and organizes documents, extracts valuable conceptual entities from document content, generates entity relationship graphs, and enables cross-document AI chat and queries.

## Demo Projects

The repository includes two demo projects, so you can explore the examples without configuring a Model Provider.

## Quick Start

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

`start.sh` starts the API (default: `http://127.0.0.1:8000`) and frontend, and stores runtime data in `data/`.

## Main Features

- **Asset import and parsing**: Supports common text, PDF, and Office formats while preserving project and asset hierarchies.
- **Asset Graph**: Explore relationships between assets with filtering, search, zoom, focus, and layout controls.
- **Entity Graph**: Extract entities, definitions, and relationships from asset content while retaining their source assets.
- **Search and AI Query**: Search assets and entities; AI Query answers using graph evidence and provides citations and relationship paths.
- **Project-scoped MCP**: Enable DocSeek's MCP server to provide third-party agents with tools for knowledge base management.
- **Role-based access control**: Supports strict role- and group-based permissions for enterprise users and multi-user organizations.

## Screenshots

### AI Query

![AI Query](screenshots/ai-query.png)

### Entity Graph Overview

![Entity Graph Overview](screenshots/entity-graph-overview.png)

### Entity Relationship Preview

![Entity Relationship Preview](screenshots/entity-relation-preview.png)
