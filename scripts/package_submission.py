"""Create and verify the clean submission archive and its SHA-256 manifest."""

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT.parent / "cinema-grpc-submission.zip"
MANIFEST = ROOT / "reports" / "PACKAGE_MANIFEST.txt"
EXCLUDED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache",
                 ".ruff_cache", ".tmp", "node_modules"}
REQUIRED = ["README.md", "reports/TEST_REPORT.md", "reports/test_results.txt",
            "reports/concurrency_results.csv", "reports/fault_tolerance_results.json",
            "reports/benchmark_results.csv", "reports/benchmark_latency_samples.csv",
            "reports/benchmark_summary.json",
            "reports/figures/latency_comparison.png", "reports/figures/throughput_comparison.png",
            "reports/figures/request_outcomes.png", "reports/figures/contention_outcomes.png",
            "reports/PACKAGE_MANIFEST.txt"]


def excluded(path):
    parts = path.relative_to(ROOT).parts
    if any(part in EXCLUDED_DIRS for part in parts):
        return True
    name = path.name.lower()
    if name in {".env", ".env.local", ".env.production"}:
        return True
    if path.suffix.lower() in {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".zip"}:
        return True
    if any(part.lower() == "data" for part in parts) and path.suffix.lower() in {".db-wal", ".db-shm"}:
        return True
    return False


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def package():
    files = sorted(path for path in ROOT.rglob("*") if path.is_file() and not excluded(path))
    manifest_rows = ["Cinema gRPC submission package manifest",
                     "SHA-256 is calculated for every packaged file except this manifest itself.", ""]
    manifest_rows.extend(f"{digest(path)}  {path.relative_to(ROOT).as_posix()}  {path.stat().st_size} bytes"
                         for path in files if path != MANIFEST)
    MANIFEST.write_text("\n".join(manifest_rows) + "\n", encoding="utf-8")
    files = sorted(path for path in ROOT.rglob("*") if path.is_file() and not excluded(path))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=7) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())

    with zipfile.ZipFile(OUTPUT) as archive:
        bad_entry = archive.testzip()
        if bad_entry:
            raise RuntimeError(f"ZIP integrity failure: {bad_entry}")
        names = set(archive.namelist())
        missing = [name for name in REQUIRED if name not in names]
        if missing:
            raise RuntimeError(f"Required package entries missing: {missing}")
        forbidden = [name for name in names if any(part in EXCLUDED_DIRS for part in Path(name).parts)
                     or name.lower().endswith((".pyc", ".pyo", ".db", ".sqlite", ".zip"))
                     or Path(name).name.lower() in {".env", ".env.local", ".env.production"}]
        if forbidden:
            raise RuntimeError(f"Unexpected cache/database/secret entries: {forbidden}")
        with tempfile.TemporaryDirectory(prefix="cinema-package-check-") as temporary:
            archive.extractall(temporary)
            extracted_root = Path(temporary)
            manifest = extracted_root / "reports" / "PACKAGE_MANIFEST.txt"
            for line in manifest.read_text(encoding="utf-8").splitlines()[3:]:
                expected_hash, relative, _size = line.split("  ", 2)
                if digest(extracted_root / relative) != expected_hash:
                    raise RuntimeError(f"Manifest hash mismatch: {relative}")
            for required in REQUIRED:
                if not (extracted_root / required).is_file():
                    raise RuntimeError(f"Extracted file missing: {required}")
            import compileall
            if not compileall.compile_dir(extracted_root, quiet=1):
                raise RuntimeError("Extracted Python project does not compile")
            suite = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                                   cwd=extracted_root, capture_output=True, text=True, timeout=90)
            if suite.returncode:
                raise RuntimeError("Extracted unittest suite failed:\n" + suite.stdout + suite.stderr)
            if shutil.which("node"):
                web_suite = subprocess.run(["node", "tests/test_web_state.js"], cwd=extracted_root,
                                           capture_output=True, text=True, timeout=30)
                if web_suite.returncode:
                    raise RuntimeError("Extracted JavaScript test failed:\n" + web_suite.stdout + web_suite.stderr)
            print("Extracted package tests: unittest PASS; JavaScript PASS when Node.js is available")

    print(f"ZIP: {OUTPUT}")
    print(f"Files: {len(files)}")
    print(f"Size: {OUTPUT.stat().st_size} bytes")
    print("Integrity, required entries, manifest hashes, and extracted script syntax: PASS")


if __name__ == "__main__":
    package()
