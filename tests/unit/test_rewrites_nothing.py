"""ComplexGitSync rewrites nothing — a guard over the whole of `src/`.

`AdditionalSpecs.md`, *The hard prohibitions*: no command, `autofix` included,
amends, rebases, squashes, filters or force-pushes. The rule was only prose
until RuleConformity G3, so an agent could plan and build a rewrite without a
test noticing; the `tmpAutoFix` branch did exactly that. This reads every
string literal in `git_runner.py` and fails on a Git argument that rewrites
history.

Why only that file: it is the one module allowed to run Git (`subprocess`
confinement, checked by `check-ceilings`), so a rewrite has to pass through an
argument built there. A CLI option elsewhere may share a word with a Git flag
without being one (`init-from-submodules --force` is ComplexGitSync's own).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "ComplexGitSync"
GIT_RUNNER = SRC / "git_runner.py"

#: Exact literals: each is a Git subcommand or flag that rewrites or force-pushes.
FORBIDDEN = {
    "--amend",
    "rebase",
    "--force",
    "--force-with-lease",
    "filter-branch",
    "filter-repo",
    "cherry-pick",
    "--squash",
}

#: Fetch refspecs that begin with `+` update remote-tracking refs, never a branch.
ALLOWED_PLUS_REFSPECS = {"+refs/heads/*:refs/remotes/{remote}/*"}


def _literals(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def test_no_git_argument_in_src_rewrites_history():
    found = [
        f"git_runner.py:{line}: {value!r}"
        for line, value in _literals(GIT_RUNNER)
        if value in FORBIDDEN
    ]
    assert not found, "ComplexGitSync must rewrite nothing:\n" + "\n".join(found)


def test_no_push_refspec_starts_with_plus():
    found = [
        f"git_runner.py:{line}: {value!r}"
        for line, value in _literals(GIT_RUNNER)
        if value.startswith("+refs/") and value not in ALLOWED_PLUS_REFSPECS
    ]
    assert not found, "a '+' refspec forces an update:\n" + "\n".join(found)


def test_the_guard_sees_a_planted_rewrite(tmp_path):
    planted = tmp_path / "planted.py"
    planted.write_text('ARGS = ["commit", "--amend", "-m", "x"]\n', encoding="utf-8")

    assert any(value in FORBIDDEN for _, value in _literals(planted))


def test_git_runner_is_the_only_module_that_runs_git():
    importers = [
        path.relative_to(SRC).as_posix()
        for path in sorted(SRC.rglob("*.py"))
        if any(
            isinstance(node, ast.Import) and any(alias.name == "subprocess" for alias in node.names)
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    ]
    assert importers == ["git_runner.py"], importers
