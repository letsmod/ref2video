"""Which models exist, on which providers, with what limits and what prices.

The unit here is an Offering: one model as sold by one provider. That is the
right granularity because the same model genuinely differs between providers -
Kling O3 needs a source clip on Segmind but is images-only on fal, Seedance
bills reference-clip seconds on Segmind and fal but not on Replicate, and the
same field is called 'ratio' in one place and 'aspect_ratio' in another.

Prices are USD per second of generated output, taken from each provider's
published rate card. They are only ever used for the up-front estimate; the
actual amount billed is read back from the provider where it reports one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import R2VError

# --------------------------------------------------------------------------- #
# Models (provider-independent identity)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Model:
    key: str
    label: str
    summary: str
    best_for: str


MODELS: dict[str, Model] = {
    "seedance-2.5": Model(
        key="seedance-2.5",
        label="Seedance 2.5 (ByteDance)",
        summary="The big reference set: up to 30 images and 10 clips, 4-30s, native audio.",
        best_for="Long clips and large reference sets, or when you need motion clips "
                 "and stills in the same request.",
    ),
    "seedance-2.0": Model(
        key="seedance-2.0",
        label="Seedance 2.0 (ByteDance)",
        summary="The previous generation: 9 images and 3 clips, 4-15s, up to 4k.",
        best_for="Drafting. Roughly half the price of 2.5 with the same reference "
                 "workflow, and it goes higher than 720p.",
    ),
    "minimax-h3": Model(
        key="minimax-h3",
        label="MiniMax Hailuo H3 (Hailuo 03)",
        summary="Identity-focused reference-to-video, 4-15s, up to 2K (4K on fal).",
        best_for="Holding one product or face identical across the clip. The cheapest "
                 "per second of the four, and the highest resolution.",
    ),
    "kling-o3": Model(
        key="kling-o3",
        label="Kling O3 Reference (Kuaishou)",
        summary="Character/product elements driven by reference images, 3-15s.",
        best_for="Cinematic motion and restyling. On Segmind it transforms a source "
                 "clip you supply; on fal it works from stills alone.",
    ),
}

MODEL_ALIASES = {
    "seedance": "seedance-2.5",
    "seedance2.5": "seedance-2.5",
    "seedance25": "seedance-2.5",
    "2.5": "seedance-2.5",
    "seedance2.0": "seedance-2.0",
    "seedance20": "seedance-2.0",
    "2.0": "seedance-2.0",
    "minimax": "minimax-h3",
    "hailuo": "minimax-h3",
    "hailuo-3": "minimax-h3",
    "h3": "minimax-h3",
    "kling": "kling-o3",
    "klingo3": "kling-o3",
    "o3": "kling-o3",
}


# --------------------------------------------------------------------------- #
# Offerings (one model on one provider)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Offering:
    model: str
    provider: str
    slug: str                                    # endpoint slug / model id
    slug_by_quality: dict = field(default_factory=dict)  # when the tier is in the URL

    # Request field names. None means "this provider does not expose it".
    images_param: str | None = "reference_images"
    videos_param: str | None = "reference_videos"
    source_video_param: str | None = None        # transforms ONE input clip
    duration_param: str | None = "duration"
    quality_param: str | None = "resolution"
    aspect_param: str | None = "aspect_ratio"
    audio_param: str | None = None
    seed_param: str | None = None

    # Accepted values
    duration_range: tuple[int, int] = (4, 15)
    duration_as_string: bool = False
    duration_auto: str | int | None = None       # value meaning "model decides"
    quality_choices: tuple[str, ...] = ()
    default_quality: str = ""
    aspect_choices: tuple[str, ...] = ()
    default_aspect: str = "16:9"

    # Reference limits
    min_images: int = 0
    max_images: int = 0
    max_videos: int = 0
    max_combined_refs: int | None = None         # images + clips share one budget
    min_clip_s: float | None = None
    max_clip_s: float | None = None
    max_reference_total_s: float | None = None
    max_image_mb: float | None = None
    max_video_mb: float | None = None

    # How the prompt cites references on this provider
    image_tag: str = "@Image {n}"
    video_tag: str = "@Video {n}"
    # Prompt ceiling where the provider declares one. None means undeclared,
    # not unlimited - we simply have nothing to check against.
    max_prompt_chars: int | None = None

    # Pricing, USD per second of generated output
    rates: dict = field(default_factory=dict)              # quality -> $/s
    rates_with_video: dict = field(default_factory=dict)   # when a clip is attached
    rates_audio_on: dict = field(default_factory=dict)     # when audio is generated
    rate_table: dict = field(default_factory=dict)         # [mode][quality][aspect]
    bills_reference_seconds: bool = False                  # clips metered like output
    image_surcharge: tuple = ()                            # (free_count, $ per extra)
    audio_free: bool = True                                # False where audio costs more

    extra: dict = field(default_factory=dict)              # constant payload additions
    notes: str = ""
    caveat: str = ""                                       # printed before every run

    def endpoint_slug(self, quality: str) -> str:
        return self.slug_by_quality.get(quality, self.slug)

    def supports_images(self) -> bool:
        return bool(self.images_param) and self.max_images > 0

    def supports_videos(self) -> bool:
        return bool(self.videos_param) and self.max_videos > 0

    def needs_source_video(self) -> bool:
        return bool(self.source_video_param)


# Segmind prices Seedance by resolution *and* aspect ratio, and moves to a
# cheaper per-second rate when a reference video is present - but then meters
# the clip's own seconds at that rate too, which usually costs more than the
# discount saves. Verified against a real invoice: 5s of 720p 16:9 output with
# 28s of reference clips billed $4.681872.
SEGMIND_25_RATES = {
    "video": {
        "480p": {"16:9": 0.0637, "4:3": 0.0626, "1:1": 0.0609,
                 "3:4": 0.0626, "9:16": 0.0637, "21:9": 0.0637},
        "720p": {"16:9": 0.1429, "4:3": 0.1438, "1:1": 0.1429,
                 "3:4": 0.1438, "9:16": 0.1429, "21:9": 0.1436},
    },
    "text_or_image": {
        "480p": {"16:9": 0.1065, "4:3": 0.1046, "1:1": 0.1018,
                 "3:4": 0.1046, "9:16": 0.1065, "21:9": 0.1065},
        "720p": {"16:9": 0.2389, "4:3": 0.2404, "1:1": 0.2389,
                 "3:4": 0.2404, "9:16": 0.2389, "21:9": 0.2401},
    },
    # 1080p is accepted but Segmind publishes no per-second rate for it, so the
    # estimate falls back to "unavailable" and we rely on the x-cost header.
}

SEGMIND_20_RATES = {
    "video": {
        "480p": {"16:9": 0.0432, "4:3": 0.0424, "1:1": 0.0413,
                 "3:4": 0.0424, "9:16": 0.0432, "21:9": 0.0432},
        "720p": {"16:9": 0.0929, "4:3": 0.0935, "1:1": 0.0929,
                 "3:4": 0.0935, "9:16": 0.0929, "21:9": 0.0933},
    },
    "text_or_image": {
        "480p": {"16:9": 0.0703, "4:3": 0.0691, "1:1": 0.0672,
                 "3:4": 0.0691, "9:16": 0.0703, "21:9": 0.0703},
        "720p": {"16:9": 0.1512, "4:3": 0.1522, "1:1": 0.1512,
                 "3:4": 0.1522, "9:16": 0.1512, "21:9": 0.1519},
    },
}

SEEDANCE_ASPECTS = ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive")
MINIMAX_ASPECTS = ("adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16")


OFFERINGS: list[Offering] = [
    # ---------------------------------------------------------------- Seedance 2.5
    Offering(
        model="seedance-2.5", provider="segmind", slug="seedance-2.5",
        duration_range=(4, 30), duration_auto=-1,
        quality_choices=("480p", "720p", "1080p"), default_quality="720p",
        aspect_choices=SEEDANCE_ASPECTS,
        audio_param="generate_audio", seed_param="seed",
        max_images=30, max_videos=10,
        min_clip_s=2, max_clip_s=30, max_reference_total_s=30.2,
        max_image_mb=30, max_video_mb=200,
        rate_table=SEGMIND_25_RATES, bills_reference_seconds=True,
        extra={"return_last_frame": False, "skip_moderation": False},
        notes="Reference clips are billed like output seconds here - the big cost trap.",
    ),
    Offering(
        model="seedance-2.5", provider="replicate", slug="bytedance/seedance-2.5",
        duration_range=(4, 30), duration_auto=-1,
        quality_choices=("480p", "720p"), default_quality="720p",
        aspect_choices=SEEDANCE_ASPECTS,
        audio_param="generate_audio", seed_param="seed",
        max_images=30, max_videos=10,
        min_clip_s=2, max_clip_s=30, max_reference_total_s=30.2,
        image_tag="[Image{n}]", video_tag="[Video{n}]",
        max_prompt_chars=2000,
        rates={"480p": 0.1028, "720p": 0.2312},
        rates_with_video={"480p": 0.4304, "720p": 0.9676},
        notes="Clip seconds are not metered, but attaching any clip quadruples the "
              "per-second rate. No 1080p tier, and prompts stop at 2000 characters.",
    ),
    Offering(
        model="seedance-2.5", provider="fal",
        slug="bytedance/seedance-2.5/reference-to-video",
        images_param="image_urls", videos_param="video_urls",
        duration_range=(4, 30), duration_as_string=True, duration_auto="auto",
        quality_choices=("480p", "720p", "1080p"), default_quality="720p",
        aspect_choices=("auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"),
        audio_param="generate_audio",
        max_images=30, max_videos=10, max_combined_refs=50,
        min_clip_s=1.8, max_clip_s=30, max_reference_total_s=30.2,
        max_image_mb=30, max_video_mb=200,
        image_tag="@Image{n}", video_tag="@Video{n}",
        rates={"480p": 0.2205, "720p": 0.4730},
        rates_with_video={"480p": 0.1323, "720p": 0.2838},
        bills_reference_seconds=True,
        notes="Attaching a clip drops the rate 40% but adds the clip's own seconds "
              "to the bill.",
    ),

    # ---------------------------------------------------------------- Seedance 2.0
    Offering(
        model="seedance-2.0", provider="segmind", slug="seedance-2.0",
        duration_range=(4, 15),
        quality_choices=("480p", "720p", "1080p", "4k"), default_quality="720p",
        aspect_choices=SEEDANCE_ASPECTS,
        audio_param="generate_audio", seed_param="seed",
        max_images=9, max_videos=3,
        min_clip_s=2, max_clip_s=15,
        max_image_mb=30, max_video_mb=200,
        rate_table=SEGMIND_20_RATES, bills_reference_seconds=True,
        notes="Cheapest Segmind route. 1080p and 4k are accepted but unpriced publicly.",
    ),
    Offering(
        model="seedance-2.0", provider="replicate", slug="bytedance/seedance-2.0",
        duration_range=(4, 15), duration_auto=-1,
        quality_choices=("480p", "720p", "1080p", "4k"), default_quality="720p",
        aspect_choices=SEEDANCE_ASPECTS,
        audio_param="generate_audio", seed_param="seed",
        max_images=9, max_videos=3,
        min_clip_s=2, max_clip_s=15,
        image_tag="[Image{n}]", video_tag="[Video{n}]",
        max_prompt_chars=4000,
        rates={"480p": 0.08, "720p": 0.18, "1080p": 0.45, "4k": 1.00},
        rates_with_video={"480p": 0.10, "720p": 0.22, "1080p": 0.55, "4k": 1.25},
        notes="Flat per-second pricing all the way to 4k, and clip seconds are free.",
    ),
    Offering(
        model="seedance-2.0", provider="fal",
        slug="bytedance/seedance-2.0/reference-to-video",
        images_param="image_urls", videos_param="video_urls",
        duration_range=(4, 15), duration_as_string=True, duration_auto="auto",
        quality_choices=("480p", "720p", "1080p", "4k"), default_quality="720p",
        aspect_choices=("auto", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"),
        audio_param="generate_audio",
        max_images=9, max_videos=3, max_combined_refs=12,
        min_clip_s=2, max_clip_s=15, max_reference_total_s=15,
        image_tag="@Image{n}", video_tag="@Video{n}",
        rates={"720p": 0.3034, "1080p": 0.682},
        rates_with_video={"720p": 0.1814, "1080p": 0.4092},
        bills_reference_seconds=True,
        notes="480p and 4k are accepted but fal publishes no rate for them.",
    ),

    # ---------------------------------------------------------------- MiniMax H3
    Offering(
        model="minimax-h3", provider="segmind",
        slug="minimax-h3-reference-to-video",
        videos_param=None,
        duration_range=(4, 15),
        quality_choices=("768P", "2K"), default_quality="2K",
        aspect_param="ratio", aspect_choices=MINIMAX_ASPECTS, default_aspect="adaptive",
        min_images=1, max_images=5,
        max_image_mb=30,
        image_tag="Image {n}", video_tag="Video {n}",
        rates={"768P": 0.1125, "2K": 0.1625},
        notes="Images only on this endpoint - Segmind exposes no clip input.",
    ),
    Offering(
        model="minimax-h3", provider="replicate", slug="minimax/h3",
        images_param="reference_image_urls", videos_param="reference_video_urls",
        duration_range=(4, 15),
        quality_choices=("768P", "2K"), default_quality="2K",
        aspect_param="ratio", aspect_choices=MINIMAX_ASPECTS,
        min_images=0, max_images=9, max_videos=3,
        min_clip_s=2, max_clip_s=15, max_reference_total_s=15,
        image_tag="Image {n}", video_tag="Video {n}",
        rates={"768P": 0.08, "2K": 0.13},
        caveat="Replicate declares H3's reference fields as plain strings rather than "
               "file inputs, so it may not resolve URLs hosted on Replicate's own "
               "Files API. If references appear to be ignored, run this model on fal "
               "or Segmind instead.",
        notes="Cheapest 2K of the three providers, but see the reference-URL caveat.",
    ),
    Offering(
        model="minimax-h3", provider="fal", slug="minimax/h3/reference-to-video",
        images_param="reference_image_urls", videos_param="reference_video_urls",
        duration_range=(5, 15),
        quality_choices=("480P", "768P", "2K", "4K"), default_quality="2K",
        aspect_choices=MINIMAX_ASPECTS, default_aspect="adaptive",
        seed_param="seed",
        min_images=0, max_images=9, max_videos=3, max_combined_refs=12,
        min_clip_s=2, max_clip_s=15, max_reference_total_s=15,
        image_tag="Image {n}", video_tag="Video {n}",
        rates={"480P": 0.05, "768P": 0.06, "2K": 0.13, "4K": 0.16},
        image_surcharge=(5, 0.08),
        extra={"enable_prompt_expansion": True},
        notes="Widest resolution range (480P to 4K) and no reference-URL caveat. "
              "First 5 reference images are free, then $0.08 each.",
    ),

    # ---------------------------------------------------------------- Kling O3
    Offering(
        model="kling-o3", provider="segmind",
        slug="kling-o3-video2video-reference",
        images_param="image_urls", videos_param=None, source_video_param="video_url",
        duration_range=(3, 15), duration_as_string=True,
        quality_param="mode", quality_choices=("std", "pro"), default_quality="pro",
        aspect_choices=("16:9", "9:16", "1:1"),
        max_images=4, max_videos=1,
        max_video_mb=200,
        image_tag="@Image{n}", video_tag="",
        rates={"std": 0.315, "pro": 0.42},
        extra={"keep_audio": False, "shot_type": "customize"},
        notes="Needs exactly one --ref clip as the source video to transform.",
    ),
    Offering(
        model="kling-o3", provider="fal",
        slug="fal-ai/kling-video/o3/pro/reference-to-video",
        slug_by_quality={
            "std": "fal-ai/kling-video/o3/standard/reference-to-video",
            "pro": "fal-ai/kling-video/o3/pro/reference-to-video",
        },
        images_param="image_urls", videos_param=None,
        duration_range=(3, 15), duration_as_string=True,
        quality_param=None,  # the tier is part of the URL, not a field
        quality_choices=("std", "pro"), default_quality="pro",
        aspect_choices=("16:9", "9:16", "1:1"),
        audio_param="generate_audio",
        max_images=4,
        max_image_mb=30,
        image_tag="@Image{n}", video_tag="",
        rates={"std": 0.084, "pro": 0.112},
        rates_audio_on={"std": 0.112, "pro": 0.14},
        audio_free=False,   # audio raises the per-second rate by 25%
        extra={"shot_type": "customize"},
        notes="A quarter of Segmind's price, and it works from stills alone - no "
              "source clip needed.",
    ),
]

BY_MODEL_PROVIDER = {(o.model, o.provider): o for o in OFFERINGS}
PROVIDERS_FOR = {m: [o.provider for o in OFFERINGS if o.model == m] for m in MODELS}

# Different providers spell the same quality tier differently.
QUALITY_SYNONYMS = {
    "std": ("standard",), "standard": ("std",),
    "768p": ("768P",), "2k": ("2K",), "4k": ("4K",),
}


def resolve_model_key(name: str) -> str:
    key = MODEL_ALIASES.get(name.strip().lower(), name.strip().lower())
    if key not in MODELS:
        raise R2VError(
            f"Unknown model '{name}'. Available: {', '.join(MODELS)}. "
            f"Run --list-models to see what each one does."
        )
    return key


def get_offering(model_key: str, provider_key: str) -> Offering:
    offering = BY_MODEL_PROVIDER.get((model_key, provider_key))
    if offering is None:
        available = PROVIDERS_FOR.get(model_key, [])
        raise R2VError(
            f"{MODELS[model_key].label} is not available on {provider_key}. "
            f"It is on: {', '.join(available) if available else 'no configured provider'}."
        )
    return offering


def resolve_quality(offering: Offering, value: str | None) -> str:
    """Normalise a quality tier to whatever this offering actually calls it."""
    if value is None:
        return offering.default_quality
    if value in offering.quality_choices:
        return value
    lowered = value.strip().lower()
    for choice in offering.quality_choices:
        if choice.lower() == lowered or lowered in QUALITY_SYNONYMS.get(choice.lower(), ()):
            return choice
    raise R2VError(
        f"{MODELS[offering.model].label} on {offering.provider} does not support "
        f"--resolution {value}. Valid values: {', '.join(offering.quality_choices)}."
    )


def price_per_second(offering: Offering, quality: str, aspect: str,
                     has_video: bool, audio_on: bool) -> float | None:
    """Published USD per second of output, or None where none is published."""
    if offering.rate_table:
        mode = "video" if has_video else "text_or_image"
        return offering.rate_table.get(mode, {}).get(quality, {}).get(aspect)
    if audio_on and offering.rates_audio_on:
        return offering.rates_audio_on.get(quality)
    if has_video and offering.rates_with_video:
        return offering.rates_with_video.get(quality)
    return offering.rates.get(quality)


def estimate_cost(offering: Offering, quality: str, aspect: str, output_seconds: float | None,
                  reference_seconds: float, image_count: int,
                  audio_on: bool, has_video: bool) -> tuple[float | None, float | None, float]:
    """Return (estimate, rate, billed_seconds) for the run about to be made."""
    rate = price_per_second(offering, quality, aspect, has_video, audio_on)
    if output_seconds is None:
        return None, rate, 0.0
    billed = output_seconds + (reference_seconds if offering.bills_reference_seconds else 0.0)
    if rate is None:
        return None, None, billed
    total = rate * billed
    if offering.image_surcharge:
        free, per_image = offering.image_surcharge
        total += max(0, image_count - free) * per_image
    return total, rate, billed


def cheapest_provider(model_key: str, seconds: int = 5) -> tuple[str, float] | None:
    """The provider with the lowest published price for a plain images-only run."""
    best: tuple[str, float] | None = None
    for offering in (o for o in OFFERINGS if o.model == model_key):
        rate = price_per_second(offering, offering.default_quality,
                                offering.default_aspect, False, False)
        if rate is None:
            continue
        if best is None or rate * seconds < best[1]:
            best = (offering.provider, rate * seconds)
    return best
