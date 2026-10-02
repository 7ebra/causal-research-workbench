# Reproduce and inspect

## Offline path

```bash
python examples/run_demo.py --out demo-output
python -m unittest discover -s research_lab -p "test_*.py"
python -m unittest discover -s tools/chat_export -p "test_*.py"
python scripts/release_check.py
```

No market API, account or token is needed. Tests use generated fixtures and mocks.
Python 3.10+ is the intended language requirement; local checks used Python 3.12.
Node.js 18+ is needed only for optional JavaScript collection drivers, not offline
Python verification. NumPy/pandas or an LLM API are not required.

## Supplied evidence

`examples/synthetic` contains a checked-in deterministic example report and input
fixtures. Re-run into a new directory; outputs refuse to overwrite an existing
run. Generated prices and fixture-policy outcomes are manufactured for software
testing and must not be mistaken for licensed observations or investment results.

Read the tests to inspect the actual invariants. Test counts are not a substitute
for their scope. The workflow runs Python checks on Windows/Linux with read-only
repository permissions and no account secrets.

## Optional authorized data

CSV inputs use integer UTC bar-open seconds and open/high/low/close columns, sorted
and unique. Historical studies need their own data provenance and eligibility
review. The practice connectors and public analysis collector are optional
components; they are not started by CI or the demo.

External source schemas, entitlement and licenses can change. Obtain data through
authorized access and retain it outside Git. The repository does not distribute
the original price databases or authenticate users to third-party services.

## Known audit limitations

- Rejected models' per-decision historical ledgers and weights were not retained
  in the original fixed comparison; compact metrics and protocols were saved.
- The first operational paper pilot included outages and runtime collection fixes.
- The research stages are retrospective diagnostics unless a separate post-freeze
  future test establishes otherwise.
- Clock diagnostics are external-reference estimates, not exact broker latency.
- The public source collector keeps short excerpts and hashes, not full page archives.
