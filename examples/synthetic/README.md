# Synthetic demonstration

`run-01` contains deterministic generated candles, an engine fixture ledger and
JSON/HTML mechanics reports. Every price is invented. The policy deliberately
forces actions to exercise duplicate prevention and settlement handling.

Run `python examples/run_demo.py --out demo-output` from the repository root.
The output directory must not already exist. Runtime SQLite files remain local;
they are not part of the published example. This is not a performance backtest.
