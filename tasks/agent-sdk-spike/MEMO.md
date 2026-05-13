# Claude Agent SDK feasibility — 2026-05-13 spike (#33)

**Question**: is Anthropic's Claude Agent SDK a viable replacement for khimaira's current CLI-subprocess dispatch path?

**Verdict**: **No — not as a drop-in.** Subscription auth is not supported on the SDK directly, which breaks khimaira's "no API keys required to start" pitch. Stay on the CLI substrate. Source: <https://code.claude.com/docs/en/agent-sdk/overview>.

**Findings (all per the doc above)**:

- **Auth**: "Get an API key from the Console, then set it as an environment variable: `export ANTHROPIC_API_KEY=your-api-key`." Explicit prohibition: *"Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK. Please use the API key authentication methods described in this document instead."* → fails the subscription requirement.
- **Streaming**: yes by default — `async for message in query(...)` is a streaming async iterator.
- **Multi-turn**: SDK-managed via `options=ClaudeAgentOptions(resume=session_id)`. Caller doesn't pass full history.
- **Per-call model swap**: not explicit on the overview page (need API ref to confirm).
- **Languages**: Python + TypeScript only.

**Important wrinkle — June 2026**: *"Starting June 15, 2026, Agent SDK and `claude -p` usage on subscription plans will draw from a new monthly Agent SDK credit."* The CLI path khimaira already uses (`claude -p`) maps to the same metered budget — so staying on the CLI doesn't make us *worse* than migrating, and we get subscription-billing semantics either way.

**Implication for NORTH_STAR Phase 4.1**: **mark as "deferred indefinitely"**. The migration's only value would be cleaner per-call control + tighter streaming integration; both are achievable on the CLI substrate at lower switching cost. Revisit only if Anthropic ships subscription auth for the SDK directly (the doc's wording suggests this is a deliberate boundary, not an oversight).
