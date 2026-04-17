# CLAUDE.md

Behavioral guidelines covering the full lifecycle: from idea to implementation. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 0. Before You Touch Code — Discovery Workflow

**No code without clarity. No architecture without requirements.**

Every non-trivial task has a phase before coding. The bigger the task, the more important this phase is. Use judgment to match the depth to the task size.

### When to Run the Discovery Chain

| Situation | Action |
|-----------|--------|
| New project or major feature | Full chain: BA → Grill → Architect → Grill → Developer |
| New module in existing project | Architect → Developer (requirements exist) |
| Small feature with clear scope | Skip to coding (Sections 1-5) |
| Bug fix or minor change | Skip to coding (Sections 1-5) |
| "I have an idea for..." | Start with BA |
| Scope unclear, multiple interpretations | Start with BA |
| "Stress test this" / "what am I missing" | Grill at current stage |

### The Skill Chain

```
business-analyst    → Structured interview, produces requirements.md
                       (domain-aware questions, depth levels, JSON+MD hybrid output)

grill-me            → Stress-tests any plan, requirements, or architecture
                       (adversarial questions, tracks resolved/open/critical)
                       ⤷ Optional — invoke at any point with "grill this"

system-architect    → Reads requirements, produces architecture.md
                       (modules, interface contracts, tech stack, build order)

quant-developer     → Reads architecture, produces project scaffold
                       (typed stubs, CLAUDE.md, Claude Code prompts, test structure)
```

Each skill produces a document that the next skill consumes. Grill-me can be inserted between any two steps — it reads the latest document and challenges its decisions. All outputs go to the project's `docs/` folder for permanent reference.

### How Requirements Feed Into Code

The discovery chain's outputs directly constrain coding decisions:

- **Feature IDs (F-xxx)** from requirements map to modules — every feature must trace to at least one module.
- **Interface contracts (C-xxx)** become function signatures — the developer skill generates typed stubs that match exactly.
- **Build order phases** determine what gets coded first — follow them, don't cherry-pick.
- **Priority labels** (must-have / should-have / nice-to-have) determine what goes in MVP vs. later — resist scope creep.
- **NFRs** (performance, security, deployment) inform technical choices — don't override without justification.

If a coding task contradicts the architecture document, stop and ask. The architecture may need updating, or the task may be misunderstood. Don't silently deviate.

### Multi-Model Assignment

The architect skill assigns each module a suggested model (opus / sonnet / haiku):
- **opus** — Complex algorithms, mathematical models, core business logic
- **sonnet** — Application code, integrations, UI components, standard patterns
- **haiku** — Config files, boilerplate, simple utilities

Respect these assignments in Claude Code multi-agent workflows. Override only with explicit justification.

---

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.
- If a requirements or architecture document exists in `docs/`, read it first. Your implementation must be traceable to the documented decisions.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Git & Development Standards

### Conventional Commits
Format: `<type>(<scope>): <description>`

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `ci`, `perf`, `build`

Scopes: `oms`, `offer`, `pricing`, `gateway`, `adapter`, `admin`, `common`, `infra`, `ci`

### Branch Strategy
```
main        Production-ready. Protected. Merge via PR only.
develop     Integration branch. Feature branches merge here.
feature/    feature/MON-{ticket}-{short-desc}
hotfix/     hotfix/MON-{ticket}-{short-desc} (from main, merge to main AND develop)
release/    release/{semver} (from develop, merge to main)
```

### AI Assistant Git Workflow (MANDATORY)
- **ASLA direkt `main` branch'ine commit atma.**
- Her yeni istekte, icerigin turune gore branch ac:
  - `feature/MON-{ticket}-{short-desc}` — yeni ozellikler
  - `bugfix/MON-{ticket}-{short-desc}` — hata duzeltmeleri
  - `refactor/MON-{ticket}-{short-desc}` — yeniden yapilandirma
- Islem tamamlaninca `main`'e merge icin PR ac (`gh pr create`).
- **Kullanici onayi olmadan hicbir PR merge edilmez.** PR actiktan sonra dur ve kullanicinin onayini bekle.
- PR onayi alinmadan `gh pr merge` veya `git push origin main` calistirilmaz.

---

**These guidelines are working if:** discovery questions come before code, architecture decisions are documented before implementation, fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
