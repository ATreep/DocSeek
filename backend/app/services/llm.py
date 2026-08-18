import json
from typing import Any

from .display_language import language_instruction, localized_messages
from .providers import ProviderError, chat_provider
from .retry import DEFAULT_MODEL_ATTEMPTS, retry_model_call
from .system_prompts import AI_QUERY_SYSTEM_PROMPT


MAX_PROPERTY_CONTENT_READS = 3
MAX_AI_QUERY_TOOL_ROUNDS = 8
READ_PROPERTY_CONTENT_TOOL = {
    "type": "function",
    "function": {
        "name": "read_property_content",
        "description": "Read one retrieved property's full text only when its metadata and relations are insufficient and its content is crucial.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {
                    "type": "string",
                    "description": "Retrieved property ID.",
                }
            },
            "required": ["property_id"],
            "additionalProperties": False,
        },
    },
}
QUERY_ENTITIES_TOOL = {
    "type": "function",
    "function": {
        "name": "query_entities",
        "description": "Search entities; returns score, name, and ID. Inspect useful results with get_entity_detail.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Entity query."},
                "max_result": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 5,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}
QUERY_PROPERTIES_TOOL = {
    "type": "function",
    "function": {
        "name": "query_properties",
        "description": "Search properties; returns score, filename, and ID. Inspect useful results with get_property_detail.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Property query."},
                "max_result": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 5,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}
GET_ENTITY_DETAIL_TOOL = {
    "type": "function",
    "function": {
        "name": "get_entity_detail",
        "description": "Return an entity's definition, relations, and source properties; adds a citation.",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "ID from query_entities.",
                }
            },
            "required": ["entity_id"],
            "additionalProperties": False,
        },
    },
}
GET_PROPERTY_DETAIL_TOOL = {
    "type": "function",
    "function": {
        "name": "get_property_detail",
        "description": "Return a property's definition, relations, and owned entities; adds a citation.",
        "parameters": {
            "type": "object",
            "properties": {
                "property_id": {
                    "type": "string",
                    "description": "ID from query_properties.",
                }
            },
            "required": ["property_id"],
            "additionalProperties": False,
        },
    },
}
GET_PROPERTY_GROUP_TREE_TOOL = {
    "type": "function",
    "function": {
        "name": "get_property_group_tree",
        "description": "Return nested group names and filenames for hierarchy or location questions.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}
PROJECT_GRAPH_TOOLS = [
    QUERY_ENTITIES_TOOL,
    QUERY_PROPERTIES_TOOL,
    GET_ENTITY_DETAIL_TOOL,
    GET_PROPERTY_DETAIL_TOOL,
    GET_PROPERTY_GROUP_TREE_TOOL,
]


def _retry_stream_before_output(factory):
    last_error: Exception | None = None
    for attempt in range(DEFAULT_MODEL_ATTEMPTS):
        emitted = False
        try:
            for event in factory():
                emitted = True
                yield event
            return
        except Exception as exc:
            last_error = exc
            if emitted or attempt == DEFAULT_MODEL_ATTEMPTS - 1:
                raise
    if last_error is not None:
        raise last_error


class AnswerLLM:
    calls = 0

    def __init__(self, settings=None, provider=None, toolbox=None):
        self.provider = provider or (chat_provider(settings, route_key="ai_query_route") if settings is not None else None)
        self.toolbox = toolbox

    @staticmethod
    def _citations(context: dict) -> list[dict]:
        citations = []
        for kind, items, label_field in (
            ("property", context.get("properties", []), "filename"),
            ("entity", context.get("entities", []), "name"),
        ):
            for item in items:
                citation = {
                    "kind": kind,
                    "id": item.get("id"),
                    "label": item.get(label_field),
                }
                reason = item.get("retrieval_reason")
                path = item.get("retrieval_path")
                if isinstance(reason, str) and reason:
                    citation["reason"] = reason
                if (
                    isinstance(path, list)
                    and path
                    and all(isinstance(part, str) and part for part in path)
                ):
                    citation["path"] = path
                citations.append(citation)
        return citations

    @staticmethod
    def _property_metadata(context: dict) -> list[dict]:
        return [
            {
                "id": item.get("id"),
                "filename": item.get("filename"),
                "definition": item.get("definition"),
            }
            for item in context.get("properties", [])
        ]

    @staticmethod
    def _entity_context(context: dict) -> list[dict]:
        return [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "definition": item.get("definition"),
                "contexts": item.get("source_contexts", item.get("contexts", [])),
            }
            for item in context.get("entities", [])
        ]

    @staticmethod
    def _property_relations(context: dict) -> list[dict]:
        fields = (
            "source",
            "target",
            "type",
            "source_filename",
            "target_filename",
        )
        return [
            {field: relation.get(field) for field in fields}
            for relation in context.get("property_relations", [])
        ]

    @staticmethod
    def _relations(context: dict) -> list[dict]:
        fields = (
            "source",
            "source_label",
            "source_kind",
            "type",
            "target",
            "target_label",
            "target_kind",
        )
        return [
            {field: relation.get(field) for field in fields}
            for relation in context.get("relations", [])
            if isinstance(relation, dict)
        ]

    @staticmethod
    def _retrieval_paths(context: dict) -> list[dict]:
        fields = (
            "seed",
            "seed_id",
            "seed_kind",
            "target",
            "target_id",
            "target_kind",
            "path",
            "score",
        )
        return [
            {field: item.get(field) for field in fields if field in item}
            for item in context.get("retrieval_paths", [])
            if isinstance(item, dict)
        ]

    @staticmethod
    def _messages(
        question: str,
        context: dict,
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, Any]]:
        prompt = json.dumps(
            {
                "question": question,
                "properties": AnswerLLM._property_metadata(context),
                "property_relations": AnswerLLM._property_relations(context),
                "entities": AnswerLLM._entity_context(context),
                "relations": AnswerLLM._relations(context),
                "retrieval_paths": AnswerLLM._retrieval_paths(context),
                "output_instruction": language_instruction(),
            },
            ensure_ascii=True,
        )
        conversation = [
            {"role": message["role"], "content": message["content"]}
            for message in history or []
            if message.get("role") in {"user", "assistant"}
            and isinstance(message.get("content"), str)
            and message["content"]
        ]
        return localized_messages([
            {
                "role": "system",
                "content": AI_QUERY_SYSTEM_PROMPT,
            },
            *conversation,
            {"role": "user", "content": prompt},
        ], include=False)

    @staticmethod
    def _read_property_content(
        context: dict,
        property_id: str,
        read_property_ids: set[str],
    ) -> dict:
        if property_id in read_property_ids:
            return {
                "error": "Property content has already been read for this question.",
                "property_id": property_id,
            }
        item = next(
            (
                property_item
                for property_item in context.get("properties", [])
                if str(property_item.get("id") or "") == property_id
            ),
            None,
        )
        if item is None:
            return {
                "error": "Property is not available in the retrieved AI Query context.",
                "property_id": property_id,
            }
        read_property_ids.add(property_id)
        return {
            "property_id": property_id,
            "filename": item.get("filename"),
            "definition": item.get("definition"),
            "content": item.get("content") or "",
        }

    @classmethod
    def _execute_tool_call(
        cls,
        context: dict,
        tool_call: dict,
        read_property_ids: set[str],
        toolbox=None,
        citations: list[dict] | None = None,
    ) -> dict:
        function = tool_call.get("function")
        if not isinstance(function, dict):
            return {"error": "Unsupported AI Query tool."}
        name = str(function.get("name") or "")
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except (TypeError, json.JSONDecodeError):
            return {"error": f"Invalid {name or 'tool'} arguments."}
        if not isinstance(arguments, dict):
            return {"error": f"Invalid {name or 'tool'} arguments."}
        if name != "read_property_content":
            if toolbox is None:
                return {"error": "Project graph tools are unavailable."}
            result = toolbox.execute(name, arguments)
            if (
                isinstance(result, dict)
                and "error" not in result
                and name in {"get_entity_detail", "get_property_detail"}
                and citations is not None
            ):
                kind = "entity" if name == "get_entity_detail" else "property"
                identifier = str(result.get("identifier") or "")
                label = str(result.get("name") or identifier)
                if identifier and not any(
                    citation.get("kind") == kind
                    and str(citation.get("id") or "") == identifier
                    for citation in citations
                ):
                    citations.append(
                        {
                            "kind": kind,
                            "id": identifier,
                            "label": label,
                            "reason": "Inspected by AI Query",
                        }
                    )
            return result
        property_id = str(arguments.get("property_id") or "").strip()
        if not property_id:
            return {"error": "Invalid read_property_content arguments."}
        if len(read_property_ids) >= MAX_PROPERTY_CONTENT_READS:
            return {
                "error": "The property content read limit was reached for this question.",
                "property_id": property_id,
            }
        return cls._read_property_content(context, property_id, read_property_ids)

    def _tool_aware_chunks(
        self,
        messages: list[dict[str, Any]],
        context: dict,
        citations: list[dict],
    ):
        conversation = list(messages)
        read_property_ids: set[str] = set()
        tool_rounds = 0
        available_tools = [READ_PROPERTY_CONTENT_TOOL]
        if self.toolbox is not None:
            available_tools.extend(PROJECT_GRAPH_TOOLS)
        while True:
            tool_calls = []
            for event in _retry_stream_before_output(
                lambda: self.provider.stream_with_tools(
                    conversation,
                    tools=available_tools,
                    temperature=0.1,
                )
            ):
                if event.get("type") == "content":
                    content = event.get("content")
                    if isinstance(content, str) and content:
                        yield content
                elif event.get("type") == "tool_calls" and isinstance(
                    event.get("tool_calls"), list
                ):
                    tool_calls.extend(event["tool_calls"])
                else:
                    raise ProviderError("provider returned an invalid streaming response")
            if not tool_calls:
                return
            tool_rounds += 1
            if tool_rounds > MAX_AI_QUERY_TOOL_ROUNDS:
                raise ProviderError("AI Query exceeded the tool-call round limit")
            conversation.append(
                {"role": "assistant", "content": None, "tool_calls": tool_calls}
            )
            for index, tool_call in enumerate(tool_calls):
                function = tool_call.get("function")
                tool_name = (
                    str(function.get("name") or "")
                    if isinstance(function, dict)
                    else ""
                )
                call_id = str(
                    tool_call.get("id") or f"ai-query-tool-{tool_rounds}-{index}"
                )
                result = self._execute_tool_call(
                    context,
                    tool_call,
                    read_property_ids,
                    self.toolbox,
                    citations,
                )
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": tool_name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

    def stream_answer(
        self,
        question: str,
        context: dict,
        history: list[dict[str, str]] | None = None,
    ) -> dict:
        type(self).calls += 1
        property_names = [item.get("filename", "property") for item in context.get("properties", [])]
        entity_names = [item.get("name", "entity") for item in context.get("entities", [])]
        citations = self._citations(context)
        if self.provider:
            messages = self._messages(question, context, history)
            if hasattr(self.provider, "stream_with_tools"):
                chunks = self._tool_aware_chunks(messages, context, citations)
            elif hasattr(self.provider, "stream"):
                chunks = _retry_stream_before_output(
                    lambda: self.provider.stream(messages, temperature=0.1)
                )
            else:
                chunks = iter(
                    [
                        retry_model_call(
                            lambda: self.provider.complete(
                                messages, temperature=0.1
                            )
                        )
                    ]
                )
            return {"chunks": chunks, "citations": citations}
        sources = ", ".join(property_names + entity_names) or "the active project graph"
        answer = f"Based on {sources}, the available graph context addresses: {question}"
        chunks = (answer[index:index + 24] for index in range(0, len(answer), 24))
        return {"chunks": chunks, "citations": citations}

    def answer(
        self,
        question: str,
        context: dict,
        history: list[dict[str, str]] | None = None,
    ) -> dict:
        result = self.stream_answer(question, context, history)
        return {"answer": "".join(result["chunks"]), "citations": result["citations"]}
