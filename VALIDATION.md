# Executed Validation

Date: October 9, 2026.
Working directory: repository root.
Platform: `Windows-11-10.0.26200-SP0`; demo interpreter: Python 3.13.3.

## Final Test Matrix

These are full local suites, not selected individual passes. Each process
printed its terminal `Ran 52 tests` and `OK` record and exited 0.
No skips, failures, errors or resource warnings were reported.

| Exact command | Tests | Suite time | Exit |
| --- | --- | --- | --- |
| `py -3.10 -B -W error::ResourceWarning -m unittest discover -s . -p 'test_*.py' -q` | 52 passed | 0.894s | 0 |
| `py -3.12 -B -W error::ResourceWarning -m unittest discover -s . -p 'test_*.py' -q` | 52 passed | 0.913s | 0 |
| `py -3.13 -B -W error::ResourceWarning -m unittest discover -s . -p 'test_*.py' -q` | 52 passed | 0.798s | 0 |

The tests include real loopback HTTP, subprocess CLI exits 0/1/2, all five
supported redirect codes, redirect budgets/loops, blocked origins, duplicates,
title/selector failures, timeout/network failure injection, body/input/report
bounds, CSV formula protection, Markdown escaping and output confinement.
Test temporary directories were created and removed inside this proof only.
`-B` prevented bytecode cache creation.

An initial Python 3.10 run completed 47 tests with one failure: its standard
URL parser accepted `http://[bad]/`. The validator was repaired to require
valid IPv6 for bracketed authorities, with regression cases retained.
The final matrix above includes the fix and all subsequently added tests.

## Generated Demo

Exact command: `py -3.13 -B demo.py`.

- Demo runner exit: **0**, expected result verified.
- Actual child CLI exit: **1**, intentional **HOLD**, not an all-pass claim.
- Results: **10 records, 3 passed, 7 failed, 14 local GET attempts**.
- Failure categories: status mismatch, missing title, missing selector,
  destination mismatch, blocked origin, redirect loop and duplicate entry.
- The local fixture shut down after completion.
- Output: one `demo-output/` bundle, five files, **19,973 bytes total**.
- JSON and CSV each contain exactly 10 result records.
- Post-run SHA-256 checks found **zero mismatches across 11 source/input/output
  hashes** recorded in `demo-output/provenance.json`.

The demo files were generated directly by `demo.py` and `gate.py`, not patched.
No downloaded data, external requests, remote repository, publishing or
outreach was involved.

## Candidate Identity

Source tree SHA-256:
`bf2f169fb3ea864b52e079dd04a4b2d777f84986ed44a0e3fe66344b33260833`

Definition: SHA-256 of compact, key-sorted JSON containing path/SHA-256 pairs
for `gate.py`, `synthetic_fixture.py`, `demo.py`, `test_gate.py`, `README.md`,
`.gitignore` and `samples/manifest.csv`. Individual hashes and exact child CLI
arguments are in `demo-output/provenance.json`. This post-run validation note
and generated outputs are excluded from that source-tree fingerprint.

Base/upstream SHA: not applicable. This is a new standalone proof directory.

## Remaining Boundaries

Only Windows and the three runtime versions above were executed. There is no
claim of Linux/macOS, hosted CI, browser/JavaScript or real staging validation.
No security/load tests or production checks were performed. Timeout, DNS and
TLS failure tests use deterministic injected failures, not remote services.
No separate lint/type-check or packaging/build step is configured; this
standard-library CLI runs directly from source.

The README defines the authorized-target, GET-only, static-element,
timeout/DNS and release-approval limitations. This is synthetic, AI-assisted
portfolio evidence, not client work or production release authorization.
