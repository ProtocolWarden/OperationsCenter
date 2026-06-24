---
status: open
---

# Lineage / Operational-Graph Visualization

**Status: OPEN — specced adversarially, decision pending. Do NOT implement
unprompted.** One of four open-gap specs from the Osprey/Praetorian arc; see also
[Context Discipline](./CONTEXT_DISCIPLINE.md),
[Risk-Tiered Approval](./RISK_TIERED_APPROVAL.md),
[Runtime Capability Enforcement](./RUNTIME_CAPABILITY_ENFORCEMENT.md), and the
master [Execution Lineage & Determinism Boundary](./EXECUTION_LINEAGE_AND_DETERMINISM_BOUNDARY.md).

**Headline verdict:** WON'T-BUILD a UI/visual surface. The lineage read-model
already renders as a human-readable trust-labeled tree — the genuine deficiency
is **discoverability**, not visualization. Fix it by wiring the existing CLI into
the tool the operator already uses (~3 lines).

## Current real state

- **The read-model already renders for humans.** `lineage/cli.py` `render_chain`
  prints, per task: the repo, every edge as `src --kind--> dst`, with an honest
  trust glyph — `[steerable]` vs
  `[display-only: text-derived, unverified, expired, host-relative]` — steerable
  edges first, then display-only, then a `steerable edges: N / M` summary.
  `--json` emits `display_view()`. It renders one task or all (`build_all`).
- **But it is unreachable in practice (verified).** The only `lineage` mention in
  `pyproject.toml` is a lint exception (`T201` for the CLI's prints) — there is
  **no `[project.scripts]` entry**. There is **no `lineage` verb** in
  `scripts/operations-center.sh`. Invocation is documented only as
  `python -m operations_center.lineage.cli <task_id>`. (The `--lineage` flags in
  `tools/loop/controller.py` are ContextLifecycle *session-resume*, unrelated to
  execution-lineage.)
- **What the operator routinely looks at shows no lineage:** the status panes
  (`worker_backend_status` — a rich table of backend cooldowns), per-role
  `*.status.json`, heartbeat/liveness surfaces. `observer/dashboard.py` is a
  data-provider (coverage/flaky/latency dicts), served by no entrypoint, with
  zero lineage content. There is **no HTTP dashboard anywhere** (the only servers
  are infra: ci_webhook, error_ingest, key_proxy, egress_proxy). No
  graphviz/mermaid/d3 anywhere in `src/`.

## Steelman

The honest, trust-labeled chain — the one artifact this whole arc produced *for
humans* — is surfaced nowhere the operator actually looks. It is gated behind a
`python -m …` incantation that appears in no script, no `--help`, no status pane.
So the four-dimension trust flags (the entire point of "observable, honest about
its own trust state") are invisible in practice; when a task misbehaves across
retries the operator cannot eyeball "task → run → PR → verdict, and which edges
are display-only vs ground truth" without knowing the chain exists and hand-typing
a module path. **A read-model a human never reads is, operationally, not
observable.**

## Adversarial round 1

The steelman conflates "not discoverable" with "not visualized," and only the
first is true.

- **The read-model IS rendered for humans** — `render_chain` already produces the
  trust-split tree. The gap is a missing wrapper line so
  `operations-center.sh lineage <task_id>` works. That is a shell-case arm, not a
  visualization build.
- **Who is the consumer of a graph UI?** A single operator, at a terminal that
  already shows rich tables, does not need a browser graph, a server, a port, an
  auth surface, and a JS pipeline to read a chain that is **at most ~6 nodes**
  (task → run → pr → verdict). A box-and-arrows GUI for a 6-node DAG is *less*
  legible than the existing indented tree and far more to maintain.
- **It is the highest-entropy, most-forbidden move.** A graph UI is a new
  long-lived attack surface — exactly what the priors say to avoid. And it would
  render `display_view()`, which by contract **carries the attacker-controllable
  `goal_text`**, into a browser (HTML/JS) — manufacturing an XSS/injection sink
  for adversarial issue-body text that the CLI's plain-stdout rendering does not
  have. **The visualization would re-introduce the harm the lineage design spent
  its trust-split removing.**

## Adversarial round 2

Even the surviving "expose the chain as a graph format" idea mostly fails:

- The master spec §7 already pre-rejected a greenfield
  `OperationalGraphRuntime` / canonical runtime-state authority as the
  highest-entropy move and a fourth source of truth. A standing graph surface is
  its read-only face.
- Directly analogous precedent, resolved one day prior:
  [LINEAGE_STEERING_CONSUMER](./LINEAGE_STEERING_CONSUMER.md) was killed across 3
  adversarial rounds with the same logic (standalone subsystem ~90% already
  covered + adds new harm loses to a small additive delta). Visualization is the
  same anti-pattern on the display axis.
- The only sliver with merit — "I can't eyeball *cross-task* lineage roots" — is
  thin: `build_all` already emits every chain, and grouping repeated failures by
  lineage-root is the cross-task need already routed to `detect_convergence_stall`
  as a pending operator decision, not a picture.
- Format, if ever wanted, is a one-liner (`--format=dot|mermaid` on `cli.py`),
  not a surface — and even that is speculative (no recorded operator request).

## Minimal real delta

- **Tier 1 — BUILD-minimal (~3 lines), the only thing with present
  justification.** Add a `lineage` verb to `scripts/operations-center.sh` that
  shells `python -m operations_center.lineage.cli "$@"`, plus its `usage`/`doctor`
  line. (Equivalently, a `[project.scripts]` entry
  `operations-center-lineage = "operations_center.lineage.cli:main"`.) Closes the
  *actual* gap — the existing trust-labeled tree becomes reachable from the tool
  the operator already uses — with no new surface, server, or authority.
- **Tier 2 — DEFER (do not build unprompted).** A `--format=dot|mermaid` branch
  in `lineage/cli.py:main` emitting graph text to stdout for piping into an
  external renderer. Still no service. Justified only if an operator says the tree
  is insufficient for a specific chain.

**Hard stop:** no web service, no daemon, no HTML/JS rendering of
`display_view()` (it carries attacker-controllable `goal_text`).

## Disposition

| Item | Disposition | Why |
|---|---|---|
| Operational graph visualization (a UI/visual surface) | **WON'T-BUILD** | Highest-entropy move; new attack surface for one operator; re-introduces the attacker-text injection sink the lineage design removes. |
| Lineage CLI unreachable from the operator's tooling | **BUILD-minimal (Tier 1)** | The genuine gap is discoverability; ~3-line `operations-center.sh` verb. |
| `--format=dot\|mermaid` graph output | **DEFER (Tier 2)** | YAGNI; no operator request on record. |

**Left open** pending the operator's bigger-picture decision on this arc.
