"""qzc2 bit-history state machine self-test (qzc2 selftest).

From every state, a long run of one bit value must reach a deterministic state with prior confidence > 88%.
The original machine (v0, used by every v3/v4/v5 stream and by v6 unless flag 256 is set) has absorbing mixed
states such as (2,15) at 0.861 and (1,16): the test pins that count (220) so the frozen table can't change
silently (old streams would stop decoding), and requires the v6 flag-256 variant to have none.
"""
import os, subprocess
import pytest
from src import qzrouter as Q

BIN = os.path.join(Q.BIN, "qzc2")

@pytest.mark.skipif(not os.path.exists(BIN), reason="qzc2 not built (src/qzcore/build.sh)")
def test_state_machine_selftest():
    r = subprocess.run([BIN, "selftest"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "states v0 (v4/v5 streams): 220 " in r.stdout
    assert "states v1 (v6 flag 256): 0" in r.stdout
