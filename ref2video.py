#!/usr/bin/env python3
"""Generate a video from local reference images and clips.

One command in front of the reference-to-video models on Segmind, Replicate and
fal. Point it at some local stills and clips, give it a prompt, and it uploads
the references, runs the model, saves the MP4 and logs what it cost.

    python ref2video.py                       a short guide
    python ref2video.py --list-models         what exists and what it costs
    python ref2video.py --model minimax-h3 --provider fal \
        --prompt-file prompts/prompt1.txt --ref-image refs/product/

The interesting logic lives in the r2v package: r2v/catalog.py is where models,
limits and prices are declared, and r2v/providers/ is where each API is spoken.
"""

from __future__ import annotations

import sys

try:
    import requests  # noqa: F401  (imported for the friendly error below)
except ImportError:
    sys.exit("Missing dependency. Run: pip install -r requirements.txt")

from r2v.cli import main

if __name__ == "__main__":
    sys.exit(main())
