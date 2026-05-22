# Routing Contract Fidelity

## Problem Resolved

SwitchBoard policy already routed to values such as:

- `direct_local`
- `team_executor`
- `dag_executor`
- `openclaw`

The old contract narrowed `LaneDecision.selected_backend` and silently rewrote unknown values to a single backend name, which made the canonical routing output untrustworthy.

## Current Rule

`LaneDecision.selected_backend` now uses the full canonical backend value space in `BackendName`.

Selector behavior is now:

- emit the real backend value
- validate policy against the canonical backend universe
- fail validation loudly if policy references an unsupported backend

Selector behavior is no longer:

- emit a single backend name as a lossy fallback for unsupported backend values

## Routing Outputs

Two routing artifacts are now explicitly allowed:

- `LaneDecision`: the minimal canonical routing decision used for execution handoff
- `RoutingPlan`: a richer routing-side artifact containing fallbacks, escalations, and blocked alternatives

The invariant is not “only one routing model may exist.”

The invariant is:

- routing outputs must be canonical
- routing outputs must be truthful
- routing outputs must not execute work
