"""Deterministic contract tests. All real HTTP is to our loopback fixture."""

import contextlib
import csv
import io
import json
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gate
from synthetic_fixture import CONTACT, HOME, ROUTES, fixture


def setUpModule():
    global network_guard
    real_connect = socket.create_connection

    def loopback_only(address, *args, **kwargs):
        if address[0] != "127.0.0.1":
            raise AssertionError(f"Test attempted a non-fixture connection: {address[0]}")
        return real_connect(address, *args, **kwargs)

    network_guard = patch("socket.create_connection", side_effect=loopback_only)
    network_guard.start()


def tearDownModule():
    network_guard.stop()


def codes(result):
    return [issue["code"] for issue in result.issues]


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.workspace = tempfile.TemporaryDirectory(prefix=".test-", dir=gate.ROOT)
        self.work = Path(self.workspace.name).resolve()
        if not self.work.is_relative_to(gate.ROOT):
            raise AssertionError("Test temporary directory escaped the proof root.")
        self.addCleanup(self.workspace.cleanup)

    def manifest(self, rows, headers=gate.COLUMNS):
        path = self.work / "input.csv"
        with path.open("w", encoding="utf-8", newline="") as destination:
            writer = csv.writer(destination)
            writer.writerow(headers)
            writer.writerows(rows)
        return path


class ManifestTests(WorkspaceTest):
    def test_optional_selector_and_utf8_bom(self):
        path = self.work / "input.csv"
        path.write_text(
            "\ufeffold_url,expected_url,expected_status,title_marker\n"
            'HTTP://EXAMPLE.test:80,http://example.test/,200,"Home, team"\n',
            encoding="utf-8",
        )
        entries, digest = gate.load_manifest(path)
        self.assertEqual(entries, [gate.Entry(2, "http://example.test/", "http://example.test/",
                                             200, "Home, team", "")])
        self.assertEqual(len(digest), 64)

    def test_schema_errors(self):
        for headers in [[], gate.COLUMNS[:3], (*gate.COLUMNS, "extra"),
                        (*gate.COLUMNS, "old_url")]:
            with self.subTest(headers=headers), self.assertRaises(gate.InputError):
                gate.load_manifest(self.manifest([], headers))

    def test_missing_or_extra_fields(self):
        for row in [["http://example.test"], ["x"] * 6]:
            with self.subTest(row=row), self.assertRaises(gate.InputError):
                gate.load_manifest(self.manifest([row]))

    def test_bad_encoding_and_unclosed_quote(self):
        path = self.work / "input.csv"
        for raw in [b"\xff", b'old_url,expected_url,expected_status,title_marker\n"unclosed']:
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                with self.assertRaises(gate.InputError):
                    gate.load_manifest(path)

    def test_field_and_file_caps(self):
        base = ["http://example.test/", "http://example.test/", "200", "", ""]
        for index, value in [(0, "x" * 2049), (3, "x" * 257), (4, ".x" * 65), (3, "   ")]:
            row = base.copy()
            row[index] = value
            with self.subTest(index=index), self.assertRaises(gate.InputError):
                gate.load_manifest(self.manifest([row]))
        with patch.object(gate, "MAX_MANIFEST_BYTES", 10), self.assertRaises(gate.InputError):
            gate.load_manifest(self.manifest([base]))

    def test_row_cap_and_empty_manifest(self):
        row = ["http://example.test/", "http://example.test/", 200, "", ""]
        for rows in [[], [row] * 101]:
            with self.subTest(count=len(rows)), self.assertRaises(gate.InputError):
                gate.load_manifest(self.manifest(rows))
        self.assertEqual(len(gate.load_manifest(self.manifest([row] * 100))[0]), 100)

    def test_invalid_terminal_status(self):
        for status in ["199", "600", "301", "302", "303", "307", "308", "200.0", " 200"]:
            with self.subTest(status=status), self.assertRaises(gate.InputError):
                gate.load_manifest(self.manifest([
                    ["http://example.test/", "http://example.test/", status, "", ""]
                ]))

    def test_url_rejections(self):
        for url in ["ftp://example.test/", "//example.test/", "http://user:pw@example.test",
                    "http://example.test/#", "http://example.test:0/", "http://example.test:65536/",
                    "http://example.test:/", "http://exa mple.test", "http://example.test/\n",
                    "http://example.test\\@evil.test", "http://%31%32%37.0.0.1",
                    "http://[bad]/", "http://[127.0.0.1]/", "http://example.test/\N{SNOWMAN}"]:
            with self.subTest(url=url), self.assertRaises(gate.InputError):
                gate.normalize_url(url)

    def test_url_normalization_preserves_meaningful_differences(self):
        self.assertEqual(gate.normalize_url("HTTPS://EXAMPLE.test:443"), "https://example.test/")
        self.assertEqual(gate.normalize_url("http://[::1]:80"), "http://[::1]/")
        self.assertNotEqual(gate.normalize_url("http://example.test/a"),
                            gate.normalize_url("http://example.test/a/"))
        self.assertNotEqual(gate.normalize_url("http://example.test/?a=1&b=2"),
                            gate.normalize_url("http://example.test/?b=2&a=1"))

    def test_origin_rejects_paths_and_queries(self):
        for value in ["https://example.test/staging", "https://example.test/?", "https://example.test/?a=1"]:
            with self.subTest(value=value), self.assertRaises(gate.InputError):
                gate.normalize_origin(value)

    def test_unsupported_selectors_rejected_in_preflight(self):
        for selector in ["form input", "a[href]", "a:hover", "#", "*", "form,button", ".a#b"]:
            with self.subTest(selector=selector), self.assertRaises(gate.InputError):
                gate.load_manifest(self.manifest([
                    ["http://example.test/", "http://example.test/", 200, "", selector]
                ]))


class IntegrationTests(WorkspaceTest):
    @classmethod
    def setUpClass(cls):
        context = fixture()
        cls.base, cls.server = context.__enter__()
        cls.addClassCleanup(context.__exit__, None, None, None)

    def entry(self, path, expected=None, status=200, marker="", selector=""):
        return gate.Entry(2, self.base + path, self.base + (expected or path),
                          status, marker, selector)

    def check(self, entry, **kwargs):
        return gate.check_entry(entry, {self.base}, kwargs.get("timeout", 1), kwargs.get("max_redirects", 5))

    def test_direct_success(self):
        result = self.check(self.entry("/home", marker="Willow Studio", selector="a#book.cta.primary"))
        self.assertEqual(result.outcome, "PASS")
        self.assertTrue(result.selector_found)
        self.assertEqual(result.actual_title, "Willow Studio | Home")

    def test_multi_hop_redirect(self):
        result = self.check(self.entry("/old-contact", "/contact", marker="Contact", selector="form#contact-form"))
        self.assertEqual(result.outcome, "PASS")
        self.assertEqual([hop["status"] for hop in result.chain], [302, 308, 200])
        self.assertEqual(result.attempts, 3)

    def test_all_supported_redirect_codes(self):
        for status in sorted(gate.REDIRECTS):
            with self.subTest(status=status), patch.dict(ROUTES, {"/test-hop": (status, "home", b"")}):
                result = self.check(self.entry("/test-hop", "/home"))
                self.assertEqual(result.outcome, "PASS")
                self.assertEqual([hop["status"] for hop in result.chain], [status, 200])

    def test_expected_error_status_is_not_transport_failure(self):
        for path, status in [("/gone", 410), ("/unknown", 404)]:
            with self.subTest(path=path):
                self.assertEqual(self.check(self.entry(path, status=status)).outcome, "PASS")

    def test_multiple_page_mismatches_retained(self):
        result = self.check(self.entry("/missing-cta", status=404, marker="Wrong", selector="form#contact-form"))
        self.assertEqual(codes(result), ["status_mismatch", "title_marker_missing", "selector_missing"])

    def test_destination_drift(self):
        result = self.check(self.entry("/drift", "/contact"))
        self.assertEqual(codes(result), ["destination_mismatch"])
        self.assertEqual(result.actual_url, self.base + "/home")

    def test_offsite_redirect_is_not_requested(self):
        before = len(self.server.requests)
        result = self.check(self.entry("/external", "/home"))
        self.assertEqual(codes(result), ["unexpected_origin"])
        self.assertEqual(len(self.server.requests) - before, 1)
        self.assertEqual(result.chain[0]["location"], "https://not-authorized.invalid/collect")

    def test_unapproved_old_or_expected_origin_makes_no_request(self):
        for old, expected in [
            ("https://outside.invalid/", self.base + "/home"),
            (self.base + "/home", "https://outside.invalid/"),
            (self.base.replace("127.0.0.1", "localhost") + "/home", self.base + "/home"),
        ]:
            with self.subTest(old=old, expected=expected), patch("gate.fetch_http") as fetch:
                result = self.check(gate.Entry(2, old, expected, 200, "", ""))
                self.assertEqual(codes(result), ["unexpected_origin"])
                fetch.assert_not_called()

    def test_loop_is_stopped_without_repeating_request(self):
        result = self.check(self.entry("/loop-a", "/home"))
        self.assertEqual(codes(result), ["redirect_loop"])
        self.assertEqual(result.attempts, 2)

    def test_redirect_budget_exact_boundary(self):
        result = self.check(self.entry("/old-contact", "/contact"), max_redirects=1)
        self.assertEqual(codes(result), ["redirect_limit"])
        self.assertEqual(result.attempts, 2)
        self.assertEqual(self.check(self.entry("/old-contact", "/contact"), max_redirects=2).outcome, "PASS")
        self.assertEqual(self.check(self.entry("/home"), max_redirects=0).outcome, "PASS")
        self.assertEqual(self.check(self.entry("/old-home", "/home"), max_redirects=0).attempts, 1)

    def test_missing_and_invalid_location(self):
        for path, expected in [("/no-location", "missing_location"), ("/unsafe-location", "invalid_redirect")]:
            with self.subTest(path=path):
                self.assertEqual(codes(self.check(self.entry(path))), [expected])

    def test_network_disconnect(self):
        result = self.check(self.entry("/drop"))
        self.assertEqual(codes(result), ["network_error"])
        self.assertIsNone(result.actual_status)
        self.assertEqual(result.last_requested_url, self.base + "/drop")

    def test_oversized_body_is_not_a_pass(self):
        self.assertEqual(codes(self.check(self.entry("/large"))), ["body_too_large"])

    def test_non_html_requires_explicit_no_marker(self):
        self.assertEqual(self.check(self.entry("/not-html")).outcome, "PASS")
        self.assertEqual(codes(self.check(self.entry("/not-html", marker="Contact"))), ["not_html"])

    def test_duplicate_conflicting_row_is_skipped_and_gate_holds(self):
        path = self.manifest([
            [self.base + "/home", self.base + "/home", 200, "Willow", ""],
            [self.base.upper() + "/home", self.base + "/home", 404, "Wrong", ""],
        ])
        entries, digest = gate.load_manifest(path)
        report = gate.audit(entries, digest, {self.base}, synthetic=True)
        self.assertEqual(report["gate"], "HOLD")
        self.assertEqual(report["summary"], {"total": 2, "passed": 1, "failed": 1, "requests_attempted": 1})
        self.assertEqual(report["results"][1]["duplicate_of"], 2)
        self.assertEqual(report["results"][1]["attempts"], 0)

    def test_real_cli_exit_codes_and_report_consistency(self):
        for path, status, expected_exit in [("/home", 200, 0), ("/home", 404, 1)]:
            with self.subTest(expected_exit=expected_exit):
                manifest = self.manifest([[self.base + path, self.base + path, status, "Willow", "a#book"]])
                output = self.work / f"reports-{expected_exit}"
                process = subprocess.run(
                    [sys.executable, "-B", str(gate.ROOT / "gate.py"), str(manifest),
                     "--allow-origin", self.base, "--authorized-staging", "--synthetic", "--out", str(output)],
                    capture_output=True, text=True, timeout=15, cwd=gate.ROOT,
                )
                self.assertEqual(process.returncode, expected_exit, process.stderr)
                report = json.loads((output / "report.json").read_text(encoding="utf-8"))
                self.assertEqual(report["gate"], "PASS" if expected_exit == 0 else "HOLD")
                with (output / "report.csv").open(encoding="utf-8", newline="") as source:
                    rows = list(csv.DictReader(source))
                self.assertEqual(len(rows), report["summary"]["total"])
                self.assertEqual(rows[0]["outcome"], report["results"][0]["outcome"])
                self.assertIn(f"Decision: {report['gate']}", (output / "report.md").read_text(encoding="utf-8"))

    def test_real_cli_input_error_exit(self):
        manifest = self.manifest([[self.base + "/home", self.base + "/home", 200, "", ""]])
        output = self.work / "no-report"
        process = subprocess.run(
            [sys.executable, "-B", str(gate.ROOT / "gate.py"), str(manifest),
             "--allow-origin", self.base, "--out", str(output)],
            capture_output=True, text=True, timeout=15, cwd=gate.ROOT,
        )
        self.assertEqual(process.returncode, 2)
        self.assertFalse(output.exists())
        self.assertIn("--authorized-staging", process.stderr)


class TransportTests(unittest.TestCase):
    def test_timeout_network_and_tls_failures(self):
        for scheme, error, expected in [
            ("http", TimeoutError(), "timeout"),
            ("http", socket.gaierror(), "network_error"),
            ("http", ConnectionRefusedError(), "network_error"),
            ("https", ssl.SSLCertVerificationError(), "network_error"),
        ]:
            kind = "HTTPSConnection" if scheme == "https" else "HTTPConnection"
            with self.subTest(error=type(error).__name__), patch(f"gate.http.client.{kind}") as factory:
                factory.return_value.request.side_effect = error
                with self.assertRaises(gate.CheckError) as caught:
                    gate.fetch_http(f"{scheme}://example.test/", 0.25)
                self.assertEqual(caught.exception.code, expected)
                factory.return_value.close.assert_called_once()

    def test_body_deadline_is_deterministic(self):
        with patch("gate.http.client.HTTPConnection") as factory, patch("gate.time.monotonic", side_effect=[0, 2]):
            response = factory.return_value.getresponse.return_value.__enter__.return_value
            response.status = 200
            response.headers.get_all.return_value = []
            with self.assertRaises(gate.CheckError) as caught:
                gate.fetch_http("http://example.test/", 1)
            self.assertEqual(caught.exception.code, "timeout")
            response.read1.assert_not_called()

    def test_ambiguous_redirect_and_incomplete_body(self):
        for locations, status, length, expected in [
            (["/a", "/b"], 302, 0, "ambiguous_redirect"),
            ([], 200, 10, "incomplete_body"),
        ]:
            with self.subTest(expected=expected), patch("gate.http.client.HTTPConnection") as factory:
                response = factory.return_value.getresponse.return_value.__enter__.return_value
                response.status = status
                response.headers.get_all.return_value = locations
                response.read1.return_value = b""
                response.length = length
                with self.assertRaises(gate.CheckError) as caught:
                    gate.fetch_http("http://example.test/", 1)
                self.assertEqual(caught.exception.code, expected)

    def test_boundary_body_size_and_get_only(self):
        with patch("gate.http.client.HTTPConnection") as factory:
            response = factory.return_value.getresponse.return_value.__enter__.return_value
            response.status = 200
            response.headers.get_all.return_value = []
            response.length = 0
            response.getheader.return_value = ""
            response.read1.side_effect = [b"x" * 16384] * 8 + [b""]
            actual = gate.fetch_http("http://example.test/a?q=1", 1)
            self.assertEqual(len(actual.body), gate.MAX_BODY_BYTES)
            self.assertEqual(factory.return_value.request.call_args.args, ("GET", "/a?q=1"))
            self.assertNotIn("body", factory.return_value.request.call_args.kwargs)

    def test_invalid_redirect_references_are_never_followed(self):
        entry = gate.Entry(2, "https://example.test/", "https://example.test/home", 200, "", "")
        for location in ["ftp://example.test/", "/home#x", "/home\n", "/home\\x",
                         "https://user:pw@example.test/", "/" + "x" * 2048]:
            with self.subTest(location=location[:30]), patch("gate.fetch_http", return_value=gate.Response(302, location)) as fetch:
                result = gate.check_entry(entry, {"https://example.test"}, 1, 5)
                self.assertEqual(codes(result), ["invalid_redirect"])
                fetch.assert_called_once()

    def test_downgrade_refused_even_when_both_origins_allowed(self):
        entry = gate.Entry(2, "https://example.test/", "http://example.test/", 200, "", "")
        with patch("gate.fetch_http", return_value=gate.Response(301, "http://example.test/")) as fetch:
            result = gate.check_entry(entry, {"https://example.test", "http://example.test"}, 1, 5)
            self.assertEqual(codes(result), ["https_downgrade"])
            fetch.assert_called_once()

    def test_timeout_is_a_failed_row_not_an_uncaught_exception(self):
        entry = gate.Entry(2, "http://example.test/", "http://example.test/", 200, "", "")
        with patch("gate.fetch_http", side_effect=gate.CheckError("timeout", "Timed out.")):
            report = gate.audit([entry], "0" * 64, {"http://example.test"})
        self.assertEqual(report["gate"], "HOLD")
        self.assertEqual(report["results"][0]["issues"][0]["code"], "timeout")
        self.assertEqual(report["summary"]["requests_attempted"], 1)

    def test_timeout_does_not_skip_remaining_rows(self):
        entries = [
            gate.Entry(2, "http://example.test/a", "http://example.test/a", 200, "", ""),
            gate.Entry(3, "http://example.test/b", "http://example.test/b", 200, "", ""),
        ]
        with patch("gate.fetch_http", side_effect=[
            gate.CheckError("timeout", "Timed out."), gate.Response(200),
        ]):
            report = gate.audit(entries, "0" * 64, {"http://example.test"})
        self.assertEqual([row["outcome"] for row in report["results"]], ["FAIL", "PASS"])
        self.assertEqual(report["summary"], {"total": 2, "passed": 1, "failed": 1, "requests_attempted": 2})


class ProbeTests(unittest.TestCase):
    def test_entities_whitespace_and_first_title_only(self):
        response = gate.Response(200, content_type="text/html", body=(
            b"<title>Home &amp;\n Team</title><title>Ignored</title>"
            b'<FORM id="lead" class="primary wide"><input></FORM>'
        ))
        probe = gate.inspect_page(response, "form#lead.wide.primary")
        self.assertEqual(probe.title, "Home & Team")
        self.assertTrue(probe.found)

    def test_selector_subset_and_case(self):
        for selector, expected in [("form", True), ("#contact-form", True), ("button", True),
                                   ("#CONTACT-form", False), (".absent", False), ("a", False)]:
            with self.subTest(selector=selector):
                self.assertEqual(gate.inspect_page(gate.Response(
                    200, content_type="text/html", body=CONTACT), selector).found, expected)

    def test_script_text_and_body_are_not_title_or_elements(self):
        probe = gate.inspect_page(gate.Response(200, content_type="text/html", body=(
            b'<p>Contact</p><script>const html = "<form id=lead>";</script>'
        )), "form")
        self.assertEqual(probe.title, "")
        self.assertFalse(probe.found)

    def test_title_and_textarea_markup_do_not_create_false_selector_passes(self):
        probe = gate.inspect_page(gate.Response(200, content_type="text/html", body=(
            b'<title>Contact &amp;lt; <form id=lead></title>'
            b'<textarea><form id=lead></textarea>'
        )), "form#lead")
        self.assertEqual(probe.title, "Contact &lt; <form id=lead>")
        self.assertFalse(probe.found)

    def test_malformed_declaration_is_a_check_failure(self):
        with self.assertRaises(gate.CheckError) as caught:
            gate.inspect_page(gate.Response(200, content_type="text/html", body=b"<![broken]>"), "")
        self.assertEqual(caught.exception.code, "html_parse_error")

    def test_charset_decode_and_content_encoding_errors(self):
        for content_type, encoding, body, expected in [
            ("text/plain", "", HOME, "not_html"),
            ("text/html; charset=missing-codec", "", HOME, "decode_error"),
            ("text/html; charset=utf-8", "", b"\xff", "decode_error"),
            ("text/html", "gzip", HOME, "unsupported_encoding"),
        ]:
            with self.subTest(expected=expected), self.assertRaises(gate.CheckError) as caught:
                gate.inspect_page(gate.Response(200, content_type=content_type,
                                               content_encoding=encoding, body=body), "")
            self.assertEqual(caught.exception.code, expected)

    def test_report_title_is_bounded_without_truncating_marker_check(self):
        entry = gate.Entry(2, "http://example.test/", "http://example.test/", 200, "EndMarker", "")
        response = gate.Response(200, content_type="text/html",
                                 body=b"<title>" + b"x" * 1000 + b"EndMarker</title>")
        with patch("gate.fetch_http", return_value=response):
            result = gate.check_entry(entry, {"http://example.test"}, 1, 5)
        self.assertEqual(result.outcome, "PASS")
        self.assertTrue(result.title_truncated)
        self.assertEqual(len(result.actual_title), 512)


class ReportAndCliTests(WorkspaceTest):
    def report(self, marker="Home"):
        entry = gate.Entry(2, "http://example.test/", "http://example.test/", 200, marker, "")
        response = gate.Response(200, content_type="text/html", body=HOME)
        with patch("gate.fetch_http", return_value=response):
            return gate.audit([entry], "0" * 64, {"http://example.test"}, synthetic=True)

    def test_formula_injection_prefixes(self):
        for value in ["=1+1", "+SUM(A1)", "-2+3", "@SUM(A1)", " \t=1", "\ufeff=1",
                      "\x00@x", "\rtext", "\ntext", "\ttext", "\u200b-2"]:
            with self.subTest(value=repr(value)):
                self.assertEqual(gate.csv_safe(value), "'" + value)
        for value in ["Home", "hello@example.test", "1+1", "'=1"]:
            self.assertEqual(gate.csv_safe(value), value)

    def test_all_csv_cells_are_sanitized_and_roundtrip(self):
        report = self.report("=HYPERLINK(\"bad\")")
        report["results"][0]["actual_title"] = '@SUM(1,2)\n"quoted"'
        output = gate.render_reports(report)
        row = list(csv.DictReader(io.StringIO(output["report.csv"], newline="")))[0]
        self.assertEqual(row["title_marker"], "'=HYPERLINK(\"bad\")")
        self.assertEqual(row["actual_title"], '\'@SUM(1,2)\n"quoted"')
        self.assertEqual(json.loads(output["report.json"])["results"][0]["title_marker"], '=HYPERLINK("bad")')

    def test_markdown_escapes_html_and_table_syntax(self):
        cell = gate.markdown_cell('<script>alert(1)</script>|[x](y)\n`code`')
        self.assertNotIn("<script>", cell)
        self.assertIn("&lt;script&gt;", cell)
        self.assertIn("\\|", cell)
        self.assertNotIn("\n", cell)
        self.assertLessEqual(len(gate.markdown_cell("x" * 500)), 110)

    def test_markdown_findings_include_missing_expectations(self):
        report = self.report("Contact")
        report["results"][0]["selector"] = "form#contact-form"
        report["results"][0]["issues"].append({"code": "selector_missing", "detail": "Missing element."})
        output = gate.render_markdown(report)
        self.assertIn("Expected marker: Contact", output)
        self.assertIn("Observed title: Willow Studio", output)
        self.assertIn("Expected selector: form#contact-form", output)

    def test_report_cap_fails_before_creating_output(self):
        target = self.work / "report"
        report = self.report()
        with patch.object(gate, "MAX_REPORT_BYTES", 32), self.assertRaises(gate.InputError):
            gate.write_reports(report, target)
        self.assertFalse(target.exists())

    def test_existing_output_and_escape_are_rejected(self):
        for path in [gate.ROOT, gate.ROOT.parent / "must-not-create", self.work]:
            with self.subTest(path=path), self.assertRaises(gate.InputError):
                gate.confined_output(path)
        target = self.work / "new"
        self.assertEqual(gate.confined_output(target), target)

    def test_cli_preflight_errors_do_not_fetch_or_create_reports(self):
        manifest = self.manifest([["http://example.test/", "http://example.test/", 200, "", ""]])
        base = [str(manifest), "--allow-origin", "http://example.test", "--out", str(self.work / "report")]
        for extra in [[], ["--authorized-staging", "--timeout", "nan"],
                      ["--authorized-staging", "--timeout", "11"],
                      ["--authorized-staging", "--max-redirects", "-1"],
                      ["--authorized-staging", "--synthetic"]]:
            with self.subTest(extra=extra), patch("gate.fetch_http") as fetch, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(gate.main(base + extra), 2)
                fetch.assert_not_called()
        self.assertFalse((self.work / "report").exists())

    def test_invalid_late_record_prevents_all_network_requests(self):
        manifest = self.manifest([
            ["http://example.test/", "http://example.test/", 200, "", ""],
            ["http://example.test/next", "http://example.test/next", 200, "", "form input"],
        ])
        with patch("gate.fetch_http") as fetch, contextlib.redirect_stderr(io.StringIO()):
            code = gate.main([str(manifest), "--allow-origin", "http://example.test",
                              "--authorized-staging", "--out", str(self.work / "report")])
        self.assertEqual(code, 2)
        fetch.assert_not_called()

    def test_write_reports_preserves_existing_evidence(self):
        target = self.work / "report"
        report = self.report()
        gate.write_reports(report, target)
        before = {path.name: path.read_bytes() for path in target.iterdir()}
        with self.assertRaises(gate.InputError):
            gate.write_reports(self.report("Wrong"), target)
        self.assertEqual(before, {path.name: path.read_bytes() for path in target.iterdir()})
        self.assertEqual(set(before), {"report.json", "report.csv", "report.md"})


if __name__ == "__main__":
    unittest.main()
