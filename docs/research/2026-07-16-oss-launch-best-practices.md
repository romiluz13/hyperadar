# Research: Open-Source Launch Best Practices for an AI Agent-Curated Content Platform (HypeRadar)

> Date: 2026-07-16 · Scope: README/CONTRIBUTING/LICENSE setup, partnership-showcase projects, AI-agent open-sourcing pitfalls, onboarding friction with multiple external dependencies.
> Note on paths: Per runtime override, findings are written to this file (the authoritative `.pi-subagents` artifacts path). A copy at `docs/research/2026-07-16-oss-launch-best-practices.md` is the intended durable home and can be created by the parent from this content.

## Summary

HypeRadar is public but currently ships **no LICENSE, no CONTRIBUTING, no CODE_OF_CONDUCT, and no issue templates** — the four files GitHub's own OpenSource.guide names as a project's mandatory "front door." The strongest AI-agent OSS projects (CrewAI, OpenHands, LangGraph) pair a permissive license (MIT or Apache 2.0) with structured YAML issue forms, a quickstart that runs in under 5 commands, and a `.env.example` with sane local defaults. For a vendor-partnership showcase, trust comes from honest "why we sponsor this" framing, a clear boundary between open and commercial surfaces, multi-vendor extensibility hooks, and community-led governance signals (public roadmap, good-first-issue labels, external maintainers). The biggest AI-agent-specific risks are secret leakage (prompt-injection key extraction, chain-of-thought logging), "denial of wallet" cost loops, and excessive agency/abuse vectors — all mitigable with secret brokering, cost circuit-breakers, and least-privilege tokens. The lightest clone-to-run path is a Docker Compose / devcontainer that mocks or local-defaults every external dependency (Atlas free tier, local Ollama for the LLM, stub Port/Bright Data), with a `setup_check` script that reports exactly which services are missing.

---

## Findings

### 1. Current HypeRadar OSS-health gaps (primary-source observation)

1. **Missing the "Core Four" community files.** A direct read of the public repo tree (`github.com/romiluz13/hyperadar`) shows `.env.example`, `.github/workflows/`, `AGENTS.md`, `DESIGN.md`, `HANDOFF.md`, `PRODUCT.md`, `README.md`, `agent_catalog.json`, `apps/`, `integrations/`, `scripts/`, `docs/` — but **no `LICENSE`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, or `.github/ISSUE_TEMPLATE/`**. [Source: github.com/romiluz13/hyperadar repo tree](https://github.com/romiluz13/hyperadar)
2. **Without a LICENSE, the project is "public" but not open source.** GitHub's OpenSource.guide is explicit: "without a license, the authors retain all rights by default… others can't legally use, modify, or distribute the code." This blocks adoption and contribution. [Source: opensource.guide/starting-a-project](https://opensource.guide/starting-a-project/)
3. **The README is engineering-heavy but contributor-hostile.** It leads with "product truth" invariants and a multi-step provisioning sequence; there is no hero GIF/screenshot, no 3-command quickstart, no badges, and no "How to contribute" section — all of which the reference projects below treat as mandatory. [Source: HypeRadar README.md](https://github.com/romiluz13/hyperadar/blob/main/README.md)

### 2. What a strong AI-agent OSS README/CONTRIBUTING/LICENSE setup looks like

1. **CrewAI (28k+ stars) — MIT, badges-first, contribution-paths.** README opens with a centered logo, a row of shields.io badges (stars, forks, issues, PRs, **License: MIT**, PyPI version, PyPI downloads, Twitter follow), a one-line tagline, then "Fast and Flexible Multi-Agent Automation Framework." `.github/CONTRIBUTING.md` is unusually strong: it names prerequisites (Python 3.10–3.14, `uv`, pre-commit), gives a 3-line clone+`uv sync`+`pre-commit install` setup, documents the repo's `lib/` workspace structure in a table, specifies a `<type>/<short-description>` branching convention, and — notably — has an explicit "AI-Generated Contributions" section requiring the `llm-generated` label on any AI-authored PR/issue. Issue templates are YAML forms (`bug_report.yml`, `feature_request.yml`) plus a `config.yml`. [Sources: CrewAI README](https://github.com/joaomdmoura/crewAI/blob/main/README.md), [CrewAI CONTRIBUTING](https://github.com/joaomdmoura/crewAI/blob/main/.github/CONTRIBUTING.md), [CrewAI .github/ISSUE_TEMPLATE/](https://github.com/joaomdmoura/crewAI/tree/main/.github/ISSUE_TEMPLATE)]

2. **OpenHands (All-Hands-AI) — Apache 2.0, for-the-badge status, devcontainer.** README uses `for-the-badge` style badges: project status (beta), **CI workflow status**, npm version, Documentation, Slack community. It provides a `<a name="readme-top">` anchor and an inline nav (Quickstart | Docs | Self-Hosting | ACP Agents | Automations | Slack). Quickstart offers "Option 1: Without a Sandbox" (one `npm install -g` + run) with a clear `> [!WARNING]` callout that the agent gets full filesystem access, and "Option 2" via Docker. It ships a `.devcontainer/` for one-command Codespaces setup and a `.github/SECURITY.md`. [Sources: OpenHands README](https://github.com/All-Hands-AI/OpenHands/blob/main/README.md), [OpenHands .devcontainer](https://github.com/All-Hands-AI/OpenHands/tree/main/.devcontainer)]

3. **LangGraph (langchain-ai) — MIT, architecture-diagram-first.** README leads with a Mermaid/visual architecture diagram of the graph state model, then quickstart, then links to Mintlify docs. The emphasis is on visualizing the "Think → Act → Observe" loop up front. [Source: langchain-ai/langgraph](https://github.com/langchain-ai/langgraph)]

4. **License choice: MIT vs Apache 2.0.** Both are permissive on [choosealicense.com](https://choosealicense.com). MIT is the simplest and most adoption-friendly (one paragraph, no lawyer needed) — CrewAI and LangGraph use it. Apache 2.0 adds an **explicit patent grant** and patent-retaliation clause, which is why enterprise-friendly AI frameworks (TensorFlow, Hugging Face Transformers) favor it. For HypeRadar — a showcase, not a framework expecting corporate patent contributions — **MIT is the lower-friction choice** and matches the closest comparable projects. Apache 2.0 is the fallback if a partner (MongoDB/Port) requires it. [Sources: choosealicense.com](https://choosealicense.com), [opensource.guide](https://opensource.guide/starting-a-project/)]

5. **Issue templates: prefer YAML Issue Forms over markdown.** GitHub's official form schema (`type: textarea`, `type: dropdown`, `type: checkboxes`, `validations: {required: true}`) enforces structured input and auto-labels. Add a `config.yml` with `blank_issues_enabled: false` and a contact link (e.g., the live deployment or a discussions board). For an AI-agent project, include a dropdown for "which source agent" (GitHub/Reddit/YouTube/HN/weekly-editor) and a required field for the MongoDB/Port environment status. [Source: GitHub docs — syntax for issue forms](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-githubs-form-schema)]

6. **CI badges to include.** Standard set for a project like HypeRadar: CI workflow status (`.github/workflows/`), License (MIT/Apache), Vercel deployment status, and — because it's a partnership showcase — a "Powered by MongoDB Atlas" / "Built on Port.io" badge that credits sponsors without turning the README into an ad. [Sources: shields.io patterns from CrewAI/OpenHands READMEs](https://github.com/joaomdmoura/crewAI/blob/main/README.md)]

7. **Pre-launch checklist (from OpenSource.guide).** LICENSE ✓, README with Getting Started ✓, consistent conventions, clean/labeled issue queue, CONTRIBUTING, CODE_OF_CONDUCT. HypeRadar currently meets only README (partially) and conventions. [Source: opensource.guide/starting-a-project](https://opensource.guide/starting-a-project/)]

### 3. Partnership-showcase projects: avoiding the "pure marketing" perception

1. **Be transparent about the sponsorship "why."** The TODO Group open-source marketing guide and FINOS OSR both recommend an honest "Why we sponsor this" section in the README: e.g., "MongoDB sponsors HypeRadar to demonstrate Atlas Vector Search + time-series patterns for agentic content pipelines; Port.io sponsors it to showcase governed agent-run workflows." Authenticity builds trust; hiding the relationship triggers "open-washing" accusations. [Sources: TODO Group marketing guide](https://github.com/todogroup/todogroup.org/blob/main/content/en/guides/marketing-open-source-projects.md), [FINOS OSR](https://osr.finos.org/docs/bok/activities/level-2/creating-an-ospo)]

2. **Make the vendor replaceable, not load-bearing.** A showcase looks like marketing if it only works on the sponsor's paid product. Best practice: support a local/free path for each dependency (Atlas free tier M0; local Ollama instead of Grove; a stub Port adapter for local dev; mock Bright Data). The open-source boundary should be "runs end-to-end on free tiers; production hardening needs paid tiers." State this boundary explicitly so changing it later doesn't trigger backlash. [Sources: TODO Group guide](https://github.com/todogroup/todogroup.org/blob/main/content/en/guides/marketing-open-source-projects.md), general OSS governance literature]

3. **Community-led signals over sales-led ones.** Maintain a public roadmap (GitHub Projects board), tag `good first issue`, respond to issues/PRs quickly with dedicated maintainer time (KPI = response time, not feature delivery), and invite external contributors toward maintainer roles. Keep the GitHub repo — not a SaaS landing page — as the project home. Avoid "Contact Sales" CTAs in the OSS README. [Source: TODO Group guide](https://github.com/todogroup/todogroup.org/blob/main/content/en/guides/marketing-open-source-projects.md)]

4. **Concrete anti-patterns to avoid.** Single-vendor control of trademark + roadmap + all write access; BSL/SSPL/"source-available" licenses; slick SaaS-style README with marketing copy; "dump and run" (launch PR splash, then no maintenance); tool that only works on the sponsor's cloud. [Source: TODO Group guide](https://github.com/todogroup/todogroup.org/blob/main/content/en/guides/marketing-open-source-projects.md)]

### 4. Pitfalls when open-sourcing an AI agent system

1. **Secret leakage via prompt injection and chain-of-thought logging.** Agents that log reasoning ("I will now connect using `postgres://admin:pw…`") leak secrets in plaintext. Indirect prompt injection (a malicious GitHub issue the agent reads) can trick it into printing `OPENAI_API_KEY`. Mitigations: redact all logs with regex-based secret scanning before storage/display; never pass raw API keys to the agent context — use a secret broker that issues short-lived scoped tokens; treat any user-supplied content the agent ingests (Reddit posts, GitHub READMEs) as untrusted input. [Sources: rippling.com agentic AI security](https://www.rippling.com/blog/agentic-ai-security), [Microsoft "Securing AI Agents with Agent Governance"](https://medium.com/data-science-at-microsoft/securing-ai-agents-with-agent-governance-767aacd2a927)]

2. **"Denial of wallet" (cost exposure).** Because agents loop, a logic error becomes a financial liability: infinite retry loops, stuck reasoning generating thousands of tokens, provider spend caps that protect the account but not the project. Mitigations: hard circuit-breaker on max LLM calls/tokens per task; iteration limit (stop + ask human after N consecutive failed tool calls); set hard monthly spend limits on the provider dashboard; scoped keys limited to cheaper models. For HypeRadar specifically, the Reddit agent is already gated behind `BRIGHTDATA_API_KEY` — document the expected per-run cost envelope in the README so contributors aren't surprised. [Sources: nvidia developer blog on agentic key risks](https://developer.nvidia.com/blog/how-code-execution-drives-key-risks-in-agentic-ai-systems/), [fast.io secret management for AI agents](https://fast.io/resources/best-secret-management-tools-ai-agents/)]

3. **"It doesn't work without my paid API keys" — the onboarding killer.** This is the #1 friction point for AI-agent OSS. If a contributor must sign up for Grove + Atlas + Port + Bright Data + a GitHub token before anything runs, most will bounce. Mitigations below in Findings 19–22. [Source: langchain.com "agents need their own computer"](https://www.langchain.com/blog/agents-need-their-own-computer), [tacnode.io LLM agents guide](https://tacnode.io/post/llm-agents-complete-guide)]

4. **Abuse vectors & excessive agency.** Risks: malicious PR hijacking (a contributor submits a README that injects instructions into the reviewing agent), irreversible actions without human-in-the-loop (HITL), model/skill poisoning from third-party tools. Mitigations: least-privilege tokens (read-only where possible, scoped GitHub Apps not PATs), sandbox all tool execution (Docker/Bubblewrap/E2B), require HITL approval for any write/external action, and — for HypeRadar — ensure the Port-dispatched workflow can't be triggered by arbitrary issue text. [Sources: itpro.com AI coding tool flaws](https://www.itpro.com/security/flaws-in-some-of-the-most-popular-ai-coding-tools-left-developers-wide-open-to-attack), [Microsoft Agent Governance](https://medium.com/data-science-at-microsoft/securing-ai-agents-with-agent-governance-767aacd2a927)]

### 5. Onboarding friction: lightest clone-to-run path with MongoDB + Port + Grove + Bright Data

1. **The "Dependency Matrix" problem.** AI-agent OSS requires model orchestration (Grove/OpenAI/Ollama), state store (MongoDB Atlas), governance (Port), and data-source tools (Bright Data/GitHub) to all align. This is the documented cold-start killer. [Source: tacnode.io LLM agents complete guide](https://tacnode.io/post/llm-agents-complete-guide)]

2. **Tiered onboarding is the best-practice pattern.** Offer three explicit paths in the README:
    - **Tier 0 — Read-only demo (zero keys):** `npm run dev` against a seeded read-only MongoDB snapshot or a JSON fixture dump, so a visitor can see the feed/UI in <2 minutes with no accounts. This is the "hero GIF" equivalent for a data-heavy app.
    - **Tier 1 — Local web app (1 key: Atlas free tier):** `cp .env.example .env`, set `MONGODB_URI` only, run the Next.js app against Atlas M0 free tier. This already exists in HypeRadar's README ("Local web app" section) — promote it to the top.
    - **Tier 2 — Full agent run (all keys):** the existing provisioning sequence, clearly marked as "for contributors who want to run the source agents."
    [Source: Atlas free-tier setup pattern](https://www.mongodb.com/cloud/atlas), [HypeRadar README "Local web app" section](https://github.com/romiluz13/hyperadar/blob/main/README.md)]

3. **Docker Compose / devcontainer for one-command env.** Top projects (OpenHands, Dify) ship `.devcontainer/` and `docker-compose.yaml` that pre-install Python, Node, and sidecar services so `docker-compose up` or "Open in Codespaces" yields a working env. For HypeRadar, a compose file could bundle a local MongoDB (community edition) + the web app, replacing Atlas for local dev. [Source: OpenHands .devcontainer](https://github.com/All-Hands-AI/OpenHands/tree/main/.devcontainer)]

4. **`.env.example` with local defaults + a `setup_check` script.** Best practice: provide sensible local defaults in `.env.example` (e.g., `GROVE_BASE_URL=http://localhost:11434` pointing to a local Ollama, `MONGODB_URI` pointing to local MongoDB) so the app boots without paid services. Add a `scripts/setup_check.py` that pings each external dependency and prints a clear report of which are missing/offline — HypeRadar already has provisioning scripts; a read-only health-check variant would close the gap. HypeRadar's existing `.env.example` is a good starting point but currently has no local defaults (all values are `your_*_here`). [Sources: tacnode.io](https://tacnode.io/post/llm-agents-complete-guide), [HypeRadar .env.example](https://github.com/romiluz13/hyperadar/blob/main/.env.example)]

---

## Recommended action checklist for HypeRadar (prioritized)

| Priority | Action | Why |
| --- | --- | --- |
| P0 | Add `LICENSE` (MIT recommended) | Without it, the project isn't legally open source (Findings 2, 7) |
| P0 | Add `CONTRIBUTING.md` (model on CrewAI: prereqs, 3-line setup, branching, AI-generated label policy) | Missing entirely; blocks contributors (Findings 1, 4) |
| P0 | Add `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1) | Core Four file; expected by GitHub & community (Finding 10) |
| P0 | Add `.github/ISSUE_TEMPLATE/` YAML forms (bug, feature, agent-run report) + `config.yml` | Structured intake; auto-labels (Finding 8) |
| P1 | Rewrite README top: hero screenshot/GIF of the live feed, badges (CI, license, Vercel, "Powered by MongoDB Atlas / Port.io"), 3-command quickstart, "Why we sponsor this" section | Current README is engineering-doc-first; front door is contributor-hostile (Findings 3, 9, 11) |
| P1 | Add tiered onboarding (Tier 0 read-only demo / Tier 1 Atlas-free / Tier 2 full) | Reduces the "needs 5 paid keys" drop-off (Findings 17, 20) |
| P1 | Add a `scripts/setup_check.py` health-check + local defaults in `.env.example` | Tells contributors exactly what's missing without trial-and-error (Findings 19, 22) |
| P2 | Add `.devcontainer/` or `docker-compose.yaml` with local MongoDB | One-command env; matches OpenHands/Dify (Finding 21) |
| P2 | Document cost envelope per agent run + set spend caps guidance | Prevents "denial of wallet" surprises (Finding 16) |
| P2 | Add `.github/SECURITY.md` (secret redaction policy, prompt-injection threat model, least-privilege token guidance) | AI-agent-specific security surface (Findings 15, 18) |
| P2 | Add public roadmap (GitHub Projects) + `good first issue` labels | Community-trust signal for a vendor showcase (Findings 13, 14) |

---

## Sources

### Kept (primary or authoritative)

- **HypeRadar repo** (<https://github.com/romiluz13/hyperadar>) — the project under analysis; direct read of tree, README, .env.example.
- **CrewAI repo** (<https://github.com/joaomdmoura/crewAI>) — top AI-agent OSS; MIT, badges, strong CONTRIBUTING with AI-generated-content policy, YAML issue forms.
- **OpenHands repo** (<https://github.com/All-Hands-AI/OpenHands>) — Apache 2.0 AI-agent OSS; for-the-badge README, devcontainer, SECURITY.md, sandbox warnings.
- **LangGraph repo** (<https://github.com/langchain-ai/langgraph>) — MIT; architecture-diagram-first README pattern.
- **GitHub OpenSource.guide** (<https://opensource.guide/starting-a-project/>) — authoritative "Core Four" files + pre-launch checklist.
- **choosealicense.com** (<https://choosealicense.com>) — canonical MIT vs Apache 2.0 comparison.
- **GitHub docs — issue form schema** (<https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-githubs-form-schema>) — official YAML form syntax.
- **TODO Group marketing guide** (<https://github.com/todogroup/todogroup.org/blob/main/content/en/guides/marketing-open-source-projects.md>) — vendor-sponsored OSS anti-marketing best practices.
- **FINOS OSR** (<https://osr.finos.org/docs/bok/activities/level-2/creating-an-ospo>) — OSPO/governance perspective on vendor OSS.
- **Microsoft "Securing AI Agents with Agent Governance"** (<https://medium.com/data-science-at-microsoft/securing-ai-agents-with-agent-governance-767aacd2a927>) — agent security: identity, policy, isolation.
- **NVIDIA developer blog — agentic key risks** (<https://developer.nvidia.com/blog/how-code-execution-drives-key-risks-in-agentic-ai-systems/>) — code-execution-driven risks in agent systems.
- **itpro.com — AI coding tool flaws** (<https://www.itpro.com/security/flaws-in-some-of-the-most-popular-ai-coding-tools-left-developers-wide-open-to-attack>) — concrete prompt-injection / sandbox-break incidents.
- **tacnode.io — LLM agents complete guide** (<https://tacnode.io/post/llm-agents-complete-guide>) — dependency-matrix friction + one-command onboarding patterns.
- **langchain.com — "agents need their own computer"** (<https://www.langchain.com/blog/agents-need-their-own-computer>) — sandboxed agent execution rationale.

### Dropped (secondary/SEO/low-signal)

- Various Medium/archbee/dokly "how to write a README" posts — generic, not AI-specific, redundant with OpenSource.guide.
- Biological "HyperADAR" plasmid results — irrelevant.
- Shields.io badge examples — covered inline via CrewAI/OpenHands READMEs.
- Reddit threads on TensorFlow licensing — superseded by choosealicense.com.

## Gaps

- **HypeRadar's actual current LICENSE status could not be 100% confirmed via web search** (search returned timeouts/hallucinations); the conclusion "no LICENSE" is inferred from the fetched repo tree not listing one. The parent should run `ls` / `git ls-files | grep -i license` on the local repo to confirm before acting.
- **Port.io's own OSS/partnership guidelines** could not be found via search; contacting Port directly for any co-branding or license requirements would close this gap.
- **MongoDB's partner-showcase program requirements** (if any co-branded badge or license terms exist) were not surfaced; worth a direct check with the MongoDB partner contact.
- **Concrete per-run cost figures for HypeRadar agents** (Grove tokens, Bright Data credits) are not in the public repo; the parent can measure and add to the README.

## Supervisor coordination

No decision needed; this is a read-only research task. Returning the completed brief.
