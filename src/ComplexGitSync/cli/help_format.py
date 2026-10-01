"""cli.help_format — how `--help` is laid out, and the `help` command.

Ring: 4. Contract: after `build_parser()` assembles every command, apply the
    sentences and examples of `cli/help_text.py`, show a group's subcommands with
    their options on one line, group the top level under the README's headings,
    and offer `cgitsync help [--all] [<command> ...]`. Presentation only: it adds
    no capability, so it calls no client method.
Imports: configuration, environment, exit_codes, expert, help_text, minimalist
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import textwrap
from collections.abc import Iterator

from . import configuration, environment, expert, minimalist
from .exit_codes import EXIT_OK, EXIT_UNUSABLE
from .help_text import COMMAND_HELP, GROUP_DESCRIPTIONS, START_HERE

COMMANDS: dict[str, str] = {
    "help": "Help on one command (cgitsync help memory explore), or every command and option at once (--all).",
}

#: The top level's headings, in the README's own order, each with the commands it holds.
_HEADINGS: tuple[tuple[str, dict[str, str]], ...] = (
    ("Minimalist — the everyday workflow", minimalist.COMMANDS),
    ("Expert — one Git operation across the whole tree", expert.COMMANDS),
    ("Configuration — write or check a .cgs", configuration.COMMANDS),
    ("Environment", environment.COMMANDS),
    ("Help", COMMANDS),
)


class HelpFormatter(argparse.HelpFormatter):
    """argparse's formatter, except that text already laid out in lines is kept as written.

    A one-paragraph description is wrapped as usual; an *Examples* block, the top
    level's command list and its "start here" contain line breaks on purpose, and
    wrapping them would destroy the columns.
    """

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        if "\n" in text:
            return "".join(indent + line for line in text.splitlines(keepends=True))
        return super()._fill_text(text, width, indent)


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    return next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)


def walk(parser: argparse.ArgumentParser, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], argparse.ArgumentParser]]:
    """Every parser in the tree, parent before children, with its command path."""
    yield path, parser
    action = _subparsers(parser)
    if action is not None:
        for name, child in action.choices.items():
            yield from walk(child, (*path, name))


def _options_line(parser: argparse.ArgumentParser) -> str:
    """A parser's usage, without ``usage:``, its own name or ``[-h]``: what to type after it."""
    usage = " ".join(parser.format_usage().split())
    usage = usage.removeprefix("usage:").strip().removeprefix(parser.prog).strip()
    return usage.replace("[-h] ", "").replace("[-h]", "").strip()


#: One option of a usage line: a bracketed group kept whole, or a bare word.
_OPTION = re.compile(r"\[[^\]]*\]|\S+")


def _wrap_invocation(parts: list[str], width: int) -> str:
    """Join *parts* into lines of at most *width*, breaking only between whole options.

    argparse prints an invocation as one line, so the breaks are put in here, with
    continuation lines indented under the subcommand's name.
    """
    lines = [parts[0]]
    for part in parts[1:]:
        if len(lines[-1]) + 1 + len(part) > width:
            lines.append(part)
        else:
            lines[-1] += f" {part}"
    return "\n      ".join(lines)


def _examples(examples: tuple[str, ...]) -> str:
    return "Examples:\n" + "\n".join(f"  {example}" for example in examples)


def _grouped_commands(width: int) -> str:
    """The top level's command list, under the README's headings, generated from the parser's own table."""
    names = [name for _, commands in _HEADINGS for name in commands]
    column = max(len(name) for name in names) + 4
    lines: list[str] = []
    for heading, commands in _HEADINGS:
        lines.append(f"{heading}:")
        for name, summary in commands.items():
            wrapped = textwrap.wrap(summary, max(30, width - column - 2)) or [""]
            lines.append(f"  {name.ljust(column - 2)}{wrapped[0]}")
            lines.extend(" " * column + rest for rest in wrapped[1:])
        lines.append("")
    return "\n".join(lines).rstrip()


def apply(root: argparse.ArgumentParser) -> None:
    """Lay out every parser's help. Called once, after every command is registered."""
    width = min(shutil.get_terminal_size((100, 24)).columns, 100)
    for path, parser in walk(root):
        parser.formatter_class = HelpFormatter
        description, examples = COMMAND_HELP.get(path, (None, ()))
        if path in GROUP_DESCRIPTIONS:
            description = GROUP_DESCRIPTIONS[path]
        if description:
            parser.description = description
        if examples:
            parser.epilog = _examples(examples)
        action = _subparsers(parser)
        if action is None or not path:
            continue
        # A group: each subcommand on one line with its own options, so they are found here.
        action.metavar = "<subcommand>"
        for choice in action._choices_actions:
            choice.metavar = _wrap_invocation(
                [choice.dest, *_OPTION.findall(_options_line(action.choices[choice.dest]))], max(40, width - 8)
            )
        parser._positionals.title = "subcommands (each shown with its options)"

    action = _subparsers(root)
    action.help = argparse.SUPPRESS  # listed under headings in the description instead
    root.usage = "%(prog)s [-h] [--version] <command> ..."
    intro = "\n".join(textwrap.wrap(root.description or "", width - 2))
    root.description = f"{intro}\n\n{_grouped_commands(width)}"
    root.epilog = START_HERE


def register(subparsers: argparse._SubParsersAction, root: argparse.ArgumentParser) -> None:
    """Add ``help`` to the top level."""
    parser = subparsers.add_parser("help", help=COMMANDS["help"], description=COMMANDS["help"])
    parser.add_argument("topic", nargs="*", metavar="COMMAND", help="The command to explain, e.g. 'memory explore'.")
    parser.add_argument("--all", action="store_true", help="Every command, with its description and options, on one page.")
    parser.set_defaults(handler=_handle_help, root_parser=root)


def full_reference(root: argparse.ArgumentParser) -> str:
    """Every command's help, one after the other, so a single grep finds any option."""
    pages = []
    for path, parser in walk(root):
        title = " ".join(("cgitsync", *path))
        pages.append(f"{'=' * 78}\n{title}\n{'=' * 78}\n{parser.format_help().rstrip()}")
    return "\n\n".join(pages) + "\n"


def _handle_help(args: argparse.Namespace) -> int:
    root: argparse.ArgumentParser = args.root_parser
    if args.all:
        sys.stdout.write(full_reference(root))
        return EXIT_OK
    target = root
    for name in args.topic:
        action = _subparsers(target)
        if action is None or name not in action.choices:
            known = ", ".join(sorted(action.choices)) if action is not None else "none"
            print(f"cgitsync help: no command '{' '.join(args.topic)}'. Known here: {known}.", file=sys.stderr)
            return EXIT_UNUSABLE
        target = action.choices[name]
    sys.stdout.write(target.format_help())
    return EXIT_OK


__all__ = ["COMMANDS", "HelpFormatter", "apply", "full_reference", "register", "walk"]
