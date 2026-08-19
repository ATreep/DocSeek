import json
import uuid
from datetime import datetime, timezone

from .config import Settings
from .db import connect
from .security import CAPABILITIES, hash_password
from .services.catalog import PropertyCatalog
from .services.graph_store import (
    DEFAULT_ENTITY_PROMPT,
    DEFAULT_ENTITY_SCHEMA,
    PREVIOUS_ASCII_READABLE_ENTITY_IDENTIFIER_PROMPT,
    PREVIOUS_COMPACT_ENTITY_PROMPT,
    PREVIOUS_CONCISE_DEFINITION_ENTITY_PROMPT,
    PREVIOUS_DEFAULT_ENTITY_PROMPT,
    PREVIOUS_DYNAMIC_RELATION_ENTITY_PROMPT,
    PREVIOUS_ENTITY_IDENTIFIER_PROMPT,
    PREVIOUS_ENTITY_PROMPT,
    PREVIOUS_FIXED_RELATION_ENTITY_PROMPT,
    PREVIOUS_LOWERCASE_READABLE_ENTITY_IDENTIFIER_PROMPT,
    PREVIOUS_READABLE_ENTITY_IDENTIFIER_PROMPT,
    PREVIOUS_SELECTION_ENTITY_PROMPT,
    PREVIOUS_SHORT_ASCII_ENTITY_IDENTIFIER_PROMPT,
)
from .services.parallelism import BATCH_LLM_CONCURRENCY_KEY
from .services.retrieval_limits import RETRIEVAL_LIMIT_DEFAULTS


def seed_defaults(settings: Settings) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with connect(settings.sqlite_path) as db:
        if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            user_id = str(uuid.uuid4())
            group_id = str(uuid.uuid4())
            role_id = str(uuid.uuid4())
            db.execute("INSERT INTO users(id, username, password_hash, created_at) VALUES (?, ?, ?, ?)", (user_id, "admin", hash_password("admin"), now))
            db.execute("INSERT INTO groups(id, name) VALUES (?, ?)", (group_id, "Administrators"))
            db.execute("INSERT INTO roles(id, name, immutable, capabilities_json) VALUES (?, ?, ?, ?)", (role_id, "Superuser", 1, json.dumps(sorted(CAPABILITIES))))
            db.execute("INSERT INTO user_groups(user_id, group_id) VALUES (?, ?)", (user_id, group_id))
            db.execute("INSERT INTO group_roles(group_id, role_id) VALUES (?, ?)", (group_id, role_id))
        role_templates = {
            "Project Manager": ["project.view", "project.create", "project.rename", "project.delete"],
            "Knowledge Editor": ["project.view", "property.view", "property.upload", "property.replace", "property.edit", "property.delete", "property.rename", "property.move", "property.attribute.view", "property.attribute.edit"],
            "Knowledge Analyst": ["project.view", "property.view", "property.attribute.view", "graph.property.view", "graph.entity.view", "search.properties", "search.entities", "query.execute", "agent.status.view"],
            "Reader": ["project.view", "property.view", "property.attribute.view", "graph.property.view", "graph.entity.view"],
        }
        for role_name, capabilities in role_templates.items():
            if not db.execute("SELECT 1 FROM roles WHERE name=?", (role_name,)).fetchone():
                db.execute("INSERT INTO roles(id, name, immutable, capabilities_json) VALUES (?, ?, 1, ?)", (str(uuid.uuid4()), role_name, json.dumps(sorted(capabilities))))
        defaults = {"entity_schema": DEFAULT_ENTITY_SCHEMA, "entity_prompt": DEFAULT_ENTITY_PROMPT}
        legacy_values = {
            "entity_schema": {"DocSeekEntity(name,type)", "DocSeekEntity(name,type,definition,source_property_ids)"},
            "entity_prompt": {
                "Extract entities",
                "Extract entities and relationships from property text.",
                "Extract entities and relationships from the supplied property text.",
                PREVIOUS_ENTITY_PROMPT,
                PREVIOUS_FIXED_RELATION_ENTITY_PROMPT,
                PREVIOUS_DYNAMIC_RELATION_ENTITY_PROMPT,
                PREVIOUS_CONCISE_DEFINITION_ENTITY_PROMPT,
                PREVIOUS_SELECTION_ENTITY_PROMPT,
                PREVIOUS_DEFAULT_ENTITY_PROMPT,
                PREVIOUS_COMPACT_ENTITY_PROMPT,
                PREVIOUS_ENTITY_IDENTIFIER_PROMPT,
                PREVIOUS_READABLE_ENTITY_IDENTIFIER_PROMPT,
                PREVIOUS_ASCII_READABLE_ENTITY_IDENTIFIER_PROMPT,
                PREVIOUS_SHORT_ASCII_ENTITY_IDENTIFIER_PROMPT,
                PREVIOUS_LOWERCASE_READABLE_ENTITY_IDENTIFIER_PROMPT,
                (
                    "Only extract key nouns from the property content, such as human names, product names, "
                    "technology stacks, brand names, and company names. Prioritize nouns mentioned many times. "
                    "Do not extract generic words, file names, sentences, or summaries. Return an entity identifier "
                    "and one brief definition for each noun. Some entities may already exist, so resolve against this "
                    "current entity inventory of identifier and definition: {current_entities}"
                ),
            },
        }
        for key, value in defaults.items():
            existing = db.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
            if not existing or existing["value"] in legacy_values[key]:
                db.execute("INSERT INTO system_config(key,value,updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (key, value, now))
        for key, value in RETRIEVAL_LIMIT_DEFAULTS.items():
            db.execute(
                "INSERT INTO system_config(key,value,updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO NOTHING",
                (key, str(value), now),
            )
        db.execute(
            "INSERT INTO system_config(key,value,updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO NOTHING",
            (BATCH_LLM_CONCURRENCY_KEY, str(settings.batch_llm_concurrency), now),
        )
    catalog = PropertyCatalog(settings)
    for catalog_path in settings.projects_dir.glob("*/jobs/property-catalog.json"):
        catalog.list(catalog_path.parent.parent.name)
