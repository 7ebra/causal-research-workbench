---
name: controlled-model-updates
description: "Manage model challengers and replacement using frozen versions, bounded trials, data gates and fresh future evaluation. Use for continuous-learning systems, retraining, drift-driven updates and champion/challenger promotion; not ordinary application deployments or unrelated code refactoring."
---

# Controlled Model Updates

Allow research to improve a model without rewriting the policy being evaluated.
Separate source discovery, candidate training, evaluation and authorized replacement.
Preserve the user's existing scope, budget and permissions; this skill grants no
additional authority to place trades, deploy or purchase resources.

## Identify the current contract

- Find the active model, code/config/data hashes, protocol, risk limits, deadline,
  already-scored periods and prior candidates. Check actual artifacts and process
  identity before assuming a run is healthy, stopped or complete.
- Treat a frozen trial as immutable. Operational repairs may improve logging,
  connectivity or integrity without retuning its policy; record material runtime
  changes and any effect on comparability. A behavior-changing fix is a new version.
- Set a bounded candidate/compute/data budget and explicit stop or escalation
  conditions for unattended work. Resource discovery does not justify unlimited
  experiments or subscriptions.

## Stage each challenger

- Check data integrity and input availability before fitting. Authentication
  failures, clock ambiguity, partial downloads or missing input types must not
  cause silent source substitution. Scope any diagnostic fitting permission
  separately from strategy approval.
- Register the hypothesis and comparison before evaluation. Fit and select on
  permitted training/validation data; record every attempted challenger, including
  failures and inactivity. Keep code, source registry and model versions identifiable.
- Freeze the selected challenger and hash it before scoring its test. Do not update
  it on test rewards, change thresholds after losses or repeat selection against
  already-scored outcomes. If validation rejects every challenger, stop that branch.
- Establish incremental benefit over the champion and simple baselines under the
  same timing, exposure and cost conventions. Report uncertainty and dependence;
  a favorable historical comparison is only a diagnostic screen.

## Require fresh confirmation before replacement

- Start a new future/shadow period only after the candidate is frozen, with its
  duration, sample sufficiency, integrity gates and rejection rules predefined.
  Data collected before freezing are not new prospective confirmation.
- Drift can suspend eligibility or propose another challenger. It must not silently
  update an active evaluation policy. Distinguish data-feed drift, feature changes
  and outcome deterioration before choosing a response.
- Replace a champion only when the required independent test passes and replacement
  is within the user's authorized scope. Use existing authorization; do not ask for
  another approval solely because this skill is loaded. If replacement is not
  authorized, finish a concrete reviewable candidate and report the remaining step.
- Preserve a rollback version and a decision record. Rejecting a challenger and
  retaining no approved model are valid outcomes, not failures to keep learning.

## Handle interruption and completion

Use [the lifecycle record](references/model-lifecycle.md) for a concrete update.
Check completed markers before notifying again or starting work. Resume an existing
run only when its protocol supports resume, preserving data, model identity and
deadline; never duplicate workers. A failed single-attempt selection remains a
failed attempt unless a separately justified recovery preserves its contract.

Report the actual stage, evidence, blocked inputs and next permissible action.
Stop obsolete monitoring after its bounded purpose is fulfilled. Do not present
elapsed time, quote counts, provider agreement or automatic retraining as an edge.
