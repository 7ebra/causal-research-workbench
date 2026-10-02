# Model lifecycle record

Use the existing project state machine when one exists. For a new workflow:

| Stage | Evidence needed to advance |
| --- | --- |
| Proposed | Hypothesis, input requirements, attempt budget and stop conditions |
| Data ready | Assessed integrity, timing, versions and permitted fitting scope |
| Validation | Trained versions, matched controls, activity and recorded rejections |
| Selected and frozen | Validation-only choice and immutable candidate/code hashes |
| Diagnostic test | Actual results with costs, coverage and uncertainty |
| Future confirmation | New post-freeze interval with predefined success/failure rules |
| Replacement | Passed required test, authorized scope and rollback identity |
| Rejected / incomplete / blocked | Preserved evidence and explicit reason; no hidden reset |

Keep candidate ID, parent/champion ID, feature contract, source registry hash,
data partition IDs, model/code hashes, observed start/end, budgets and result links.
Differentiate a predeclared trial deadline from a later observed completion time.

When operational code changes during a run, identify whether policy decisions
were unchanged, which collection/reporting behavior changed and the resulting
limitations. A model fingerprint alone does not prove all runtime behavior matched.

Notification markers record what was reported, not a substitute for actual
assessment. A running process alone does not prove data is advancing; verify
committed records and both source and observation timestamps.
