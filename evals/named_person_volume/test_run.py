"""`uv run pytest`-visible wrapper around `run.py --self-test` (#712 D1).

Not a test of extraction correctness -- the eval harness itself measures a
live model and cannot run under `pytest`'s no-network contract. This file
exists so `run.py`'s own `--self-test` (no model, scripted backend) is
reachable with `uv run pytest evals/named_person_volume -k self_test`
without adding a package `__init__.py` to a directory that also ships
standalone scripts, mirroring how every other harness in `evals/` stays a
plain script."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import run as npv_run  # noqa: E402


def test_self_test() -> None:
    """`run._self_test()` must exit `0` -- the same guarantee every other
    eval's `--self-test` gives, proven here without shelling out."""
    exit_code = npv_run._self_test()
    if exit_code != 0:
        pytest.fail(f"run.py --self-test failed (exit code {exit_code})")
