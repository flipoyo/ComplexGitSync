# DocSpecs — Standing Documentation Philosophy

This file captures the owner's reusable, project-agnostic documentation
principles. Every project that conforms to **DevSpecs** must also follow every
section below. Project-specific additions (scope, chapter outline, glossary
terms) belong in the project's `AdditionalSpecs.md`.

---

## Location and Format

- All user-facing documentation source lives in the `docs/` (or `Doc$ProjectName/`, referred too hereafter as `docs/`) directory at the
  project root.
- `docs/` is preferably a Git submodule pointing to a standalone documentation
  repository, so documentation can evolve independently and be shared across
  projects.
- The documentation is written in **LaTeX**. The LaTeX project must be
  self-contained: running `latexmk` (or equivalent) from `docs/` must produce
  the final PDF without requiring files outside `docs/`.

## Mandatory Documents

Every `docs/` project must contain at least the following two documents:

### Getting Started (getting_started.tex)

A concise, step-by-step guide that takes a brand-new user from installation to
first successful use of the main workflow. It must:

- Cover prerequisites and installation in full.
- For Python projects, show only `uv` or `pixi` workflows for environment and
  dependency management — never raw `pip` / `venv` instructions.
- Walk through the primary workflow end-to-end with concrete examples.
- Be short enough to read in one sitting (target: ≤ 15 pages).

### User Guide (MASTER.tex)

A complete, authoritative reference for every user-facing feature, command,
configuration option, and error condition. It must:

- Document every public command / API entry-point.
- Provide at least one usage example per feature.
- Include a troubleshooting section for common error scenarios.
- Stay in sync with the source code; documentation must be updated in the same
  PR as the code change that introduces or modifies a user-facing feature.

## Structure Conventions

- Each document occupies its own `.tex` file under `docs/`.
- A top-level `docs/main.tex` (or equivalent) includes all documents and
  produces the full PDF.
- Shared macros, style settings, and the project title page live in a
  `docs/preamble.tex` (or equivalent) that is included by every document.
- Figures and diagrams live in `docs/figures/` as vector sources (`.pdf`,
  `.svg`, or TikZ).
- Bibliography (if any) is managed with BibTeX / BibLaTeX and lives in
  `docs/references.bib`.

## Style Conventions

- Language: English, formal but accessible register.
- Code samples in `\texttt{}` or `lstlisting` environments; command-line
  invocations prefixed with `$`.
- Consistent use of defined macros for product name, version, and file paths —
  never hard-code these in prose.
- Sections follow a logical hierarchy: `\chapter` → `\section` →
  `\subsection`; avoid going deeper than three levels.

## Review and Currency

- The User Guide must reflect the current release at all times.
- Stale sections must be marked with a `\todo{}` or `\deprecated{}` macro and
  scheduled for removal in the next release cycle.
- Documentation changes do not require code review approval, but must not
  contradict the source code.

## Compiler and build requirements
- Use `pdflatex` as baseline compiler.
- Because `minted` is used in shared packages, compilation should support shell escape (`minted` calls the external Pygments process for syntax highlighting).

- 
## .gitignore (must be preserved)
The repository ignores LaTeX build artifacts and generated PDF outputs:

```gitignore
*.aux
*.log
*.out
*.toc
*.bbl
*.blg
*.fls
*.fdb_latexmk
*.synctex.gz
*.run.xml
build/
*.brf
*.mtc*
*.lof
*.lot
*.maf
*.lol
*.nlo
*.pdf
```

This reflects the current repository policy: generated PDFs are treated as build artifacts and are not tracked.
