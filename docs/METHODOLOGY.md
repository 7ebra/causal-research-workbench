# Timing and evaluation methodology

## Candle contract

An M5 timestamp is the bar's opening time. Features at index i are available after
that bar closes. Historical examples enter on a later open; their expiry must be
strictly inside the partition's exclusive end. The feature warmup requires 60
consecutive bars and resets after a gap. No interpolated candle creates a decision.

The tabular example uses fixed training seeds and support from unique timestamps,
not repeated epochs. Q-values are discounted reward estimates, not win probabilities.
Some routines select a diagnostic historical candidate even when rejected; the
staged `knowledge_experiment.py` permits no selection if validation has no eligible
education-feature challenger. Neither routine approves deployment.

## Paper observation contract

The forward prototype records the source quote time and actual observation time.
Entries are blocked for stale inputs and risk/position gates. Settlement requires
a quote timestamp at or after the intended expiry and within a declared late
observation allowance. Otherwise the result is a separately counted void.

Restart behavior is component-specific: the paper engine can preserve its database,
model fingerprint and original deadline. The OANDA collector retains tokens only
for its process lifetime and does not resume an existing collection after reboot.
Missing ticks cannot be recovered by reconnection.

## Source availability

First observation is not interchangeable with a source's older publication date.
Revisions remain new versions. HTTP Date/Last-Modified/cache headers cannot make an
old article into a fresh forecast. Public lexical context needs semantic review;
conditional commentary must not become an invented flat direction or expiry.

## Statistical interpretation

Use one declared hypothesis, accessible data, attainable sample count, costs,
controls, pass/fail rules and a stop date before fitting. Purge crossing targets;
account for dependence and all tried variants. Bootstrap intervals in this
prototype are descriptive, not multiple-testing-adjusted evidence.

Fixed 80% binary payouts are hypothetical examples and differ from executable
bid/ask FX returns. Changing reward targets or adding features does not establish
positive expectancy. Provider reference prices cannot prove another broker's
settlement behavior.

The demo only checks software mechanics on manufactured input. It is intentionally
not a strategy benchmark or a forecast accuracy result.

## Primary references

- [OANDA stream sampling specification](https://developer.oanda.com/rest-live-v20/pricing-ep/)
- [OANDA authentication](https://developer.oanda.com/rest-live-v20/authentication/)
- [Python SQLite URI behavior](https://docs.python.org/3/library/sqlite3.html#how-to-work-with-sqlite-uris)
