"""Tutorial 8 (`tutorials/08_omniscience.md`) replayed: the commands it shows, in its order, in a local sandbox.

`omniscience mount` (§2) is not replayed here: it needs the real provider's repository, and
`test_omniscience_register.py` covers it. `bootstrap restored.gts` (§6) clones over the network;
what is replayed is everything up to the `.gts` it takes, and that file is checked to be the State.
"""

from __future__ import annotations

import json
import shutil
import time

from test_omniscience_append import (
    _checkpoint,
    _latest_state_declares_the_mounts,
    _newest,
)
from test_omniscience_query import _identity, _one, addresses  # noqa: F401
from test_push_folds_memory import _git

from ComplexGitSync.cli import main as cli_main
from ComplexGitSync.gts_document import GtsDocument


def test_sections_3_to_7_register_ask_prove_read_restore_and_check(tmp_path, addresses, monkeypatch, capsys):  # noqa: F811
    omni, tree, client, _ = _one(tmp_path, addresses, monkeypatch)
    state = _newest(tree)
    _latest_state_declares_the_mounts(client, tree)
    where = ["--search-dir", str(tree["root"])]

    def run(*args):
        code = cli_main(["omniscience", *args, *where])
        out = capsys.readouterr().out
        return code, "\n" + out

    # §3 register: record prints, register adds, and doing it again does nothing twice
    code, out = run("record", state[:4])
    assert code == 0 and "file=register/" in out
    code, out = run("register", "--state", state[:4])  # the ledger has moved since the first registration: a new record
    assert code == 0 and "registered retries=0" in out and "checkpoint=extended" in out
    code, out = run("register", "--state", state[:4])
    assert code == 0 and "already registered" in out and "checkpoint=unchanged" in out

    # §4 ask by name
    code, out = run("get", "demo")
    assert code == 0 and "\nstatus=verified" in out and "os_id=" in out and "branches=main" in out
    code, out = run("branches", "demo")
    assert code == 0 and "branch main: states=1" in out
    code, out = run("history", "demo")
    assert code == 0 and "main state(" in out
    newest = _checkpoint(omni)
    code, out = run("history", "demo", "--checkpoint", newest["parent"][:12])  # the register as it stood one checkpoint ago
    assert code == 0 and out.count(" main state(") == 1

    # §5 prove
    code, out = run("proof", "demo", state[:4])
    assert code == 0 and "proof_verified=True" in out and "of 2, root" in out
    code, out = run("verify", "demo", state[:4])
    assert code == 0 and "register=intact" in out and "last_seen_tip_known" in out

    # §6 read the memory, write the State, and check the file is the State
    code, out = run("memory", "demo", "main")
    assert code == 0 and "ledger: " in out, out
    code, out = run("state", "demo", state[:4])
    assert code == 0 and "written=" not in out
    restored = tmp_path / "restored.gts"
    code, out = run("state", "demo", state[:4], "--out", str(restored))
    assert code == 0 and f"written={restored}" in out
    assert GtsDocument.from_toml(restored).compute_snapshot_hash() == state
    code, out = run("state", "demo", state[:4], "--out", str(restored))  # never over a file
    assert code != 0 or "never overwrites" in out

    # §7 still there? nothing observed is unavailable; observed is verified; gone is unavailable, and the proof stands
    code, out = run("status", "demo", "memory")
    assert code == 1 and "status=unavailable (not_observed)" in out
    code, out = run("observe")
    assert code == 0 and "report=ok" in out
    code, out = run("status", "demo", "memory")
    assert code == 0 and "availability=AVAILABLE" in out
    shutil.rmtree(tree["memory_remote"])
    time.sleep(1.1)  # two findings are never in the same second
    code, out = run("observe")
    assert code == 0 and "report=warning" in out
    code, out = run("status", "demo", "memory")
    assert code == 1 and "availability=NOT_FOUND" in out and "registration_proof_verified=True" in out
    code, out = run("proof", "demo", state[:4])
    assert code == 0  # the registration still verifies
    code, out = run("report", "demo", "--json")
    answer = json.loads(out[out.index("{"):])
    assert code == 0 and answer["completeness"]["verdict"] == "warning" and answer["proposed"] == []
    assert _git(omni, "rev-list", "--count", "demo") != "0"
