# Experiment record

Use existing project formats when available. Otherwise record these fields in a
small structured artifact before fitting or scoring:

| Area | Required evidence |
| --- | --- |
| Identity | Trial ID, hypothesis, creation time, prior related attempts |
| Inputs | Source/version, data hashes, collection interval, gaps and revisions |
| Timing | Decision time, available features, entry/expiry or target interval |
| Partitions | UTC half-open boundaries, label purging and embargo rationale |
| Model | Code/config hashes, seeds, preprocessing and candidate search budget |
| Economics | Reward definition, price basis, fees, spread, slippage and payout assumptions |
| Risk | Stake/position sizing, overlap rules, loss limits and operational stops |
| Comparisons | Baselines, eligibility rules and paired comparison unit |
| Decision | Sample requirements, uncertainty method, acceptance and rejection rules |
| Outputs | Saved decisions/ledger, reconciliation, selected-model hash, failures |

For trading ledgers distinguish decision, source quote, receipt, intended entry,
actual entry and settlement timestamps. If exact fills are unavailable, report the
simulation convention and delay distribution. Coverage is an observed sampling
measure unless independent evidence establishes actual connection uptime.

Useful result labels: rejected at validation, diagnostic only, confirmation pending,
incomplete due to data, or supported under the stated test. Do not equate any label
with live execution authorization.
