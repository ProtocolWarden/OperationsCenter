# EVAL answer-key signing — operator runbook

This is the **one human step** in the whole trust-hardening spec. Everything else
self-heals; this is the part only you can do. It is a **one-time anchoring**, then
occasional appends — not a recurring chore. Read this once; it covers the normal
flow *and* what to do if you lose the key.

---

## What a signature actually is (read this first)

You are **not** signing code, a PR, or a merge. You are signing a small,
human-readable **answer-key entry** — one exam question and its correct answer:

```json
{"case_id": "legit-code-quality-fail",
 "kind": "verdict",
 "input":  {"checks": [{"check_id": "code_quality", "status": "fail", "...": "..."}]},
 "ground_truth": {"result": "CONCERNS", "failing": ["code_quality"]}}
```

Your signature attests one sentence: **"for this reviewer input, the correct
verdict is X."** That's it.

Two things that make this low-stakes:

- **The key encrypts nothing.** The corpus (inputs + correct answers) is plaintext
  in `eval/corpus/ledger.jsonl`, readable forever. The signature is just a **wax
  stamp** proving you vouched for an answer. Lose the stamp and the answers are
  still right there — you just make a new stamp (see *Lost key* below).
- **You sign once.** A signed case is permanent; the gate re-checks it on every CI
  run with no human. No expiry, no renewal, no re-signing old cases. You only sign
  again when a genuinely *new* kind of reviewer mistake appears that's worth adding
  — and the system flags those for you.

---

## One-time anchoring (turns the gate from report-only to blocking)

Run these on a machine you trust. The private key must **never** be committed and
**never** placed on a fleet host — only the *public* key is committed.

### 1. Generate your keypair

```bash
python -m operations_center.eval.sign keygen --private-out operator_priv.pem
```

This writes the **private** key to `operator_priv.pem` and **prints the public key
hex**. Save `operator_priv.pem` somewhere safe and offline — a password-manager
entry is ideal. You do not need it day-to-day; only to sign future cases.

### 2. Anchor the public key

Open `eval/constitution/operator_pubkey.ed25519`, delete the placeholder, and paste
the printed public-key hex as the **sole first line**. Commit it (you own this file
via CODEOWNERS).

### 3. Sign the corpus

```bash
python -m operations_center.eval.sign sign \
  --private operator_priv.pem \
  --ledger eval/corpus/ledger.jsonl \
  --signer your-handle
```

This converts the unsigned candidate cases into signed graded cases and re-chains
the ledger. Re-running is safe (already-signed cases are skipped).

### 4. Verify and commit

```bash
python -m operations_center.eval.verify
```

You want to see `gate [blocking]: all graded cases pass and floor is cleared`.
Commit the updated `eval/corpus/ledger.jsonl` + `operator_pubkey.ed25519`. CI's
required `EVAL corpus integrity` check now enforces the answer key on every PR.

---

## Lost the private key? (rotation — it's fine)

Losing the key is **recoverable**, not a disaster. The key only *stamps* the
plaintext corpus; it doesn't lock it. To rotate:

1. `python -m operations_center.eval.sign keygen --private-out operator_priv.pem --force`
   (makes a fresh keypair).
2. Paste the **new** public hex into `eval/constitution/operator_pubkey.ed25519`.
3. Re-run the **sign** command from step 3 above. The old signatures no longer
   verify against the new key, so they count as unsigned and get re-stamped
   automatically.
4. `verify`, commit.

Because the public-key file is CODEOWNERS-pinned, only you can rotate it — an
attacker who lost-and-found nothing can't swap in their own key.

> **Note:** losing the key means you can't make *new* signatures with the *old*
> key — but you never need to. You just mint a new key and re-stamp. The answers
> were never at risk.

---

## Adding a case later (the only recurring action — and it's rare)

When the outcome flagger or drift monitor surfaces a *new* class of reviewer
mistake worth memorializing:

1. Append it as an **unsigned candidate** (the fleet can do this; it's replayed and
   reported but doesn't gate).
2. When you're ready, read it, then `sign` it (optionally `--case-id <id>` to sign
   just that one).

If the existing cases already cover the space, you sign nothing. There is no clock.

---

## Why a signature instead of just "don't change this"

`CODEOWNERS` already pins the corpus to you, and the hash chain already makes any
out-of-band edit visible. The signature is the third layer: it makes "don't change
unless I say so" an **un-forgeable cryptographic fact** rather than a rule the
autonomous (possibly injected) fleet could edit. If you ever decide the crypto
isn't worth the custody for your setup, a no-key CODEOWNERS-trust mode is a
reasonable alternative — ask and it can be added.

See `docs/design/HARNESS_TRUST_HARDENING.md` §4.2 for the full design.
