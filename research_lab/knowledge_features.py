"""Separate educationally motivated features; no fitting, rewards or orders.

Fixed exploratory specification: SMA20/SMA50 trend, simple-average RSI14,
ATR14 relative to ATR60. Only completed input bars are used. This is an
experimental implementation, not OANDA's proprietary analysis or a validated edge.
"""
import math
import statistics
from screener import BAR, bucket


def features(rows):
    states=[None]*len(rows)
    true_ranges=[]; gains=[]; losses=[]; run=0
    previous=None
    for i,row in enumerate(rows):
        t=row['t']; prices=[row[k] for k in ('open','high','low','close')]
        if t%BAR or (previous is not None and t<=previous['t']):
            raise ValueError('Bars must be unique, increasing and M5 aligned')
        if not all(math.isfinite(v) and v>0 for v in prices):
            raise ValueError('Invalid candle prices')
        if not row['low']<=min(row['open'],row['close'])<=max(row['open'],row['close'])<=row['high']:
            raise ValueError('Invalid candle OHLC')
        contiguous=previous is not None and t-previous['t']==BAR
        run=run+1 if contiguous else 1
        if contiguous:
            delta=row['close']-previous['close']
            tr=max(row['high']-row['low'],abs(row['high']-previous['close']),abs(row['low']-previous['close']))
        else:
            delta=0.; tr=row['high']-row['low']
        true_ranges.append(max(tr,row['close']*1e-8))
        gains.append(max(delta,0)); losses.append(max(-delta,0))
        if run>=60:
            closes=[r['close'] for r in rows[i-59:i+1]]
            atr=statistics.fmean(true_ranges[i-13:i+1])
            slow_atr=statistics.fmean(true_ranges[i-59:i+1])
            trend=bucket((statistics.fmean(closes[-20:])-statistics.fmean(closes[-50:]))/atr,.25)
            gain=statistics.fmean(gains[i-13:i+1]); loss=statistics.fmean(losses[i-13:i+1])
            rsi=50. if gain==loss==0 else 100. if loss==0 else 100.-100./(1.+gain/loss)
            momentum=-1 if rsi<30 else 1 if rsi>70 else 0
            states[i]=f'{trend},{momentum},{int(atr>slow_atr)}'
        previous=row
    return states
