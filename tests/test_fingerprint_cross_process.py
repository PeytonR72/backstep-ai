"""Guards against dict ordering or PYTHONHASHSEED randomization leaking into
fingerprint output across separate Python processes.

Does not need Postgres — `_fingerprint_state` operates purely on in-memory
Python structures, so this test exercises `backstep.codec` + the fingerprint
function directly and must always run (no `requires_postgres` marker).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from backstep.adapters.postgres import _fingerprint_state
from backstep.codec import decode, encode


def _sample_state() -> dict:
    return {
        "pk_columns": ["id"],
        "rows": [
            {"id": 2, "amount": Decimal("10.50"), "note": "b", "ext_id": str(uuid4())},
            {"id": 1, "amount": Decimal("1234.5678"), "note": "a", "ext_id": str(uuid4())},
        ],
    }


_SUBPROCESS_SCRIPT = """
import json, sys
from backstep.adapters.postgres import _fingerprint_state
from backstep.codec import decode

encoded_state = json.loads(sys.argv[1])
state = decode(encoded_state)
print(_fingerprint_state(state))
"""


def test_fingerprint_stable_across_processes(tmp_path: Path) -> None:
    state = _sample_state()

    # Decimal isn't JSON-native, so round it through the codec-aware encoder
    # before crossing the subprocess boundary via argv/json. `_fingerprint_state`
    # expects native Python objects (e.g. Decimal) in state["rows"] — it calls
    # codec.dumps internally, which calls encode() itself — so the subprocess
    # must decode() back to native types before calling _fingerprint_state,
    # mirroring exactly what this process does.
    fp_here = _fingerprint_state(state)

    encoded_state = encode(state)
    # sanity: decode(encode(state)) reproduces an equivalent native structure
    assert decode(encoded_state) == state

    script_path = tmp_path / "fp_subprocess.py"
    script_path.write_text(_SUBPROCESS_SCRIPT, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(script_path), json.dumps(encoded_state)],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONHASHSEED": "random", **os.environ},
    )
    fp_subprocess = result.stdout.strip()

    assert fp_here == fp_subprocess


def test_fingerprint_stable_across_processes_multiple_hashseeds(tmp_path: Path) -> None:
    """Belt-and-suspenders: run the same fingerprint in three fresh processes
    with different explicit PYTHONHASHSEED values and confirm they all agree
    with each other and with the in-process result — a single random seed
    could coincidentally match, three independent ones are much stronger
    evidence dict ordering isn't leaking into the digest.
    """
    state = _sample_state()
    fp_here = _fingerprint_state(state)
    encoded_state = encode(state)

    script_path = tmp_path / "fp_subprocess_multi.py"
    script_path.write_text(_SUBPROCESS_SCRIPT, encoding="utf-8")

    for seed in ("0", "1", "12345"):
        result = subprocess.run(
            [sys.executable, str(script_path), json.dumps(encoded_state)],
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONHASHSEED": seed, **os.environ},
        )
        assert result.stdout.strip() == fp_here, f"mismatch with PYTHONHASHSEED={seed}"
