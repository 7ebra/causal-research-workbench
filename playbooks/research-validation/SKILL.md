---
name: research-validation
description: "Design or audit time-dependent model and trading experiments with chronological evaluation, realistic costs, matched baselines and explicit rejection criteria. Use for backtests, RL evaluations and claims of predictive improvement; not routine software testing or general market commentary."
---

# Research Validation

Produce an evaluation whose conclusion is traceable to actual data, a fixed
hypothesis and a reproducible ledger. Preserve the user's objective and any
existing frozen protocol; do not introduce universal trade-count or duration rules.

## Establish the experiment

- Locate the dataset, current protocol, prior attempts and scored intervals before
  proposing changes. Identify whether the task is prospective confirmation,
  retrospective diagnosis, replication or a new hypothesis.
- Specify the target, action timing, reward, instruments, feature availability,
  costs, risk constraints, stopping conditions and acceptance/rejection rules
  before inspecting evaluation outcomes. Use project thresholds where they exist;
  otherwise justify and record the chosen sample requirements.
- Define baselines and candidate budget in advance. Track all attempts and rejected
  versions; do not present the best result from an undisclosed search as a single
  independent test. A new implementation does not make previously studied market
  dates untouched.

## Preserve chronology and comparability

- Use chronological training, validation and test windows for temporal data. Fit
  preprocessing, feature thresholds and policy selection on the permitted earlier
  partitions. Purge examples whose labels/outcomes cross a boundary; account for
  overlapping horizons when assessing independence. Add an embargo only when the
  dependency structure justifies it, and record its rationale.
- Verify as-of feature availability using source publication, first observation
  and revision information. Preserve gaps instead of inventing continuity. Reset
  stateful features after gaps where required by the feature contract.
- Compare learned policies with appropriate simple and no-action baselines using
  matched opportunities and risk rules. Report exposure differences caused by
  abstention or loss stops; account-level trades sharing prices are not independent.
- Match rewards and costs to the claimed instrument: FX bid/ask fills, fees,
  financing and slippage differ from hypothetical binary payouts. Label a quote
  basis sensitivity on a fixed ledger separately from a full execution simulation.

## Validate evidence before interpreting performance

- Reconcile source data, decision events, fills/settlements, ledger and balances.
  Check causal timestamps, duplicate entries, position overlap, missing outcomes,
  stale prices and actual collection coverage. Voids are missing outcomes, not
  losses, wins or zero-profit trades; report their proportion and possible bias.
- Inspect trade activity before committing to a long confirmation run. Surface
  whether inactivity comes from data eligibility, unseen states, weak support,
  policy abstention or risk gates. Do not force trades to improve sample counts.
- Evaluate uncertainty at a dependence-aware unit, such as suitable time blocks
  rather than every tick. Describe bootstrap assumptions and account for repeated
  candidate selection; descriptive intervals do not prove an edge.
- Freeze the selected model before scoring its test. If no candidate meets the
  predefined validation criteria, report rejection rather than nominate a winner
  or consume a reserved test simply to produce a result.

## Deliver and stop

Use [the experiment record](references/experiment-record.md) when preparing or
auditing a concrete trial. Save protocol/data/code hashes, actual outputs, baseline
comparisons, coverage limits and the decision. Distinguish infrastructure readiness,
historical diagnostics, prospective support and broker execution validity.

Preserve a failed or interrupted attempt. Diagnose without silently resetting its
deadline, replacing its data or repeating selection on scored outcomes. A materially
new hypothesis needs a separately registered trial and new confirmation data.
