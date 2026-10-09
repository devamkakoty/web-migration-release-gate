# Staging Migration Release Gate

**Synthetic, AI-assisted portfolio proof. Not client work.**

A small, inspectable proof for a fixed-price **staging-only website migration QA**
offer: turn an agreed URL map into a clear hold/pass report, with redirect evidence,
missing page markers and missing form/CTA elements identified before handoff.
Zero third-party dependencies. Python 3.10 or newer.

## Offer And Acceptance

For an agreed batch of up to 100 routes, the deliverable is a route-by-route
JSON/CSV evidence bundle, a readable Markdown blocker list, and an agreed
recheck against the same manifest after the buyer's team makes corrections.
Price, route count, environments and recheck count are agreed before work.
This proof is not a client engagement, a quote, a measured client result, or
evidence that a production migration has been approved.

Acceptance for this automated slice: every unique supplied route reaches its
expected terminal URL/status, its requested title marker and static element
exist, and no duplicate, blocked redirect or incomplete check remains.
The buyer retains the actual release decision.

## Run Locally

From this directory:

```powershell
python -B -m unittest discover -s . -p "test_*.py" -v
python -B demo.py
```

The demo starts an ephemeral HTTP fixture on `127.0.0.1`, generates a manifest
from `samples/manifest.csv`, invokes the real CLI, verifies the result, then
stops the fixture. It never requests a remote target. The `.invalid` redirect
in the fixture is deliberately blocked before lookup or connection.

Expected demo: **HOLD, 3 passed / 10 records, 7 failed, 14 local GET attempts**.
This is intentional: it demonstrates status mismatch, missing title marker,
missing form, destination drift, off-origin redirect, redirect loop and a
duplicate route. The child gate exits **1**; the demo runner exits **0** only
when that expected result and fixture request count are verified.

One generated bundle lives in `demo-output/`:

- `manifest.csv`: actual run input, with its ephemeral loopback origin.
- `reports/report.json`: schema version, scope, summary, per-route evidence.
- `reports/report.csv`: spreadsheet-safe flat results and JSON hop history.
- `reports/report.md`: human-readable hold/pass and blocker handoff.
- `provenance.json`: runtime, commands, source/output hashes and verification.

Outputs are never overwritten. To rerun, use a new directory:

```powershell
python -B demo.py --out demo-rerun
```

Dates and ephemeral ports vary; expected outcomes/counts do not. The captured
manifest refers to a fixture that has stopped; regenerate instead of replaying
its old port.

## Manifest Contract

UTF-8 CSV, optionally with a BOM; exact headers in any order:

| Column | Meaning |
| --- | --- |
| `old_url` | Absolute authorized staging URL to request. |
| `expected_url` | Expected terminal URL, not the first redirect target. |
| `expected_status` | Terminal HTTP status, 200-599 excluding followed redirect codes. |
| `title_marker` | Case-sensitive substring in the first HTML title; blank skips it. |
| `selector` | Optional column. Blank skips element checking. |

The deliberately small, reviewable sample is a fixture template, not a captured
dataset. `{origin}` is expanded by `demo.py`, not by the gate CLI.

Selectors support a single tag, `#id`, `.class`, or compound such as
`form#contact-form` or `a#book.cta.primary`. Tag matching is case-insensitive;
IDs/classes are case-sensitive. No combinators, attributes, pseudo-classes,
selector lists or JavaScript evaluation. Unsupported syntax is an input error,
not a false "missing element" result.

Title text is entity-decoded and whitespace-normalized before comparison.
An absent title fails a requested marker. A status-only check can intentionally
accept a 404 or 410. Marker/selector checks require an HTML Content-Type;
declared charset is honored, otherwise UTF-8 is required. Compressed responses
are rejected; the client asks for identity encoding.

URL comparison lowercases scheme/host, removes default ports and treats an
empty path as `/`. It preserves path case, query order, percent-encoding and
trailing slash differences. Credentials, fragments, whitespace, backslashes
and non-ASCII URLs are rejected; use ASCII hostnames and percent-encoded paths.
Duplicate normalized old URLs, even with conflicting expectations, produce
failed/skipped rows referring to the first record. Records are numbered from 2
after the header; a multiline field does not change record numbering.

## Authorized Staging Use

The CLI is capable of reading explicitly authorized staging URLs, but **only
the local synthetic fixture was used for this portfolio proof**. It cannot
infer ownership or staging status from a hostname. The operator must verify
authorization for every allowed origin and safe GET behavior of each route.

Illustrative command, not an instruction to contact this placeholder:

```powershell
python -B gate.py agreed-manifest.csv `
  --allow-origin https://staging.example.invalid `
  --authorized-staging `
  --timeout 3 --max-redirects 5 `
  --out reviewed-run
```

Repeat `--allow-origin` for separately authorized origins. Matching includes
scheme, hostname and effective port: subdomains, other ports and alternate host
spellings are not implicitly trusted. Both old/expected URLs must be allowed.
Each redirect is checked before fetching; HTTPS-to-HTTP downgrades are refused
even if both origins are allowed. The client follows 301/302/303/307/308 and
records bounded Location values, loops, missing/invalid/ambiguous Location,
budget exhaustion and transport failures. No automatic retries or proxies.
TLS uses Python's default certificate verification.

`--synthetic` additionally restricts allowed hosts to literal `127.0.0.1` and
labels the report as synthetic. It does not prove who owns a local service.
The demo itself owns its fixture. Tests guard real socket connections so they
cannot silently contact non-fixture hosts; timeout/DNS/TLS failures are
deterministically injected, not produced by contacting outside services.

## Bounds And Exit Codes

| Limit | Contract |
| --- | --- |
| Input | 1 MiB file; 100 data records; 2,048 characters per URL/field. |
| Markers | 256-character title marker; 128-character selector. |
| Network | Sequential GET only; zero retries; 0-10 redirects, default 5. |
| Timeout | 0.05-10 seconds per hop, default 3; socket/body deadline checks. |
| Body | 128 KiB maximum per terminal response; never retained in reports. |
| Output | At most 2 MiB per JSON/CSV/Markdown file; error instead of silent omission. |
| Text | Observed title capped at 512 characters with a truncation flag; full title checked. |

System DNS resolution can outlast the socket timeout. This is not a hard
whole-run deadline or a sandbox against malicious servers/DNS rebinding.
Origin authorization remains the operator's responsibility. Sequential,
bounded requests are not permission to run against arbitrary websites.

- **0:** all supplied checks passed and the report bundle was written.
- **1:** report written with HOLD; at least one failed, skipped or blocked row.
- **2:** invalid input/configuration or output failure. Do not approve a release
  from a partial bundle left by an I/O failure.

All manifest syntax is preflighted before networking. Reports may only be
written to a new directory strictly inside this proof, including resolved-path
checks. This prevents accidental overwriting of the manifest or prior evidence.
There is no stdout dump of page bodies or datasets.

CSV fields are quoted and leading `=`, `+`, `-`, `@` formula triggers are
apostrophe-prefixed, including after whitespace/control/format characters.
Leading tab/CR/LF are also protected. JSON preserves original values; do not
remove the CSV protection when importing or re-exporting untrusted content.
Markdown table cells escape HTML/formatting and shorten long values.

## Explicit Boundaries

- Authorized staging targets only; no production requests or mutation.
- No security scanning, vulnerability probing, load or performance testing.
- No form submissions, button clicks, login, cookies, credentials or auth bypass.
- Static HTML only. Presence is not proof of visibility, enabled state,
  deliverability, payments, working integrations or successful submission.
- No visual/browser, accessibility, sitemap discovery, robots, SEO ranking,
  analytics, TLS configuration audit or universal migration-completeness claim.
- GET-only behavior does not guarantee a server has no side effects; exclude
  unsafe routes. Protect the local reports if real staging URLs contain secrets.
- No third-party downloads, telemetry, remote repository, publishing or outreach.

`test_gate.py` covers actual local HTTP and CLI exits plus deterministic
transport/error and report-escaping cases. `VALIDATION.md` records the executed
runtime matrix and exact results for this generated proof.
