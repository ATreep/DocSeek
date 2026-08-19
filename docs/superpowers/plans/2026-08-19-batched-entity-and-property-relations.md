# Batched Entity and Property Relations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans (recommended). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace whole-inventory entity and property relation calls with bounded collection calls, add redundant entity merging, preserve unaffected graph edges, and measure the resulting stages using the configured provider routes.

**Architecture:** Add shared deterministic collection and bounded-call helpers. Keep model-specific prompt parsing in `GraphRAGBuilder` and `PGBAgent`; keep directed merge-graph propagation pure and provider-independent. The pipeline runs entity extraction, redundant merging, entity relation substages, and property relation substages with explicit barriers and the existing user-configured concurrency limit.

**Tech Stack:** Python 3.12, FastAPI/LangGraph pipeline, `concurrent.futures`, pytest, React/TypeScript i18n, SQLite-configured OpenAI-compatible providers.

---

### Task 1: Add shared collection specifications and deterministic graph helpers

**Files:**
- Create: `backend/app/services/relation_batches.py`
- Create: `backend/tests/test_relation_batches.py`

- [ ] **Step 1: Write failing collection schedule tests**

Cover `chunks(items, 10)` at 0, 1, 10, and 11 items. Assert merge specifications contain one internal `Ai` call per new collection, `Ai-Aj` only for `i < j`, every `Ai-Bj`, and no `B-B`. Assert relation specifications contain `A` internal calls and the same cross-call pairs.

- [ ] **Step 2: Run the focused tests and verify the missing helper fails**

Run:

```bash
uv run pytest backend/tests/test_relation_batches.py -q
```

Expected: collection helper import failure.

- [ ] **Step 3: Implement collection dataclasses and schedule enumeration**

Define immutable `CollectionPair` data with `left`, `right`, `kind`, and `allow_same_side` fields. Implement deterministic `chunk_nodes`, `merge_call_specs`, and `relation_call_specs`. Return no specs for empty inputs and preserve input ordering.

- [ ] **Step 4: Implement directed merge graph propagation**

Add pure functions for Tarjan strongly connected components, cycle-edge filtering, topological propagation, and terminal-source removal. Accept entity dictionaries and directed `(source_id, target_id)` proposals. Union property IDs and contexts by their stable keys, retain unmentioned new nodes, remove only new sources with surviving outgoing edges, and never remove old nodes.

- [ ] **Step 5: Add chain, fan-out, cycle, and retention tests**

Assert `1 -> 2 -> 3` gives node 3 metadata from all three and removes 1 and 2; fan-out copies source metadata to every terminal target; cyclic internal proposals are discarded; unmentioned new nodes remain; and old-old edges are not touched by the helper.

- [ ] **Step 6: Run the helper tests**

Run:

```bash
uv run pytest backend/tests/test_relation_batches.py -q
```

Expected: PASS.

### Task 2: Implement bounded redundant entity merge calls

**Files:**
- Modify: `backend/app/services/graph_store.py`
- Modify: `backend/app/services/system_prompts.py`
- Modify: `backend/tests/test_graph_contracts.py`

- [ ] **Step 1: Write failing merge-call contract tests**

Test internal, `A-A`, and `A-B` prompts; compact `id/name/definition` payloads; valid direction filtering; empty response acceptance; mixed-validity response acceptance without retry; wholly-invalid nonempty response retry; malformed response retry; and exhausted retry failure.

- [ ] **Step 2: Run the contract tests to verify the current whole-graph API fails**

Run:

```bash
uv run pytest backend/tests/test_graph_contracts.py -k "merge or relation" -q
```

Expected: failures because there is no bounded merge invocation API.

- [ ] **Step 3: Add the role-specific merge prompt and response parser**

Add an unbranded role prompt describing redundant-entity judgment by definition similarity. Implement a `GraphRAGBuilder` method that accepts one `CollectionPair`, emits `{"merges":[["source","target"]]}`, validates individual items, ignores invalid items when at least one item is valid, and retries only malformed or wholly-invalid responses.

- [ ] **Step 4: Connect the pure merge graph application**

Expose a builder method that consolidates exact IDs, applies the validated directed merge set through `relation_batches.py`, preserves source metadata transitively, and returns surviving entities. Keep the existing embedding refresh behavior after the relation stages.

- [ ] **Step 5: Run entity merge contract tests**

Run:

```bash
uv run pytest backend/tests/test_graph_contracts.py -k "merge" -q
```

Expected: PASS.

### Task 3: Implement bounded entity relation calls

**Files:**
- Modify: `backend/app/services/graph_store.py`
- Modify: `backend/app/services/system_prompts.py`
- Modify: `backend/tests/test_graph_contracts.py`

- [ ] **Step 1: Write failing within/cross relation tests**

Verify a within-collection prompt rejects same-side violations only through validation, a cross prompt permits either direction but requires one endpoint per collection, and outputs contain only compact entity identity data.

- [ ] **Step 2: Implement one-pair relation invocation**

Add a `GraphRAGBuilder` method that receives a `CollectionPair`, requests `edges`, accepts the existing object edge shape and compact list shape, normalizes Unicode relation types, rejects unknown/self/same-side endpoints, and applies the existing strict retry policy.

- [ ] **Step 3: Remove the one-response full graph requirement**

Keep compatibility wrappers only where existing tests or callers require them, but stop constructing prompts containing the complete entity inventory and complete old edge set. Make the pipeline consume per-pair edges and preserve old-old edges in application code.

- [ ] **Step 4: Run entity relation contract tests**

Run:

```bash
uv run pytest backend/tests/test_graph_contracts.py -k "relation_generation or relation_call" -q
```

Expected: PASS.

### Task 4: Refactor Property Graph Building Agent to two substages

**Files:**
- Modify: `backend/app/services/agents.py`
- Modify: `backend/tests/test_graph_contracts.py`

- [ ] **Step 1: Write failing property pair-call tests**

Test compact property metadata, within-collection and cross-collection endpoint constraints, no `Q-Q` contract, Unicode relation types, and preservation of the existing local fallback behavior.

- [ ] **Step 2: Implement `PGBAgent.propose_pair`**

Accept one `CollectionPair`, build a prompt that explicitly forbids same-side edges for cross calls, parse compact list or existing object edge forms, validate endpoints/types, and retain retry behavior. Keep `propose(inventory)` as a compatibility wrapper for a single within-collection call where existing unit tests need it.

- [ ] **Step 3: Add property edge aggregation tests**

Assert only edges whose endpoints are both unchanged properties are preserved; edges touching a changed property are regenerated; duplicate triples are removed; and no old-old model call is scheduled.

- [ ] **Step 4: Run property contract tests**

Run:

```bash
uv run pytest backend/tests/test_graph_contracts.py -k "pgb or property" -q
```

Expected: PASS.

### Task 5: Integrate staged concurrency and barriers in the pipeline

**Files:**
- Modify: `backend/app/services/pipeline.py`
- Modify: `backend/tests/test_pipeline.py`

- [ ] **Step 1: Write failing orchestration tests**

Use fake providers and synchronization events to assert merge calls run concurrently, no merge result is applied before all merge futures finish, entity relation Substage 2 waits for all Substage 1 futures, property relation Substage 2 waits for all property Substage 1 futures, and worker counts never exceed `batch_llm_concurrency`.

- [ ] **Step 2: Add bounded worker orchestration**

Add a reusable pipeline helper that executes call specifications with a bounded `ThreadPoolExecutor`, preserves deterministic result ordering after completion, checks cancellation, updates heartbeats, and closes each worker provider.

- [ ] **Step 3: Replace the current entity full rebuild call**

Partition exact-ID-consolidated entities, run the Redundant Entity Merging stage, apply all merge results after the barrier, repartition surviving new entities, run entity relation Substage 1, wait, then run Substage 2. Preserve old-old edges and combine validated edges with deterministic deduplication.

- [ ] **Step 4: Replace the current one-call property future**

Partition processed/new properties from unchanged properties, run property relation Substage 1 and Substage 2 through the same bounded worker helper, preserve unchanged–unchanged edges, and return the rebuilt property edge list. Keep property work concurrent with entity extraction where the existing pipeline does so, but ensure its internal Substage 2 barrier is complete before the final snapshot is written.

- [ ] **Step 5: Add aggregate progress updates**

Use the visible stage label `Redundant Entity Merging`. Emit merge proposal progress as completed/total and apply-merge detail. Emit `Generating relations for entities. <percent%>` after each entity relation invocation, counting both entity substages. Emit `Generating property relations. <percent%>` after each property relation invocation, counting both property substages.

- [ ] **Step 6: Run focused pipeline tests**

Run:

```bash
uv run pytest backend/tests/test_pipeline.py -k "parallel_entity_generation or relation or property" -q
```

Expected: PASS.

### Task 6: Update localized frontend progress mappings

**Files:**
- Modify: `frontend/src/processing-status.ts`
- Modify: `frontend/src/processing-status.test.ts`
- Modify: `frontend/src/i18n.ts`

- [ ] **Step 1: Add failing mapping assertions**

Assert `Redundant Entity Merging`, aggregate entity relation percentages, aggregate property relation percentages, and their English/Chinese keys.

- [ ] **Step 2: Implement mappings and translations**

Add complete translations without hard-coded visible component strings. Use `资产` for property and `实体` for entity in Chinese.

- [ ] **Step 3: Run focused frontend tests**

Run:

```bash
cd frontend && npm test -- --run src/processing-status.test.ts
```

Expected: PASS.

### Task 7: Full focused verification and configured-model timing benchmark

**Files:**
- Verify: `backend/app/services/relation_batches.py`, `backend/app/services/agents.py`, `backend/app/services/graph_store.py`, `backend/app/services/pipeline.py`, and the focused test files.

- [ ] **Step 1: Run independent backend unit files in parallel**

Run:

```bash
uv run pytest backend/tests/test_relation_batches.py backend/tests/test_graph_contracts.py backend/tests/test_pipeline.py -q
```

Do not run full-volume import tests.

- [ ] **Step 2: Run focused frontend tests**

Run:

```bash
cd frontend && npm test -- --run src/processing-status.test.ts
```

- [ ] **Step 3: Run a real configured-model benchmark**

Read the active `entity_agent_route`, `pgb_agent_route`, and `batch_llm_concurrency` from `data/conf/docseek.sqlite3`. Use `Settings(data_dir=Path("./data"))` and the selected routes, construct 11 synthetic new nodes and 1 old node for each graph kind, and invoke the actual worker methods with a monotonic clock. Record separately:

```text
Redundant Entity Merging: total wall time and call count
Entity Relation Substage 1: total wall time and call count
Entity Relation Substage 2: total wall time and call count
Property Relation Substage 1: total wall time and call count
Property Relation Substage 2: total wall time and call count
```

Do not print or expose provider secrets. Report model names and concurrency only, plus elapsed durations and call counts. If the configured provider is unavailable, report the exact provider failure instead of inventing timings.

- [ ] **Step 4: Review final status and test output**

Run `git diff --check`, inspect only task-related diffs, and report any pre-existing failures separately from failures introduced by this change. Include the measured timing table in the final response.
