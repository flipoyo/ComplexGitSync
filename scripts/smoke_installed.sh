#!/usr/bin/env bash
# Smoke check for an INSTALLED cgitsync — the user's route, not the contributor's.
#
# Run it from any directory outside a ComplexGitSync checkout, with `cgitsync` on PATH
# (for example after `pipx install dist/*.whl`). It needs Git and nothing else: no Pixi,
# no network, no mounted developer repositories. It exercises a real local workspace:
# discover a repository, write a .cgs, validate it, and read its status.
set -euo pipefail

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
# A contributor's shell may export these; they outrank the directory, so never inherit them.
unset CGSHOME CGSTREE
export HOME="$work/home" CGSPATH="$work/cgs"
mkdir -p "$HOME"
git config --global user.name smoke
git config --global user.email smoke@example.test
# Git itself maps the hosted address in the .cgs onto a local bare remote: no network.
git config --global url."$work/".insteadOf "git@github.com:owner/"

cgitsync --version
cgitsync --help > /dev/null

git init -q --bare -b main "$work/proj.git"
git init -q -b main "$work/seed"
git -C "$work/seed" commit -q --allow-empty -m init
git -C "$work/seed" remote add origin "$work/proj.git"
git -C "$work/seed" push -q origin main

cd "$work"
cat > proj.cgs <<'CGS'
project = { name = "proj", default_branch = "main" }
repos = [
  { repository = "github:owner/proj", relative_path = "." },
]
CGS
cgitsync validate proj.cgs
cgitsync bootstrap proj.cgs proj

# bootstrap names the workspace it made; read it back from the state it wrote.
workspace="$(find "$HOME/.cgs" -maxdepth 2 -type d -name proj | head -1)"
status="$(CGSHOME="$workspace" cgitsync status)"
echo "$status"
case "$status" in
  *"errors=0"*"ready=true"*|*"ready=true"*"errors=0"*) ;;
  *) echo "smoke: status did not report a ready tree with errors=0" >&2; exit 1 ;;
esac
echo "smoke: installed cgitsync works outside the checkout"
