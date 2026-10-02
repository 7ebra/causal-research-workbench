"""Connection health and bounded retry scheduling; independent of trading policy."""
ASSETS = ('EURUSD', 'USDJPY', 'AUDUSD')


def advance(previous, valid_assets, now, base_seconds=60, errors=None):
    previous = previous or {}
    valid = set(valid_assets).intersection(ASSETS)
    failed = sorted(set(ASSETS) - valid)
    state = 'ONLINE' if not failed else 'DEGRADED' if valid else 'RECONNECTING'
    old_state = previous.get('state', 'STARTING')
    streak = previous.get('failure_streak', 0) + 1 if not valid else 0
    delay = min(300, base_seconds * 2 ** min(max(streak - 1, 0), 6)) if not valid else base_seconds
    since = previous.get('outage_since')
    if failed and since is None:
        since = now
    recovered = state == 'ONLINE' and old_state in ('RECONNECTING', 'DEGRADED')
    duration = now - since if recovered and since is not None else None
    result = {'state': state, 'checked_at': now, 'failure_streak': streak,
              'failed_assets': failed, 'errors': errors or {}, 'retry_seconds': delay,
              'next_attempt_at': now + delay, 'outage_since': since if failed else None,
              'last_online_at': now if not failed else previous.get('last_online_at'),
              'recovery_count': previous.get('recovery_count', 0) + int(recovered),
              'last_outage_seconds': duration if recovered else previous.get('last_outage_seconds')}
    event = None
    if state != old_state:
        event = 'CONNECTION_RECOVERED' if recovered else 'CONNECTION_LOST' if not valid else 'CONNECTION_DEGRADED' if failed else 'CONNECTION_ONLINE'
    return result, event
