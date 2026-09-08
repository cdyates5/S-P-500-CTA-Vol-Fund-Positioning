#!/usr/bin/env python3
"""
S&P 500 Volatility-Control Fund Positioning Model  ----  Tier1 Alpha-style replication.

Vol-control / target-volatility funds (managed-vol, risk-control indices, vol-target
sleeves) size equity exposure INVERSELY to realised volatility, targeting a constant
portfolio volatility:

  exposure_t = clamp( target_vol / realised_vol_t , 0 , max_leverage )

When realised vol rises, they mechanically de-lever (sell); when it falls, they re-lever
(buy). Exposure is convex in 1/vol, so vol crushes drive outsized buying and vol spikes
drive outsized selling. (Tier1 Alpha: a decline in 1-month realised vol -> vol-control
funds increase equity exposure.)

Methodology:
  1. Daily S&P 500 (^GSPC) closes, ~15y.
  2. Realised volatility at 3 windows (1m / 3m / 6m): annualised RMS of daily log returns
       sigma_W = sqrt( mean(r^2 over last W days) * 252 ) * 100      (zero-mean / RiskMetrics convention)
  3. Blend the windows (adjustable weights) -> realised vol estimate.
  4. exposure = clamp(target_vol / blended_vol, 0, max_lev)   reported as % allocation and $bn (AUM).
  5. Conditional 1-month projections: evolve realised vol forward under 5 vol regimes
       (Vol Spike / Up / Flat / Down / Crush, multiplicative on current 1m vol); as the
       forward window fills, realised vol -> scenario vol and exposure re-prices ->
       projected equity exposure path and implied buy / sell flow.
  6. Vol thresholds: the realised-vol levels at which exposure hits the leverage cap, 100%,
       50%, 25% (= target / exposure). Plus marginal flow sensitivity (~$bn per +1 vol pt).

Per-window realised-vol series are exported so the dashboard can re-blend the vol estimate
and re-price exposure live (weight sliders, target, max-leverage, AUM) without recomputing.
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
WINDOWS = {'1m': 21, '3m': 63, '6m': 126}
DEFAULT_W = {'1m': 0.50, '3m': 0.40, '6m': 0.10}   # weight the responsive short windows
TARGET_VOL = 12.0      # target annualised portfolio vol (%) - common managed-vol target (10-15% range)
MAX_LEV = 1.50         # leverage cap (150%) - levered target-vol / risk-control complex
AUM_BN = 350.0         # assumed vol-control equity complex AUM ($bn) - ~Morningstar estimate
ANN = 252

logp = np.log(spx)
ret = logp.diff()
r2 = ret ** 2

# per-window annualised realised vol (%), zero-mean RMS convention
vol = {}
for name, W in WINDOWS.items():
    vol[name] = np.sqrt(r2.rolling(W, min_periods=max(10, W // 2)).mean() * ANN) * 100
voldf = pd.DataFrame(vol)

def blend_vol(weights, frame=voldf):
    w = np.array([weights[k] for k in WINDOWS]); w = w / w.sum()
    return (frame[list(WINDOWS)] * w).sum(axis=1)

def exposure_from_vol(bv, target=TARGET_VOL, maxlev=MAX_LEV):
    return np.clip(target / bv, 0.0, maxlev)

blended = blend_vol(DEFAULT_W)
exp = exposure_from_vol(blended)        # fraction (0..max_lev)

full = pd.DataFrame({'SPX': spx, 'rvol': blended, 'exp': exp})
for k in WINDOWS:
    full['vol_' + k] = voldf[k]
full = full.dropna()

# display 2017+, keep full for stats; trailing 10y for percentile
disp = full[full.index >= '2017-01-01'].copy()
PCTL_YEARS = 10
cutoff10 = full.index[-1] - pd.DateOffset(years=PCTL_YEARS)
p10 = full[full.index >= cutoff10].copy()

# ----------------------------------------------------------------------------- current snapshot
last_date = full.index[-1]
cur_spx = float(spx.iloc[-1])
cur_rvol = float(blended.loc[last_date])
cur_exp = float(exp.loc[last_date])

def at_exp(days_ago):
    if len(full) > days_ago:
        return float(full['exp'].iloc[-1 - days_ago])
    return None

pct_rank = float((full['exp'] <= cur_exp).mean() * 100)
pct_rank_10y = float((p10['exp'] <= cur_exp).mean() * 100)

def regime(e):
    pct = e * 100
    if pct >= MAX_LEV * 100 - 2: return 'Max Leverage'
    if pct >= 115: return 'Levered Long'
    if pct >= 85:  return 'Fully Invested'
    if pct >= 55:  return 'Moderate'
    if pct >= 30:  return 'De-risking'
    if pct >= 12:  return 'Defensive'
    return 'De-risked'

# ----------------------------------------------------------------------------- vol thresholds + sensitivity
# realised-vol level (%) at which exposure hits each allocation (= target / exposure, capped)
def vol_for_exposure(e):
    return TARGET_VOL / e if e > 0 else None
thresholds = {
    'cap':  TARGET_VOL / MAX_LEV,     # at/below this vol -> capped at max leverage
    'full': TARGET_VOL / 1.00,        # 100% invested
    'half': TARGET_VOL / 0.50,        # 50%
    'quarter': TARGET_VOL / 0.25,     # 25%
}
# marginal flow: d(exposure)/d(vol) * AUM per +1 vol pt  =  -target/vol^2 * AUM
marg_per_volpt = -(TARGET_VOL / (cur_rvol ** 2)) * AUM_BN   # $bn per +1 vol pt (negative = selling)

# ----------------------------------------------------------------------------- conditional projections
H = 21                                  # 1 trading month forward
# scenarios: multiplicative on CURRENT 1-month realised vol
SCEN = {'Vol Spike': 2.00, 'Vol Up': 1.40, 'Flat': 1.00, 'Vol Down': 0.78, 'Vol Crush': 0.60}
SCEN_CTX = {'Vol Spike': 'crash / stress', 'Vol Up': 'choppy / risk-off', 'Flat': 'vol persists',
            'Vol Down': 'calming tape', 'Vol Crush': 'calm grind / melt-up'}
proj_dates = [(last_date + pd.tseries.offsets.BDay(i)).date().isoformat() for i in range(1, H + 1)]

cur_v1m = float(voldf['1m'].loc[last_date])
r2_hist = [float(x) for x in r2.dropna().iloc[-WINDOWS['6m']:].values]   # last 126 daily squared returns (decimal^2)

def sim_scenario(mult, weights=DEFAULT_W, target=TARGET_VOL, maxlev=MAX_LEV):
    fwd_ann = max(5.0, mult * cur_v1m)
    fwd_var_d = (fwd_ann / 100.0) ** 2 / ANN
    buf = {k: list(r2_hist[-WINDOWS[k]:]) for k in WINDOWS}
    exp_path, vol_path = [], []
    w = np.array([weights[k] for k in WINDOWS]); w = w / w.sum()
    for _ in range(H):
        bl = 0.0
        for wi, k in zip(w, WINDOWS):
            buf[k].append(fwd_var_d)
            if len(buf[k]) > WINDOWS[k]:
                buf[k].pop(0)
            vk = np.sqrt(np.mean(buf[k]) * ANN) * 100
            bl += wi * vk
        e = float(np.clip(target / bl, 0.0, maxlev))
        exp_path.append(e); vol_path.append(float(bl))
    return exp_path, vol_path, fwd_ann

proj = {}
for sname, mult in SCEN.items():
    ep, vp, fa = sim_scenario(mult)
    flow = (ep[-1] - cur_exp) * AUM_BN
    proj[sname] = {'exp': [round(x, 4) for x in ep], 'vol': [round(x, 2) for x in vp],
                   'fwd_vol': round(fa, 1), 'end_exp': round(ep[-1], 4), 'flow_bn': round(flow, 1)}

# recent actual exposure (gray history lead-in for projection)
lead = full['exp'].iloc[-40:]
proj_hist = {'dates': [d.date().isoformat() for d in lead.index],
             'exp_bn': [round(float(v) * AUM_BN, 2) for v in lead.values]}

# ----------------------------------------------------------------------------- assemble export
def ser(s, r=3):
    return [round(float(v), r) for v in s.values]

dates = [d.date().isoformat() for d in disp.index]
out = {
    'meta': {
        'source': src,
        'as_of': last_date.date().isoformat(),
        'generated': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'windows': WINDOWS,
        'default_weights': DEFAULT_W,
        'target_vol': TARGET_VOL,
        'max_lev': MAX_LEV,
        'aum_bn': AUM_BN,
        'ann': ANN,
        'n_obs': len(spx),
        'hist_start': full.index[0].date().isoformat(),
        'pctl_years': PCTL_YEARS,
        'pctl_start': p10.index[0].date().isoformat(),
        'pctl_n': int(len(p10)),
    },
    'series': {
        'dates': dates,
        'spx': [round(float(v), 2) for v in disp['SPX'].values],
        'vol': {k: ser(disp['vol_' + k], 2) for k in WINDOWS},        # per-window realised vol %, display
        'vol_pctl': {k: ser(p10['vol_' + k], 2) for k in WINDOWS},    # per-window realised vol %, 10y
    },
    'snapshot': {
        'spx': round(cur_spx, 2),
        'exp': round(cur_exp, 4),
        'exp_pct': round(cur_exp * 100, 1),
        'notional_bn': round(cur_exp * AUM_BN, 1),
        'rvol': round(cur_rvol, 2),
        'vol_1m': round(float(voldf['1m'].loc[last_date]), 2),
        'vol_3m': round(float(voldf['3m'].loc[last_date]), 2),
        'vol_6m': round(float(voldf['6m'].loc[last_date]), 2),
        'percentile': round(pct_rank, 1),
        'percentile_10y': round(pct_rank_10y, 1),
        'regime': regime(cur_exp),
        'per_window': {k: round(float(voldf[k].loc[last_date]), 2) for k in WINDOWS},
        'vw_1w': {k: round(float(voldf[k].iloc[-6]), 2) for k in WINDOWS} if len(voldf) > 6 else None,
        'vw_1m': {k: round(float(voldf[k].iloc[-22]), 2) for k in WINDOWS} if len(voldf) > 22 else None,
        'hist_mean': round(float(full['exp'].mean()), 4),
        'hist_max': round(float(full['exp'].max()), 4),
        'hist_min': round(float(full['exp'].min()), 4),
        'marg_per_volpt': round(marg_per_volpt, 1),
    },
    'thresholds': {k: round(v, 2) for k, v in thresholds.items()},
    'projection': {
        'dates': proj_dates,
        'hist': proj_hist,
        'r2_hist': [round(x, 8) for x in r2_hist],
        'cur_v1m': round(cur_v1m, 2),
        'scenarios': {s: {**proj[s], 'mult': SCEN[s], 'ctx': SCEN_CTX[s]} for s in SCEN},
    },
}

with open('data_vol.json', 'w') as f:
    json.dump(out, f)

print('--- SNAPSHOT ---', file=sys.stderr)
print(f"As of {out['meta']['as_of']}  SPX {cur_spx:.0f}", file=sys.stderr)
print(f"Implied exposure: {cur_exp*100:.1f}%  ({regime(cur_exp)})  ~${cur_exp*AUM_BN:.0f}bn  (AUM {AUM_BN:.0f})", file=sys.stderr)
print(f"Realised vol blended {cur_rvol:.1f}%  (1m {voldf['1m'].loc[last_date]:.1f}  3m {voldf['3m'].loc[last_date]:.1f}  6m {voldf['6m'].loc[last_date]:.1f})  target {TARGET_VOL:.0f}%  cap {MAX_LEV*100:.0f}%", file=sys.stderr)
print(f"Percentile 10y: {pct_rank_10y:.0f}th  (window {p10.index[0].date()}..{p10.index[-1].date()}, n={len(p10)})  |  15y: {pct_rank:.0f}th", file=sys.stderr)
print(f"Vol thresholds (%): cap@{thresholds['cap']:.1f}  100%@{thresholds['full']:.1f}  50%@{thresholds['half']:.1f}  25%@{thresholds['quarter']:.1f}   marginal {marg_per_volpt:.0f}bn / +1 vol pt", file=sys.stderr)
print('1m scenario flows ($bn):', {s: proj[s]['flow_bn'] for s in SCEN}, file=sys.stderr)
print('1m scenario fwd vol (%):', {s: proj[s]['fwd_vol'] for s in SCEN}, file=sys.stderr)
print('JSON bytes:', len(json.dumps(out)), file=sys.stderr)
