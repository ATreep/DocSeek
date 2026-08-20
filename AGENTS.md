# Project Rules

- Do not use `superpowers:brainstorming` by default unless the user explicitly requires it.
- After testing, remove all test-created projects, roles, groups, and users from the application data stores.
- In all user-facing WebUI text, use canonical full agent names. Never display the abbreviations `DG-Agent`, `GA-Agent`, or `PGB-Agent` to users.
- Use `Definition Generation Agent`, `Group Arrangement Agent`, `Property Graph Building Agent`, and `Entity Extraction Agent` as the canonical display names. Use `Shared Embedding Model` for the shared embedding route.
- Abbreviated agent names may remain in internal code identifiers, persisted route keys, stage identifiers, prompts, and developer-facing technical documentation.
- For Chinese WebUI translations, always translate `property` as `资产` and `entity` as `实体`. Do not substitute synonyms for these two canonical nouns.
- Whenever adding or changing user-facing WebUI text, update the i18n resources in the same change and provide complete English and Chinese translations. Do not hard-code untranslated visible UI strings in components.
- Whenever changing a user-facing progress value, format, or calculation, apply the same progress semantics and format to every supported language in the same change.
- Every user-facing floating window must use a consistent title header containing a localized eyebrow label followed by a localized `h2` title.
- Use the approved Add Property window as the floating-window design standard: true-white surface, restrained 1 px border, soft elevation, 28 px desktop padding, generous section spacing, and a localized eyebrow-plus-`h2` header with the close action aligned on the right.
- Use `14px` as the single shared corner-radius token for all non-circular WebUI controls and surfaces, including floating windows, panels, buttons, inputs, textareas, selects, cards, and inline containers. Fully circular avatars, status dots, spinners, badges, and semantic pills are exempt.
- LLM system prompts must use an unbranded, role-specific persona that explains the agent's responsibility. Never use a persona such as `You are DocSeek ...`.
