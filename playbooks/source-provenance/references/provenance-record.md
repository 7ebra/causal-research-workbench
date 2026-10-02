# Provenance record

Prefer the project's schema. For a new ingestion design, useful minimum fields:

| Field | Meaning |
| --- | --- |
| source_id / canonical_url | Stable provider identity and original location |
| kind | Education, commentary, forecast, release or quote |
| version_id / content_hash / hash_basis | Immutable version and what was hashed |
| published_at / publication_timezone | Source-stated time and interpretation |
| first_observed_at / received_at | Actual first availability to this system |
| available_at / availability_basis | Decision-use boundary and supporting evidence |
| observed_monotonic_ns | Local sequencing independent of wall-clock changes |
| revised_at / supersedes | Revision relationship without overwriting history |
| instrument / horizon / target | What the information could predict |
| eligibility / reason | Usable, delayed, late, edited, unmatched or unverifiable |

For an archived historical version, record the archive/vintage identity and the
evidence for its historical availability. For a newly observed public version,
available_at must not precede first_observed_at. Do not mix these two conventions.

Example: a forecast says it was published at 11:49 UTC, the system first sees it
at 11:50:10, and its entry was 11:50. It is late for that entry even if its page
date is earlier. A revision seen at 11:55 cannot change the saved 11:50 assessment.

Treat actual-minus-consensus macro surprises as time-sensitive features: record
the consensus vintage, first release and later revisions separately. A calendar
queried after an event does not prove its values were available beforehand.
