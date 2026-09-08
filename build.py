#!/usr/bin/env python3
"""
Build both systematic-positioning dashboards into ./public for GitHub Pages.

Runs each model (which fetches fresh SPX data: Yahoo -> FRED fallback), injects the
freshly computed JSON into its single-file template, and writes the self-contained HTML.
Also writes a small index.html landing page with the current readings + last-refresh time.

Run locally:  python build.py
In CI:        invoked by .github/workflows/refresh.yml on a weekly schedule.
"""
import subprocess, json, sys, datetime, pathlib

ROOT = pathlib.Path(__file__).resolve().parent
PUB = ROOT / 'public'
PUB.mkdir(exist_ok=True)

# (label, model script, data file it writes, template, output filename, snapshot reading fn)
JOBS = [
    {
        'name': 'S&P 500 · CTA Trend Positioning',
        'script': 'build_cta.py', 'data': 'data.json',
        'template': 'template_cta.html', 'out': 'sp500_cta_positioning.html',
        'reading': lambda s, m: f"Net {s['net_pct']:+.0f}% of max long · ${s['net_bn']:+.0f}bn · "
                                f"{s['regime']} · {int(round(s['percentile_10y']))}th pctile (10y) · vol {s['ann_vol']:.1f}%",
    },
    {
        'name': 'S&P 500 · Vol-Control Positioning',
        'script': 'build_vol.py', 'data': 'data_vol.json',
        'template': 'template_vol.html', 'out': 'sp500_volcontrol_positioning.html',
        'reading': lambda s, m: f"Implied exposure {s['exp_pct']:.0f}% · ${s['notional_bn']:.0f}bn · "
                                f"{s['regime']} · {int(round(s['percentile_10y']))}th pctile (10y) · realised vol {s['rvol']:.1f}%",
    },
]


def run_job(job):
    print(f"\n=== {job['name']}: running {job['script']} ===", flush=True)
    subprocess.run([sys.executable, job['script']], cwd=ROOT, check=True)
    data = (ROOT / job['data']).read_text()
    tpl = (ROOT / job['template']).read_text()
    if tpl.count('__DATA__') != 1:
        raise SystemExit(f"template {job['template']} must contain exactly one __DATA__ placeholder")
    (PUB / job['out']).write_text(tpl.replace('__DATA__', data))
    d = json.loads(data)
    s, m = d['snapshot'], d['meta']
    reading = job['reading'](s, m)
    print(f"    wrote public/{job['out']}  (as_of {m['as_of']})  ->  {reading}", flush=True)
    return {'name': job['name'], 'out': job['out'], 'as_of': m['as_of'], 'reading': reading}


def write_index(results, refreshed):
    cards = "\n".join(f"""
      <a class="card" href="{r['out']}">
        <div class="c-top"><span class="c-name">{r['name']}</span><span class="c-asof">data {r['as_of']}</span></div>
        <div class="c-read">{r['reading']}</div>
        <div class="c-open">Open dashboard &rarr;</div>
      </a>""" for r in results)
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Systematic Positioning Dashboards</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root{{--bg:#fbfaf6;--panel:#fff;--ink:#16181d;--mut:#5b6675;--dim:#8893a3;--faint:#aeb7c4;
    --line:#e7e3d8;--acc:#ea580c;--acc2:#c2410c;--mono:'IBM Plex Mono',monospace;--disp:'Space Grotesk',system-ui,sans-serif;}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--ink);font-family:var(--disp);padding:48px 22px;line-height:1.5;
    -webkit-font-smoothing:antialiased}}
  .wrap{{max-width:760px;margin:0 auto}}
  .eyebrow{{font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--acc2);margin-bottom:10px}}
  h1{{font-size:30px;font-weight:600;letter-spacing:-.02em;margin-bottom:8px}}
  .sub{{color:var(--mut);font-size:14.5px;max-width:620px;margin-bottom:6px}}
  .refreshed{{font-family:var(--mono);font-size:11.5px;color:var(--dim);margin-bottom:30px}}
  .cards{{display:grid;gap:14px}}
  .card{{display:block;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px 22px;
    text-decoration:none;color:inherit;transition:border-color .15s,transform .15s,box-shadow .15s}}
  .card:hover{{border-color:var(--acc);transform:translateY(-2px);box-shadow:0 8px 26px rgba(22,24,29,.07)}}
  .c-top{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:9px}}
  .c-name{{font-size:18px;font-weight:600;letter-spacing:-.01em}}
  .c-asof{{font-family:var(--mono);font-size:10.5px;color:var(--faint);white-space:nowrap}}
  .c-read{{font-family:var(--mono);font-size:12.5px;color:var(--mut);margin-bottom:12px}}
  .c-open{{font-family:var(--mono);font-size:11.5px;color:var(--acc2);font-weight:600}}
  .foot{{margin-top:34px;font-family:var(--mono);font-size:10.5px;color:var(--faint);line-height:1.6;
    border-top:1px solid var(--line);padding-top:16px}}
</style></head><body><div class="wrap">
  <div class="eyebrow">Systematic Flow Monitor</div>
  <h1>S&amp;P 500 Positioning Dashboards</h1>
  <div class="sub">Self-contained reconstructions of systematic-fund equity positioning. Rebuilt automatically each week from fresh market data.</div>
  <div class="refreshed">Last refreshed: {refreshed}</div>
  <div class="cards">{cards}
  </div>
  <div class="foot">Independent quantitative reconstructions for research and educational use only; not investment advice, and not affiliated with any third-party research provider. Data via Yahoo Finance (FRED fallback).</div>
</div></body></html>"""
    (PUB / 'index.html').write_text(html)
    print(f"\n    wrote public/index.html", flush=True)


def main():
    results = [run_job(j) for j in JOBS]
    refreshed = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    write_index(results, refreshed)
    print(f"\nBuild complete: {len(results)} dashboards + index  ->  {PUB}", flush=True)


if __name__ == '__main__':
    main()
