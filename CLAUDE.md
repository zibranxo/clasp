# CLASP — Web UI Reality Pass + Feature Roadmap

> Two phases in this file. Phase 0 is the reality pass (make what already exists
> real). Phase 1+ is new feature work from PLANNN.md. Do not start Phase 1 until
> Phase 0 is verified complete — building new features on a UI that's still
> partly faking data just gives you more surface area to fake on.

## Note on source documents

- `plan.md` — the actual technical spec: architecture, endpoint contracts,
  provider/routing logic. This is the source of truth for how the backend works
  and what's real.
- `PLANNN.md` — a feature roadmap (KeyKing-inspired additions: tour, update
  checker, command palette, vault encryption, priority rules, team collab,
  analytics, model catalog, anomaly detection, dark mode). Treat this as a
  prioritized feature list and objectives only. Do not treat its "Current State
  Assessment" section as fact — it claims the UI is production-ready and fully
  functional, which is not confirmed and may just be wrong. Go by the actual
  audit (`ui_audit.md` / `ui_verification.md` from the reality pass), not by
  what a planning doc asserts about itself.
- Ignore PLANNN.md's success metrics (support ticket %, satisfaction scores,
  adoption rates), team structure (FTEs), and budget sections. This is a
  solo-built tool for personal/portfolio use, not a staffed product team. Those
  numbers aren't measurable here and treating them as targets would mean
  reporting against made-up baselines — the same fake-data problem this whole
  project is trying to eliminate. Acceptance criteria for every feature below is
  binary: it does the real thing it claims to do, or it doesn't.

## Role for this session

Same as before: implementing against an existing/growing codebase, not
architecting from scratch. Cross-check every new feature against plan.md's
actual system design before building it — if a feature needs data (model lists,
latency history, request counts), it should come from real state the backend
already tracks or will track for real, not a new fake layer bolted on to look
like it matches PLANNN.md's description faster.

## Non-negotiable rule: no fake values, anywhere (still applies)

Same rule as the reality pass, extended to every new feature:

- Tour system: fine to be static content, but "first visit" detection and
  completion state must be real (real localStorage/backend flag), not always-on
  or always-off regardless of state.
- Update checker: must hit a real version source (e.g. actual GitHub
  releases/tags for this repo) and do a real semver comparison — not a hardcoded
  "you're up to date" or a fake "update available" banner.
- Command palette: must call the real handlers for real actions (reuses
  whatever Task 2 of the reality pass already wired up) — not a new decorative
  layer that duplicates fake versions of already-real functions.
- Vault encryption: must be real encryption (e.g. actual AES-GCM with a
  passphrase-derived key, not base64-and-call-it-encrypted). If you're not
  confident an approach is cryptographically sound, say so and flag it rather
  than shipping something that only looks encrypted.
- Priority rules engine: routing decisions it makes must actually change which
  provider/key gets used at request time — not a UI that saves rules nobody
  reads.
- Usage analytics: numbers must come from real request/token counters already
  in the system (or built for real if missing) — not randomly generated
  "sample" data to make the dashboard look populated.
- AI model catalog: model list and metadata must come from real provider
  API responses (or a real static list you've actually verified against current
  provider docs) — not invented model names/context windows/pricing.
- Anomaly detection: must run against real historical metrics and use a stated,
  actual threshold/method (e.g. rolling z-score on real latency data) — not a
  hardcoded "everything is fine" or randomly firing alert.
- Dark/light mode: this one's legitimately just CSS variables + a toggle, no
  fake-data risk here. Lowest priority, do it last, don't overthink it.

## Alternatives rule (still applies)

Same as before. Specifically flag these decisions rather than picking silently:

- Whether team/multi-user collaboration is even worth building — this is a
  single-operator local proxy tool as far as I've described it to you. Ask
  before building shared-vault/role/permission infrastructure for a tool with
  one user, rather than assuming PLANNN.md's feature list applies wholesale.
- Which encryption library/approach for the vault (tradeoffs: browser
  WebCrypto vs a backend-side encrypted-at-rest file vs OS keychain integration)
- What anomaly detection method to use (simple threshold vs rolling stats vs
  something heavier) — give options with actual false-positive/complexity
  tradeoffs, don't just pick one.
- Whether the model catalog needs to be curated/static or live-fetched per
  provider — tradeoffs are staleness vs API calls on every page load.

## Phase 0 — Reality pass (prerequisite, do this first if not already done)

Unchanged from the prior session:
1. Audit `ui/static/app.js` function by function → `ui_audit.md`. Stop for
   sign-off.
2. Fix function by function against plan.md Section 5 / Section 17, no fake
   data left anywhere, nothing dropped.
3. Verify every fix live → `ui_verification.md`.

Do not proceed to Phase 1 until `ui_verification.md` shows everything in scope
passing or explicitly and honestly marked as unverifiable (e.g. no live key
available), not silently marked passed.

## Phase 1 — Quick wins (only after Phase 0 verified)

Priority order for a solo daily-use tool (re-ordered from PLANNN.md's Phase 1,
same three features):

1. **Command palette** — highest actual value for you personally since you're
   the power user here. Wire to real existing actions only.
2. **Tour system** — lower priority for a single-user tool you built yourself,
   but cheap and harmless. Keep it simple; real completion-state tracking only.
3. **Update checker** — only meaningful once this is something you're
   distributing/reinstalling across machines. Confirm this is actually useful
   to you before building it — flag as open question if unclear.

## Phase 2 — Security (only after Phase 1)

1. **Vault encryption** — real encryption per the alternatives rule above.
   This is the one place in PLANNN.md's roadmap where getting it wrong has real
   consequences (leaked API keys), so err toward the simpler, well-reviewed
   approach over a custom scheme.
2. **Priority rules engine** — must actually affect routing decisions in
   `clasp/`'s real routing logic per plan.md, not just a UI-only preference
   store.
3. **Team collaboration** — flagged above as open question. Do not build unless
   confirmed needed.

## Phase 3 — Analytics & polish (only after Phase 2)

1. **Usage analytics dashboard** — real counters only.
2. **AI model catalog** — real provider data only.
3. **Anomaly detection** — real thresholds on real data, stated method.
4. **Dark/light mode** — CSS variables, last, lowest risk.

## Rules for this session (still applies, extended)

- No fake data, ever, in any phase — the primary failure mode this whole
  project is being run to eliminate.
- Don't treat PLANNN.md's self-reported "current state" or success metrics as
  real — they're not verified and mostly don't apply to a solo project.
- Flag team-collaboration and update-checker as "confirm this is actually
  needed" before building, don't build by default just because it's in the doc.
- Give options instead of a single best guess wherever the approach isn't fully
  determined (encryption method, anomaly detection method, catalog freshness).
- Don't drop working functionality while adding new functionality.
- Each phase gets its own audit-or-verification step before moving to the next
  phase — don't chain straight through all ten features without a checkpoint.