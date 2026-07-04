#!/usr/bin/env python3
"""Local-first PyPI release for the Provenova packages.

Usage:
    python scripts/release.py check                # build + validate + venv smoke
    python scripts/release.py publish              # upload to PyPI (needs PYPI_API_TOKEN)
    python scripts/release.py publish --test-pypi  # upload to TestPyPI (needs TEST_PYPI_API_TOKEN)

check:
  1. preflight — clean git tree, identical versions across pyprojects,
     no stray legacy imports, README/LICENSE present per package
  2. python -m build each package into dist/
  3. twine check dist/*
  4. fresh temp venv (removed after): install the pinned built wheels
     (--find-links dist), then an offline smoke test — import all three
     packages, capture a Bell run on Aer, verify its hash offline, boot the ql CLI

publish (runs check first):
  uploads provenova-core, waits for it to appear on the index, then uploads
  provenova + provenova-crawler. The server package is BUSL-1.1 and is NOT
  published (its import package would need the app->provenova_server rename).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PACKAGES = ["packages/ql-core", "packages/ql-sdk", "packages/ql-crawler"]
DIST_NAMES = {"packages/ql-core": "provenova-core",
              "packages/ql-sdk": "provenova",
              "packages/ql-crawler": "provenova-crawler"}
UPLOAD_ORDER = ["provenova-core", "provenova", "provenova-crawler"]


def run(cmd, **kw):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, check=True, **kw)


def die(msg):
    print(f"FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def _version_of(pkg_dir: Path) -> str:
    text = (pkg_dir / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else die(f"no version in {pkg_dir}/pyproject.toml")


def preflight():
    print("== preflight ==")
    git = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                         capture_output=True, text=True)
    if git.returncode != 0:
        die(f"git status failed: {git.stderr.strip()}")
    if git.stdout.strip():
        die("git tree is not clean — commit or stash first")

    versions = {p: _version_of(ROOT / p) for p in PACKAGES}
    if len(set(versions.values())) != 1:
        die(f"version mismatch across packages: {versions}")
    print(f"  version lockstep: {next(iter(versions.values()))}")

    # No legacy import/install references may survive in the shipped packages.
    grep = subprocess.run(
        ["grep", "-rnE", "--include=*.py", "--include=*.toml", "--include=*.md",
         r"^\s*(from|import)\s+quantumledger|pip install .?quantumledger|name = .quantumledger",
         *[str(ROOT / p) for p in PACKAGES]],
        capture_output=True, text=True)
    if grep.returncode > 1:  # 0 = matches found, 1 = none, >1 = grep error
        die(f"guard grep failed (rc={grep.returncode}): {grep.stderr.strip()}")
    if grep.stdout.strip():
        die(f"stray legacy references in package sources:\n{grep.stdout.strip()}")

    for p in PACKAGES:
        for f in ("README.md", "LICENSE", "pyproject.toml"):
            if not (ROOT / p / f).exists():
                die(f"{p}/{f} missing")
    print("  preflight OK")
    return next(iter(versions.values()))


def build():
    print("== build ==")
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    for p in PACKAGES:
        run([sys.executable, "-m", "build", "--outdir", str(DIST), str(ROOT / p)])
    run([sys.executable, "-m", "twine", "check", *map(str, DIST.glob("*"))])


def smoke(version):
    print("== venv smoke test (offline, from built wheels) ==")
    tmp = Path(tempfile.mkdtemp(prefix="pv_release_"))
    try:
        venv.create(tmp / "venv", with_pip=True)
        py = tmp / "venv" / "bin" / "python"
        # Pin to the just-built version so we validate the wheels in dist/, not
        # whatever the resolver might find for the bare name on the index.
        run([py, "-m", "pip", "install", "-q", "--find-links", str(DIST),
             f"provenova[aer]=={version}", f"provenova-crawler=={version}"])
        env = {**os.environ, "QL_HOME": str(tmp / "qlhome")}
        script = (
            "import provenova as ql\n"
            "import provenova_crawler  # crawler imports cleanly\n"
            "from provenova_core import verify_run_hash\n"
            "from qiskit import QuantumCircuit\n"
            "from qiskit_aer import AerSimulator\n"
            "import tempfile\n"
            "ledger = ql.LocalLedger(f'sqlite:///{tempfile.mkdtemp()}/l.db')\n"
            "from provenova.agent import CaptureAgent\n"
            "agent = CaptureAgent(ledger)\n"
            "@ql.capture(project='smoke', agent=agent, shots=256)\n"
            "def _run():\n"
            "    qc = QuantumCircuit(2); qc.h(0); qc.cx(0,1); qc.measure_all()\n"
            "    return AerSimulator().run(qc, shots=256)\n"
            "_run()\n"
            "rid = ledger.list_runs(limit=1)[0]['id']\n"
            "doc = ledger.get_run_doc(rid)\n"
            "assert verify_run_hash(doc), 'offline hash verification failed'\n"
            "print('smoke OK:', rid)\n"
        )
        run([py, "-c", script], env=env)
        run([tmp / "venv" / "bin" / "ql", "--help"], env=env,
            stdout=subprocess.DEVNULL)  # entry point resolves + CLI boots
        run([py, "-c",
             "from importlib.metadata import version; print('dist:', version('provenova'))"],
            env=env)
        print("  smoke OK")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # ~0.5 GB of qiskit — don't leak it


def _on_index(name: str, version: str, test: bool) -> bool:
    host = "test.pypi.org" if test else "pypi.org"
    try:
        with urllib.request.urlopen(f"https://{host}/pypi/{name}/{version}/json", timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def publish(test: bool):
    env_var = "TEST_PYPI_API_TOKEN" if test else "PYPI_API_TOKEN"
    token = os.environ.get(env_var)
    if not token:
        die(f"{env_var} not set — refuse to publish")
    version = preflight()
    build()
    smoke(version)
    print(f"== publish to {'TestPyPI' if test else 'PyPI'} ==")
    env = {**os.environ, "TWINE_USERNAME": "__token__", "TWINE_PASSWORD": token}
    repo_args = ["--repository-url", "https://test.pypi.org/legacy/"] if test else []
    for dist_name in UPLOAD_ORDER:
        prefix = dist_name.replace("-", "_") + "-"
        files = [f for f in DIST.glob("*") if f.name.startswith(prefix)]
        if not files:
            die(f"no built artifacts for {dist_name}")
        # --skip-existing makes a re-run after a partial failure safe (PyPI
        # rejects re-uploading an identical filename otherwise).
        run([sys.executable, "-m", "twine", "upload", "--skip-existing",
             *repo_args, *map(str, files)], env=env)
        if dist_name == "provenova-core":
            print("  waiting for provenova-core to appear on the index …")
            for _ in range(30):
                if _on_index(dist_name, version, test):
                    break
                time.sleep(10)
            else:
                die("provenova-core did not appear on the index after 5 minutes")
    print(f"\nPublished {', '.join(UPLOAD_ORDER)} == {version}")
    print(f"Tag it: git tag v{version} && git push --tags")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["check", "publish"])
    ap.add_argument("--test-pypi", action="store_true")
    args = ap.parse_args()
    if args.command == "check":
        version = preflight()
        build()
        smoke(version)
        print("\nrelease check PASSED")
    else:
        publish(test=args.test_pypi)


if __name__ == "__main__":
    main()
