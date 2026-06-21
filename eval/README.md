# EVAL — agent-quality guard (Phase 4)

Self-healing evaluation of the reviewer/worker agents, per
[`docs/design/HARNESS_TRUST_HARDENING.md`](../docs/design/HARNESS_TRUST_HARDENING.md) §4.

The design principle: **the fleet grades itself, but cannot grade its own answer
key.** Everything self-heals with no human in the per-correction loop *except* the
one irreducible anchor — an operator signature on each ground-truth label, encoded
once, offline.

## Layout

| Path | Role |
|------|------|
| `corpus/ledger.jsonl` | Append-only, **hash-chained** case ledger. Editing/deleting any past entry breaks the chain → the required integrity check goes red. |
| `constitution/baseline_floor.json` | **Monotonic** bar — may only rise. Encodes the report-only→blocking graduation threshold. |
| `constitution/operator_pubkey.ed25519` | The operator's Ed25519 **public** key. A case is *graded* only if it carries a signature verifying against this key. Placeholder until anchored. |
| `seed_candidates.py` | One-shot seeder for the initial unsigned candidate cases (dev/operator tooling). |

Source code lives in `src/operations_center/eval/` (`corpus`, `signing`, `replay`,
`critic`, `constitution`, `verify`).

## How a case is graded

1. A case is `(input, ground_truth verdict, rationale)`. Today the graded layer is
   `kind: "verdict"` — the `input.checks` are replayed through the **deterministic
   code-computed verdict** (`pr_review_watcher.verdict.compute_verdict`) and must
   exactly match `ground_truth`. No model → zero flakiness → safe to block.
2. Real-model check-extraction is the **separate, non-blocking** drift monitor
   (`critic.py`), run on a *different model family* and N-of-M voted.

## Candidate vs graded (the exam/answer-key split)

- The fleet may **append unsigned candidate cases** — they are replayed and
  reported, but never gate.
- Only an **operator-signed** case counts toward the gate. The signature is made
  offline with a key that never touches a fleet host; no compute inside the trust
  boundary can mint one.

## Gate state today

Report-only. The committed corpus holds **candidate** cases only and
`operator_pubkey.ed25519` is a placeholder, so the gate cannot block. It graduates
to blocking once the operator anchors a key and signs ≥ `min_graded_cases` cases —
the only deferred, irreducibly-human step.

## Operator: anchoring the key and signing cases

See `constitution/operator_pubkey.ed25519` for key generation. To sign a candidate
into a graded case, sign `signing.signing_bytes(case)` with the offline private key
and write the hex `signature` + `signer` onto that ledger entry (re-chaining via
`corpus.append_case` for new entries, or an operator-side re-sign tool for existing
candidates). The verifier then counts it toward the gate.
