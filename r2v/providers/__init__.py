"""Provider registry."""

from __future__ import annotations

from ..errors import R2VError
from .base import GenerationResult, Provider
from .falai import FalProvider
from .replicate import ReplicateProvider
from .segmind import SegmindProvider

PROVIDER_CLASSES: dict[str, type[Provider]] = {
    SegmindProvider.key: SegmindProvider,
    ReplicateProvider.key: ReplicateProvider,
    FalProvider.key: FalProvider,
}

PROVIDER_ALIASES = {
    "fal.ai": "fal", "falai": "fal",
    "rep": "replicate", "replicate.com": "replicate",
    "seg": "segmind", "segmind.com": "segmind",
}


def resolve_provider_key(name: str) -> str:
    key = PROVIDER_ALIASES.get(name.strip().lower(), name.strip().lower())
    if key not in PROVIDER_CLASSES:
        raise R2VError(
            f"Unknown provider '{name}'. Available: {', '.join(PROVIDER_CLASSES)}."
        )
    return key


def provider_class(key: str) -> type[Provider]:
    return PROVIDER_CLASSES[resolve_provider_key(key)]


__all__ = ["PROVIDER_CLASSES", "GenerationResult", "Provider", "provider_class",
           "resolve_provider_key"]
