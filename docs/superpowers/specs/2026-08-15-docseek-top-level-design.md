# DocSeek Top-Level Platform Design

Date: 2026-08-15
Status: Revised design approved in brainstorming; pending written-spec review

## 1. Product Boundary

DocSeek is a self-hosted internal knowledge-base management and query system for one enterprise, organization, or person per deployment. Release 1 includes property ingestion and editing, definition generation, smart grouping, Property Graph and Entity Graph construction, natural-language Search, GraphRAG-based AI Query, the React WebUI, local authentication, global role-based permissions, user/group/role administration, System Configuration, and MCP exposure.

Audit is explicitly excluded. DocSeek keeps only the operational state needed for locks, processing status, recovery, and retry. It does not provide an audit timeline or compliance-audit subsystem.

## 2. Deployment and Runtime

The first release is optimized for a private server or other self-hosted infrastructure. One deployment serves one organization-wide authentication realm and one global permission policy. The application is a modular monolith with API, worker, and React runtime processes where useful, but all business capabilities share explicit domain interfaces.

The principal modules are:

- React WebUI and project workbench
- Python API and domain services
- Local authentication, groups, roles, and authorization
- Project and property management
- Definition Generation Agent (DG-Agent)
- Group Arrangement Agent (GA-Agent)
- Property Graph Building Agent (PGB-Agent)
- Neo4j GraphRAG Entity Graph pipeline
- Neo4j Property Graph and Entity Graph databases
- Shared embedding and retrieval services
- AI Query
- MCP server and MCP management view

EC-Agent and EGB-Agent are removed. Entity extraction and entity-graph writing are performed by the Neo4j GraphRAG pipeline.

## 3. Projects and Filesystem Boundary

Projects are independent knowledge corpora. Every authenticated user can discover and use all projects for which the relevant global capabilities are granted; there are no project-specific ACLs in release 1.

The application directory has this shape:

```text
<program directory>/
  conf/
  projects/
    <stable-project-id>/
      properties/          # original uploaded properties
      extracted-text/      # normalized text for text properties
      jobs/                # optional operational job artifacts
```

The user must enter a project name when creating a project. The server assigns a stable project ID for the directory. Renaming changes the display name and can happen whenever the user has `project.rename`; it does not move the stable project directory.

`conf/` stores system configuration, local users/groups/roles, provider profiles, and durable operational state. Neo4j stores canonical property, attribute, entity, graph, and vector-index data. No property or entity records are stored in project SQLite.

## 4. Neo4j Graph Architecture

DocSeek uses one Neo4j server with two named databases:

```text
property_graph
entity_graph
```

### 4.1 Property Graph

Every property becomes a node in `property_graph`, including image properties. A property node stores its stable property ID, filename, type, definition, source metadata, and embedding.

PGB-Agent receives the current property-node inventory, property names, definitions, and applicable relationship rules. It generates **edges only**. DocSeek validates and writes those edges between existing property nodes. PGB-Agent never creates property nodes.

### 4.2 Entity Graph

Only non-image property text documents are sent to the Neo4j GraphRAG Knowledge Graph Builder. The builder extracts entity nodes and relationships, applies the fixed DocSeek entity schema and extraction prompt, resolves duplicate entities, and writes the result to `entity_graph`.

Images are never input to the Entity Graph pipeline. The Entity Graph tab reads all entity nodes and relationships from `entity_graph`.

The DocSeek entity schema and extraction prompt are system-wide and editable only in System Configuration. Projects cannot override them. A schema or prompt change requires rebuilding `entity_graph` from all current non-image property text.

### 4.3 Shared Embeddings and Retrieval

One configured embedding route is shared by:

- Property Graph property nodes
- Entity Graph entity/document records
- Natural-language Search
- AI Query retrieval

Search embeds the user query, retrieves matching Properties and Entities from the two Neo4j databases, and may expand relevant graph neighborhoods. Search never invokes an LLM.

AI Query retrieves from both `property_graph` and `entity_graph`, combines graph and source context, and sends that context to the configured AI Query LLM for answer generation.

## 5. Property Lifecycle and Agent Pipeline

Supported properties include text, Markdown, PDF, Word, HTML, code, and images.

1. User uploads, edits, replaces, or removes a property.
2. The project-wide property-mutation lock activates.
3. DG-Agent generates a definition. For images, the configured DG-Agent model must support image input; its vision result is used as the property definition.
4. The property node and shared embedding are written to a candidate `property_graph` snapshot.
5. PGB-Agent proposes edges only; validated edges are written between candidate property nodes.
6. Non-image properties are normalized into text documents and passed as the complete corpus to Neo4j GraphRAG for a candidate `entity_graph` rebuild.
7. Candidate graph/vector indexes are validated.
8. The candidate snapshot becomes active, or the previous active snapshot remains available on failure.
9. The project unlocks.

Remove permanently deletes the original property and its Property Graph node, embedding, PGB edges, and all derived references. The Entity Graph is rebuilt from the remaining non-image text properties.

## 6. Project-Wide Consistency Lock

While a worker pipeline is processing a project, all property mutations in that project are disabled. Read-only property tree, graph, preview, Search, and AI Query views remain available against the previous active snapshot. The API rejects bypass attempts.

The operational state store persists the lock, job stage, heartbeat, candidate snapshot ID, and failure details. It is not a property/entity store and is not an audit history.

Candidate graph data uses a snapshot identifier. Reads use the active snapshot until every required candidate database/index is ready. A failed candidate never replaces the active state. Restart recovery detects stale jobs and clears or requeues them so a project cannot remain permanently locked.

## 7. React WebUI Structure

The selected project workspace has:

- A top bar with project selector, natural-language Search, user profile, and permission-gated administration.
- A left panel containing only the property tree.
- A center tab page with Entity Graph, Property Graph, and AI Query.
- Floating Property Preview and Property Attribute windows when a property is selected in the tree or Property Graph.
- A project-scoped MCP Management view with explicit Open MCP and Close MCP actions.

Property Preview renders image properties as images and supported document formats through their previewers. Property Attribute reads the property node’s definition and related metadata from `property_graph`.

Entity Graph displays all entities from `entity_graph` by default. Property Graph displays all property nodes and PGB-Agent edges from `property_graph` by default. Filters and focus controls alter only the viewport.

## 8. System Configuration

System Configuration is superuser-only by default. It provides:

- Multiple LLM provider profiles.
- Multiple embedding provider profiles.
- A DG-Agent route that must support images when image properties are processed.
- A GA-Agent route.
- A PGB-Agent route.
- An AI Query LLM route.
- One shared embedding route for both graph databases and Search.
- Neo4j URI, credentials, `property_graph` database, and `entity_graph` database.
- Neo4j schema/index readiness checks.
- Fixed system-wide Entity Graph schema, constraints, and extraction prompt.
- Project-root and original-file storage validation.
- MCP endpoint enablement settings.

Workers capture routes at job start; configuration changes do not alter active work.

## 9. Permission Model

Permissions are global and action-level. Permissions are assigned to roles, roles are assigned to groups, and users receive the union of role capabilities through group membership. There are no direct user grants, deny rules, or project-specific permission tables.

Built-in role templates and custom roles are supported. The immutable Superuser role always has every capability and cannot be edited or removed through the WebUI.

Initial capabilities include:

- `project.view`, `project.create`, `project.rename`, `project.delete`
- `property.view`, `property.upload`, `property.replace`, `property.edit`, `property.delete`, `property.rename`, `property.move`
- `property.attribute.view`, `property.attribute.edit`
- `graph.property.view`, `graph.entity.view`
- `search.properties`, `search.entities`, `query.execute`
- `agent.status.view`, `agent.retry`, `agent.cancel`
- `system.config.view`, `system.config.edit`
- `user.manage`, `group.manage`, `role.manage`
- `mcp.use`, `mcp.configure`

## 10. MCP Contract and Lifecycle

MCP is not globally enabled by default. After opening a project, the user must open MCP manually in the MCP Management view. The server binds to the currently selected project and is network-accessible without a separate MCP authentication step.

The endpoint inherits the effective capabilities of the logged-in WebUI user who opened it. Each tool operation is checked against those capabilities. The network boundary is the only caller-authentication boundary.

Closing MCP stops the server. Closing the project, logging out, or switching projects stops the old server. Switching to a new project does not automatically open MCP; the user must open it manually again.

MCP has no project-management tools. The project context is implicit. Release-1 tools are:

- `list_properties`, `get_property`, `get_property_attribute`
- `add_property`, `replace_property`, `remove_property`
- `list_entities`, `get_entity`
- `search_properties`, `search_entities`
- `get_property_graph`, `get_entity_graph`
- `ask_ai_query`, `get_processing_status`

MCP property mutations use the same project-wide lock and permanent-remove behavior as the WebUI.

## 11. Failure Handling and Verification

- Parser or DG-Agent failure prevents property activation.
- PGB-Agent failure prevents candidate Property Graph edges from activating.
- Neo4j GraphRAG failure prevents candidate Entity Graph data from activating.
- Invalid provider configuration leaves the last valid route active.
- Search errors return retrieval failures without invoking an LLM.
- AI Query errors preserve the question and expose a retryable generation error.
- Server restart recovers stale jobs and preserves the previous active graphs.

Verification includes unit tests for capability resolution, lock state, project naming, snapshot transitions, and provider routing; Neo4j integration tests for schemas, vector indexes, PGB edge writes, GraphRAG extraction, entity resolution, and two-database isolation; image processing tests; Search tests proving no LLM call; AI Query tests proving both graphs are retrieved; React end-to-end tests; and MCP lifecycle/capability tests.

## 12. Implementation Decomposition

Release 1 remains one product but is split into bounded tracks:

1. Runtime foundation, local authentication, roles, groups, and authorization.
2. Project lifecycle, property originals, operational state, and project lock.
3. DG-Agent, parsers, image preview, definitions, embeddings, and property node writes.
4. PGB-Agent edge generation and `property_graph` Neo4j integration.
5. Neo4j GraphRAG Entity Graph pipeline, fixed schema/prompt, entity resolution, and `entity_graph` integration.
6. Search, AI Query, graph views, property overlays, and React workbench.
7. System Configuration, MCP Management, MCP tools, and end-to-end verification.

## 13. Release 1 Success Criteria

A self-hosted operator can create local accounts and roles, configure Neo4j and model providers, create and rename projects, upload text and image properties, see images in Property Preview, receive DG definitions, browse Property Graph and Entity Graph data from the two Neo4j databases, search Properties and Entities without an LLM, ask AI Query questions using both graphs, operate the project-scoped MCP tools, and recover safely from worker/server interruption without losing the last active state.
