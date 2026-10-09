"""GET-only staging migration checks. Python 3.10+, standard library only."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import http.client
import io
import ipaddress
import json
import re
import socket
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ROWS = 100
MAX_URL = 2048
MAX_BODY_BYTES = 128 * 1024
MAX_REPORT_BYTES = 2 * 1024 * 1024
REDIRECTS = {301, 302, 303, 307, 308}
COLUMNS = ("old_url", "expected_url", "expected_status", "title_marker", "selector")
SELECTOR = re.compile(
    r"(?P<tag>[A-Za-z][A-Za-z0-9-]*)?"
    r"(?P<id>#[A-Za-z_][A-Za-z0-9_-]*)?"
    r"(?P<classes>(?:\.[A-Za-z_][A-Za-z0-9_-]*)*)"
)


class InputError(ValueError):
    """Invalid input or output configuration; no passing report is possible."""


class CheckError(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def normalize_url(value: str) -> str:
    if not value or len(value) > MAX_URL:
        raise InputError("URLs must contain 1 to 2048 characters.")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise InputError("URL whitespace and control characters are not supported.")
    if not value.isascii() or "\\" in value:
        raise InputError("Use ASCII URLs with percent-encoded paths; no backslashes.")
    try:
        parts = urlsplit(value)
        host = parts.hostname
        if parts.scheme not in {"http", "https"} or not host:
            raise ValueError
        if parts.username is not None or parts.password is not None or "#" in value:
            raise ValueError
        if "%" in host or parts.netloc.endswith(":"):
            raise ValueError
        if ":" in host or "[" in parts.netloc or "]" in parts.netloc:
            host = "[" + str(ipaddress.IPv6Address(host)) + "]"
        elif not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
            raise ValueError
        port = parts.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        default_port = 80 if parts.scheme == "http" else 443
        netloc = host if port in (None, default_port) else f"{host}:{port}"
        return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))
    except ValueError as exc:
        raise InputError("Use an HTTP(S) URL without credentials or fragments.") from exc


def origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def normalize_origin(value: str) -> str:
    url = normalize_url(value)
    parts = urlsplit(url)
    if parts.path != "/" or parts.query or "?" in value:
        raise InputError("--allow-origin must be scheme://host[:port], with no path/query.")
    return origin(url)


def parse_selector(value: str) -> tuple[str, str, set[str]] | None:
    if not value:
        return None
    match = SELECTOR.fullmatch(value)
    if len(value) > 128 or match is None:
        raise InputError("Selector must be a single tag, #id, .class, or compound thereof.")
    return (
        (match["tag"] or "").lower(),
        (match["id"] or "")[1:],
        set(filter(None, match["classes"].split("."))),
    )


@dataclass(frozen=True)
class Entry:
    row: int
    old_url: str
    expected_url: str
    expected_status: int
    title_marker: str
    selector: str


def load_manifest(path: Path) -> tuple[list[Entry], str]:
    with path.open("rb") as source:
        raw = source.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise InputError("Manifest exceeds the 1 MiB limit.")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        headers = reader.fieldnames or []
        if (
            len(headers) != len(set(headers))
            or not set(COLUMNS[:-1]).issubset(headers)
            or not set(headers).issubset(COLUMNS)
        ):
            raise InputError("Expected four required headers and optional selector; see README.")
        entries = []
        for number, row in enumerate(reader, start=2):
            if len(entries) >= MAX_ROWS:
                raise InputError("Manifest exceeds the 100-row limit.")
            if None in row or any(value is None for value in row.values()):
                raise InputError(f"Record {number}: wrong number of CSV fields.")
            if any(len(value) > MAX_URL for value in row.values()):
                raise InputError(f"Record {number}: field exceeds 2048 characters.")
            status = row["expected_status"]
            marker = row["title_marker"]
            selector = row.get("selector", "")
            if not re.fullmatch(r"[2-5][0-9]{2}", status) or int(status) in REDIRECTS:
                raise InputError(f"Record {number}: expected_status must be a terminal HTTP status.")
            if len(marker) > 256 or (marker and not marker.strip()):
                raise InputError(f"Record {number}: title marker must be nonblank and <=256 characters.")
            parse_selector(selector)
            entries.append(Entry(
                number, normalize_url(row["old_url"]), normalize_url(row["expected_url"]),
                int(status), marker, selector,
            ))
    except (UnicodeError, csv.Error) as exc:
        raise InputError("Manifest must be well-formed UTF-8 CSV.") from exc
    if not entries:
        raise InputError("Manifest has no data rows.")
    return entries, hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Response:
    status: int
    location: str | None = None
    content_type: str = ""
    content_encoding: str = ""
    body: bytes = b""


def fetch_http(url: str, timeout: float) -> Response:
    """One hop only: no proxy, redirect handler, cookies, auth, or retries."""
    parts = urlsplit(url)
    connection_type = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(parts.hostname, parts.port, timeout=timeout)
    deadline = time.monotonic() + timeout
    try:
        connection.request(
            "GET", urlunsplit(("", "", parts.path or "/", parts.query, "")),
            headers={"User-Agent": "StagingMigrationGate/1.0", "Accept-Encoding": "identity"},
        )
        with connection.getresponse() as response:
            locations = response.headers.get_all("Location", [])
            if response.status in REDIRECTS and len(locations) > 1:
                raise CheckError("ambiguous_redirect", "Multiple Location headers; not followed.")
            body = bytearray()
            if response.status not in REDIRECTS:
                # read1 avoids waiting for the full budget on slow streaming bodies.
                # Socket timeouts plus a monotonic deadline bound body consumption.
                while len(body) <= MAX_BODY_BYTES:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError
                    if connection.sock is not None:
                        connection.sock.settimeout(remaining)
                    chunk = response.read1(min(16384, MAX_BODY_BYTES + 1 - len(body)))
                    if not chunk:
                        break
                    body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    raise CheckError("body_too_large", "Response exceeds 128 KiB; not evaluated.")
                if response.length not in (None, 0):
                    raise CheckError("incomplete_body", "Response ended before its declared length.")
            return Response(
                response.status, locations[0] if locations else None,
                response.getheader("Content-Type", ""),
                response.getheader("Content-Encoding", ""), bytes(body),
            )
    except (socket.timeout, TimeoutError) as exc:
        raise CheckError("timeout", "Request exceeded its timeout; no retry was attempted.") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise CheckError("network_error", f"Request failed ({type(exc).__name__}); no retry.") from exc
    finally:
        connection.close()


class PageProbe(HTMLParser):
    CDATA_CONTENT_ELEMENTS = ("script", "style", "title", "textarea")

    def __init__(self, selector: str):
        super().__init__(convert_charrefs=True)
        self.selector = parse_selector(selector)
        self.found = False
        self.in_title = False
        self.seen_title = False
        self.title_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "title" and not self.seen_title:
            self.seen_title = self.in_title = True
        if self.selector:
            wanted_tag, wanted_id, wanted_classes = self.selector
            attributes = dict(attrs)
            self.found |= (
                (not wanted_tag or wanted_tag == tag)
                and (not wanted_id or wanted_id == attributes.get("id"))
                and wanted_classes.issubset(set((attributes.get("class") or "").split()))
            )

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str:
        return " ".join(html.unescape("".join(self.title_parts)).split())


def inspect_page(response: Response, selector: str) -> PageProbe:
    if response.content_encoding.strip().lower() not in {"", "identity"}:
        raise CheckError("unsupported_encoding", "Compressed content is not evaluated.")
    content = Message()
    content["Content-Type"] = response.content_type
    if content.get_content_type() not in {"text/html", "application/xhtml+xml"}:
        raise CheckError("not_html", "Marker/selector checks require an HTML Content-Type.")
    try:
        text = response.body.decode(content.get_content_charset() or "utf-8", errors="strict")
    except (LookupError, UnicodeError) as exc:
        raise CheckError("decode_error", "Unsupported charset or invalid encoded page content.") from exc
    probe = PageProbe(selector)
    try:
        probe.feed(text)
        probe.close()
    except (AssertionError, ValueError) as exc:
        raise CheckError("html_parse_error", "HTML parser could not evaluate this response.") from exc
    return probe


@dataclass
class Result:
    row: int
    old_url: str
    expected_url: str
    expected_status: int
    title_marker: str
    selector: str
    outcome: str = "PASS"
    actual_url: str | None = None
    actual_status: int | None = None
    last_requested_url: str | None = None
    actual_title: str | None = None
    title_truncated: bool = False
    selector_found: bool | None = None
    duplicate_of: int | None = None
    attempts: int = 0
    chain: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)

    def fail(self, code: str, detail: str):
        self.outcome = "FAIL"
        self.issues.append({"code": code, "detail": detail})


def check_entry(entry: Entry, allowed: set[str], timeout: float, max_redirects: int) -> Result:
    result = Result(**asdict(entry))
    if origin(entry.old_url) not in allowed or origin(entry.expected_url) not in allowed:
        result.fail("unexpected_origin", "Old and expected URLs must both be explicitly allowed.")
        return result
    current = entry.old_url
    visited: set[str] = set()
    while True:
        visited.add(current)
        result.attempts += 1
        result.last_requested_url = current
        try:
            response = fetch_http(current, timeout)
        except CheckError as exc:
            result.fail(exc.code, exc.detail)
            return result
        result.actual_url, result.actual_status = current, response.status
        result.chain.append({"url": current, "status": response.status})
        if response.status in REDIRECTS and response.location is not None:
            result.chain[-1]["location"] = response.location[:MAX_URL]
            result.chain[-1]["location_truncated"] = len(response.location) > MAX_URL
        if response.status not in REDIRECTS:
            break
        if not response.location:
            result.fail("missing_location", "Redirect has no Location header.")
            return result
        try:
            # Validate the raw reference before urljoin can discard unsafe whitespace.
            if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in response.location):
                raise InputError("Unsafe redirect reference.")
            target = normalize_url(urljoin(current, response.location))
        except (InputError, ValueError):
            result.fail("invalid_redirect", "Redirect target is not a supported HTTP(S) URL.")
            return result
        if origin(target) not in allowed:
            result.fail("unexpected_origin", "Redirect leaves the allowlist; target was not requested.")
            return result
        if urlsplit(current).scheme == "https" and urlsplit(target).scheme == "http":
            result.fail("https_downgrade", "HTTPS-to-HTTP redirect refused, even to an allowed origin.")
            return result
        if target in visited:
            result.fail("redirect_loop", "Redirect revisits an already requested URL.")
            return result
        if len(result.chain) > max_redirects:
            result.fail("redirect_limit", "Redirect budget exhausted; next target was not requested.")
            return result
        current = target
    if current != entry.expected_url:
        result.fail("destination_mismatch", "Final URL does not exactly match the normalized expected URL.")
    if response.status != entry.expected_status:
        result.fail("status_mismatch", "Final HTTP status differs from the manifest.")
    if entry.title_marker or entry.selector:
        try:
            probe = inspect_page(response, entry.selector)
            result.actual_title = probe.title[:512]
            result.title_truncated = len(probe.title) > 512
            if entry.title_marker and " ".join(entry.title_marker.split()) not in probe.title:
                result.fail("title_marker_missing", "Expected case-sensitive marker not found in the first title.")
            if entry.selector:
                result.selector_found = probe.found
                if not probe.found:
                    result.fail("selector_missing", "Expected element not found in static HTML.")
        except CheckError as exc:
            result.fail(exc.code, exc.detail)
    return result


def audit(entries: list[Entry], digest: str, allowed: set[str], timeout: float = 3.0,
          max_redirects: int = 5, synthetic: bool = False) -> dict:
    seen: dict[str, int] = {}
    results = []
    for entry in entries:
        if entry.old_url in seen:
            result = Result(**asdict(entry), duplicate_of=seen[entry.old_url])
            result.fail("duplicate_entry", "Duplicate normalized old URL; skipped without another request.")
        else:
            seen[entry.old_url] = entry.row
            result = check_entry(entry, allowed, timeout, max_redirects)
        results.append(asdict(result))
    passed = sum(result["outcome"] == "PASS" for result in results)
    return {
        "schema_version": "1.0",
        "tool": "staging-migration-release-gate",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_label": "Synthetic / AI-assisted portfolio proof; not client work."
        if synthetic else "Authorized staging check using an AI-assisted portfolio tool.",
        "manifest_sha256": digest,
        "scope": {"allowed_origins": sorted(allowed), "method": "GET", "retries": 0,
                  "timeout_seconds_per_hop": timeout, "max_redirects": max_redirects,
                  "max_rows": MAX_ROWS, "max_body_bytes": MAX_BODY_BYTES},
        "gate": "PASS" if passed == len(results) else "HOLD",
        "summary": {"total": len(results), "passed": passed, "failed": len(results) - passed,
                    "requests_attempted": sum(result["attempts"] for result in results)},
        "results": results,
    }


def csv_safe(value) -> str:
    text = "" if value is None else str(value)
    probe = text
    while probe and (probe[0].isspace() or unicodedata.category(probe[0]) in {"Cc", "Cf"}):
        probe = probe[1:]
    if text.startswith(("\t", "\r", "\n")) or probe.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def markdown_cell(value, limit=110) -> str:
    text = " ".join(str(value if value is not None else "-").split())
    text = "".join(c for c in text if unicodedata.category(c) not in {"Cc", "Cf"})
    if len(text) > limit:
        text = text[:limit - 3] + "..."
    text = html.escape(text, quote=True)
    for character in "\\`*_|[]":
        text = text.replace(character, "\\" + character)
    return text


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Staging Migration Release Gate", "", report["evidence_label"], "",
        f"**Decision: {report['gate']}** | {summary['passed']}/{summary['total']} checks passed"
        f" | {summary['failed']} blockers | {summary['requests_attempted']} GET attempts", "",
        f"Run: {report['created_utc']}",
        f"Manifest SHA-256: `{report['manifest_sha256']}`", "",
        "This decision covers only the supplied manifest, not a production release authorization.",
        "Selectors prove static element presence, not visibility or working form submission.", "",
        "## Route Checks", "",
        "| Record | Result | Old URL | Expected destination | Observed destination | Status expected / observed | Chain |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in report["results"]:
        cells = [
            result["row"], result["outcome"], result["old_url"], result["expected_url"],
            result["actual_url"], f"{result['expected_status']} / {result['actual_status'] or '-'}",
            " > ".join(str(hop["status"]) for hop in result["chain"]) or "not fetched",
        ]
        lines.append("| " + " | ".join(markdown_cell(cell) for cell in cells) + " |")
    lines += ["", "## Findings", ""]
    for result in report["results"]:
        for issue in result["issues"]:
            detail = issue["detail"]
            if issue["code"] == "title_marker_missing":
                detail += (f" Expected marker: {markdown_cell(result['title_marker'])}."
                           f" Observed title: {markdown_cell(result['actual_title'])}.")
            elif issue["code"] == "selector_missing":
                detail += f" Expected selector: {markdown_cell(result['selector'])}."
            elif issue["code"] == "duplicate_entry":
                detail += f" First occurrence: record {result['duplicate_of']}."
            elif issue["code"] == "unexpected_origin" and result["chain"]:
                detail += f" Blocked Location: {markdown_cell(result['chain'][-1].get('location'))}."
            elif issue["code"] in {"timeout", "network_error"}:
                detail += f" Last request: {markdown_cell(result['last_requested_url'])}."
            lines.append(f"- Record {result['row']} **{issue['code']}**: {detail}")
    if not summary["failed"]:
        lines.append("No mismatches found within the stated scope.")
    lines += [
        "", "## Handoff", "",
        "Resolve each blocker against the agreed route map, or explicitly correct its expected value.",
        "Rerun the same manifest after changes. Keep blocked redirects and transport failures unresolved",
        "until verified; do not treat an unobserved page as passing.",
        "Review forms, JavaScript routes, accessibility, visual layout and integrations separately.",
        "", "Full URLs, expected markers/selectors, observed titles and hop evidence are in report.json",
        "and report.csv. This Markdown view shortens long cells; response bodies are never retained.", "",
    ]
    return "\n".join(lines)


def render_reports(report: dict) -> dict[str, str]:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, quoting=csv.QUOTE_ALL, lineterminator="\n")
    headers = (
        "row", "outcome", "old_url", "expected_url", "expected_status", "actual_url",
        "actual_status", "last_requested_url", "title_marker", "actual_title", "title_truncated", "selector",
        "selector_found", "duplicate_of", "attempts", "issue_codes", "issue_details", "chain_json",
    )
    writer.writerow(headers)
    for result in report["results"]:
        flat = dict(result)
        flat["issue_codes"] = ";".join(issue["code"] for issue in result["issues"])
        flat["issue_details"] = " ".join(issue["detail"] for issue in result["issues"])
        flat["chain_json"] = json.dumps(result["chain"], ensure_ascii=True)
        writer.writerow([csv_safe(flat[header]) for header in headers])
    rendered = {
        "report.json": json.dumps(report, ensure_ascii=True, indent=2) + "\n",
        "report.csv": stream.getvalue(),
        "report.md": render_markdown(report),
    }
    if any(len(text.encode("utf-8")) > MAX_REPORT_BYTES for text in rendered.values()):
        raise InputError("A report exceeds the 2 MiB output cap; split the manifest.")
    return rendered


def confined_output(value: str | Path) -> Path:
    target = (ROOT / value).resolve()
    if target == ROOT or not target.is_relative_to(ROOT):
        raise InputError("Output must be a new directory strictly inside this proof directory.")
    if target.exists():
        raise InputError("Output directory already exists; use a new name to preserve evidence.")
    return target


def write_reports(report: dict, target: Path) -> None:
    target = confined_output(target)
    rendered = render_reports(report)
    target.mkdir(parents=True, exist_ok=False)
    for filename, text in rendered.items():
        with (target / filename).open("x", encoding="utf-8", newline="") as destination:
            destination.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="UTF-8 expected route CSV")
    parser.add_argument("--allow-origin", action="append", required=True,
                        help="Explicit authorized scheme://host[:port]; repeat as needed")
    parser.add_argument("--authorized-staging", action="store_true",
                        help="Confirm staging target authorization (not inferred from hostname)")
    parser.add_argument("--synthetic", action="store_true", help="Label a synthetic local run")
    parser.add_argument("--out", required=True, help="New report directory inside this proof")
    parser.add_argument("--timeout", type=float, default=3.0, help="Per-hop timeout, 0.05-10 seconds")
    parser.add_argument("--max-redirects", type=int, default=5, help="0-10; default 5")
    args = parser.parse_args(argv)
    try:
        if not args.authorized_staging:
            raise InputError("Explicit --authorized-staging confirmation is required.")
        if not 0.05 <= args.timeout <= 10 or not 0 <= args.max_redirects <= 10:
            raise InputError("Timeout must be 0.05-10 seconds and max-redirects 0-10.")
        if len(args.allow_origin) > 10:
            raise InputError("At most 10 explicitly allowed origins.")
        allowed = {normalize_origin(value) for value in args.allow_origin}
        if args.synthetic and any(urlsplit(value).hostname != "127.0.0.1" for value in allowed):
            raise InputError("--synthetic runs require literal 127.0.0.1 origins.")
        target = confined_output(args.out)
        entries, digest = load_manifest(args.manifest)
        report = audit(entries, digest, allowed, args.timeout, args.max_redirects, args.synthetic)
        write_reports(report, target)
    except (InputError, OSError) as exc:
        print(f"Input/output error: {str(exc)[:240]}", file=sys.stderr)
        return 2
    print(f"{report['gate']}: {report['summary']['passed']}/{report['summary']['total']} passed; "
          f"{report['summary']['failed']} failed. Reports: {target}")
    return 0 if report["gate"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
