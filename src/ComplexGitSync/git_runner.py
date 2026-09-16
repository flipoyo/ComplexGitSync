"""git_runner — Git subprocess execution boundary.

Ring: 2 (the sole `import subprocess` module in the codebase)
Contract: given a repository path and a well-formed set of arguments, run
    exactly the corresponding `git` subprocess and either return its parsed
    stdout or raise GitSyncError with the command and captured stderr/stdout
    — never mutates state beyond the git repository being operated on, and
    performs no validation of Git semantics beyond what the git binary itself
    enforces.
Imports: errors, git_repo
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from .errors import GitSyncError
from .git_repo import SyncState

# Git output is bytes, not text. Most of it is UTF-8, but some of it is
# whatever was in the files: ``git merge-tree``'s legacy form prints a diff of
# the conflicting content itself, so a tracked PDF (docs/ keeps its built PDFs
# tracked) puts raw Flate-compressed bytes on stdout. Paths are bytes too, and
# need not be UTF-8 either. Decoding that strictly — which is what
# ``subprocess(text=True)`` does — raises UnicodeDecodeError *before* the
# caller can look at the exit code, turning "are these branches mergeable?"
# into a traceback (.localSpec/DevTickets/archive/20260910_MergeOutputDecoding_
# DevPlanTicket.md). Replacement decoding keeps every byte sequence readable
# enough for the things this module actually looks for, all of which are
# ASCII: exit codes, object ids, ref names, porcelain status codes, and
# conflict markers. Replacement can never swallow an ASCII byte — a UTF-8
# continuation byte is 0x80-0xBF, so a "<" or a digit always survives intact.
_GIT_OUTPUT_ENCODING = "utf-8"
_GIT_OUTPUT_ERRORS = "replace"

#: What ``git merge-tree``'s legacy form writes into the diff of a file it
#: could not merge. Bytes, because the surrounding content is arbitrary.
_MERGE_CONFLICT_MARKER = b"<<<<<<<"

#: What the legacy form writes to stderr for a binary file it could not
#: merge. A binary conflict prints no marker, so this is the only sign of it.
_BINARY_CONFLICT_WARNING = b"Cannot merge binary files"


#: The fetch refspec a repository needs in order to map every branch of its
#: remote into ``refs/remotes/<remote>/``. ``git clone --single-branch`` writes
#: a branch-specific one instead and leaves it there forever, so a branch made
#: afterwards can never resolve ``@{upstream}`` even straight after a
#: successful ``push -u`` — see
#: ``.localSpec/DevTickets/archive/20260911_UpstreamBranchDisplay_DevPlanTicket.md``.
_WIDE_FETCH_REFSPEC = "+refs/heads/*:refs/remotes/{remote}/*"

#: What ``--single-branch`` writes in its place: one branch, mapped by name.
_NARROW_FETCH_REFSPEC = re.compile(r"^\+?refs/heads/(?P<branch>[^*:]+):refs/remotes/(?P<remote>[^*:]+)/(?P=branch)$")


def _is_clone_written_refspec(value: str, remote: str) -> bool:
    """True if *value* is the branch-specific refspec ``--single-branch`` wrote.

    Narrow in both senses: it must map a single named branch, and it must map
    it into *remote*'s own tracking namespace. A refspec pointing anywhere
    else was written by hand and is not this function's business.
    """
    matched = _NARROW_FETCH_REFSPEC.match(value)
    return matched is not None and matched.group("remote") == remote


def _decode_git_output(raw: bytes | str) -> str:
    """Decode one stream of git output under this module's decoding policy.

    Accepts ``str`` unchanged so that a test double or embedder standing in
    for ``subprocess.run`` may hand back already-decoded output without
    having to know which mode this module runs the real one in.
    """
    if isinstance(raw, str):
        return raw
    return raw.decode(_GIT_OUTPUT_ENCODING, errors=_GIT_OUTPUT_ERRORS)


#: Every locale category except ``LC_MESSAGES``. An inherited ``LC_ALL``
#: overrides all of them at once, so :func:`_english_message_locale` must write
#: its value into each of these before dropping it.
_PRESERVED_LOCALE_CATEGORIES = ("LC_CTYPE", "LC_COLLATE", "LC_NUMERIC", "LC_TIME", "LC_MONETARY")


def _english_message_locale(env: dict[str, str]) -> None:
    """Pin *env* so git writes its own messages in English. Mutates in place.

    Git translates its messages and this module reads them: no exit code says
    whether a fetch failed for want of credentials, so ``orchestre.py`` matches
    English fragments of git's prose to decide whether to offer the
    ``--force-protocol`` recovery. On a French machine nothing matched, so the
    hint never fired for anyone whose shell was not English (see
    ``.localSpec/DevTickets/archive/20260911_GitLocaleIndependence_DevPlanTicket.md``). The
    deliberate trade-off: a French user's git errors, quoted inside
    ``GitSyncError``, now read in English — the alternative was a French
    sentence inside an English one *and* a hint nobody ever saw.

    **Do not "simplify" this to ``LC_ALL=C.UTF-8``.** Measured on a
    ``LANG=fr_FR.UTF-8 LANGUAGE=fr_FR`` machine, one failing command: inherited
    gives French; ``LC_ALL=C.UTF-8`` **French too**; ``LC_ALL=C`` and
    ``LC_MESSAGES=C`` English; ``LC_MESSAGES=C`` with ``LC_ALL=fr_FR.UTF-8``
    inherited **French again**. ``C.UTF-8`` fails because gettext consults
    ``$LANGUAGE`` for any locale but ``C``/``POSIX`` — so it works for whoever
    writes it and breaks wherever ``LANGUAGE`` is set. That last row is why
    this is not a one-liner: ``LC_ALL`` outranks ``LC_MESSAGES``, so an
    inherited one defeats the pin. Dropping it after copying its value into
    every other category changes only the prose — verified: ``LC_CTYPE`` still
    reports ``fr_FR.UTF-8`` in the child. ``os.environ`` is never written.
    """
    override = env.pop("LC_ALL", None)
    if override is not None:
        # LC_ALL outranked any explicit per-category value, so restoring the
        # effective locale means overwriting them, not filling in the blanks.
        for category in _PRESERVED_LOCALE_CATEGORIES:
            env[category] = override
    # Redundant once LC_MESSAGES is C, but it removes the one variable whose
    # precedence rules are least obvious to whoever reads this next.
    env.pop("LANGUAGE", None)
    env["LC_MESSAGES"] = "C"


def _non_interactive_git_env() -> dict[str, str]:
    """Environment for a git subprocess that must never block on a prompt.

    ComplexGitSync stores no credentials and has no private-repository
    authentication story (see ``import-submodules``/``discover``'s own
    docs) — every git operation is meant to succeed on ambient
    credentials already cached by the environment, or fail. Without this,
    a missing/expired credential makes ``git`` silently wait on a
    terminal or GUI prompt that never arrives, hanging the whole CLI with
    no visible error until the user notices and interrupts it.
    ``GIT_TERMINAL_PROMPT=0`` disables the terminal prompt; ``GIT_ASKPASS``
    pointed at ``echo`` makes any GUI/helper askpass return an empty
    credential immediately instead of popping up a window. Either way,
    git fails fast with a normal, catchable error instead of hanging.

    Also pins the message locale; :func:`_english_message_locale` says why.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}
    _english_message_locale(env)
    return env


@dataclass
class MergeCheckResult:
    """Would this merge conflict, and in which files? Paths are repo-relative."""

    is_clean: bool
    conflicting_paths: list[Path]


def _extract_paths_from_modern_merge_tree(output: str) -> list[Path]:
    # Git prints three blocks, split by a blank line: the merged tree's id,
    # one conflicting path per line, then notes. The notes also name files
    # that merged fine ("Auto-merging clean.txt"), so stop at the blank line.
    lines = output.split("\n")[1:]
    paths: list[Path] = []
    for line in lines:
        if not line.strip():
            break
        paths.append(Path(line))
    return paths


def _extract_paths_from_legacy_merge_tree(stdout: bytes, stderr: bytes) -> list[Path]:
    # Old git prints a block for every file both branches changed, whether or
    # not it conflicts: a header, the base/our/their lines naming the file,
    # then its diff. Only a block whose diff holds a conflict marker is a real
    # conflict, so track which file each block is about and keep it only once
    # a marker shows up. Binary files get no marker; git names those on stderr.
    conflicting: set[str] = set()
    current: str | None = None

    for line in stdout.split(b"\n"):
        if line[:1] in (b" ", b"\t"):
            parts = _decode_git_output(line).split()
            if len(parts) >= 4 and parts[0] in ("base", "our", "their") and len(parts[2]) == 40:
                current = " ".join(parts[3:])
                continue
        if current and _MERGE_CONFLICT_MARKER in line:
            conflicting.add(current)

    for line in _decode_git_output(stderr).split("\n"):
        marker = _BINARY_CONFLICT_WARNING.decode()
        if marker in line:
            named = line.split(marker, 1)[1].lstrip(": ")
            # git appends " (.our vs. .their)"; a path may itself contain " (".
            cut = named.rfind(" (")
            conflicting.add(named[:cut] if cut > 0 else named)

    return [Path(p) for p in sorted(conflicting)]

# ============================================================
#  GitRunnerProtocol — the boundary other rings type against
# ============================================================


@runtime_checkable
class GitRunnerProtocol(Protocol):
    """Structural contract for anything that can stand in for :class:`GitRunner`.

    Lists every public method `GitRunner` exposes, with its exact signature,
    so callers elsewhere in the codebase (``orchestre.py``'s `Orchestre` /
    `ComplexGitSyncClient`, `operations.py`, `git_tree.py`, `master.py`) can
    eventually type against this Protocol instead of the concrete class, and
    so tests can hand a hand-written fake instead of a `GitRunner` instance
    or a `unittest.mock.Mock`.

    Marked ``@runtime_checkable`` so ``isinstance(obj, GitRunnerProtocol)``
    works as a cheap sanity check (e.g. in tests asserting a fake satisfies
    the contract). `runtime_checkable` only verifies that the named methods
    *exist* on the object — it does not check signatures or return types, so
    this is a smoke check, not a substitute for the static type checker
    actually verifying call sites against the Protocol.
    """

    def remote_branch_exists(self, remote_url: str, branch: str) -> bool: ...

    def remote_tag_exists(self, remote_url: str, tag: str) -> bool: ...

    def remote_get_url(self, repo_path: Path | str, remote_name: str = "origin") -> str | None: ...

    def configure_remote(self, repo_path: Path | str, remote_name: str, remote_url: str) -> None: ...

    def clone(self, remote_url: str, destination: Path | str, *, branch: str) -> None: ...

    def ensure_fetch_refspec(self, repo_path: Path | str, *, remote: str = "origin") -> bool: ...

    def rev_parse_head(self, repo_path: Path | str) -> str: ...

    def commit_authored_at(self, repo_path: Path | str, sha: str) -> str: ...

    def current_branch(self, repo_path: Path | str) -> str | None: ...

    def local_branch_exists(self, repo_path: Path | str, branch: str) -> bool: ...

    def branch_known(
        self, repo_path: Path | str, branch: str, *, remote: str = "origin"
    ) -> bool: ...

    def remote_tracking_branch_exists(
        self, repo_path: Path | str, branch: str, *, remote: str = "origin"
    ) -> bool: ...

    def create_branch(
        self, repo_path: Path | str, branch: str, *, start_point: str | None = None
    ) -> None: ...

    def checkout(self, repo_path: Path | str, branch: str) -> None: ...

    def has_uncommitted_changes(self, repo_path: Path | str) -> bool: ...

    def status_porcelain(self, repo_path: Path | str) -> list[str]: ...

    def tracked_gitlink_paths(self, repo_path: Path | str) -> set[Path]: ...

    def has_staged_changes(self, repo_path: Path | str) -> bool: ...

    def stage_all(self, repo_path: Path | str) -> None: ...

    def stage_path(self, repo_path: Path | str, relative_path: str) -> None: ...

    def commit(
        self,
        repo_path: Path | str,
        message: str,
        *,
        user_name: str | None = None,
        user_email: str | None = None,
    ) -> None: ...

    def push(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
        set_upstream: bool = False,
    ) -> None: ...

    def pull(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
    ) -> None: ...

    def force_pull(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
    ) -> None: ...

    def merge(
        self,
        repo_path: Path | str,
        ref_name: str,
        *,
        ff_only: bool = False,
        no_ff: bool = False,
        message: str | None = None,
    ) -> None: ...

    def can_merge_cleanly(self, repo_path: Path | str, ref_name: str) -> MergeCheckResult: ...

    def merge_abort(self, repo_path: Path | str) -> None: ...

    def configured_merge_tool(self, repo_path: Path | str) -> str | None: ...

    def mergetool(
        self,
        repo_path: Path | str,
        *,
        tool: str | None = None,
        tool_command: str | None = None,
    ) -> None: ...

    def fetch(
        self, repo_path: Path | str, *, remote: str = "origin", ref_name: str | None = None
    ) -> None: ...

    def reset_hard(self, repo_path: Path | str, ref_name: str = "HEAD") -> None: ...

    def clean_untracked(self, repo_path: Path | str) -> None: ...

    def rm_cached(self, repo_path: Path | str, path: str) -> None: ...

    def remove(self, repo_path: Path | str, path: str) -> None: ...

    def create_tag(self, repo_path: Path | str, tag_name: str) -> None: ...

    def remote_exists(self, repo_path: Path | str, remote: str = "origin") -> bool: ...

    def tag_exists(self, repo_path: Path | str, tag_name: str) -> bool: ...

    def has_unresolved_merge(self, repo_path: Path | str) -> bool: ...

    def branch_tracking_state(self, repo_path: Path | str) -> SyncState | None: ...

    def upstream_ref(self, repo_path: Path | str) -> str | None: ...

    def branch_tracking_counts(self, repo_path: Path | str) -> tuple[int, int] | None: ...

    def local_only_commit_count(self, repo_path: Path | str) -> int: ...

    def has_upstream(self, repo_path: Path | str) -> bool: ...

    def upstream_configured(self, repo_path: Path | str) -> bool: ...


# ============================================================
#  GitRunner — the concrete Ring-2 implementation
# ============================================================


@dataclass(slots=True)
class GitRunner:
    """Git subprocess wrapper — executes git commands for clone/checkout/push actions."""

    executable: str = "git"

    def remote_branch_exists(self, remote_url: str, branch: str) -> bool:
        return self._remote_ref_exists(remote_url, "--heads", branch)

    def remote_tag_exists(self, remote_url: str, tag: str) -> bool:
        return self._remote_ref_exists(remote_url, "--tags", tag)

    def _remote_ref_exists(self, remote_url: str, ref_selector: str, ref_name: str) -> bool:
        completed = self._run("ls-remote", ref_selector, remote_url, ref_name)
        return bool(completed.stdout.strip())

    def remote_get_url(self, repo_path: Path | str, remote_name: str = "origin") -> str | None:
        """Return the URL configured for *remote_name*, or ``None`` when unset.

        Used by :meth:`ComplexGitSyncClient.discover_repos` to recover a
        checked-out repository's upstream address. A repository with no such
        remote is a normal, reportable condition — not an error — so the
        missing case is returned as ``None`` rather than raised.
        """
        try:
            url = self._run("remote", "get-url", remote_name, cwd=repo_path).stdout.strip()
        except GitSyncError:
            return None
        return url or None

    def configure_remote(self, repo_path: Path | str, remote_name: str, remote_url: str) -> None:
        """Add or update *remote_name* in *repo_path*."""
        try:
            existing = self._run("remote", "get-url", remote_name, cwd=repo_path).stdout.strip()
        except GitSyncError:
            self._run("remote", "add", remote_name, remote_url, cwd=repo_path)
            return
        if existing != remote_url:
            self._run("remote", "set-url", remote_name, remote_url, cwd=repo_path)

    def clone(self, remote_url: str, destination: Path | str, *, branch: str) -> None:
        destination_path = Path(destination)
        if destination_path.exists():
            if not destination_path.is_dir() or any(destination_path.iterdir()):
                raise GitSyncError(
                    f"Clone destination already exists and is not empty: {destination_path}"
                )
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        args: list[str] = []
        if self._uses_file_transport(remote_url):
            args.extend(["-c", "protocol.file.allow=always"])
        args.extend(
            ["clone", "--branch", branch, "--single-branch", remote_url, str(destination_path)]
        )
        self._run(*args)
        # --single-branch limits the download *and* narrows the stored fetch
        # refspec. Keep the cheap download; widen what the clone remembers.
        self.ensure_fetch_refspec(destination_path)

    def ensure_fetch_refspec(self, repo_path: Path | str, *, remote: str = "origin") -> bool:
        """Make *remote*'s fetch refspec map every branch. Idempotent.

        Returns ``True`` when it changed the configuration, ``False`` when the
        repository already mapped every branch — so a caller may repair a
        workspace on every invocation without writing anything after the
        first.

        Why this exists: ``git clone --single-branch`` writes
        ``+refs/heads/B:refs/remotes/origin/B`` into ``remote.origin.fetch``
        and leaves it there. ``git push -u origin X`` then does half its job —
        it writes ``branch.X.remote`` and ``branch.X.merge``, but it can only
        create ``refs/remotes/origin/X`` if the fetch refspec maps that
        branch. ``@{upstream}`` needs the *remote-tracking ref*, not the
        config, so it fails on every branch made after the clone. Widening the
        refspec is enough on its own: no extra fetch is needed, because
        ``push -u`` writes the tracking ref itself once the mapping exists.

        A repository whose refspec was configured by hand keeps what it has —
        the wide one is *added* rather than substituted. Only the
        branch-specific form that ``--single-branch`` writes is replaced,
        since it is exactly the thing being repaired and is redundant once the
        wildcard covers it.
        """
        wanted = _WIDE_FETCH_REFSPEC.format(remote=remote)
        configured = [
            line.strip()
            for line in self._query(
                "config", "--get-all", f"remote.{remote}.fetch", cwd=repo_path
            ).stdout.splitlines()
            if line.strip()
        ]
        if wanted in configured:
            return False
        clone_written = configured and all(
            _is_clone_written_refspec(value, remote) for value in configured
        )
        flag = "--replace-all" if clone_written else "--add"
        self._run("config", flag, f"remote.{remote}.fetch", wanted, cwd=repo_path)
        return True

    def rev_parse_head(self, repo_path: Path | str) -> str:
        return self._run("rev-parse", "HEAD", cwd=repo_path).stdout.strip()

    def commit_authored_at(self, repo_path: Path | str, sha: str) -> str:
        """When a commit was authored, as the memory records it.

        A question, not an operation: a commit that cannot be read — one
        rewritten away between the write and the recording — answers with an
        empty string rather than raising, because a missing date is not a
        reason to lose the message it belongs to.
        """
        completed = self._query("show", "-s", "--format=%aI", sha, cwd=repo_path)
        return completed.stdout.strip() if completed.returncode == 0 else ""

    def current_branch(self, repo_path: Path | str) -> str | None:
        branch = self._run("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_path).stdout.strip()
        return None if branch == "HEAD" else branch

    def local_branch_exists(self, repo_path: Path | str, branch: str) -> bool:
        """Return ``True`` if *branch* exists as a local branch in *repo_path*."""
        try:
            self._run("rev-parse", "--verify", f"refs/heads/{branch}", cwd=repo_path)
            return True
        except GitSyncError:
            return False

    def branch_known(
        self, repo_path: Path | str, branch: str, *, remote: str = "origin"
    ) -> bool:
        """Whether *branch* exists locally **or** as a remote-tracking ref.

        Offline: reads the refs this clone already has, and never contacts
        the remote. :meth:`remote_branch_exists` is the one that asks the
        network, and takes a URL rather than a path because it can run
        before a clone exists.

        Used to decide whether a derived branch is real, on a code path —
        ``checkout`` — that must keep working with no network.
        """
        if self.local_branch_exists(repo_path, branch):
            return True
        return self.remote_tracking_branch_exists(repo_path, branch, remote=remote)

    def remote_tracking_branch_exists(
        self, repo_path: Path | str, branch: str, *, remote: str = "origin"
    ) -> bool:
        """Whether ``refs/remotes/<remote>/<branch>`` exists in *repo_path*.

        The half of :meth:`branch_known` that says the branch came from
        somebody else. A caller about to *create* a local branch needs that
        distinction: starting it at ``HEAD`` when the remote already has a
        branch of that name forks a second, unrelated history under a name
        the user believes they are joining.

        Offline, like :meth:`branch_known`: it reads the refs this clone
        already holds. Whether they are current is the business of whatever
        last fetched.
        """
        return (
            self._query(
                "rev-parse", "--verify", f"refs/remotes/{remote}/{branch}", cwd=repo_path
            ).returncode
            == 0
        )

    def create_branch(
        self, repo_path: Path | str, branch: str, *, start_point: str | None = None
    ) -> None:
        """Create *branch* in *repo_path* without switching to it (``git branch``).

        *start_point* is where the branch begins, defaulting to ``HEAD``.
        Given a remote-tracking ref it is passed with ``--track``, so the new
        branch both starts from that history and records it as its upstream —
        which is what makes ``status`` measure it from the first command.
        """
        if start_point is None:
            self._run("branch", branch, cwd=repo_path)
            return
        self._run("branch", "--track", branch, start_point, cwd=repo_path)

    def checkout(self, repo_path: Path | str, branch: str) -> None:
        """Switch *repo_path* to *branch* (``git checkout``)."""
        self._run("checkout", branch, cwd=repo_path)

    def has_uncommitted_changes(self, repo_path: Path | str) -> bool:
        """Return ``True`` if *repo_path* has any tracked or staged modifications."""
        result = self._run("status", "--porcelain", cwd=repo_path)
        return bool(result.stdout.strip())

    def status_porcelain(self, repo_path: Path | str) -> list[str]:
        """Return ``git status --porcelain`` lines for *repo_path*."""
        result = self._run("status", "--porcelain", cwd=repo_path)
        return [line for line in result.stdout.splitlines() if line.strip()]

    def tracked_gitlink_paths(self, repo_path: Path | str) -> set[Path]:
        """Return paths tracked as gitlinks (mode ``160000``) in *repo_path*."""
        result = self._run("ls-files", "--stage", cwd=repo_path)
        gitlinks: set[Path] = set()
        for line in result.stdout.splitlines():
            if not line.startswith("160000 "):
                continue
            try:
                path = line.split("\t", 1)[1]
            except IndexError:
                continue
            gitlinks.add(Path(path))
        return gitlinks

    def has_staged_changes(self, repo_path: Path | str) -> bool:
        """Return ``True`` if *repo_path* has changes staged for the next commit."""
        result = self._run("diff", "--cached", "--name-only", cwd=repo_path)
        return bool(result.stdout.strip())

    def stage_all(self, repo_path: Path | str) -> None:
        """Stage all changes in *repo_path* (``git add --all``)."""
        self._run("add", "--all", cwd=repo_path)

    def stage_path(self, repo_path: Path | str, relative_path: str) -> None:
        """Stage a single path in *repo_path* (``git add -- <relative_path>``)."""
        self._run("add", "--", relative_path, cwd=repo_path)

    def commit(
        self,
        repo_path: Path | str,
        message: str,
        *,
        user_name: str | None = None,
        user_email: str | None = None,
    ) -> None:
        """Commit staged changes in *repo_path* with *message* (``git commit``)."""
        args: list[str] = []
        if user_name is not None:
            args.extend(["-c", f"user.name={user_name}"])
        if user_email is not None:
            args.extend(["-c", f"user.email={user_email}"])
        args.extend(["commit", "-m", message])
        self._run(*args, cwd=repo_path)

    def push(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
        set_upstream: bool = False,
    ) -> None:
        """Push *remote* (and optionally *ref_name*) in *repo_path* (``git push``)."""
        args = ["push"]
        if set_upstream:
            args.append("-u")
        args.append(remote)
        if ref_name:
            args.append(ref_name)
        self._run(*args, cwd=repo_path)

    def pull(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
    ) -> None:
        """Pull *remote* (and optionally *ref_name*) in *repo_path* (``git pull --ff-only``)."""
        args = ["pull", "--ff-only", remote]
        if ref_name:
            args.append(ref_name)
        self._run(*args, cwd=repo_path)

    def force_pull(
        self,
        repo_path: Path | str,
        *,
        remote: str = "origin",
        ref_name: str | None = None,
    ) -> None:
        """Force the local branch to match *remote/ref_name* and clean untracked files."""
        # Deliberately not git_branch.DEFAULT_BRANCH: this module knows
        # nothing about a .cgs and must stay usable on a bare repository
        # path with no tree behind it. Reaching a literal here means both
        # the caller's ref and the checkout's own branch were unreadable —
        # a last resort before `git fetch`, not a link in the .cgs fallback
        # chain that git_branch.py owns.
        selected_ref = ref_name or self.current_branch(repo_path) or "main"
        self._run("fetch", remote, selected_ref, cwd=repo_path)
        self._run("checkout", "-B", selected_ref, "FETCH_HEAD", cwd=repo_path)
        self.clean_untracked(repo_path)

    def merge(
        self,
        repo_path: Path | str,
        ref_name: str,
        *,
        ff_only: bool = False,
        no_ff: bool = False,
        message: str | None = None,
    ) -> None:
        """Merge *ref_name* into the current branch of *repo_path* (``git merge``).

        ``ff_only`` and ``no_ff`` map to Git's own flags and are mutually
        exclusive. With neither, Git's default applies: fast-forward when it
        can, a merge commit when it cannot.

        Raises :exc:`~.errors.GitSyncError` on a conflict, leaving the merge
        in progress exactly as ``git`` does — the caller decides whether to
        :meth:`merge_abort`. Ask :meth:`can_merge_cleanly` first to avoid
        getting there at all.
        """
        if ff_only and no_ff:
            raise ValueError("merge: ff_only and no_ff are mutually exclusive")
        args = ["merge"]
        if ff_only:
            args.append("--ff-only")
        if no_ff:
            args.append("--no-ff")
        if message is not None:
            args.extend(["-m", message])
        args.append(ref_name)
        self._run(*args, cwd=repo_path)

    def can_merge_cleanly(self, repo_path: Path | str, ref_name: str) -> MergeCheckResult:
        """Whether merging *ref_name* would apply without a conflict.

        **Read-only.** Neither branch below touches the worktree, the index,
        or ``HEAD`` — which is what makes it safe to ask about every
        repository in a tree before merging any of them. A trial
        ``git merge`` would answer the same question but would leave a
        repository mid-merge if the process died between the merge and the
        abort, and a preflight must not be able to break what it is checking.

        Two ways to ask, because the answer depends on the Git in front of
        us. ``merge-tree --write-tree`` (Git 2.38+) prints the merged tree's
        OID and exits 0 when the merge applies, or exits 1 and lists the
        conflicted paths. Older Git knows only the three-argument form, which
        needs an explicit merge base and reports conflicts as markers inside
        a diff, exiting 0 either way — and that diff carries the *content* of
        the conflicting files, which is why this branch reads raw bytes: a
        repository holding a PDF, an image, or a latin-1 file must still get
        an answer rather than a decoding error. ``<<<<<<<`` is ASCII, so
        searching the bytes for it is exact regardless of what surrounds it.

        A repository that cannot name *ref_name*, or shares no history with
        it, is not "clean" — it is unmergeable, and the caller gets
        ``is_clean=False`` rather than an exception, because this is a
        question, not an operation. The same applies to any unexpected failure
        of either form: unmergeable, never assumed clean. Such a repository
        conflicts without naming a file, so the path list comes back empty.
        """
        head = self.current_branch(repo_path) or "HEAD"

        modern = self._query(
            "merge-tree", "--write-tree", "--name-only", head, ref_name, cwd=repo_path
        )
        if modern.returncode == 0:
            return MergeCheckResult(is_clean=True, conflicting_paths=[])
        if modern.returncode == 1 and modern.stdout.strip():
            paths = _extract_paths_from_modern_merge_tree(modern.stdout)
            return MergeCheckResult(is_clean=False, conflicting_paths=paths)

        # Anything else from the modern form — a usage error on old Git
        # (exit 129 before 2.38, where --write-tree does not exist), a bad
        # ref — means fall through and ask the way old Git understands.
        base = self._query("merge-base", head, ref_name, cwd=repo_path)
        if base.returncode != 0 or not base.stdout.strip():
            return MergeCheckResult(is_clean=False, conflicting_paths=[])
        legacy = self._query_bytes(
            "merge-tree", base.stdout.strip(), head, ref_name, cwd=repo_path
        )
        if legacy.returncode != 0:
            return MergeCheckResult(is_clean=False, conflicting_paths=[])

        has_conflict_marker = _MERGE_CONFLICT_MARKER in legacy.stdout
        has_binary_conflict = _BINARY_CONFLICT_WARNING in legacy.stderr
        is_clean = not (has_conflict_marker or has_binary_conflict)

        if is_clean:
            return MergeCheckResult(is_clean=True, conflicting_paths=[])
        else:
            paths = _extract_paths_from_legacy_merge_tree(legacy.stdout, legacy.stderr)
            return MergeCheckResult(is_clean=False, conflicting_paths=paths)

    def tool_version(self, executable: str) -> str | None:
        """The version string *executable* reports, or ``None`` if it has none.

        Not a Git question, and here anyway: this module is the project's
        only ``import subprocess``, and a second importer would break the
        rule that makes the decoding and environment policies inescapable.
        The memory workstream records which tools produced each ledger entry
        (``.localSpec/AdditionalSpecs.md``, *The hash-chained register*), and
        that answer has to come from somewhere.

        ``None`` means "not installed" — a missing executable is an ordinary
        answer here, not a failure. The caller decides what to record.
        """
        try:
            completed = subprocess.run(
                [executable, "--version"],
                capture_output=True,
                check=False,
                env=_non_interactive_git_env(),
            )
        except (OSError, ValueError):
            return None
        if completed.returncode != 0:
            return None
        reported = _decode_git_output(completed.stdout).strip()
        return reported.splitlines()[0].strip() if reported else None

    def _query_bytes(
        self,
        *args: str,
        cwd: Path | str | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run a git command for its answer, undecoded; never raises.

        The raw boundary. A caller that searches git's output for an ASCII
        marker inside content it does not control — ``can_merge_cleanly``'s
        legacy conflict check — wants the bytes exactly as git wrote them,
        with no decoding step able to fail or to alter what it is searching
        for. Everything else should use :meth:`_query`.
        """
        return subprocess.run(
            [self.executable, *args],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            check=False,
            env=_non_interactive_git_env(),
        )

    def _query(
        self,
        *args: str,
        cwd: Path | str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a git command for its answer, not its effect; never raises.

        :meth:`_run` raises on a non-zero exit because its callers are
        performing an operation. A caller asking a *question* needs the exit
        code itself, so this returns the completed process untouched.

        Output is decoded under this module's replacement policy (see
        :func:`_decode_git_output`), so undecodable bytes anywhere in the
        answer can never turn a question into an exception.
        """
        completed = self._query_bytes(*args, cwd=cwd)
        return subprocess.CompletedProcess(
            completed.args,
            completed.returncode,
            _decode_git_output(completed.stdout),
            _decode_git_output(completed.stderr),
        )

    def merge_abort(self, repo_path: Path | str) -> None:
        """Abort a merge left in progress (``git merge --abort``)."""
        self._run("merge", "--abort", cwd=repo_path)

    def configured_merge_tool(self, repo_path: Path | str) -> str | None:
        """The merge tool the user already configured, if any."""
        answer = self._query("config", "--get", "merge.tool", cwd=repo_path)
        if answer.returncode != 0:
            return None
        return answer.stdout.strip() or None

    def mergetool(
        self,
        repo_path: Path | str,
        *,
        tool: str | None = None,
        tool_command: str | None = None,
    ) -> None:
        """Open the conflicted files in a merge tool (``git mergetool``).

        Git stages each file the user resolves, so callers must not stage
        again. *tool_command* is passed for this one call: the user's own git
        config is never written.
        """
        # keepBackup=false: the .orig files git leaves otherwise are
        # untracked, so the next `cgitsync status` would call the repository
        # dirty and the next `cgitsync add` would stage them.
        args: list[str] = []
        if tool is not None:
            if tool_command is not None:
                args += ["-c", f"mergetool.{tool}.cmd={tool_command}"]
                args += ["-c", f"mergetool.{tool}.trustExitCode=true"]
            args += ["-c", f"merge.tool={tool}"]
        args += ["-c", "mergetool.keepBackup=false"]
        self._run(*args, "mergetool", "--no-prompt", cwd=repo_path)

    def fetch(
        self, repo_path: Path | str, *, remote: str = "origin", ref_name: str | None = None
    ) -> None:
        """Update remote-tracking refs from *remote* (``git fetch``).

        Touches no branch and no worktree — only ``refs/remotes``. Separate
        from :meth:`pull`, which fetches *and* merges into the current
        branch; a caller that wants to decide what to merge for itself needs
        the two halves apart.
        """
        args = ["fetch", remote]
        if ref_name:
            args.append(ref_name)
        self._run(*args, cwd=repo_path)

    def reset_hard(self, repo_path: Path | str, ref_name: str = "HEAD") -> None:
        """Discard local tracked changes in *repo_path*."""
        self._run("reset", "--hard", ref_name, cwd=repo_path)

    def clean_untracked(self, repo_path: Path | str) -> None:
        """Remove untracked files and directories in *repo_path*."""
        self._run("clean", "-fd", cwd=repo_path)

    def rm_cached(self, repo_path: Path | str, path: str) -> None:
        """Remove *path* from the index (``git rm --cached``), keeping the working tree.

        Drops a tracked gitlink without deleting the child's working tree or
        its ``.git`` directory, preserving any local history inside the child.
        """
        self._run("rm", "--cached", path, cwd=repo_path)

    def remove(self, repo_path: Path | str, path: str) -> None:
        """Remove *path* from the working tree and stage the removal (``git rm -- <path>``).

        A plain tracked-file deletion, distinct from :meth:`rm_cached`
        (index-only, built for the submodule-to-plain-clone conversion).
        """
        self._run("rm", "--", path, cwd=repo_path)

    def create_tag(self, repo_path: Path | str, tag_name: str) -> None:
        """Create *tag_name* in *repo_path*."""
        self._run("tag", tag_name, cwd=repo_path)

    def remote_exists(self, repo_path: Path | str, remote: str = "origin") -> bool:
        """Return ``True`` when *remote* exists in *repo_path*."""
        try:
            self._run("remote", "get-url", remote, cwd=repo_path)
            return True
        except GitSyncError:
            return False

    def _ref_query(self, *args: str, cwd: Path | str) -> bool:
        """Ask git a yes/no question that answers ``1`` for no. Never guesses.

        ``show-ref`` and ``rev-parse --verify`` report a missing ref as exit
        ``1`` and a real failure as anything else. Collapsing the two into
        ``False`` would report a damaged repository as a tag that is merely
        absent, so only ``1`` becomes ``False``; the rest raises.
        """
        completed = self._query(*args, cwd=cwd)
        if completed.returncode in (0, 1):
            return completed.returncode == 0
        command = " ".join([self.executable, *args])
        details = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
        raise GitSyncError(f"Git command failed ({command}): {details}")

    def tag_exists(self, repo_path: Path | str, tag_name: str) -> bool:
        """Return ``True`` when *tag_name* already exists in *repo_path*."""
        return self._ref_query(
            "show-ref", "--verify", "--quiet", f"refs/tags/{tag_name}", cwd=repo_path
        )

    def has_unresolved_merge(self, repo_path: Path | str) -> bool:
        """Return ``True`` when *repo_path* has an in-progress merge conflict."""
        return self._ref_query("rev-parse", "--verify", "--quiet", "MERGE_HEAD", cwd=repo_path)

    def branch_tracking_state(self, repo_path: Path | str) -> SyncState | None:
        """Return upstream tracking state for the current branch in *repo_path*."""
        counts = self.branch_tracking_counts(repo_path)
        if counts is None:
            return None
        ahead, behind = counts
        if ahead and behind:
            return SyncState.DIVERGED
        if ahead:
            return SyncState.AHEAD
        if behind:
            return SyncState.BEHIND
        return SyncState.ALIGNED

    def upstream_ref(self, repo_path: Path | str) -> str | None:
        """Return the upstream ref for the current branch, e.g. ``origin/main``."""
        upstream = self._query(
            "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", cwd=repo_path
        )
        if upstream.returncode != 0:
            return None
        return upstream.stdout.strip() or None

    def branch_tracking_counts(self, repo_path: Path | str) -> tuple[int, int] | None:
        """Return ``(ahead, behind)`` counts against upstream for the current branch."""
        if self.upstream_ref(repo_path) is None:
            return None
        counts = self._run("rev-list", "--left-right", "--count", "HEAD...@{upstream}", cwd=repo_path)
        ahead_raw, behind_raw = counts.stdout.strip().split()
        return (int(ahead_raw), int(behind_raw))

    def local_only_commit_count(self, repo_path: Path | str) -> int:
        """Count commits reachable from HEAD that no remote-tracking ref holds.

        Read-only, and a sharper question than "is the branch ahead of its
        upstream": it is true of a branch with no upstream at all, and false
        of a detached HEAD parked on a commit the remote already has -- which
        is exactly what a submodule checkout looks like. ``0`` means every
        commit here can be fetched again.

        Returns ``0`` for a repository with no commits yet, since an unborn
        HEAD holds nothing to lose.
        """
        counted = self._query("rev-list", "--count", "HEAD", "--not", "--remotes", cwd=repo_path)
        if counted.returncode != 0:
            return 0
        raw = counted.stdout.strip()
        return int(raw) if raw.isdigit() else 0

    def upstream_configured(self, repo_path: Path | str) -> bool:
        """Return ``True`` when the current branch *names* an upstream.

        The other half of :meth:`has_upstream`, which asks whether the
        upstream **resolves**. The two answers differ exactly when a branch
        was pushed with ``-u`` into a repository whose fetch refspec does not
        map it: the configuration is written, the remote-tracking ref is not
        (see :meth:`ensure_fetch_refspec`). Telling them apart is what lets
        ``status`` say "never pushed" and "pushed, but unmeasurable" in
        different words instead of calling both ``unknown``.

        Reads ``branch.<current>.merge``, which is what ``git push -u`` and
        ``git branch --set-upstream-to`` write. A detached HEAD names no
        branch, so it names no upstream either: ``False``.
        """
        branch = self.current_branch(repo_path)
        if branch is None:
            return False
        return (
            self._query("config", "--get", f"branch.{branch}.merge", cwd=repo_path).returncode == 0
        )

    def has_upstream(self, repo_path: Path | str) -> bool:
        """Return ``True`` when the current branch has a *resolvable* upstream.

        True only when the remote-tracking ref exists, which is the question
        ``git rev-parse @{upstream}`` answers and the one a caller about to
        measure ahead/behind counts needs. :meth:`upstream_configured` asks
        the weaker question — whether the branch names one at all.
        """
        return self.upstream_ref(repo_path) is not None

    def _run(
        self,
        *args: str,
        cwd: Path | str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = " ".join([self.executable, *args])
        # subprocess raises a bare FileNotFoundError when cwd is gone, which
        # callers that degrade on GitSyncError never catch — a repository
        # deleted from under the loaded .gts crashed status instead of
        # showing an error row. Fail early, in this module's own error type.
        if cwd is not None and not Path(cwd).is_dir():
            raise GitSyncError(f"Git command failed ({command}): no such directory '{cwd}'.")
        raw = subprocess.run(
            [self.executable, *args],
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            check=False,
            env=_non_interactive_git_env(),
        )
        # Same decoding policy as _query: an operation must fail with this
        # module's own error naming the command, never with a decoding
        # traceback from a non-UTF-8 path or a byte git echoed back.
        completed = subprocess.CompletedProcess(
            raw.args,
            raw.returncode,
            _decode_git_output(raw.stdout),
            _decode_git_output(raw.stderr),
        )
        if completed.returncode != 0:
            details = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
            raise GitSyncError(f"Git command failed ({command}): {details}")
        return completed

    @staticmethod
    def _uses_file_transport(remote_url: str) -> bool:
        parsed = urlsplit(remote_url)
        if parsed.scheme == "file":
            return True
        drive_letter = len(parsed.scheme) == 1 and parsed.scheme.isalpha()
        if drive_letter and len(remote_url) >= 2 and remote_url[1] == ":":
            return True
        if parsed.scheme:
            return False
        return bool(remote_url) and not remote_url.startswith("git@")
