# Engineering case study

## Problem

A short-horizon financial research prototype needed to distinguish observed data,
simulation assumptions and unsupported strategy claims. External price feeds can
be delayed; processes can restart; source material can be revised; and a model
that mostly abstains can make elapsed collection time look more informative than
it is. The engineering task was to make those limits visible and reproducible.

## Implemented decisions

1. **Separate clocks and preserve causality.** Provider timestamps, receipt times
   and monotonic sequencing serve different purposes. Features use completed
   bars; target intervals cannot cross a partition or a missing-bar interval.
2. **Keep SQLite authoritative.** Paper account state, quote observations,
   decisions and settlement outcomes persist across supported engine resumes.
   File-reader locks can delay report publication without losing committed state.
3. **Keep missing outcomes explicit.** A late/missing expiry quote becomes a void
   with no fabricated price or profit. Comparison accounts are separate experiments,
   not a combined portfolio or independent market observations.
4. **Freeze evaluation inputs.** Candidate code, data and configuration hashes are
   recorded before fitting. A failed validation stage can end the branch without
   consuming the reserved test. Interrupted attempts are preserved.
5. **Restrict collector capability.** OANDA connectors use practice hosts and
   allowed GET routes. Tokens are entered locally and passed through private stdin
   pipes rather than files or process arguments. Transport failures use bounded
   retries; authorization failures stop instead of silently changing provider.
6. **Make research shareable with limits.** The transcript utility reads a complete
   JSONL prefix, reports an incomplete trailing record and excludes internal
   instructions/private reasoning. Redaction is explicitly heuristic.

## Measured operational exercise

A bounded practice-feed exercise collected 316,198 unique quotes and 16,824
heartbeats across four instruments over its planned 24-hour window. Observed
minute-bin coverage ranged from 98.8% to 99.5%. Eleven connection errors and eight
recoveries were recorded; one message gap exceeded 30 seconds, lasting 47.844s.
Bid/ask and derived mid/spread checks passed. The original records and account
details are private and are not redistributed here.

Those metrics establish collection behavior under this particular exercise,
not exact uptime, every market tick, trading profitability or future reliability.
Clock-reference probes ranged from approximately -0.110s to +0.294s; raw source
ages were not corrected by guessing.

## What the study did not establish

No profitable strategy was validated. A fixed feature comparison rejected all
24 variants at validation, and the reserved test was not scored. A minimum trade
threshold was a screening rule rather than a study-specific power calculation.
The original engineering effort exceeded its signal evidence; future statistical
claims need a predeclared hypothesis, attainable sample size and search-aware
evaluation before another long run.

This is why the public deliverable emphasizes tooling and evidence handling.
There are no broker orders, performance marketing claims, paid-client results or
unattended self-learning guarantees.

## Reuse beyond the financial example

The transferable contracts are immutable versioning, as-of availability,
chronological validation, explicit missingness, failure classification and
reconciled outputs. The playbooks also apply to other time-sensitive model and
data-analysis tasks when their own targets, costs and acceptance rules are defined.
