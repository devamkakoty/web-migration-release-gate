# Staging Migration Release Gate

Synthetic / AI-assisted portfolio proof; not client work.

**Decision: HOLD** | 3/10 checks passed | 7 blockers | 14 GET attempts

Run: 2026-10-09T07:50:13.352897+00:00
Manifest SHA-256: `5f9e3a210fd355d03eabf054ad838f3019580c1f031154e20c5ad5168570847d`

This decision covers only the supplied manifest, not a production release authorization.
Selectors prove static element presence, not visibility or working form submission.

## Route Checks

| Record | Result | Old URL | Expected destination | Observed destination | Status expected / observed | Chain |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | PASS | http://127.0.0.1:63472/old-home | http://127.0.0.1:63472/home | http://127.0.0.1:63472/home | 200 / 200 | 301 &gt; 200 |
| 3 | PASS | http://127.0.0.1:63472/old-contact | http://127.0.0.1:63472/contact | http://127.0.0.1:63472/contact | 200 / 200 | 302 &gt; 308 &gt; 200 |
| 4 | PASS | http://127.0.0.1:63472/gone | http://127.0.0.1:63472/gone | http://127.0.0.1:63472/gone | 410 / 410 | 410 |
| 5 | FAIL | http://127.0.0.1:63472/wrong-status | http://127.0.0.1:63472/wrong-status | http://127.0.0.1:63472/wrong-status | 200 / 503 | 503 |
| 6 | FAIL | http://127.0.0.1:63472/missing-title | http://127.0.0.1:63472/missing-title | http://127.0.0.1:63472/missing-title | 200 / 200 | 200 |
| 7 | FAIL | http://127.0.0.1:63472/missing-cta | http://127.0.0.1:63472/missing-cta | http://127.0.0.1:63472/missing-cta | 200 / 200 | 200 |
| 8 | FAIL | http://127.0.0.1:63472/drift | http://127.0.0.1:63472/contact | http://127.0.0.1:63472/home | 200 / 200 | 301 &gt; 200 |
| 9 | FAIL | http://127.0.0.1:63472/external | http://127.0.0.1:63472/home | http://127.0.0.1:63472/external | 200 / 302 | 302 |
| 10 | FAIL | http://127.0.0.1:63472/loop-a | http://127.0.0.1:63472/home | http://127.0.0.1:63472/loop-b | 200 / 302 | 301 &gt; 302 |
| 11 | FAIL | http://127.0.0.1:63472/old-home | http://127.0.0.1:63472/home | - | 200 / - | not fetched |

## Findings

- Record 5 **status_mismatch**: Final HTTP status differs from the manifest.
- Record 6 **title_marker_missing**: Expected case-sensitive marker not found in the first title. Expected marker: Contact. Observed title: Default template.
- Record 7 **selector_missing**: Expected element not found in static HTML. Expected selector: form#contact-form.
- Record 8 **destination_mismatch**: Final URL does not exactly match the normalized expected URL.
- Record 9 **unexpected_origin**: Redirect leaves the allowlist; target was not requested. Blocked Location: https://not-authorized.invalid/collect.
- Record 10 **redirect_loop**: Redirect revisits an already requested URL.
- Record 11 **duplicate_entry**: Duplicate normalized old URL; skipped without another request. First occurrence: record 2.

## Handoff

Resolve each blocker against the agreed route map, or explicitly correct its expected value.
Rerun the same manifest after changes. Keep blocked redirects and transport failures unresolved
until verified; do not treat an unobserved page as passing.
Review forms, JavaScript routes, accessibility, visual layout and integrations separately.

Full URLs, expected markers/selectors, observed titles and hop evidence are in report.json
and report.csv. This Markdown view shortens long cells; response bodies are never retained.
