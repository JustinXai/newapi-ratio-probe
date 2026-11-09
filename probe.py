#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
newapi-ratio-probe — inspect public model ratios on one-api / new-api compatible
LLM gateways (often called "API 中转站" in Chinese communities).

Why this exists
---------------
Aggregators advertise "0.1x of official price". That number is `model_ratio`,
not a discount. This tool reads each gateway's public `/api/pricing` endpoint
(no auth, no keys) and normalises the ratios so you can compare sites
model-by-model instead of trusting a homepage banner.

What `model_ratio` actually means
---------------------------------
In new-api, billing is: `price = official_price * model_ratio * group_ratio`.
A ratio of 1.0 corresponds to roughly $0.002 / 1K tokens, i.e. ~$2 / 1M tokens.
So ratio 0.5 is not "half of GPT-4o"; it is "half of the $2/M reference".
Always compare the same model across sites — never compare ratio to a vendor's
USD price list.

Usage
-----
    # probe a list of domains (one per line, host only, no scheme)
    python probe.py --domains domains.txt

    # inline domains, 16 workers, write JSON
    python probe.py --domains-api a.example.com,b.example.com --workers 16 -o out.json

    # include a per-model dump instead of only per-family aggregates
    python probe.py --domains domains.txt --models

Design notes
------------
* No credentials are used. The endpoint is the same one the gateway's own
  front-end calls to render its pricing page.
* A site that fails over both https and http is reported as unreachable rather
  than silently skipped, because "unreachable" is itself a useful signal
  (many gateways die after a few weeks).
* Ratios are aggregated by family with median + min, because a single gateway
  usually lists dozens of variants of the same model and averages are skewed by
  them.
"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# Explicit prefixes only: a loose regex would fold grok / glm into gpt.
# `gpt-?\d` covers the whole GPT-4/5/6 line (gpt-5.6-sol, gpt-6-astra, gpt-6.1-terra...).
# The o1/o3/o4 entries are 2025-era models that still show up as legacy stock on many
# gateways, so they are kept for completeness but are not the current lineup.
FAMILIES = [
    ("gpt", re.compile(r"^(gpt-?\d|chatgpt|gpt4|o[134](?![0-9a-z]))", re.I)),
    ("claude", re.compile(r"^claude", re.I)),
    ("gemini", re.compile(r"^gemini", re.I)),
    ("grok", re.compile(r"^grok", re.I)),
    ("deepseek", re.compile(r"^deepseek", re.I)),
    ("qwen", re.compile(r"^(qwen|qwq)", re.I)),
    ("glm", re.compile(r"^(glm|chatglm)", re.I)),
    ("kimi", re.compile(r"^(kimi|moonshot)", re.I)),
    ("doubao", re.compile(r"^doubao", re.I)),
]


def _ctx() -> ssl.SSLContext:
    # Many gateways serve a mismatched or self-signed cert. We are reading a
    # public pricing document, not sending secrets, so we accept it and say so.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


CTX = _ctx()


def fetch_pricing(domain: str, timeout: float = 12.0):
    """Return (ok, payload_or_error). Tries https, then http."""
    domain = domain.strip().rstrip("/")
    for scheme in ("https", "http"):
        url = f"{scheme}://{domain}/api/pricing"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, context=CTX, timeout=timeout) as r:
                if r.status != 200:
                    continue
                data = json.loads(r.read().decode("utf-8", "ignore"))
                if isinstance(data, dict) and (data.get("data") or data.get("models")):
                    return True, data
        except Exception as exc:  # network, TLS, JSON, HTTP error
            last = exc
    return False, locals().get("last")


def family_of(model: str):
    for name, pat in FAMILIES:
        if pat.match(model):
            return name
    return None


def normalise(data):
    """Extract (model, ratio) pairs from either known response shape.

    new-api returns {"data": [{"model_name": ..., "model_ratio": ...}, ...]}
    some forks nest it under {"data": {"models": ...}} — handle both.
    """
    rows = data.get("data") or data.get("models") or []
    if isinstance(rows, dict):
        rows = rows.get("models") or rows.get("data") or []
    pairs = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        name = item.get("model_name") or item.get("model") or item.get("name")
        ratio = item.get("model_ratio")
        if ratio is None:
            ratio = item.get("ratio")
        try:
            ratio = float(ratio)
        except (TypeError, ValueError):
            continue
        if not name or ratio <= 0:
            continue
        pairs.append((str(name), ratio))
    return pairs


def probe_one(domain: str, want_models: bool, timeout: float):
    ok, payload = fetch_pricing(domain, timeout)
    if not ok:
        return {"domain": domain, "ok": False, "error": str(payload)[:120]}
    pairs = normalise(payload)
    if not pairs:
        return {"domain": domain, "ok": False, "error": "no usable ratios"}

    per_family: dict[str, list[float]] = {}
    for name, ratio in pairs:
        fam = family_of(name)
        if fam:
            per_family.setdefault(fam, []).append(ratio)

    out = {"domain": domain, "ok": True, "models": len(pairs), "families": {}}
    for fam, ratios in per_family.items():
        out["families"][fam] = {
            "n": len(ratios),
            "median": round(statistics.median(ratios), 4),
            "min": round(min(ratios), 4),
        }
    if want_models:
        out["detail"] = [{"model": n, "ratio": round(r, 4)} for n, r in sorted(pairs)[:200]]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe model ratios on one-api/new-api gateways")
    ap.add_argument("--domains", help="file with one host per line")
    ap.add_argument("--domains-api", help="comma separated hosts")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--timeout", type=float, default=12.0)
    ap.add_argument("--models", action="store_true", help="include per-model ratios in output")
    ap.add_argument("-o", "--out", default="ratios.json")
    args = ap.parse_args()

    domains: list[str] = []
    if args.domains:
        with open(args.domains, encoding="utf-8") as fh:
            domains += [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    if args.domains_api:
        domains += [d.strip() for d in args.domains_api.split(",") if d.strip()]
    domains = sorted(set(domains))
    if not domains:
        ap.error("give --domains FILE or --domains-api a.com,b.com")

    print(f"probing {len(domains)} domains with {args.workers} workers", file=sys.stderr)
    started = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(probe_one, d, args.models, args.timeout): d for d in domains}
        for done, fut in enumerate(as_completed(futures), 1):
            res = fut.result()
            results.append(res)
            flag = "ok " if res["ok"] else "-- "
            detail = (f"{res['models']} models" if res["ok"] else res["error"][:60])
            print(f"[{done:>4}/{len(domains)}] {flag}{res['domain']:<34} {detail}", file=sys.stderr)

    results.sort(key=lambda r: (not r["ok"], r["domain"]))
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "probed": len(domains),
        "reachable": sum(1 for r in results if r["ok"]),
        "results": results,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    reachable = payload["reachable"]
    print(f"\n{reachable}/{len(domains)} reachable in {time.time()-started:.1f}s -> {args.out}",
          file=sys.stderr)
    if reachable:
        fams: dict[str, list[float]] = {}
        for r in results:
            for fam, agg in (r.get("families") or {}).items():
                fams.setdefault(fam, []).append(agg["median"])
        print(f"{'family':<10}{'sites':>6}{'median':>9}{'min':>8}", file=sys.stderr)
        for fam, vals in sorted(fams.items(), key=lambda kv: -len(kv[1])):
            print(f"{fam:<10}{len(vals):>6}{statistics.median(vals):>9.3f}{min(vals):>8.3f}",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
