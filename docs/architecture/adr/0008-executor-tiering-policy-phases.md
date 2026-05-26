# ADR 0008 — Executor Tiering Policy Phases

_Status: Accepted · 2026-05-26_

## Context

OperationsCenter now has two distinct policy layers for executor execution:

- worker-backend routing: `claude_code` first, `codex_cli` fallback based on cooldown/runnability
- tier selection: `budget`, `standard`, `premium`

The worker-backend round robin is operational and observable. Tier selection is
less mature. Dynamic promotion exists mechanically, but the pipeline is still
being debugged and the task-classification inputs are not strong enough yet to
trust automatic escalation.

The immediate need is a stable, cheap, low-variance baseline while preserving a
clear path to a richer selection policy later.

## Decision

Adopt a phased tiering policy:

- Phase 0: pin all executor families to `budget`
- keep worker-backend round robin enabled
- disable dynamic tier promotion by default during pipeline debugging
- rename the middle tier from `default` to `standard`

This ADR governs `team_executor`, `dag_executor`, and `critique_executor`.

## Tier Vocabulary

- `budget`
  - lowest-cost tier
  - preferred for routine debugging, narrow mechanical work, low-risk tasks
- `standard`
  - middle tier
  - intended default for normal implementation, verification, and moderate
    reasoning work once the pipeline is stable
- `premium`
  - highest-capability tier
  - reserved for high-risk, ambiguous, or repeatedly failing work

Naming rule:
- `standard` is canonical
- `default` is retired and invalid for tier selection

## High-Level Policy Model

Long-term tier choice should be derived in this order:

1. task class
2. risk / blast radius
3. minimum viable tier
4. recent failure history
5. budget pressure
6. backend availability
7. hard safety floors

### 1. Task Class

Example classes:
- bugfix
- routine implementation
- refactor
- architecture
- recovery
- investigation
- test repair
- lint / mechanical cleanup

Task class is the first signal because it best predicts reasoning depth and
validation burden.

### 2. Risk / Blast Radius

Risk factors include:
- prod-facing behavior
- security-sensitive areas
- state machines
- schema or data migration
- cross-repo orchestration
- destructive or recovery work

Risk should constrain how far the system may downgrade a task under pressure.

### 3. Minimum Viable Tier

Choose the cheapest tier likely to succeed for the given task class and risk.

Concepts:
- `baseline_tier`: normal starting tier for the class
- `minimum_tier`: lowest allowed tier for the class
- `promotion_rule`: evidence that moves the task upward

### 4. Recent Failure History

Escalate only on meaningful evidence:
- repeated logic failure on `budget` promotes to `standard`
- repeated logic failure on `standard` promotes to `premium`
- quota failures, cooldown failures, and unrelated infra faults do not count as
  evidence that the tier was too weak

This keeps promotion tied to task difficulty instead of transient runtime noise.

### 5. Budget Pressure

Budget pressure is a secondary control, not the primary policy.

When pressure is high:
- downgrade at most one tier
- never cross a hard safety floor
- never let budget pressure alone demote high-risk work into an unsafe tier

### 6. Backend Availability

Backend availability is resolved after tier choice.

That means:
- first decide `budget` vs `standard` vs `premium`
- then execute that tier on whichever worker backend is runnable

This preserves the separation between task difficulty policy and runtime routing.

### 7. Hard Safety Floors

Some work must never fall below a floor even when budget pressure is high:
- security-sensitive changes
- schema/data migrations
- cross-repo coordination
- recovery / incident response

## Phases

### Phase 0 — Debugging Baseline

Status:
- active now

Policy:
- all three executor families default to `budget`
- dynamic tier selection disabled by default
- worker-backend round robin enabled
- operators debug pipeline behavior with low cost and low variance

Why:
- isolate pipeline bugs from tier-policy noise
- reduce spend while reliability is still being established
- preserve Claude/Codex resilience without mixing in automatic escalation

### Phase 1 — Controlled Promotion

Enable only after:
- pipeline behavior is stable
- executor artifacts are trustworthy
- failure categories distinguish task-difficulty failures from runtime failures

Policy:
- keep `budget` as the baseline
- promote to `standard` for task classes known to need more reasoning
- promote from `budget` to `standard` on repeated meaningful failure
- keep `premium` mostly manual or narrowly rule-based

Likely signals:
- task class
- low/medium risk assessment
- repeated failed attempts on lower tier

### Phase 2 — Risk-Aware Standard Baseline

Enable only after:
- task classification and risk scoring are stable
- enough run history exists to tune promotion behavior

Policy:
- `budget` remains baseline for narrow/mechanical tasks
- `standard` becomes the normal tier for routine implementation work
- budget pressure can downgrade only safe, low-risk tasks
- `premium` still gated tightly

### Phase 3 — Premium Escalation by Evidence

Enable only after:
- failure history is reliable
- high-risk categories are well defined
- operator review confirms the escalation rules are sensible

Policy:
- `premium` for explicitly hard, high-risk, or repeatedly failing work
- escalation can be driven by:
  - task class
  - risk score
  - repeated meaningful failure at `standard`
  - explicit operator override

### Phase 4 — Adaptive Tiering

Future state:
- classifier-driven baseline
- risk-aware safety floors
- evidence-based promotion/demotion
- budget pressure as a bounded secondary influence
- per-repo tuning where needed

This phase should be data-driven, not intuition-driven.

## Architectural Boundaries

- `RuntimeBindingPolicy`
  - owns task-shape and policy intent
  - should express the desired tier in `config_ref`
- executor adapters
  - own translation from desired tier into concrete backend config
- worker-backend selector
  - owns Claude vs Codex routing and cooldown handling
- `UsageStore`
  - owns capacity, cooldown, and pressure signals

Do not collapse these responsibilities into one selector. Tier policy and
worker-backend routing are related but not the same concern.

## Operational Guidance

During Phase 0:
- keep `worker_backend: claude_code`
- keep `dynamic_worker_backend_selection: true`
- keep `budget` pinned as the configured tier
- do not enable auto-promotion just because it exists mechanically

Operator goal:
- first prove pipeline correctness
- then prove classification quality
- only then trust automated tier escalation

## Consequences

Positive:
- cheaper and more stable debugging baseline
- clearer vocabulary: `budget`, `standard`, `premium`
- one canonical policy order for future automation
- preserves backend resilience while avoiding premature tier complexity

Tradeoffs:
- Phase 0 may underpower some tasks that would succeed faster on `standard`
- later phases require better classification and failure labeling than OC has today

## Follow-Up Work

1. add structured task classification inputs
2. add risk/blast-radius scoring usable by `RuntimeBindingPolicy`
3. separate meaningful task failure from runtime quota/cooldown failure in promotion logic
4. add operator reports that show tier chosen, promotion cause, and downgrade cause
5. revisit whether `premium` needs sub-modes or hard repo-specific floors
