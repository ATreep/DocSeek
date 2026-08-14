# DocSeek Top-Level Platform Design

Date: 2026-08-15
Status: Design approved in brainstorming; pending written-spec review

## 1. Purpose and Release Boundary

DocSeek is a self-hosted internal knowledge-base management and query system for one enterprise, organization, or person per deployment. Release 1 includes the complete product surface: property ingestion and editing, definition and entity extraction, smart grouping, Property Graph and Entity Graph construction, GraphRAG and vector retrieval, source-grounded AI Query, the React WebUI, local authentication and permissions, user/group/role management, System Configuration, and MCP exposure.

Audit is explicitly out of scope. DocSeek will retain only the operational status required to process work safely, display progress, recover interrupted jobs, and retry failures. It will not provide an audit timeline or compliance-audit subsystem.

The release is one product but is decomposed into bounded implementation tracks so that each subsystem has a clear owner and test boundary.

## 2. Deployment and Runtime Boundary

The first release is optimized for a private server or other self-hosted infrastructure. A deployment contains one organization-wide authentication realm and one set of global permission policies. The application is a modular monolith with separate API, worker, and React runtime processes where useful, but all business capabilities are owned by one codebase and share explicit internal interfaces.

The main modules are:

- React WebUI and navigation shell
- Python API and domain services
- Local authentication, groups, roles, and authorization
- Project and property management
- Agent orchestration and job coordination
- LLM and embedding provider adapters
- SQLite metadata access
- Vector-store and GraphRAG-store adapters
- Retrieval and AI Query
- MCP adapter

All WebUI and MCP operations pass through the same authorization policy. MCP is an alternate transport, not a second implementation of business logic.

## 3. Projects and Storage

Projects are independent knowledge corpora. Every authenticated user can discover and use all projects for which they have the relevant global module/action capability; there are no project-specific ACLs in release 1.

The server-side directory layout is:

```text
<program directory>/
  conf/
  projects/
    <project-id>/
      properties/
      metadata.sqlite
      vector-data/
      graphrag-data/
      indexes/
      jobs/
```

`conf/` stores system configuration and user configuration beside the program's Python files. The default project root is the sibling `projects/` directory. System Configuration displays and validates the resolved server paths and may set the deployment's project-root path when a mounted or external server directory is required; the configuration directory remains program-relative.

Each project stores original uploaded files and all project-derived data on the server. Project data is never implicitly sent elsewhere.

### 3.1 SQLite Metadata

The project SQLite database is the canonical source for non-graph metadata:

- Property records and the current processing revision
- One-sentence property definitions and property attributes
- Entity identities, names, types, descriptions, and canonical metadata
- Attribute-to-entity membership
- Property-to-entity affiliation metadata needed by the property UI
- Agent job state, active project snapshot, and processing status

An attribute can list multiple entities, so the relationship is normalized rather than stored as a serialized list:

```text
property_attribute_entity.property_attribute_id -> property_attribute.id
property_attribute_entity.entity_id             -> entity.id
```

SQLite foreign keys are enforced. Entity and property records in SQLite are authoritative for identity and metadata; graph storage is a derived projection.

The SQLite attribute-to-entity association is canonical metadata for the Property Attribute view. When that association is needed for graph traversal, its graph edge is materialized in the single GraphRAG database; the two stores use the same stable IDs.

### 3.2 Graph and Vector Stores

One project-level GraphRAG database stores all graph-specific data, including Property Graph and Entity Graph nodes, entity-to-entity edges, property/entity traversal edges, relationship metadata, and graph indexes. Graph records reference the canonical SQLite IDs and do not become a second authority for entity descriptions.

The project vector store contains document chunks and embeddings used for retrieval. Vector and graph stores are rebuildable from the published SQLite-backed project snapshot.

## 4. Property Mutation and Agent Pipeline

Property changes are explicit user actions: upload, Replace, or WebUI Save. A successful mutation creates the next processing input and automatically starts the worker pipeline.

The pipeline uses the following dependency order:

1. Normalize and persist the property input.
2. DG-Agent generates the one-sentence definition and a meaningful filename suggestion from document content.
3. EC-Agent updates the entity pool and entity relationships using the current project graph context.
4. GA-Agent assigns the property to an appropriate smart-tree location and suggests meaningful directory naming where needed.
5. PGB-Agent updates the complete Property Graph projection.
6. EGB-Agent updates the complete Entity Graph projection.
7. Vector and retrieval indexes are refreshed for the new project snapshot.

The exact internal scheduling may parallelize independent stages, but publication waits for every required stage.

### 4.1 Project-Wide Processing Lock

While any worker pipeline is processing a project, all property mutations in that project are disabled. The property tree, graph views, previews, and query results remain readable. The UI shows the active stage and prevents upload, Replace, edit, rename, move, and delete actions; the API rejects any bypass attempt.

This conservative project-wide lock ensures that graph and index workers never publish results based on a property pool that changed during processing. Projects not being processed remain fully writable.

### 4.2 Active Snapshot and Processing Status

The **active project snapshot** is the last complete, internally consistent set of SQLite metadata, GraphRAG data, vector data, and indexes. Workers build candidate outputs for the next snapshot in staging locations. DocSeek switches the active pointer only after validation succeeds across all required stores.

**Processing status** is the current operational state of the worker pipeline: queued, running, complete, interrupted, or failed, with the current agent, timestamps, heartbeat, and retryable failure message. It is not an audit log.

If a pipeline fails, the previous active snapshot remains readable, the failure stage and retry action are shown, and the project unlocks. DG-Agent's filename suggestion is presented after the lock is released; the user may accept or revise it without it being silently applied.

## 5. Provider and System Configuration

System Configuration is restricted to the system-configuration capability and is normally assigned only to the immutable Superuser role.

Administrators can create multiple LLM provider profiles and embedding provider profiles. Each agent has an independent route to a profile and model:

- DG-Agent
- EC-Agent
- GA-Agent
- PGB-Agent
- EGB-Agent
- AI Query
- Future agents added later

The view validates provider connectivity and model capability before accepting a profile. A worker captures its selected provider and model at job start, so a configuration change cannot modify an active job halfway through. Credentials are never displayed in clear text.

## 6. Permission Model

Release 1 uses global, action-level permissions. Permissions are assigned to roles, roles are assigned to groups, and users receive capabilities only through group membership. A user may belong to multiple groups; effective access is the union of the capabilities granted by the user's roles. There are no direct user grants or deny rules.

Built-in role templates and administrator-created custom roles are both supported. The immutable Superuser role always retains every capability, including user, group, role, and System Configuration management, and cannot be edited or removed through the WebUI.

The initial capability families are:

- `project.view`, `project.create`, `project.rename`, `project.delete`
- `property.view`, `property.upload`, `property.replace`, `property.edit`, `property.delete`, `property.rename`, `property.move`
- `property.attribute.view`, `property.attribute.edit`
- `graph.property.view`, `graph.entity.view`
- `query.execute`
- `agent.status.view`, `agent.retry`, `agent.cancel`
- `system.config.view`, `system.config.edit`
- `user.manage`, `group.manage`, `role.manage`
- `mcp.use`, `mcp.configure`

Project creation, rename, and deletion are controlled by the separate project-management capabilities. No project-specific permission table is introduced in the first release.

## 7. React WebUI

The selected project screen uses an investigation-workbench structure with a deliberately simple left panel:

```text
+--------------------------------------------------------------------+
| DocSeek | Project selector | Search | User profile | Admin actions |
+----------------------+---------------------------------------------+
| Property tree only   | [Entity Graph] [Property Graph] [AI Query]  |
|                      |                                             |
| - directory         |                 active tab                 |
| - property          |                                             |
+----------------------+---------------------------------------------+
```

The Entity Graph tab displays all entities in the selected project by default. The Property Graph tab displays all properties. Search, filtering, focus, and layout controls affect only the view. They do not change the underlying graph.

Clicking a property in the tree or Property Graph opens floating Property Preview and Property Attribute windows. Preview renders the source property; Attribute renders the definition and entity list. These windows are read-only while the project lock is active and expose edit controls only when the current user has the relevant capabilities and no worker is processing the project.

Project management, User/Group/Role management, System Configuration, MCP settings, and the current user's profile are top-level shell views or administrative routes and do not consume the property-tree panel.

## 8. MCP Contract

The MCP server can be enabled and configured through the permission-gated MCP settings view. An MCP invocation first checks `mcp.use`, then evaluates the underlying capability for the requested operation. A caller cannot gain access through MCP that the same login identity would not have in the WebUI.

MCP responses use the same project, property, graph, and query services as the WebUI so behavior, citations, processing locks, and error handling remain identical.

## 9. Failure Handling and Recovery

- Invalid file formats, parsing failures, provider errors, schema-invalid agent output, and storage errors stop publication before changing the active snapshot.
- Candidate vector and GraphRAG outputs are isolated until validation succeeds.
- The previous active snapshot remains readable after any failure.
- Persisted job heartbeats allow restart recovery. Stale running jobs become interrupted and can be safely requeued.
- Invalid provider configuration is rejected while the last valid configuration remains active.
- A mutation received during a project lock is rejected by the API and disabled in the WebUI.

## 10. Verification Strategy

- Unit tests cover permission resolution, provider routing, SQLite foreign keys, snapshot transitions, filename suggestions, and lock behavior.
- Integration tests use deterministic fake LLM and embedding providers to exercise the complete DG -> EC -> GA -> PGB/EGB pipeline, retries, rollback, and restart recovery.
- Storage adapter tests cover SQLite, vector storage, and the single GraphRAG database.
- End-to-end tests cover project management, upload/edit locking, floating property windows, graph tabs, AI Query citations, System Configuration, and role/group administration.
- MCP parity tests verify that an operation allowed or denied in the WebUI is allowed or denied identically through MCP.

## 11. Implementation Decomposition

Release 1 remains one complete product, but implementation should be split into bounded tracks:

1. Runtime foundation, local authentication, groups, roles, and authorization.
2. Server configuration, provider adapters, project directories, SQLite metadata, and processing state.
3. Property ingestion, editing, project lock, DG-Agent, and smart grouping.
4. Entity extraction, SQLite relationships, GraphRAG projections, vector indexing, and recovery behavior.
5. React workbench, graph tabs, floating property windows, and AI Query.
6. User administration, MCP exposure, permission parity, and end-to-end verification.

Each track communicates through domain services and persisted project interfaces rather than reaching into another module's storage internals.

## 12. Release 1 Success Criteria

Release 1 is successful when a self-hosted operator can create local accounts and roles, configure multiple providers and per-agent model routes, create a project, upload or edit properties, observe the project-wide processing lock, receive DG-Agent definitions and filename suggestions, browse complete Property and Entity Graph views, inspect properties through floating windows, run cited AI Queries, manage authorized users/groups/roles, and invoke the same authorized capabilities through MCP. A server restart must not lose the active project snapshot or leave the project permanently locked.
