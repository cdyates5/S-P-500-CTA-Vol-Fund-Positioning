#!/usr/bin/env python3
"""
S&P 500 CTA Positioning Model  ----  Tier1 Alpha-style replication.

Methodology (trend-following replication of systematic CTA equity exposure):
  1. Daily S&P 500 (^GSPC) closes, ~15y.
  2. EWMA realised volatility (RiskMetrics-style decay).
  3. Trend signal at 4 lookbacks (1m / 3m / 6m / 12m): standardised momentum
       z_L = ln(P_t / P_{t-L}) / (sigma_daily * sqrt(L))      (move in std-devs)
  4. Response (transfer) function: pos_L = tanh(k * z_L)   ->  position in [-1, +1]
     (smooth scale-in; saturates toward max long/short on strong trends)
  5. Combine: net = sum_i w_i * pos_L_i   ->  net exposure in [-1, +1]
     reported as % of max long/short, and as a $bn notional (gross capacity).
  6. Conditional 1-month projections: evolve price under 5 scenarios
     (Up Big / Up Small / Flat / Down Small / Down Big, sized in vol units),
     re-run the model forward -> projected net length and implied buy/sell flow.
  7. Trigger levels: price L days ago = where each horizon's trend flips sign;
     plus the aggregate price at which net exposure crosses zero (flip to seller).

Per-horizon position series are exported so the dashboard can re-weight the
composite live (weight sliders) without recomputing the model.
"""
import json, ssl, urllib.request, datetime, sys, time
import numpy as np
import pandas as pd

ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

# ----------------------------------------------------------------------------- data
def fetch_yahoo():
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?range=15y&interval=1d'
    last = None
    for attempt in range(4):
        try:
            d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), context=ctx, timeout=40).read())
            break
        except Exception as e:
            last = e; time.sleep(2 * (attempt + 1))
    else:
        raise last
    r = d['chart']['result'][0]
    ts = r['timestamp']; cl = r['indicators']['quote'][0]['close']
    idx = [datetime.date.fromtimestamp(t) for t in ts]
    s = pd.Series(cl, index=pd.to_datetime(idx), name='SPX').dropna()
    return s

def fetch_fred():
    url = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500'
    txt = urllib.request.urlopen(urllib.request.Request(url, headers=UA), context=ctx, timeout=40).read().decode()
    df = pd.read_csv(pd.io.common.StringIO(txt))
    df.columns = ['date', 'SPX']
    df['date'] = pd.to_datetime(df['date']); df['SPX'] = pd.to_numeric(df['SPX'], errors='coerce')
    return df.dropna().set_index('date')['SPX']

src = 'Yahoo Finance (^GSPC)'
try:
    spx = fetch_yahoo()
except Exception as e:
    print('Yahoo failed:', e, file=sys.stderr)
    spx = fetch_fred(); src = 'FRED (SP500)'

spx = spx[~spx.index.duplicated(keep='last')].sort_index()
print(f'Loaded {len(spx)} obs from {spx.index[0].date()} to {spx.index[-1].date()} via {src}', file=sys.stderr)

# ----------------------------------------------------------------------------- model params
LOOKBACKS = {'1m': 21, '3m': 63, '6m': 126, '12m': 252}
DEFAULT_W = {'1m': 0.20, '3m': 0.30, '6m': 0.30, '12m': 0.20}
K = 1.10                 # response sensitivity (z of ~1.5 -> ~0.90 saturation)
VOL_SPAN = 33            # EWMA vol span (~RiskMetrics lambda 0.94)
CAPACITY_BN = 65.0       # assumed gross trend-follower S&P capacity ($bn) for $ scaling

logp = np.log(spx)
ret = logp.diff()
# EWMA daily vol, annualised available if needed
vol_d = ret.ewm(span=VOL_SPAN, min_periods=20).std()

def response(z):
    return np.tanh(K * z)

# per-horizon standardised momentum + position
pos = {}
zsig = {}
for name, L in LOOKBACKS.items():
    mom = logp - logp.shift(L)
    z = mom / (vol_d * np.sqrt(L))
    zsig[name] = z
    pos[name] = response(z)

posdf = pd.DataFrame(pos)
zdf = pd.DataFrame(zsig)

def combine(weights, frame=posdf):
    w = np.array([weights[k] for k in LOOKBACKS])
    w = w / w.sum()
    return (frame[list(LOOKBACKS)] * w).sum(axis=1)

net = combine(DEFAULT_W)            # in [-1, 1]
net_bn = net * CAPACITY_BN

full = pd.DataFrame({'SPX': spx, 'net': net, 'net_bn': net_bn})
for k in LOOKBACKS:
    full['pos_' + k] = posdf[k]
full = full.dropna()

# trim display history to 2017+ (warm signals), keep full for stats
disp = full[full.index >= '2017-01-01'].copy()

# trailing 10-year window for percentile rank (genuine 10y, independent of chart range)
PCTL_YEARS = 10
cutoff10 = full.index[-1] - pd.DateOffset(years=PCTL_YEARS)
p10 = full[full.index >= cutoff10].copy()

# ----------------------------------------------------------------------------- current snapshot
last_date = full.index[-1]
cur_spx = float(spx.iloc[-1])
cur_net = float(net.loc[last_date])
cur_vol_d = float(vol_d.loc[last_date])
ann_vol = cur_vol_d * np.sqrt(252) * 100
sigma_m = cur_vol_d * np.sqrt(21)          # monthly vol (log)

def at(days_ago):
    if len(full) > days_ago:
        return float(full['net'].iloc[-1 - days_ago])
    return None

pct_rank = float((full['net'] <= cur_net).mean() * 100)
pct_rank_10y = float((p10['net'] <= cur_net).mean() * 100)

def regime(x):
    if x >= 0.85: return 'Max Long'
    if x >= 0.40: return 'Long'
    if x >= 0.15: return 'Modestly Long'
    if x > -0.15: return 'Neutral'
    if x > -0.40: return 'Modestly Short'
    if x > -0.85: return 'Short'
    return 'Max Short'

# ----------------------------------------------------------------------------- trigger levels
# price L days ago = level at which horizon trend flips sign (momentum crosses 0)
triggers = {}
for name, L in LOOKBACKS.items():
    triggers[name] = float(spx.iloc[-1 - L]) if len(spx) > L else None

# aggregate flip level: solve price X today s.t. net(X)=0, holding P_{t-L} & vol fixed
def net_at_price(X):
    lx = np.log(X)
    tot = 0.0
    w = np.array([DEFAULT_W[k] for k in LOOKBACKS]); w = w / w.sum()
    for wi, (name, L) in zip(w, LOOKBACKS.items()):
        p_lag = np.log(spx.iloc[-1 - L])
        z = (lx - p_lag) / (cur_vol_d * np.sqrt(L))
        tot += wi * np.tanh(K * z)
    return tot

def solve_level(target):
    lo, hi = cur_spx * 0.5, cur_spx * 1.8
    # ensure bracketing
    if (net_at_price(lo) - target) * (net_at_price(hi) - target) > 0:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if (net_at_price(lo) - target) * (net_at_price(mid) - target) <= 0:
            hi = mid
        else:
            lo = mid
    return float((lo + hi) / 2)

flip_short = solve_level(0.0)        # net crosses zero (turn net seller / flat)
heavy_sell = solve_level(-0.50)      # net -50% (aggressive de-gross)
ma = {f'{n}d': float(spx.rolling(n).mean().iloc[-1]) for n in (50, 100, 200)}

# ----------------------------------------------------------------------------- conditional projections
H = 21                                  # 1 trading month forward
# scenarios sized in monthly-vol units -> implied % move
SCEN = {'Up Big': 2.0, 'Up Small': 0.8, 'Flat': 0.0, 'Down Small': -0.8, 'Down Big': -2.0}
proj_dates = [(last_date + pd.tseries.offsets.BDay(i)).date().isoformat() for i in range(1, H + 1)]

# need last 300 log prices to compute forward momentum across longest lookback
hist_lp = list(logp.values)
proj = {}
scen_meta = {}
for sname, k_sd in SCEN.items():
    total_lr = k_sd * sigma_m            # total log return over the month
    scen_meta[sname] = {'pct': float((np.exp(total_lr) - 1) * 100),
                        'price': float(cur_spx * np.exp(total_lr))}
    path_lp = hist_lp.copy()
    perh = {k: [] for k in LOOKBACKS}
    netbn_path = []
    for step in range(1, H + 1):
        lr_step = total_lr * (step / H)  # linear ramp in log space
        new_lp = np.log(cur_spx) + lr_step
        path_lp.append(new_lp)
        # vol held at current level through the projection horizon (documented simplification)
        tot = 0.0
        wsum = 0.0
        w = np.array([DEFAULT_W[k] for k in LOOKBACKS]); w = w / w.sum()
        for wi, (name, L) in zip(w, LOOKBACKS.items()):
            mom = path_lp[-1] - path_lp[-1 - L]
            z = mom / (cur_vol_d * np.sqrt(L))
            p = float(np.tanh(K * z))
            perh[name].append(p)
            tot += wi * p
        netbn_path.append(tot * CAPACITY_BN)
    proj[sname] = {'perh': perh, 'net_bn': netbn_path,
                   'flow_bn': float(netbn_path[-1] - cur_net * CAPACITY_BN)}

# recent actual net_bn for the projection lead-in (gray history)
lead = full['net_bn'].iloc[-40:]
proj_hist = {'dates': [d.date().isoformat() for d in lead.index],
             'net_bn': [round(float(v), 2) for v in lead.values]}

# ----------------------------------------------------------------------------- assemble export
def ser(s, r=4):
    return [round(float(v), r) for v in s.values]

dates = [d.date().isoformat() for d in disp.index]
out = {
    'meta': {
        'source': src,
        'as_of': last_date.date().isoformat(),
        'generated': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'lookbacks': LOOKBACKS,
        'default_weights': DEFAULT_W,
        'capacity_bn': CAPACITY_BN,
        'k': K, 'vol_span': VOL_SPAN,
        'n_obs': len(spx),
        'hist_start': full.index[0].date().isoformat(),
        'pctl_years': PCTL_YEARS,
        'pctl_start': p10.index[0].date().isoformat(),
        'pctl_n': int(len(p10)),
    },
    'series': {
        'dates': dates,
        'spx': [round(float(v), 2) for v in disp['SPX'].values],
        'pos': {k: ser(disp['pos_' + k]) for k in LOOKBACKS},
        'pctl': {k: ser(p10['pos_' + k]) for k in LOOKBACKS},
    },
    'snapshot': {
        'spx': round(cur_spx, 2),
        'net': round(cur_net, 4),
        'net_pct': round(cur_net * 100, 1),
        'net_bn': round(cur_net * CAPACITY_BN, 1),
        'net_1w': round(at(5), 4) if at(5) is not None else None,
        'net_1m': round(at(21), 4) if at(21) is not None else None,
        'net_3m': round(at(63), 4) if at(63) is not None else None,
        'percentile': round(pct_rank, 1),
        'percentile_10y': round(pct_rank_10y, 1),
        'regime': regime(cur_net),
        'ann_vol': round(ann_vol, 1),
        'cur_vol_d': round(cur_vol_d, 6),
        'sigma_m_pct': round(float(sigma_m) * 100, 2),
        'hist_mean': round(float(full['net'].mean()), 4),
        'hist_max': round(float(full['net'].max()), 4),
        'hist_min': round(float(full['net'].min()), 4),
        'per_horizon': {k: round(float(posdf[k].loc[last_date]), 3) for k in LOOKBACKS},
        'ph_1w': {k: round(float(posdf[k].iloc[-6]), 3) for k in LOOKBACKS} if len(posdf) > 6 else None,
        'ph_1m': {k: round(float(posdf[k].iloc[-22]), 3) for k in LOOKBACKS} if len(posdf) > 22 else None,
        'z_horizon': {k: round(float(zdf[k].loc[last_date]), 2) for k in LOOKBACKS},
    },
    'triggers': {
        'horizon': triggers,
        'flip_short': round(flip_short, 1) if flip_short else None,
        'heavy_sell': round(heavy_sell, 1) if heavy_sell else None,
        'ma': {k: round(v, 1) for k, v in ma.items()},
    },
    'projection': {
        'dates': proj_dates,
        'hist': proj_hist,
        'scenarios': {s: {'perh': {k: [round(x, 4) for x in proj[s]['perh'][k]] for k in LOOKBACKS},
                          'meta': scen_meta[s],
                          'flow_bn': round(proj[s]['flow_bn'], 1)} for s in SCEN},
    },
}

with open('data.json', 'w') as f:
    json.dump(out, f)

print('--- SNAPSHOT ---', file=sys.stderr)
print(f"As of {out['meta']['as_of']}  SPX {cur_spx:.0f}", file=sys.stderr)
print(f"Net exposure: {cur_net*100:+.1f}% of max  ({regime(cur_net)})  ~${cur_net*CAPACITY_BN:+.1f}bn", file=sys.stderr)
print(f"Percentile 10y: {pct_rank_10y:.0f}th  (window {p10.index[0].date()}..{p10.index[-1].date()}, n={len(p10)})  |  15y: {pct_rank:.0f}th   AnnVol {ann_vol:.1f}%   MonthlyVol {sigma_m*100:.1f}%", file=sys.stderr)
print(f"Per-horizon pos: " + '  '.join(f'{k}:{posdf[k].loc[last_date]:+.2f}' for k in LOOKBACKS), file=sys.stderr)
print(f"Flip-to-seller level: {flip_short:.0f}   Heavy-sell (-50%): {heavy_sell:.0f}", file=sys.stderr)
print('Horizon triggers (price L days ago):', {k: round(v) for k, v in triggers.items()}, file=sys.stderr)
print('1m scenario flows ($bn):', {s: round(proj[s]['flow_bn'], 1) for s in SCEN}, file=sys.stderr)
print('JSON bytes:', len(json.dumps(out)), file=sys.stderr)
