from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from .errors import GitSyncError


@dataclass(slots=True)
class GitRunner:
	executable: str = "git"

	def remote_branch_exists(self, remote_url: str, branch: str) -> bool:
		completed = self._run("ls-remote", "--heads", remote_url, branch)
		return bool(completed.stdout.strip())

	def clone(self, remote_url: str, destination: Path | str, *, branch: str) -> None:
		destination_path = Path(destination)
		if destination_path.exists():
			if not destination_path.is_dir() or any(destination_path.iterdir()):
				raise GitSyncError(
					f"Clone destination already exists and is not empty: {destination_path}"
				)

		destination_path.parent.mkdir(parents=True, exist_ok=True)
		args = ["clone", "--branch", branch, "--single-branch", remote_url, str(destination_path)]
		self._run(*args)

	def rev_parse_head(self, repo_path: Path | str) -> str:
		return self._run("rev-parse", "HEAD", cwd=repo_path).stdout.strip()

	def current_branch(self, repo_path: Path | str) -> str | None:
		branch = self._run("rev-parse", "--abbrev-ref", "HEAD", cwd=repo_path).stdout.strip()
		return None if branch == "HEAD" else branch

	def _run(
		self,
		*args: str,
		cwd: Path | str | None = None,
	) -> subprocess.CompletedProcess[str]:
		completed = subprocess.run(
			[self.executable, *args],
			cwd=str(cwd) if cwd is not None else None,
			capture_output=True,
			check=False,
			text=True,
		)
		if completed.returncode != 0:
			command = " ".join([self.executable, *args])
			details = completed.stderr.strip() or completed.stdout.strip() or "unknown git error"
			raise GitSyncError(f"Git command failed ({command}): {details}")
		return completed
