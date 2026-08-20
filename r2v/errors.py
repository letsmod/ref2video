"""The one exception type the CLI knows how to print nicely."""

from __future__ import annotations


class R2VError(RuntimeError):
    """Any failure that should stop the run with a readable message.

    Anything raised as an R2VError is assumed to be aimed at the person running
    the command, so the message should say what to do next, not just what broke.
    """
