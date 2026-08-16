import json

from .providers import chat_provider


class AnswerLLM:
    calls = 0

    def __init__(self, settings=None, provider=None):
        self.provider = provider or (chat_provider(settings, route_key="ai_query_route") if settings is not None else None)

    @staticmethod
    def _citations(context: dict) -> list[dict]:
        citations = [{"kind": "property", "id": item.get("id"), "label": item.get("filename")} for item in context.get("properties", [])]
        citations += [{"kind": "entity", "id": item.get("id"), "label": item.get("name")} for item in context.get("entities", [])]
        return citations

    @staticmethod
    def _messages(
        question: str,
        context: dict,
        history: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        prompt = json.dumps(
            {
                "question": question,
                "properties": context.get("properties", []),
                "property_relations": context.get("property_relations", []),
                "entities": context.get("entities", []),
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
        return [
            {"role": "system", "content": "You are DocSeek AI Query. Use the complete conversation history to resolve follow-up questions. Answer only from the supplied property and entity graph context and say when it is insufficient."},
            *conversation,
            {"role": "user", "content": prompt},
        ]

    def stream_answer(
        self,
        question: str,
        context: dict,
        history: list[dict[str, str]] | None = None,
    ) -> dict:
        type(self).calls += 1
        property_names = [item.get("filename", "property") for item in context.get("properties", [])]
        entity_names = [item.get("name", "entity") for item in context.get("entities", [])]
        if self.provider:
            messages = self._messages(question, context, history)
            if hasattr(self.provider, "stream"):
                chunks = self.provider.stream(messages, temperature=0.1)
            else:
                chunks = iter([self.provider.complete(messages, temperature=0.1)])
            return {"chunks": chunks, "citations": self._citations(context)}
        sources = ", ".join(property_names + entity_names) or "the active project graph"
        answer = f"Based on {sources}, the available graph context addresses: {question}"
        chunks = (answer[index:index + 24] for index in range(0, len(answer), 24))
        return {"chunks": chunks, "citations": self._citations(context)}

    def answer(
        self,
        question: str,
        context: dict,
        history: list[dict[str, str]] | None = None,
    ) -> dict:
        result = self.stream_answer(question, context, history)
        return {"answer": "".join(result["chunks"]), "citations": result["citations"]}
