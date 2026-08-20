"""Reference-to-video generation across Segmind, Replicate and fal.

The package is split so that adding a model or a provider touches one file:

    catalog.py    which models exist, on which providers, with what limits and prices
    providers/    how to talk to each API (auth, upload, submit, poll, download)
    media.py      local reference files: discovery, probing, validation
    uploads.py    remembering which local file maps to which remote URL
    costlog.py    the running spend log
    cli.py        argument parsing, the help screen, and the run itself
"""

from __future__ import annotations

__version__ = "2.0.0"
