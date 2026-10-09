"""Generate one small, local-only demo bundle by running the actual CLI."""

import argparse
import csv
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone

from gate import COLUMNS, ROOT, InputError, confined_output
from synthetic_fixture import fixture


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="demo-output", help="New directory inside this proof")
    args = parser.parse_args(argv)
    try:
        output = confined_output(args.out)
        output.mkdir(parents=True, exist_ok=False)
    except (InputError, OSError) as exc:
        parser.error(str(exc))
    template = ROOT / "samples" / "manifest.csv"
    with fixture() as (base, server):
        with template.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        manifest = output / "manifest.csv"
        with manifest.open("x", encoding="utf-8", newline="") as destination:
            writer = csv.DictWriter(destination, fieldnames=COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    key: value.replace("{origin}", base) if key in {"old_url", "expected_url"} else value
                    for key, value in row.items()
                })
        command = [
            sys.executable, "-B", str(ROOT / "gate.py"), str(manifest),
            "--allow-origin", base, "--authorized-staging", "--synthetic",
            "--out", str(output / "reports"),
        ]
        recorded_command = [
            "python", "-B", "gate.py", "<generated>/manifest.csv",
            "--allow-origin", base, "--authorized-staging", "--synthetic",
            "--out", "<generated>/reports",
        ]
        child = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
        if child.returncode not in {0, 1}:
            print(f"Demo CLI error (exit {child.returncode}): {child.stderr[:500]}", file=sys.stderr)
            return 1
        report = json.loads((output / "reports" / "report.json").read_text(encoding="utf-8"))
        verified = (
            child.returncode == 1 and report["gate"] == "HOLD"
            and report["summary"] == {"total": 10, "passed": 3, "failed": 7, "requests_attempted": 14}
            and len(server.requests) == 14
        )
        source_paths = [
            ROOT / "gate.py", ROOT / "synthetic_fixture.py", ROOT / "demo.py",
            ROOT / "test_gate.py", ROOT / "README.md", ROOT / ".gitignore", template,
        ]
        source_hashes = {path.relative_to(ROOT).as_posix(): sha256(path) for path in source_paths}
        def public_path(path):
            if path.is_relative_to(output):
                return "demo-output/" + path.relative_to(output).as_posix()
            return path.relative_to(ROOT).as_posix()

        metadata = {
            "schema_version": "1.0",
            "source": "Original local synthetic_fixture.py and samples/manifest.csv",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "retrieval": "No downloads; served only by the local synthetic HTTP fixture.",
            "usage_note": "Synthetic / AI-assisted proof; no client data or third-party assets.",
            "runtime": sys.version.split()[0],
            "platform": platform.platform(),
            "command": recorded_command,
            "regenerate": ["python", "-B", "demo.py", "--out", "NEW-DEMO-DIRECTORY"],
            "cli_exit": child.returncode,
            "expected_hold_verified": verified,
            "fixture_requests": len(server.requests),
            "source_tree_sha256": hashlib.sha256(json.dumps(
                source_hashes, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest(),
            "source_tree_definition": "SHA-256 of compact sorted JSON of the seven source path/hash pairs.",
            "sha256": {
                public_path(path): sha256(path)
                for path in [
                    *source_paths,
                    manifest, *sorted((output / "reports").iterdir()),
                ]
            },
        }
        with (output / "provenance.json").open("x", encoding="utf-8") as destination:
            json.dump(metadata, destination, ensure_ascii=True, indent=2)
            destination.write("\n")
    print(child.stdout.strip())
    print(f"Demo {'verified' if verified else 'FAILED'}: expected HOLD, CLI exit {child.returncode}, "
          f"{len(server.requests)} local requests. Fixture stopped. Bundle: {output}")
    return 0 if verified else 1


if __name__ == "__main__":
    sys.exit(main())
