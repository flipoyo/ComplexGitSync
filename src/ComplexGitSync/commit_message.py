"""The commit-message rule, checked before a commit is made instead of hoped for.

Ring: 1
Contract: decide whether a message a person or an agent wrote by hand keeps
    the shape ``AgentConduct.md`` §2 gives it, and say which rule it broke.
    Reads the tree's ``pyproject.toml`` and, for the version, its
    ``pixi.toml`` (``ProjectVersion``); runs no Git; never rewrites a
    message -- a validator that rewrites is a second author.
Imports: errors, project_version

Commit ``701a98f`` reached this project's public remote with every
backtick-quoted phrase replaced by whatever the shell had substituted for it,
including the live output of ``git rev-parse --abbrev-ref HEAD``. The rule
existed and the violation was mechanical; the only check between the two was
a person reading carefully at the end of a long session.

**Whose rule this is.** ``<project-name>-<version>`` and the three-line cap are
DevSpec's convention for the messages a project's own contributors write, not
a law of Git and not something an arbitrary user of ``cgitsync`` has agreed
to. So the policy binds only a tree that has adopted DevSpec: one whose root
holds both a ``pyproject.toml`` naming a project and version, and the
``AgentConduct.md`` that states the rule. Any other tree gets ``None`` and
commits exactly as before. The prefix is accepted under the project's
packaging name *or* its console-script name, because ``pyproject.toml`` says
``ComplexGitSync`` and this project writes ``cgitsync-5.2.1``. The version is
the one a release is tagged with (``project_version.py``), read from
``pixi.toml`` first and from ``pyproject.toml`` only when that declares none.

**What it can and cannot see.** A message the shell has already mangled
arrives here as an ordinary, well-formed string -- the substituted text is
indistinguishable from prose. This module cannot recover that case; a commit
made outside ``cgitsync`` is what ``autofix``'s tip-commit inspection is for.
What it does stop is a message that still carries the trap: a surviving
backtick or ``$(`` is one paste away from being run.

Messages ComplexGitSync writes for itself (``--commit-gitignore``, the memory
push commit) do not pass through here; ``AgentConduct.md`` §2 says it governs
what a person or an agent writes by hand. ``freeze_release`` does: it commits
through ``ComplexGitSyncClient.commit``, so a release message is held to the
rule like any other -- including the release name it falls back to when
called from Python with no message, which will not carry the prefix.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .errors import GitSyncError
from .project_version import ProjectVersion

__all__ = ["CommitMessagePolicy"]

#: Where a tree says it adopted DevSpec. Its presence is what makes the
#: policy apply; a tree without it commits under no rule of ours.
_CONDUCT_PATH = Path(".agent") / ".distant" / "dev-sync" / "AgentConduct.md"

_MAX_LINES = 3

#: An agent-credit trailer: a ``Co-Authored-By:`` line, or a "Generated
#: with/by ..." line, allowing the emoji such lines usually open with.
_CREDIT_LINE = re.compile(
    r"^\W*(co-authored-by\s*:|generated\s+(with|by)\b)", re.IGNORECASE
)


@dataclass(frozen=True)
class CommitMessagePolicy:
    """What a conforming commit message looks like for one project.

    Built from a project's name and version, so checking a message is a pure
    question. :meth:`for_tree` is the only place a file is read.
    """

    stems: tuple[str, ...]
    version: str

    @classmethod
    def for_tree(cls, root: Path) -> CommitMessagePolicy | None:
        """The policy binding the tree rooted at *root*, or ``None``.

        ``None`` means the tree has not adopted DevSpec -- there is no
        ``AgentConduct.md`` under it, or no readable ``pyproject.toml`` naming
        a project and a version -- and so nothing here applies to it.
        """
        root = Path(root)
        if not (root / _CONDUCT_PATH).is_file():
            return None
        manifest = root / "pyproject.toml"
        if not manifest.is_file():
            return None
        try:
            project = tomllib.loads(manifest.read_text(encoding="utf-8")).get("project", {})
        except (OSError, tomllib.TOMLDecodeError):
            return None
        name = project.get("name")
        version = ProjectVersion.read(root) or project.get("version")
        if not isinstance(name, str) or not isinstance(version, str) or not name or not version:
            return None
        scripts = project.get("scripts", {})
        stems = (name, *(str(script) for script in scripts))
        return cls(stems=tuple(dict.fromkeys(stems)), version=version)

    def violations(self, message: str) -> list[str]:
        """Every rule *message* breaks, each named; empty when it conforms."""
        broken: list[str] = []
        body = message.strip()
        if not body:
            return ["the message is empty"]
        if not self._has_prefix(body):
            broken.append(
                f"it must start with '<project-name>-<version>' -- for example "
                f"'{self.stems[-1]}-{self.version}' -- one dash, no space and no 'v'"
            )
        lines = body.splitlines()
        if len(lines) > _MAX_LINES:
            broken.append(f"it must be {_MAX_LINES} lines at most, and is {len(lines)}")
        if "`" in body:
            broken.append(
                "it contains a backtick, which a shell runs as a command "
                "if the message is ever pasted into one"
            )
        if "$(" in body:
            broken.append("it contains '$(', which a shell runs as a command")
        if any(_CREDIT_LINE.match(line) for line in lines):
            broken.append(
                "it carries an agent-credit trailer ('Co-Authored-By:' or "
                "'Generated with'); an agent is never credited on a commit"
            )
        return broken

    def require(self, message: str) -> None:
        """Raise :class:`GitSyncError` naming every rule *message* breaks."""
        broken = self.violations(message)
        if not broken:
            return
        rules = "\n".join(f"  - {rule}" for rule in broken)
        raise GitSyncError(
            "commit message refused (AgentConduct.md §2), nothing was committed:\n"
            f"{rules}\n"
            "Write the message again in plain English, one message for every "
            "repository the change touched."
        )

    def _has_prefix(self, body: str) -> bool:
        lowered = body.lower()
        for stem in self.stems:
            prefix = f"{stem}-{self.version}".lower()
            if lowered.startswith(prefix):
                rest = body[len(prefix) :]
                # `cgitsync-3.3.01` is another version, not this one.
                if not rest or not (rest[0].isalnum() or rest[0] == "."):
                    return True
        return False
