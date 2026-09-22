"""autofix — repairs a git situation, dispatched from the error `cgitsync`
already produced.

One ``repair_*.py`` module per repair purpose, each with its own class
(``base.Repair``'s shape) — see ``main_1-1_Autofix_DevPlanTicket.md`` §7.
``repair_from_cli.FromCliRepair`` is the dispatcher `cgitsync autofix`
calls; every other module registers into it. Growth happens by adding a
new module and one line in ``repair_from_cli.FromCliRepair._REGISTRY``,
never by branching inside an existing class.
"""

from __future__ import annotations

from .base import RepairOutcome, Situation
from .repair_from_cli import FromCliRepair, NoMatchingRepairError

__all__ = [
    "FromCliRepair",
    "NoMatchingRepairError",
    "RepairOutcome",
    "Situation",
]
