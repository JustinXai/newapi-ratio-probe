# newapi-ratio-probe

Probe the public `model_ratio` values of one-api / new-api compatible LLM gateways
(「API 中转站」), so you can compare them model-by-model instead of trusting a
homepage banner that says *"0.1x official price"*.

Zero dependencies. Standard library only. Python 3.8+.

---

## The problem this solves

Almost every LLM gateway advertises something like **"官方价 0.1 折"**. That number
is not a discount — it is `model_ratio`, a billing multiplier:

```
price = official_price × model_ratio × group_ratio
```

In new-api, `model_ratio = 1.0` corresponds to roughly **$0.002 / 1K tokens
(≈ $2 / 1M tokens)**. So:

| What the banner implies | What it actually means |
|---|---|
| "0.1x of GPT-4o" | 0.1 × the **$2/M reference**, not 0.1 × GPT-4o's price |
| "ratio 0.001, insanely cheap" | usually a **reverse-engineered / shared pool** — low concurrency, frequent drops |

Worse, gateways list dozens of variants per model, so a single "starting from"
number tells you nothing about the price of the model you actually want.

This tool reads the same public endpoint the gateway's own pricing page uses
(`/api/pricing`), then aggregates per model family with **median** and **min**.

---

## Usage

```bash
# one host per line, no scheme
python probe.py --domains domains.txt

# inline, more workers, custom output
python probe.py --domains-api a.example.com,b.example.com --workers 16 -o out.json

# also dump per-model ratios (capped at 200 models per site)
python probe.py --domains domains.txt --models
```

`domains.txt` example:

```
# comments and blank lines are ignored
api.example.com
gateway.example.net
```

## Output

```json
{
  "generated_at": "2026-11-09T09:12:44Z",
  "probed": 4,
  "reachable": 4,
  "results": [
    {
      "domain": "0ait.com",
      "ok": true,
      "models": 72,
      "families": {
        "gpt":    { "n": 18, "median": 1.875, "min": 0.625 },
        "claude": { "n": 6,  "median": 2.5,   "min": 2.0   },
        "gemini": { "n": 12, "median": 0.75,  "min": 0.625 }
      }
    }
  ]
}
```

A summary table is printed to stderr:

```
family     sites   median     min
gpt            4    1.875   0.625
claude         4    2.500   2.000
gemini         3    0.750   0.625
```

`ok: false` means the site did not answer over https **or** http — that is itself
useful data, because a large share of gateways disappear within weeks.

---

## Methodology and honest caveats

- **No credentials.** We call `/api/pricing`, the unauthenticated endpoint the
  site's own front-end uses to render prices. No keys, no accounts, no billing.
- **TLS verification is disabled** for the probe. Many gateways serve expired or
  mismatched certificates. We are reading a public price list, not sending
  secrets. Do not reuse this pattern for authenticated calls.
- **Ratios are comparable only within the same model.** Never compare a ratio to
  a vendor's USD price list, and never compare ratio across different gateways
  unless both list the same model name.
- **Median, not mean.** A single gateway often lists 30+ variants of one model;
  a mean is dominated by outliers.
- **A low ratio is not a recommendation.** 0.001-class ratios are typically
  reverse-proxy or shared pools. If you need stability, look at concurrency and
  uptime history instead of the cheapest number.
- **Politeness.** Default 12 workers, 12s timeout per request, https then http.
  Do not run this against a single host at high concurrency.

## Live data built with this method

A daily-updated ranking of these ratios (plus uptime and per-model detail) is
published here: **https://aiapiprice.com/api-transit**

That page is generated from this exact method, refreshed every day, and includes
the raw numbers so you can spot-check any of them. The site is independent and
does not sell gateway credits.

## License

MIT — see [LICENSE](LICENSE).
