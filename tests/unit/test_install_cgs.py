"""Root ``install.cgs`` must stay byte-identical to ``examples/complexgitsync.cgs``.

``install.cgs`` exists purely for discoverability (a fresh clone's root
carries an install-flavoured name); ``examples/complexgitsync.cgs`` remains
the single source of truth. Nothing enforces that at runtime, so a test
does.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_install_cgs_matches_examples_complexgitsync_cgs():
    install_cgs = _REPO_ROOT / "install.cgs"
    canonical = _REPO_ROOT / "examples" / "complexgitsync.cgs"

    assert install_cgs.is_file(), "install.cgs must exist at the repo root"
    assert install_cgs.read_bytes() == canonical.read_bytes(), (
        "install.cgs has drifted from examples/complexgitsync.cgs — "
        "copy the latter over the former to resync"
    )
