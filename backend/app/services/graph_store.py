from __future__ import annotations

import asyncio
import json
import hashlib
import math
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import jieba

from ..config import Settings
from .agents import normalize_relation_type, parse_json_object
from .providers import embedding_provider

DEFAULT_ENTITY_SCHEMA = "DocSeekEntity(name, type, definition, source_property_ids)"
EMBEDDING_CHUNK_CHARS = 12_000
EMBEDDING_BATCH_SIZE = 64
ENTITY_GRAPH_MAX_TOKENS = 4096
ENTITY_CONTEXT_WORD_LIMIT = 250
PREVIOUS_ENTITY_PROMPT = (
    "Only extract notional nouns that refer to specific identifiable objects, such as people, products, "
    "technology stacks, brands, companies, organizations, or places. Do not extract generic common nouns, "
    "abstract qualities, actions, file names, sentences, or summaries. When prioritizing words that occur "
    "frequently, count repetitions only when the word has the same meaning in every context; separate or "
    "ignore occurrences that refer to different objects or meanings. The entity identifier may differ slightly "
    "from the original text when a concise canonical or clarified name helps the user understand what the object "
    "is, but do not invent information. Return the entity identifier and one brief definition for each object. "
    "Some entities may already exist, so resolve against this current entity inventory of identifier and "
    "definition: {current_entities}"
)
PREVIOUS_FIXED_RELATION_ENTITY_PROMPT = (
    f"{PREVIOUS_ENTITY_PROMPT} The entity graph may contain multiple independent subgraphs and isolated entities. "
    "Create a relationship only when the source explicitly states or clearly establishes a meaningful relation "
    "between two specific entities. Do not force relationships between unrelated entities. Do not connect entities "
    "merely because they occur in the same property, sentence, topic, or inventory. It is valid to return entities "
    "with no relationships."
)
PREVIOUS_DYNAMIC_RELATION_ENTITY_PROMPT = (
    f"{PREVIOUS_FIXED_RELATION_ENTITY_PROMPT} For every supported entity edge, choose the most appropriate relation type "
    "for the meaning and direction stated by the source. Relation types are not limited to a predefined relation list; "
    "use a concise descriptive label rather than a generic fallback when the source supports something more specific. "
    "Whenever a property is added or removed, rebuild the complete entity relationship set from all current non-image "
    "properties so existing relationships and their types may change in light of the complete current graph."
)
ENTITY_DEFINITION_GUIDANCE = (
    "For each entity definition, write a single brief plain-language sentence that lets a user understand what the entity "
    "is at a glance. Prefer 25 words or fewer and state only the minimum necessary identity, category, purpose, or role. "
    "Do not copy, quote, or lightly rephrase original source text, code, code snippets, configuration, logs, markup, or other "
    "raw document content as the definition. Do not use Markdown, lists, headings, or multiple sentences."
)
PREVIOUS_CONCISE_DEFINITION_ENTITY_PROMPT = (
    f"{PREVIOUS_DYNAMIC_RELATION_ENTITY_PROMPT} {ENTITY_DEFINITION_GUIDANCE}"
)
ENTITY_SELECTION_GUIDANCE = (
    "Do not try to extract every noun. A small result is preferable to a large list of weak entities. Extract a candidate "
    "only when it is meaningful enough to be clearly described in one short sentence without copying the source or inventing "
    "details. Exclude overly common concepts such as coding, network, PC, or user unless the text names a specific product, "
    "standard, organization, or other distinct entity. Exclude function words, filler words, structural labels, section names, "
    "standalone codes or numbers, identifiers, hashes, variable names, function names, class names, and arbitrary code tokens. "
    "Prefer rare but meaningful nouns or concepts; names of people, companies, organizations, products, brands, or places; "
    "and established real-world or professional concepts, standards, laws, or regulations. Rarity alone is not enough: each "
    "entity must have a stable, specific identity outside its sentence."
)
DEFAULT_ENTITY_PROMPT = f"{PREVIOUS_CONCISE_DEFINITION_ENTITY_PROMPT} {ENTITY_SELECTION_GUIDANCE}"

try:
    from neo4j_graphrag.experimental.pipeline.kg_builder import SimpleKGPipeline as Neo4jSimpleKGPipeline
except ImportError:  # pragma: no cover - exercised when the optional adapter is unavailable
    Neo4jSimpleKGPipeline = None


def embedding(text: str, dimensions: int = 32) -> list[float]:
    values = [0.0] * dimensions
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        bucket = int.from_bytes(hashlib.sha256(token.encode("utf-8")).digest()[:4], "big") % dimensions
        values[bucket] += 1.0
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [value / norm for value in values]


def _combine_embedding_vectors(vectors: list[list[float]]) -> list[float]:
    if len(vectors) == 1:
        return vectors[0]
    dimensions = min(len(vector) for vector in vectors)
    averaged = [
        sum(vector[index] for vector in vectors) / len(vectors)
        for index in range(dimensions)
    ]
    norm = math.sqrt(sum(value * value for value in averaged)) or 1.0
    return [value / norm for value in averaged]


def embeddings_for_texts(
    texts: list[str], embedder: Any | None = None
) -> list[list[float]]:
    if not texts:
        return []
    if embedder is None:
        return [embedding(text) for text in texts]

    flat_chunks: list[str] = []
    spans: list[tuple[int, int]] = []
    for text in texts:
        chunks = [
            text[index : index + EMBEDDING_CHUNK_CHARS]
            for index in range(0, len(text), EMBEDDING_CHUNK_CHARS)
        ] or [""]
        start = len(flat_chunks)
        flat_chunks.extend(chunks)
        spans.append((start, len(flat_chunks)))

    flat_vectors: list[list[float]] = []
    for start in range(0, len(flat_chunks), EMBEDDING_BATCH_SIZE):
        flat_vectors.extend(embedder.embed(flat_chunks[start : start + EMBEDDING_BATCH_SIZE]))
    return [
        _combine_embedding_vectors(flat_vectors[start:end]) for start, end in spans
    ]


def embeddings_for_settings(texts: list[str], settings: Settings) -> list[list[float]]:
    provider = embedding_provider(settings, route_key="shared_embedding_route")
    try:
        return embeddings_for_texts(texts, provider)
    finally:
        if provider is not None:
            close = getattr(provider, "close", None)
            if close:
                close()


def embedding_for_settings(text: str, settings: Settings) -> list[float]:
    return embeddings_for_settings([text], settings)[0]


def similarity(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def entity_embedding_text(entity: dict[str, Any]) -> str:
    parts = [str(entity.get("name") or ""), str(entity.get("definition") or "")]
    parts.extend(
        str(context.get("text") or "")
        for context in entity.get("source_contexts", [])
        if isinstance(context, dict)
    )
    return "\n".join(part.strip() for part in parts if part.strip())


@dataclass
class GraphSnapshot:
    project_id: str
    snapshot_id: str
    properties: list[dict[str, Any]]
    entities: list[dict[str, Any]]
    property_edges: list[dict[str, Any]]
    entity_edges: list[dict[str, Any]]


class LocalGraphStore:
    """Development fallback persisted as graph JSON, never as canonical SQLite rows."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.data_dir / "graph-fallback"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, project_id: str) -> Path:
        return self.root / f"{project_id}.json"

    def _candidate_path(self, project_id: str, snapshot_id: str) -> Path:
        return self.root / f"{project_id}.{snapshot_id}.candidate.json"

    def write_snapshot(self, snapshot: GraphSnapshot) -> None:
        path = self._candidate_path(snapshot.project_id, snapshot.snapshot_id)
        path.write_text(json.dumps(asdict(snapshot), indent=2), encoding="utf-8")

    def activate(self, project_id: str, snapshot_id: str) -> None:
        candidate = self._candidate_path(project_id, snapshot_id)
        if candidate.exists():
            candidate.replace(self._path(project_id))

    def read(self, project_id: str, snapshot_id: str | None = None) -> GraphSnapshot | None:
        path = self._path(project_id) if not snapshot_id else self._candidate_path(project_id, snapshot_id)
        if not path.exists():
            return None
        return GraphSnapshot(**json.loads(path.read_text(encoding="utf-8")))

    def delete_project(self, project_id: str) -> None:
        self._path(project_id).unlink(missing_ok=True)
        for candidate in self.root.glob(f"{project_id}.*.candidate.json"):
            candidate.unlink(missing_ok=True)

    def search(self, project_id: str, query: str, kind: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        snapshot = self.read(project_id)
        if not snapshot:
            return []
        query_vector = embedding_for_settings(query, self.settings)
        candidates: list[dict[str, Any]] = []
        if kind in (None, "properties"):
            for item in snapshot.properties:
                score = similarity(query_vector, item.get("embedding", embedding(item.get("definition", ""))))
                candidates.append({"kind": "property", "score": round(score, 4), **item})
        if kind in (None, "entities"):
            for item in snapshot.entities:
                score = similarity(query_vector, item.get("embedding", embedding(item.get("name", ""))))
                candidates.append({"kind": "entity", "score": round(score, 4), **item})
        return sorted(candidates, key=lambda item: item["score"], reverse=True)[:limit]

    def graph(self, project_id: str, kind: str, snapshot_id: str | None = None) -> dict[str, Any]:
        snapshot = self.read(project_id, snapshot_id)
        if not snapshot:
            return {"nodes": [], "edges": [], "snapshot_id": None}
        if kind == "property":
            return {"nodes": snapshot.properties, "edges": snapshot.property_edges, "snapshot_id": snapshot.snapshot_id}
        return {"nodes": snapshot.entities, "edges": normalize_entity_edges(snapshot.entities, snapshot.entity_edges), "snapshot_id": snapshot.snapshot_id}


class Neo4jGraphStore:
    """Neo4j implementation with two named databases and a local fallback for development."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.local = LocalGraphStore(settings)
        self.driver = None
        try:
            if not getattr(settings, "use_neo4j", False):
                return
            from neo4j import GraphDatabase
            self.driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))
            self.driver.verify_connectivity()
        except Exception:
            if not settings.allow_local_fallback:
                raise

    @property
    def using_neo4j(self) -> bool:
        return self.driver is not None

    def close(self) -> None:
        if self.driver:
            self.driver.close()

    def write_snapshot(self, snapshot: GraphSnapshot) -> None:
        if not self.driver:
            self.local.write_snapshot(snapshot)
            return
        with self.driver.session(database=self.settings.neo4j_property_database) as session:
            session.run("CREATE CONSTRAINT property_id IF NOT EXISTS FOR (p:Property) REQUIRE p.id IS UNIQUE")
            if snapshot.properties and snapshot.properties[0].get("embedding"):
                session.run("CREATE VECTOR INDEX property_embedding IF NOT EXISTS FOR (p:Property) ON (p.embedding) OPTIONS {indexConfig: {`vector.dimensions`: $dimensions, `vector.similarity_function`: 'cosine'}}", dimensions=len(snapshot.properties[0]["embedding"]))
            session.run("MATCH (p:CandidateProperty {project_id: $project, snapshot_id: $snapshot}) DETACH DELETE p", project=snapshot.project_id, snapshot=snapshot.snapshot_id)
            for item in snapshot.properties:
                session.run("CREATE (p:CandidateProperty $props)", props={**item, "snapshot_id": snapshot.snapshot_id})
            for edge in snapshot.property_edges:
                session.run("MATCH (a:CandidateProperty {id:$source, project_id:$project, snapshot_id:$snapshot}), (b:CandidateProperty {id:$target, project_id:$project, snapshot_id:$snapshot}) CREATE (a)-[:RELATED {type:$type}]->(b)", **edge, project=snapshot.project_id, snapshot=snapshot.snapshot_id)
        with self.driver.session(database=self.settings.neo4j_entity_database) as session:
            if snapshot.entities and snapshot.entities[0].get("embedding"):
                session.run("CREATE VECTOR INDEX entity_embedding IF NOT EXISTS FOR (e:Entity) ON (e.embedding) OPTIONS {indexConfig: {`vector.dimensions`: $dimensions, `vector.similarity_function`: 'cosine'}}", dimensions=len(snapshot.entities[0]["embedding"]))
            session.run("MATCH (e:CandidateEntity {project_id: $project, snapshot_id: $snapshot}) DETACH DELETE e", project=snapshot.project_id, snapshot=snapshot.snapshot_id)
            for item in snapshot.entities:
                session.run("CREATE (e:CandidateEntity $props)", props={**item, "snapshot_id": snapshot.snapshot_id})
            for edge in snapshot.entity_edges:
                session.run("MATCH (a:CandidateEntity {id:$source, project_id:$project, snapshot_id:$snapshot}), (b:CandidateEntity {id:$target, project_id:$project, snapshot_id:$snapshot}) CREATE (a)-[:RELATED {type:$type}]->(b)", **edge, project=snapshot.project_id, snapshot=snapshot.snapshot_id)

    def delete_project(self, project_id: str) -> None:
        self.local.delete_project(project_id)
        if not self.driver:
            return
        with self.driver.session(database=self.settings.neo4j_property_database) as session:
            session.run("MATCH (p:Property {project_id: $project}) DETACH DELETE p", project=project_id)
        with self.driver.session(database=self.settings.neo4j_entity_database) as session:
            session.run("MATCH (e:Entity {project_id: $project}) DETACH DELETE e", project=project_id)

    def activate(self, project_id: str, snapshot_id: str) -> None:
        if not self.driver:
            self.local.activate(project_id, snapshot_id)
            return
        with self.driver.session(database=self.settings.neo4j_property_database) as session:
            session.run("MATCH (p:Property {project_id: $project}) DETACH DELETE p", project=project_id)
            session.run("MATCH (p:CandidateProperty {project_id: $project, snapshot_id: $snapshot}) REMOVE p:CandidateProperty SET p:Property REMOVE p.snapshot_id", project=project_id, snapshot=snapshot_id)
        with self.driver.session(database=self.settings.neo4j_entity_database) as session:
            session.run("MATCH (e:Entity {project_id: $project}) DETACH DELETE e", project=project_id)
            session.run("MATCH (e:CandidateEntity {project_id: $project, snapshot_id: $snapshot}) REMOVE e:CandidateEntity SET e:Entity REMOVE e.snapshot_id", project=project_id, snapshot=snapshot_id)

    def search(self, project_id: str, query: str, kind: str | None = None, limit: int = 10):
        if not self.driver:
            return self.local.search(project_id, query, kind, limit)
        query_vector = embedding_for_settings(query, self.settings)
        results: list[dict[str, Any]] = []
        search_specs = []
        if kind in (None, "properties"):
            search_specs.append(("property", "Property", "property_embedding", self.settings.neo4j_property_database))
        if kind in (None, "entities"):
            search_specs.append(("entity", "Entity", "entity_embedding", self.settings.neo4j_entity_database))
        for result_kind, label, index_name, database in search_specs:
            with self.driver.session(database=database) as session:
                try:
                    rows = session.run("CALL db.index.vector.queryNodes($index_name, $limit, $query_vector) YIELD node, score WITH node, score WHERE node.project_id=$project RETURN node, score", index_name=index_name, limit=limit, query_vector=query_vector, project=project_id)
                    results.extend({"kind": result_kind, **dict(row["node"]), "score": round(float(row["score"]), 4)} for row in rows)
                except Exception:
                    rows = session.run(f"MATCH (node:{label} {{project_id:$project}}) RETURN node LIMIT $limit", project=project_id, limit=limit)
                    results.extend({"kind": result_kind, **dict(row["node"])} for row in rows)
        return results[:limit]

    def graph(self, project_id: str, kind: str, snapshot_id: str | None = None):
        if not self.driver:
            return self.local.graph(project_id, kind, snapshot_id)
        if snapshot_id:
            label = "CandidateProperty" if kind == "property" else "CandidateEntity"
            edge_query = f"MATCH (a:{label} {{project_id:$project, snapshot_id:$snapshot}})-[r]->(b:{label} {{project_id:$project, snapshot_id:$snapshot}}) RETURN a.id AS source, b.id AS target, coalesce(r.type, type(r)) AS type"
        else:
            label = "Property" if kind == "property" else "Entity"
            edge_query = f"MATCH (a:{label} {{project_id:$project}})-[r]->(b:{label}) RETURN a.id AS source, b.id AS target, coalesce(r.type, type(r)) AS type"
        database = self.settings.neo4j_property_database if kind == "property" else self.settings.neo4j_entity_database
        with self.driver.session(database=database) as session:
            params = {"project": project_id, **({"snapshot": snapshot_id} if snapshot_id else {})}
            nodes = [dict(row["node"]) for row in session.run(f"MATCH (node:{label} {{project_id:$project{', snapshot_id:$snapshot' if snapshot_id else ''}}}) RETURN node", **params)]
            edges = [dict(row) for row in session.run(edge_query, **params)]
        if kind == "entity":
            edges = normalize_entity_edges(nodes, edges)
        return {"nodes": nodes, "edges": edges, "snapshot_id": snapshot_id or "neo4j-active"}


def extract_entities(documents: list[dict[str, str]], current_entities: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A deterministic local GraphRAG-compatible result; replaceable with KG Builder in production."""
    entity_names: dict[str, dict[str, Any]] = {}
    current_by_id = {str(item.get("id", "")).lower(): item for item in current_entities or [] if item.get("id")}
    for document in documents:
        for name in _candidate_entity_terms(document["text"], current_by_id):
            existing = current_by_id.get(name.lower(), {})
            entity_names.setdefault(name, {"id": name.lower(), "name": name, "project_id": document["project_id"], "source_property_ids": [], **({"definition": existing["definition"]} if existing.get("definition") else {})})
            entity = entity_names[name]
            if document["property_id"] not in entity["source_property_ids"]:
                entity["source_property_ids"].append(document["property_id"])
            _append_entity_contexts(entity, document, name)
    entities = [
        {**item, "embedding": embedding(entity_embedding_text(item))}
        for item in entity_names.values()
    ]
    edges = _typed_entity_edges(documents, list(entity_names))
    return entities, edges


_ENTITY_STOPWORDS = {"the", "and", "for", "from", "with", "this", "that", "file", "document", "content", "property", "uses", "using", "owns", "has", "have", "into", "will", "should", "about", "when", "where", "which"}


def _candidate_entity_terms(text: str, current_by_id: dict[str, dict[str, Any]]) -> list[str]:
    matches = list(re.finditer(r"\b[A-Za-z][A-Za-z0-9_-]{2,}\b", text))
    counts: dict[str, int] = {}
    display: dict[str, str] = {}
    first_index: dict[str, int] = {}
    for index, match in enumerate(matches):
        token = match.group(0)
        normalized = token.lower()
        counts[normalized] = counts.get(normalized, 0) + 1
        display.setdefault(normalized, token)
        first_index.setdefault(normalized, index)
    selected = []
    for normalized, count in counts.items():
        token = display[normalized]
        is_named = token[0].isupper() or any(char.isupper() for char in token[1:]) or token.isupper()
        if normalized in current_by_id or (normalized not in _ENTITY_STOPWORDS and (is_named or count >= 2)):
            selected.append((first_index[normalized], token))
    return [token for _, token in sorted(selected)]


def _typed_entity_edges(documents: list[dict[str, str]], entity_names: list[str]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for document in documents:
        normalized = re.sub(r"\s+", " ", document["text"]).strip()
        for sentence in re.split(r"(?<=[.!?])\s+", normalized):
            for edge in _explicit_entity_edges(sentence, entity_names):
                if edge not in edges:
                    edges.append(edge)
    return edges


_RELATION_PHRASES = (
    ("depends on", "DEPENDS_ON"),
    ("powered by", "USES"),
    ("built with", "USES"),
    ("uses", "USES"),
    ("using", "USES"),
    ("owns", "OWNS"),
    ("maintains", "MAINTAINS"),
    ("contains", "CONTAINS"),
    ("includes", "CONTAINS"),
    ("references", "REFERENCES"),
    ("mentions", "REFERENCES"),
)


def _explicit_entity_edges(sentence: str, entity_names: list[str]) -> list[dict[str, str]]:
    mentions = []
    for name in entity_names:
        mentions.extend(
            (match.start(), match.end(), name)
            for match in re.finditer(rf"\b{re.escape(name)}\b", sentence, flags=re.IGNORECASE)
        )
    relations = []
    for phrase, relation_type in _RELATION_PHRASES:
        relations.extend(
            (match.start(), match.end(), relation_type)
            for match in re.finditer(rf"\b{re.escape(phrase)}\b", sentence, flags=re.IGNORECASE)
        )
    edges = []
    for relation_start, relation_end, relation_type in sorted(relations):
        left = [mention for mention in mentions if mention[1] <= relation_start]
        right = [mention for mention in mentions if mention[0] >= relation_end]
        if not left or not right:
            continue
        source = max(left, key=lambda mention: mention[1])[2]
        target = min(right, key=lambda mention: mention[0])[2]
        if source.casefold() == target.casefold():
            continue
        edge = {"source": source.lower(), "target": target.lower(), "type": relation_type}
        if edge not in edges:
            edges.append(edge)
    return edges


def _relationship_type(sentence: str) -> str | None:
    value = sentence.lower()
    for phrase, relation in _RELATION_PHRASES:
        if phrase in value:
            return relation
    return None


def normalize_entity_edges(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Upgrade legacy CO_OCCURS edges from shared stored mention context."""
    nodes_by_id = {str(node.get("id")): node for node in nodes}
    normalized = []
    for edge in edges:
        if edge.get("type") != "CO_OCCURS":
            normalized.append(edge)
            continue
        source_contexts = nodes_by_id.get(str(edge.get("source")), {}).get("source_contexts", [])
        target_contexts = nodes_by_id.get(str(edge.get("target")), {}).get("source_contexts", [])
        source_texts = {(item.get("property_id"), item.get("text")) for item in source_contexts if isinstance(item, dict) and item.get("text")}
        target_texts = {(item.get("property_id"), item.get("text")) for item in target_contexts if isinstance(item, dict) and item.get("text")}
        shared = next(iter(source_texts & target_texts), None)
        relationship_type = _relationship_type(shared[1]) if shared else None
        if relationship_type:
            normalized.append({**edge, "type": relationship_type})
    return normalized


def _entity_mention_contexts(text: str, entity_name: str) -> list[str]:
    """Keep readable source sentences with the local GraphRAG-compatible entity."""
    normalized = re.sub(r"[`*_#]", "", text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?。！？])\s*", normalized)
        if sentence.strip() and _entity_match(sentence, entity_name)
    ]


def _entity_match(text: str, entity_name: str) -> re.Match[str] | None:
    escaped = re.escape(entity_name)
    pattern = escaped if re.search(r"[\u3400-\u9fff]", entity_name) else rf"\b{escaped}\b"
    return re.search(pattern, text, flags=re.IGNORECASE)


def _context_token_spans(text: str) -> list[tuple[int, int]]:
    return [
        (start, end)
        for token, start, end in jieba.tokenize(text)
        if re.search(r"[A-Za-z0-9\u3400-\u9fff]", token)
    ]


def _context_word_count(text: str) -> int:
    return len(_context_token_spans(text))


def _truncate_context_around_entity(
    text: str, entity_name: str, max_words: int
) -> str:
    spans = _context_token_spans(text)
    if len(spans) <= max_words:
        return text.strip()
    if max_words <= 0:
        return ""
    mention = _entity_match(text, entity_name)
    if not mention:
        return ""
    mention_indexes = [
        index
        for index, (start, end) in enumerate(spans)
        if start < mention.end() and end > mention.start()
    ]
    if not mention_indexes or len(mention_indexes) > max_words:
        return ""
    center = (mention_indexes[0] + mention_indexes[-1]) // 2
    start_index = max(0, center - max_words // 2)
    end_index = min(len(spans), start_index + max_words)
    start_index = max(0, end_index - max_words)
    return text[spans[start_index][0] : spans[end_index - 1][1]].strip()


def _append_entity_contexts(
    entity: dict[str, Any], document: dict[str, str], entity_name: str
) -> None:
    contexts = entity.setdefault("source_contexts", [])
    used_words = sum(
        _context_word_count(str(context.get("text") or ""))
        for context in contexts
        if isinstance(context, dict)
    )
    remaining = ENTITY_CONTEXT_WORD_LIMIT - used_words
    if remaining <= 0:
        return
    for raw_context in _entity_mention_contexts(document["text"], entity_name):
        context = _truncate_context_around_entity(raw_context, entity_name, remaining)
        if not context or not _entity_match(context, entity_name):
            continue
        mention = {"property_id": document["property_id"], "text": context}
        if mention in contexts:
            continue
        contexts.append(mention)
        entity.setdefault("definition", context)
        remaining -= _context_word_count(context)
        if remaining <= 0:
            return


class GraphRAGBuilder:
    """GraphRAG contract with a local deterministic mode and an optional Neo4j KG Builder backend."""

    def __init__(self, schema: str, prompt: str, database: str = "entity_graph", llm: Any | None = None):
        self.schema = schema
        self.prompt = prompt
        self.database = database
        self.llm = llm
        self.last_documents: list[str] = []
        self.last_entity_inventory: list[dict[str, str]] = []
        try:
            import neo4j_graphrag  # noqa: F401
            self.backend = "neo4j-graphrag"
        except ImportError:
            self.backend = "local-fallback"

    def build(
        self,
        documents: list[dict[str, str]],
        embedder: Any | None = None,
        current_entities: list[dict[str, Any]] | None = None,
        incremental: bool = False,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        text_documents = [document for document in documents if document.get("property_type") != "image"]
        self.last_documents = [document["property_id"] for document in text_documents]
        self.last_entity_inventory = [
            {
                "id": str(item.get("id", "")),
                **(
                    {"name": str(item.get("name", ""))}
                    if item.get("name") is not None
                    else {}
                ),
                "definition": str(item.get("definition", "")),
            }
            for item in current_entities or []
            if item.get("id")
        ]
        if self.llm and text_documents:
            entities, edges = self._build_with_llm(
                text_documents, incremental=incremental
            )
        else:
            entities, edges = extract_entities(text_documents, self.last_entity_inventory)
        if embedder and entities:
            vectors = embedder.embed(
                [entity_embedding_text(entity) for entity in entities]
            )
            entities = [{**entity, "embedding": vector} for entity, vector in zip(entities, vectors)]
        return entities, edges

    def _build_with_llm(
        self, documents: list[dict[str, str]], incremental: bool = False
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        inventory = self.last_entity_inventory or []
        definition_guidance = "" if ENTITY_DEFINITION_GUIDANCE in self.prompt else f"{ENTITY_DEFINITION_GUIDANCE} "
        selection_guidance = "" if ENTITY_SELECTION_GUIDANCE in self.prompt else f"{ENTITY_SELECTION_GUIDANCE} "
        document_payload = [
            {
                "i": index,
                "text": document["text"][:12000],
            }
            for index, document in enumerate(documents)
        ]
        compact_inventory = json.dumps(
            inventory, ensure_ascii=False, separators=(",", ":")
        )
        configured_prompt = self.prompt.replace(
            "{current_entities}", compact_inventory
        )
        inventory_section = (
            ""
            if "{current_entities}" in self.prompt
            else f"Current entity inventory:\n{compact_inventory}\n"
        )
        if incremental:
            scope_guidance = (
                "For this incremental new-property call, override any earlier requirement to rebuild the complete graph. "
                "Inspect only the supplied new property documents. Treat the current entity inventory as entities already "
                "stored in the graph, not as source documents. Reuse an existing ID when the new documents mention the same "
                "entity. Do not re-extract unrelated existing entities and do not echo inventory entries that the new "
                "documents do not mention. Return only entities supported by the new documents and relationships newly "
                "supported or changed by them. "
            )
            endpoint_guidance = (
                "Edge endpoints may use returned entity IDs or IDs from the current entity inventory. "
            )
        else:
            scope_guidance = (
                "Extract the complete entity graph from all current property documents below. "
                "Always rebuild the complete entity relationship set on every call, so previous relations may be removed "
                "or assigned a different type when the current documents support it. "
            )
            endpoint_guidance = "Edge endpoints must use returned entity IDs. "
        prompt = (
            f"{configured_prompt}\n\n"
            f"{scope_guidance}"
            f"{definition_guidance}"
            f"{selection_guidance}"
            "For every supported edge, choose the most appropriate relation type for its actual meaning and direction. "
            "Relation types are not limited to a predefined relation list. "
            "Return compact JSON with arrays named entities and edges. Encode each entity as "
            '["id","name","definition",[0]] where the final array contains document i values. '
            'Encode each edge as ["source","target","type"]. '
            f"{endpoint_guidance}"
            "Do not return Markdown or explanatory text.\n"
            f"{inventory_section}"
            "Current property documents:\n"
            f"{json.dumps(document_payload, ensure_ascii=False, separators=(',', ':'))}"
        )
        raw = self.llm.complete(
            [
                {"role": "system", "content": "You are DocSeek Entity Extraction Agent."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=ENTITY_GRAPH_MAX_TOKENS,
        )
        try:
            parsed = parse_json_object(raw)
        except ValueError as exc:
            raise ValueError("entity extraction provider returned invalid JSON") from exc
        raw_entities = parsed.get("entities", []) if isinstance(parsed, dict) else []
        raw_edges = parsed.get("edges", []) if isinstance(parsed, dict) else []
        if not isinstance(raw_entities, list) or not isinstance(raw_edges, list):
            raise ValueError("entity extraction provider returned invalid graph arrays")

        project_id = documents[0]["project_id"]
        property_ids = {document["property_id"] for document in documents}
        property_ids_by_index = [document["property_id"] for document in documents]
        entities = []
        for item in raw_entities:
            if isinstance(item, dict):
                raw_id = item.get("id")
                raw_name = item.get("name")
                raw_definition = item.get("definition")
                raw_source_ids = item.get("source_property_ids", [])
            elif isinstance(item, list) and len(item) >= 4:
                raw_id, raw_name, raw_definition, raw_source_ids = item[:4]
            else:
                continue
            entity_id = str(raw_id or "").strip()
            name = str(raw_name or "").strip()
            if not entity_id or not name:
                continue
            source_ids = []
            if isinstance(raw_source_ids, list):
                for source_id in raw_source_ids:
                    if isinstance(source_id, int) and 0 <= source_id < len(property_ids_by_index):
                        resolved_id = property_ids_by_index[source_id]
                    else:
                        resolved_id = str(source_id)
                    if resolved_id in property_ids and resolved_id not in source_ids:
                        source_ids.append(resolved_id)
            entity = {
                "id": entity_id,
                "name": name,
                "definition": str(raw_definition or "").strip(),
                "project_id": project_id,
                "source_property_ids": source_ids,
                "source_contexts": [],
            }
            for document in documents:
                if document["property_id"] in source_ids:
                    _append_entity_contexts(entity, document, name)
            entities.append(
                {
                    **entity,
                    "embedding": embedding(entity_embedding_text(entity)),
                }
            )

        entity_ids = {entity["id"] for entity in entities}
        valid_edge_ids = set(entity_ids)
        if incremental:
            valid_edge_ids.update(
                str(item["id"])
                for item in inventory
                if item.get("id")
            )
        edges = []
        for item in raw_edges:
            if isinstance(item, dict):
                raw_source = item.get("source")
                raw_target = item.get("target")
                raw_type = item.get("type")
            elif isinstance(item, list) and len(item) >= 3:
                raw_source, raw_target, raw_type = item[:3]
            else:
                continue
            source = str(raw_source or "")
            target = str(raw_target or "")
            relation_type = normalize_relation_type(raw_type)
            if source not in valid_edge_ids or target not in valid_edge_ids or source == target or not relation_type:
                raise ValueError("entity extraction provider returned an invalid edge")
            edge = {"source": source, "target": target, "type": relation_type}
            if edge not in edges:
                edges.append(edge)
        return entities, edges

    def write_to_neo4j(self, documents: list[dict[str, str]], driver: Any, llm: Any, embedder: Any, current_entities: list[dict[str, Any]] | None = None) -> list[Any]:
        """Run the real Neo4j GraphRAG KG Builder for configured deployments.

        The local ``build`` method remains deterministic for offline development. This
        adapter is deliberately explicit about the provider objects so credentials and
        model selection stay in the system configuration layer.
        """
        if Neo4jSimpleKGPipeline is None:
            raise RuntimeError("neo4j-graphrag is not installed")
        text_documents = [document for document in documents if document.get("property_type") != "image"]
        self.last_documents = [document["property_id"] for document in text_documents]
        self.last_entity_inventory = [{"id": str(item.get("id", "")), "definition": str(item.get("definition", ""))} for item in current_entities or [] if item.get("id")]
        inventory = "\n".join(f"- {item['id']}: {item['definition']}" for item in self.last_entity_inventory) or "(none)"
        prompt = self.prompt.replace("{current_entities}", inventory)
        if "{current_entities}" not in self.prompt:
            prompt = f"{prompt}\n\nCurrent entity inventory:\n{inventory}"

        async def run() -> list[Any]:
            pipeline = Neo4jSimpleKGPipeline(
                llm=llm,
                driver=driver,
                embedder=embedder,
                schema="EXTRACTED",
                prompt_template=prompt,
                from_file=False,
                perform_entity_resolution=True,
                neo4j_database=self.database,
            )
            results = []
            for document in text_documents:
                results.append(await pipeline.run_async(text=document["text"], document_metadata={"project_id": document["project_id"], "property_id": document["property_id"]}))
            return results

        return asyncio.run(run())
