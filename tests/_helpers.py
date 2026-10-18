"""Shared bits for the test scripts: import path, dummy environment and a tiny expect()."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
for _name in ("STALKER_HOST", "STALKER_MAC", "STALKER_SERIAL"):  # config.py reads these at import
    os.environ.setdefault(_name, "x")

failures: list[str] = []


def expect(label, actual, expected):
    ok = actual == expected
    if not ok:
        failures.append(label)
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {actual}")


def finish():
    print("RESULT:", "PASS" if not failures else f"FAIL {failures}")
    sys.exit(1 if failures else 0)
