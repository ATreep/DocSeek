# Project Rules

- Do not use `superpowers:brainstorming` by default unless the user explicitly requires it.
- In all user-facing WebUI text, use canonical full agent names. Never display the abbreviations `DG-Agent`, `GA-Agent`, or `PGB-Agent` to users.
- Use `Definition Generation Agent`, `Group Arrangement Agent`, `Property Graph Building Agent`, and `Entity Extraction Agent` as the canonical display names. Use `Shared Embedding Model` for the shared embedding route.
- Abbreviated agent names may remain in internal code identifiers, persisted route keys, stage identifiers, prompts, and developer-facing technical documentation.
- For Chinese WebUI translations, always translate `property` as `资产` and `entity` as `实体`. Do not substitute synonyms for these two canonical nouns.
- Whenever adding or changing user-facing WebUI text, update the i18n resources in the same change and provide complete English and Chinese translations. Do not hard-code untranslated visible UI strings in components.
- Every user-facing floating window must use a consistent title header containing a localized eyebrow label followed by a localized `h2` title.
- LLM system prompts must use an unbranded, role-specific persona that explains the agent's responsibility. Never use a persona such as `You are DocSeek ...`.
