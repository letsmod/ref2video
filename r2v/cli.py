"""Command line interface: parse, confirm, run, log.

The guiding rule here is that nothing should cost money by surprise. Every
limit that a provider would reject after an upload is checked locally first,
the price is quoted and confirmed before the request goes out, and --dry-run
prints the exact request body without sending it.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import catalog, config, media, uploads
from .costlog import DEFAULT_COST_LOG, append_cost_log
from .errors import R2VError
from .providers import PROVIDER_CLASSES, provider_class, resolve_provider_key

PROGRAM = "ref2video.py"
DEFAULT_OUTPUT_DIR = Path("outputs")


# --------------------------------------------------------------------------- #
# Help and catalog rendering
# --------------------------------------------------------------------------- #

def quickstart() -> str:
    """The screen shown when the command is run with no arguments at all."""
    return f"""
Generate a video from local reference images and clips.

USAGE
  python {PROGRAM} --model <model> --provider <provider> --prompt-file <file> [refs...]

FIRST TIME
  1. pip install -r requirements.txt
  2. Put the key for the provider you want in .env (copy .env.example):
{_key_lines()}
  3. Try a dry run - it validates everything and spends nothing:
     python {PROGRAM} --model minimax-h3 --provider fal --prompt "..." \\
         --ref-image refs\\product\\ --dry-run

PICK A MODEL AND A PROVIDER
  python {PROGRAM} --list-models      what exists, what it costs, on which provider
  Leave --model or --provider off an interactive run and you will be asked.

THE REFERENCES
  --ref        motion clips  (.mp4/.mov)   cited in the prompt as @Video 1, @Video 2
  --ref-image  stills        (.jpg/.png)   cited in the prompt as @Image 1, @Image 2
  Each accepts several paths, a glob, or a whole folder:
     --ref-image refs\\product\\        --ref refs\\*.mp4        --ref a.mp4 b.mp4

THE USUAL KNOBS
  --duration 8            length in seconds (the range depends on the model)
  --resolution 720p       quality tier      (the values depend on the model)
  --aspect-ratio 9:16     output shape
  --dry-run               build and print the request without sending it
  --yes                   skip the "Proceed?" confirmation

COST
  Every run quotes a price and waits for confirmation. Spend is appended to
  {DEFAULT_COST_LOG} with a running total.
  The single biggest lever is the provider: the same model can cost four times
  as much on one as another. --list-models shows the comparison.

  python {PROGRAM} --help     for the full flag list
"""


def _key_lines() -> str:
    return "\n".join(
        f"       {cls.env_var}={cls.key_example}   ({cls.label} - {cls.key_url})"
        for cls in PROVIDER_CLASSES.values()
    )


def describe_catalog(seconds: int = 5) -> str:
    """The --list-models table: every model, every provider that carries it."""
    blocks = []
    for index, model in enumerate(catalog.MODELS.values(), start=1):
        rows = []
        for offering in (o for o in catalog.OFFERINGS if o.model == model.key):
            rate = catalog.price_per_second(
                offering, offering.default_quality, offering.default_aspect,
                has_video=False, audio_on=False)
            price = f"${rate * seconds:>6.2f}" if rate else "     ?"
            refs = []
            if offering.needs_source_video():
                refs.append("1 source clip + " + f"{offering.max_images} images")
            else:
                if offering.max_images:
                    refs.append(f"{offering.max_images} images")
                if offering.supports_videos():
                    refs.append(f"{offering.max_videos} clips")
            low, high = offering.duration_range
            has_key = config.has_key(PROVIDER_CLASSES[offering.provider].env_var)
            rows.append(
                f"      {offering.provider:<10} {price} /{seconds}s at {offering.default_quality:<5} "
                f"{'':<1}{', '.join(refs) or 'none':<26} {low}-{high}s"
                f"{'' if has_key else '   [no API key set]'}"
            )
            if offering.notes:
                rows.append(f"                 {offering.notes}")
        blocks.append(
            f"  {index}. {model.key:<13} {model.label}\n"
            f"      {model.summary}\n"
            f"      best for: {model.best_for}\n"
            + "\n".join(rows)
        )
    return "\n\n".join(blocks)


def compare_providers(model_key: str, quality_hint: str, output_seconds: float | None,
                      reference_seconds: float, image_count: int,
                      has_video: bool, current: str) -> list[str]:
    """One line per alternative provider, for the confirmation screen."""
    if output_seconds is None:
        return []
    lines = []
    for offering in (o for o in catalog.OFFERINGS if o.model == model_key):
        if offering.provider == current:
            continue
        try:
            quality = catalog.resolve_quality(offering, quality_hint)
        except R2VError:
            quality = offering.default_quality
        estimate, _, _ = catalog.estimate_cost(
            offering, quality, offering.default_aspect, output_seconds,
            reference_seconds, image_count, audio_on=False,
            has_video=has_video and (offering.supports_videos() or offering.needs_source_video()),
        )
        if estimate is None:
            continue
        note = ""
        if has_video and not offering.supports_videos() and not offering.needs_source_video():
            note = "  (images only - your clips would be dropped)"
        lines.append(f"  same run on {offering.provider:<10} ${estimate:>7.4f} "
                     f"at {quality}{note}")
    return lines


# --------------------------------------------------------------------------- #
# Interactive pickers
# --------------------------------------------------------------------------- #

def choose_model_interactively() -> str:
    keys = list(catalog.MODELS)
    print("\nModels:\n")
    print(describe_catalog())
    print()
    while True:
        answer = input(f"Model [1-{len(keys)} or name]: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return keys[int(answer) - 1]
        try:
            return catalog.resolve_model_key(answer)
        except R2VError as exc:
            print(f"  {exc}")


def choose_provider_interactively(model_key: str) -> str:
    providers = catalog.PROVIDERS_FOR[model_key]
    print(f"\n{catalog.MODELS[model_key].label} is available on:\n")
    for index, provider in enumerate(providers, start=1):
        offering = catalog.get_offering(model_key, provider)
        rate = catalog.price_per_second(offering, offering.default_quality,
                                        offering.default_aspect, False, False)
        price = f"${rate * 5:.2f} per 5s at {offering.default_quality}" if rate else "price varies"
        missing = "" if config.has_key(PROVIDER_CLASSES[provider].env_var) else "  [no API key set]"
        print(f"  {index}. {provider:<10} {price}{missing}")
        if offering.notes:
            print(f"       {offering.notes}")
    print()
    while True:
        answer = input(f"Provider [1-{len(providers)} or name]: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(providers):
            return providers[int(answer) - 1]
        try:
            key = resolve_provider_key(answer)
        except R2VError as exc:
            print(f"  {exc}")
            continue
        if key in providers:
            return key
        print(f"  {catalog.MODELS[model_key].label} is not on {key}. "
              f"Choose from: {', '.join(providers)}")


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Generate a video from local reference images and clips, using "
                    "reference-to-video models on Segmind, Replicate or fal.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            f"  python {PROGRAM} --list-models\n"
            f"  python {PROGRAM} --model minimax-h3 --provider fal \\\n"
            "      --prompt-file prompts/prompt1.txt --ref-image refs/product/\n"
            f"  python {PROGRAM} --model seedance-2.5 --provider segmind \\\n"
            "      --prompt-file prompts/prompt1.txt --ref refs/orbit.mp4 --duration 8\n"
            f"  python {PROGRAM} --model kling-o3 --provider segmind \\\n"
            "      --prompt \"...\" --ref refs/orbit.mp4 --ref-image refs/hero.png\n"
            "\nRun with no arguments for a short guide.\n"
        ),
    )

    what = parser.add_argument_group("what to run")
    what.add_argument("--model", "-m",
                      help=f"Model to use: {', '.join(catalog.MODELS)}. "
                           f"Omitted on an interactive run means you will be asked.")
    what.add_argument("--provider", "-p",
                      help=f"Where to run it: {', '.join(PROVIDER_CLASSES)}. "
                           f"Omitted on an interactive run means you will be asked.")
    what.add_argument("--list-models", action="store_true",
                      help="Show every model, the providers carrying it, and prices; then exit.")

    prompt_group = parser.add_argument_group("the prompt")
    prompt_source = prompt_group.add_mutually_exclusive_group()
    prompt_source.add_argument("--prompt",
                               help="Prompt text. Cite references as '@Video 1', '@Image 1'.")
    prompt_source.add_argument("--prompt-file", type=Path, metavar="PATH",
                               help="Read the prompt from a UTF-8 text file. Lines starting "
                                    "with '#' are treated as notes and stripped.")
    prompt_group.add_argument("--no-auto-cite", action="store_true",
                              help="Never append reference tags, even if the prompt does not "
                                   "mention the references.")

    refs = parser.add_argument_group("the references")
    refs.add_argument("--ref", "--reference-video", dest="refs", action="extend",
                      nargs="+", metavar="PATH", default=[],
                      help="Reference clip(s): .mp4/.mov. Several paths, a glob, or a folder. "
                           "Kling O3 on Segmind takes exactly one, as its source clip.")
    refs.add_argument("--ref-image", "--reference-image", dest="ref_images", action="extend",
                      nargs="+", metavar="PATH", default=[],
                      help="Reference image(s): .jpg/.png/.webp. Several paths, a glob, "
                           "or a folder.")
    refs.add_argument("--drop-excess-refs", action="store_true",
                      help="If the clips exceed the model's combined-duration cap, drop the "
                           "trailing ones instead of stopping. List the important ones first.")

    output = parser.add_argument_group("the output")
    output.add_argument("--duration", type=int, default=5,
                        help="Length in seconds; the valid range depends on the model. "
                             "Use -1 where the model supports choosing for itself. Default: 5.")
    output.add_argument("--resolution", "--quality", dest="quality", default=None,
                        help="Quality tier; the valid values depend on the model and provider "
                             "(--list-models). Defaults to each model's usual tier.")
    output.add_argument("--aspect-ratio", default=None,
                        help="Output shape, e.g. 16:9 or 9:16. Valid values depend on the model.")
    output.add_argument("--seed", type=int, default=-1,
                        help="Fix for a reproducible result; -1 means random (default).")
    audio = output.add_mutually_exclusive_group()
    audio.add_argument("--audio", action="store_true",
                       help="Force native audio on. Free on most models; on Kling via fal it "
                            "raises the rate by 25%%.")
    audio.add_argument("--no-audio", action="store_true", help="Force native audio off.")
    output.add_argument("--output", type=Path,
                        help="Where to write the MP4 "
                             "(default: outputs/<model>_<provider>_<timestamp>.mp4).")

    run = parser.add_argument_group("running")
    run.add_argument("--dry-run", action="store_true",
                     help="Validate everything and print the exact request body, without "
                          "uploading, generating, or spending anything.")
    run.add_argument("--yes", "-y", action="store_true",
                     help="Skip the cost confirmation prompt.")
    run.add_argument("--cost-log", type=Path, default=DEFAULT_COST_LOG,
                     help=f"JSONL spend log (default: {DEFAULT_COST_LOG}).")
    run.add_argument("--upload-cache", type=Path, default=uploads.DEFAULT_UPLOAD_CACHE,
                     help=f"Where uploaded-reference URLs are remembered "
                          f"(default: {uploads.DEFAULT_UPLOAD_CACHE}).")
    run.add_argument("--no-upload-cache", action="store_true",
                     help="Ignore the cache and re-upload every reference.")
    run.add_argument("--env-file", type=Path,
                     help="File holding the provider API keys (default: .env).")
    return parser


# --------------------------------------------------------------------------- #
# Prompt handling
# --------------------------------------------------------------------------- #

def load_prompt(args: argparse.Namespace) -> str:
    """The prompt text from either --prompt or --prompt-file.

    Newlines are preserved, since these models use 'Shot 1:' / 'Shot 2:' blocks
    that read much better one per line in a file.
    """
    if args.prompt_file is None:
        return args.prompt.strip()

    path = args.prompt_file.expanduser()
    if not path.is_file():
        raise R2VError(f"--prompt-file not found: {path}")
    lines = [line for line in path.read_text(encoding="utf-8").splitlines()
             if not line.lstrip().startswith("#")]
    text = "\n".join(lines).strip()
    if not text:
        raise R2VError(f"--prompt-file contains no prompt text: {path}")
    return text


def needs_reference_citation(prompt: str) -> bool:
    """True if the prompt never points at the references at all.

    These models ignore references the prompt does not mention, so we top up
    silent prompts. A prompt that refers to them in prose ("the provided
    reference videos") already counts, and is left alone rather than contradicted.
    """
    lowered = prompt.lower()
    hints = ("@video", "@image", "[video", "[image", "reference video", "reference image",
             "reference clip", "provided video", "provided image", "video 1", "image 1")
    return not any(hint in lowered for hint in hints)


def prompt_length_problem(offering: catalog.Offering, prompt: str) -> str | None:
    """Describe how the prompt overruns this offering's ceiling, if it does.

    Providers enforce this with a 422 at submit time, which on a large reference
    set lands minutes and a full round of uploads too late. Only some offerings
    declare a limit; the rest are unchecked rather than unlimited.
    """
    limit = offering.max_prompt_chars
    if limit is None or len(prompt) <= limit:
        return None

    model = catalog.MODELS[offering.model]
    others = [o for o in catalog.OFFERINGS
              if o.model == offering.model and o.provider != offering.provider]
    roomier = sorted({o.provider for o in others
                      if o.max_prompt_chars is None or o.max_prompt_chars >= len(prompt)})

    lines = [f"The prompt is {len(prompt)} characters and {model.label} on "
             f"{offering.provider} accepts {limit}.",
             f"  Trim {len(prompt) - limit} characters, or run it on "
             f"{' or '.join(roomier)}, which state no lower limit."
             if roomier else f"  Trim {len(prompt) - limit} characters."]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Validation and payload
# --------------------------------------------------------------------------- #

def apply_defaults(args: argparse.Namespace, offering: catalog.Offering) -> None:
    """Resolve the settings whose valid values depend on model and provider."""
    args.quality = catalog.resolve_quality(offering, args.quality)

    if args.aspect_ratio is None:
        args.aspect_ratio = offering.default_aspect
    elif offering.aspect_param and args.aspect_ratio not in offering.aspect_choices:
        raise R2VError(
            f"{catalog.MODELS[offering.model].label} on {offering.provider} does not support "
            f"--aspect-ratio {args.aspect_ratio}. "
            f"Valid values: {', '.join(offering.aspect_choices)}."
        )

    low, high = offering.duration_range
    if args.duration == -1 and offering.duration_auto is None:
        raise R2VError(
            f"{catalog.MODELS[offering.model].label} on {offering.provider} has no automatic "
            f"duration; pass --duration {low}-{high}."
        )
    if args.duration != -1 and not low <= args.duration <= high:
        raise R2VError(
            f"{catalog.MODELS[offering.model].label} on {offering.provider} accepts "
            f"--duration {low}-{high} seconds"
            + (", or -1 to let the model choose." if offering.duration_auto is not None else ".")
        )

    if args.audio:
        args.audio_on = True
    elif args.no_audio:
        args.audio_on = False
    else:
        # Default to on where audio is free, off where it costs more.
        args.audio_on = offering.audio_free


def validate(args: argparse.Namespace, offering: catalog.Offering
             ) -> tuple[list[Path], list[Path]]:
    """Check the references locally so nothing fails after a slow upload."""
    apply_defaults(args, offering)

    videos = media.expand_reference_tokens(args.refs, media.VIDEO_EXTENSIONS, "video")
    images = media.expand_reference_tokens(args.ref_images, media.IMAGE_EXTENSIONS, "image")
    label = f"{catalog.MODELS[offering.model].label} on {offering.provider}"

    if not videos and not images:
        raise R2VError("Pass at least one reference: --ref for clips, --ref-image for stills.")

    if videos and not offering.supports_videos() and not offering.needs_source_video():
        others = [o.provider for o in catalog.OFFERINGS
                  if o.model == offering.model and (o.supports_videos() or o.needs_source_video())]
        hint = f" It does accept clips on: {', '.join(others)}." if others else ""
        raise R2VError(f"{label} takes reference images only, so --ref cannot be used.{hint}")
    if images and not offering.supports_images():
        raise R2VError(f"{label} does not accept reference images.")

    if offering.needs_source_video() and len(videos) != 1:
        raise R2VError(
            f"{label} transforms exactly one source clip: pass a single --ref (got "
            f"{len(videos)}). Reference images guide the look; the clip supplies the motion."
        )
    if len(videos) > offering.max_videos:
        raise R2VError(f"{len(videos)} clips resolved; {label} accepts at most "
                       f"{offering.max_videos}.")
    if len(images) > offering.max_images:
        raise R2VError(f"{len(images)} images resolved; {label} accepts at most "
                       f"{offering.max_images}.")
    if len(images) < offering.min_images:
        raise R2VError(f"{label} needs at least {offering.min_images} reference image "
                       f"(--ref-image); got {len(images)}.")
    if offering.max_combined_refs and len(images) + len(videos) > offering.max_combined_refs:
        raise R2VError(
            f"{label} accepts {offering.max_combined_refs} references in total; got "
            f"{len(images)} images and {len(videos)} clips."
        )

    for path in videos:
        media.check_file_size(path, offering.max_video_mb)
    for path in images:
        media.check_file_size(path, offering.max_image_mb)
    return videos, images


def build_payload(offering: catalog.Offering, prompt: str, image_urls: list[str],
                  video_urls: list[str], args: argparse.Namespace) -> dict:
    """Assemble the request body in whatever shape this offering expects."""
    payload: dict = {"prompt": prompt}

    if offering.images_param and image_urls:
        payload[offering.images_param] = image_urls
    if offering.source_video_param and video_urls:
        payload[offering.source_video_param] = video_urls[0]
    elif offering.videos_param and video_urls:
        payload[offering.videos_param] = video_urls

    if offering.duration_param:
        if args.duration == -1:
            payload[offering.duration_param] = offering.duration_auto
        else:
            payload[offering.duration_param] = (str(args.duration)
                                                if offering.duration_as_string else args.duration)
    if offering.quality_param:
        payload[offering.quality_param] = args.quality
    if offering.aspect_param:
        payload[offering.aspect_param] = args.aspect_ratio
    if offering.audio_param:
        payload[offering.audio_param] = args.audio_on
    if offering.seed_param and args.seed >= 0:
        payload[offering.seed_param] = args.seed

    payload.update(offering.extra)
    return payload


def cite_references(offering: catalog.Offering, prompt: str, image_count: int,
                    video_count: int) -> tuple[str, str]:
    """Append the reference tags this provider understands, if the prompt has none."""
    video_tags = ([] if offering.needs_source_video() or not offering.video_tag else
                  [offering.video_tag.format(n=i) for i in range(1, video_count + 1)])
    image_tags = [offering.image_tag.format(n=i) for i in range(1, image_count + 1)]
    tags = ", ".join(video_tags + image_tags)
    if not tags:
        return prompt, ""
    # Deliberately neutral wording: naming a purpose here (motion, style) can
    # contradict a prompt that already says how to use the references.
    return f"{prompt}\n\nReferences: {tags}.", tags


# --------------------------------------------------------------------------- #
# Uploading
# --------------------------------------------------------------------------- #

def upload_all(provider, paths: list[Path], fallback_mime: str, cache: dict | None,
               cache_path: Path | None) -> list[str]:
    urls = []
    for path in paths:
        fingerprint = uploads.file_fingerprint(path) if cache is not None else None
        if fingerprint:
            cached = uploads.cache_lookup(cache, provider.key, fingerprint)
            if cached and provider.asset_alive(cached):
                print(f"  reusing {path.name} (cached upload)", flush=True)
                urls.append(cached)
                continue
            if cached:
                uploads.cache_forget(cache, provider.key, fingerprint)

        size_mb = path.stat().st_size / (1024 * 1024)
        print(f"  uploading {path.name} ({size_mb:.1f} MB)...", flush=True)
        mime = mimetypes.guess_type(path.name)[0] or fallback_mime
        url = provider.upload(path, mime)
        print(f"    -> {url}", flush=True)

        if fingerprint and cache is not None:
            uploads.cache_store(cache, provider.key, fingerprint, path, url)
            if cache_path is not None:
                # Save after each file so an interrupted run keeps what it uploaded.
                uploads.save_cache(cache_path, cache)
        urls.append(url)
    return urls


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(quickstart())
        return 0

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_models:
        print("\nEvery model, and every provider that carries it. "
              "Prices are for a 5-second clip:\n")
        print(describe_catalog())
        print("\nThe provider is the biggest cost lever - the same model can differ "
              "several-fold.\nChoose with: --model <name> --provider <name>\n")
        return 0

    if args.prompt is None and args.prompt_file is None:
        parser.error("one of --prompt or --prompt-file is required "
                     f"(run `python {PROGRAM}` with no arguments for a guide)")

    interactive = sys.stdin.isatty()
    try:
        env_file = config.load_env_file(args.env_file)

        # Model and provider first: every other limit and price depends on them.
        if args.model:
            model_key = catalog.resolve_model_key(args.model)
        elif interactive:
            model_key = choose_model_interactively()
        else:
            raise R2VError(f"--model is required. Choose one of: {', '.join(catalog.MODELS)}.")

        if args.provider:
            provider_key = resolve_provider_key(args.provider)
        elif len(catalog.PROVIDERS_FOR[model_key]) == 1:
            provider_key = catalog.PROVIDERS_FOR[model_key][0]
        elif interactive:
            provider_key = choose_provider_interactively(model_key)
        else:
            raise R2VError(
                f"--provider is required. {catalog.MODELS[model_key].label} runs on: "
                f"{', '.join(catalog.PROVIDERS_FOR[model_key])}."
            )

        offering = catalog.get_offering(model_key, provider_key)
        prompt = load_prompt(args)
        video_paths, image_paths = validate(args, offering)

        provider_cls = provider_class(provider_key)
        api_key = "" if args.dry_run else config.resolve_api_key(
            provider_cls.env_var, provider_cls.label, provider_cls.key_url,
            provider_cls.key_example, env_file)
    except R2VError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 2

    model = catalog.MODELS[model_key]

    # Trim to the combined-duration cap first, so the numbering printed below is
    # the numbering actually sent.
    if args.drop_excess_refs and video_paths and offering.max_reference_total_s:
        video_paths, dropped = media.fit_reference_clips(
            video_paths, offering.max_reference_total_s)
        for path in dropped:
            print(f"Dropped {path.name} to fit the "
                  f"{offering.max_reference_total_s}s combined limit.")

    reference_seconds = media.total_reference_seconds(video_paths)
    output_seconds = args.duration if args.duration > 0 else None
    estimate, rate, billed_seconds = catalog.estimate_cost(
        offering, args.quality, args.aspect_ratio, output_seconds, reference_seconds,
        len(image_paths), args.audio_on, has_video=bool(video_paths))

    print()
    print(f"Model      : {model.label}")
    print(f"Provider   : {provider_cls.label}  ({offering.endpoint_slug(args.quality)})")
    print(f"Settings   : {args.quality} {args.aspect_ratio}, "
          f"{args.duration if args.duration > 0 else 'auto'}s, "
          f"audio {'on' if args.audio_on else 'off'}")
    print(f"References : {len(video_paths)} clip(s), {len(image_paths)} image(s)")
    print(f"Keys from  : {env_file if env_file else 'shell environment'}")
    if offering.caveat:
        print(f"\nHeads up: {offering.caveat}\n")

    # Show the numbering, because it is what the prompt's citations resolve to.
    for index, path in enumerate(video_paths, start=1):
        seconds = media.probe_duration_seconds(path)
        length = f"  ({seconds:.1f}s)" if seconds else ""
        tag = "source clip" if offering.needs_source_video() else offering.video_tag.format(n=index)
        print(f"  {tag}: {path}{length}")
    for index, path in enumerate(image_paths, start=1):
        print(f"  {offering.image_tag.format(n=index)}: {path}")

    # The prompt that will actually be sent, including any tags auto-cite adds -
    # those count against the provider's character ceiling too.
    auto_prompt, auto_tags = (cite_references(offering, prompt, len(image_paths),
                                              len(video_paths))
                              if not args.no_auto_cite and needs_reference_citation(prompt)
                              else (prompt, ""))

    if args.prompt_file is not None:
        budget = f", limit {offering.max_prompt_chars}" if offering.max_prompt_chars else ""
        preview = prompt if len(prompt) <= 300 else prompt[:300] + " [...]"
        print(f"Prompt from: {args.prompt_file} ({len(prompt)} chars{budget})")
        print("  " + preview.replace("\n", "\n  "))

    # Stop before uploading anything: providers reject these with a 400 or 422
    # only after every file has been sent.
    problems = media.check_clip_limits(video_paths, offering.min_clip_s,
                                       offering.max_clip_s, offering.max_reference_total_s)
    problems += [p for p in (prompt_length_problem(offering, auto_prompt),) if p]
    if problems:
        sys.stdout.flush()  # keep the errors below the run summary, not above it
        for problem in problems:
            print(f"\nError: {problem}", file=sys.stderr)
        return 2

    if estimate is not None:
        print(f"\nEstimated cost: ${estimate:.4f}  ({billed_seconds:.1f} billed s "
              f"x ${rate:.4f}/s)")
        if offering.bills_reference_seconds and reference_seconds:
            # The reference share is the surprise on the invoice, so spell it out.
            print(f"  {output_seconds}s output            ${rate * output_seconds:.4f}")
            print(f"  {reference_seconds:.1f}s reference clips  "
                  f"${rate * reference_seconds:.4f}   <- metered like output here")
        if offering.image_surcharge and len(image_paths) > offering.image_surcharge[0]:
            free, per_image = offering.image_surcharge
            extra = len(image_paths) - free
            print(f"  {extra} extra image(s)      ${extra * per_image:.4f}   "
                  f"(first {free} are free)")
        for line in compare_providers(model_key, args.quality, output_seconds,
                                      reference_seconds, len(image_paths),
                                      bool(video_paths), provider_key):
            print(line)
        if not provider_cls.reports_cost:
            print(f"  {provider_cls.label} does not report the billed amount, so the log "
                  f"will record this estimate.")
    else:
        print("\nEstimated cost: no published rate for this combination; "
              "the run will still proceed and the log will say so.")

    if args.dry_run:
        payload = build_payload(
            offering, auto_prompt,
            [f"<uploaded:{p.name}>" for p in image_paths],
            [f"<uploaded:{p.name}>" for p in video_paths], args)
        print("\n--- dry run: this is the request that would be sent ---")
        print(f"POST  {provider_cls.label}  {offering.endpoint_slug(args.quality)}")
        shown = dict(payload)
        if len(shown.get("prompt", "")) > 200:
            shown["prompt"] = shown["prompt"][:200] + " [...]"
        print(json.dumps(shown, indent=2))
        if auto_tags:
            print(f"\n(auto-cite would append: {auto_tags})")
        print("\nNothing was uploaded and nothing was spent.")
        return 0

    if not args.yes:
        answer = input("\nProceed? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Aborted.")
            return 1

    provider = provider_cls(api_key)
    started = time.time()
    try:
        cache = None if args.no_upload_cache else uploads.load_cache(args.upload_cache)
        cache_path = None if args.no_upload_cache else args.upload_cache

        print("\nUploading references...")
        video_urls = upload_all(provider, video_paths, "video/mp4", cache, cache_path)
        image_urls = upload_all(provider, image_paths, "image/jpeg", cache, cache_path)

        if auto_tags:
            print(f"\nNote: the prompt did not cite the references, so '{auto_tags}' was "
                  f"appended. Use --no-auto-cite to disable.")

        payload = build_payload(offering, auto_prompt, image_urls, video_urls, args)
        print()
        result = provider.generate(offering, args.quality, payload,
                                   lambda message: print(message, flush=True))
    except R2VError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted. If generation had already started it may still be billed.",
              file=sys.stderr)
        return 130

    elapsed = time.time() - started

    output_path = args.output or (
        DEFAULT_OUTPUT_DIR /
        f"{model_key}_{provider_key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    )
    output_path = output_path.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(result.video_bytes)
    size_mb = len(result.video_bytes) / (1024 * 1024)

    # Prefer the amount the provider actually billed; fall back to our estimate.
    cost = result.cost_usd if result.cost_usd is not None else estimate
    cost_source = result.cost_source or "rate card estimate"

    seconds = output_seconds
    if seconds is None and cost is not None and rate:
        seconds = round(cost / rate - (reference_seconds
                                       if offering.bills_reference_seconds else 0), 2)
    effective_rate = (cost / seconds) if (cost is not None and seconds) else None

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": model_key,
        "provider": provider_key,
        "endpoint": offering.endpoint_slug(args.quality),
        "request_id": result.request_id,
        "prompt": auto_prompt,
        "prompt_file": str(args.prompt_file) if args.prompt_file else None,
        "reference_videos": [p.name for p in video_paths],
        "reference_images": [p.name for p in image_paths],
        "resolution": args.quality,
        "aspect_ratio": args.aspect_ratio,
        "seconds_generated": seconds,
        "reference_video_seconds": round(reference_seconds, 2),
        "billed_seconds": round(billed_seconds, 2) if billed_seconds else None,
        "price_per_second_usd": round(effective_rate, 6) if effective_rate else None,
        "cost_usd": round(cost, 6) if cost is not None else None,
        "cost_source": cost_source,
        "generation_seconds": round(elapsed, 1),
        "output_file": str(output_path),
        **({"billable_units": result.extra["billable_units"]}
           if result.extra.get("billable_units") else {}),
    }
    running_total = append_cost_log(args.cost_log, entry)

    print("\n" + "=" * 64)
    print("GENERATION COMPLETE")
    print("=" * 64)
    print(f"  Model / provider   : {model.label} on {provider_cls.label}")
    print(f"  Output file        : {output_path}  ({size_mb:.1f} MB)")
    print(f"  Wall clock         : {elapsed:.0f}s")
    print(f"  Seconds generated  : {seconds if seconds is not None else 'unknown'}")
    if effective_rate is not None:
        print(f"  Price per second   : ${effective_rate:.4f}/s")
    if cost is not None:
        print(f"  Cost of this call  : ${cost:.4f}   [{cost_source}]")
    else:
        print("  Cost of this call  : unknown (no published rate and none reported)")
    print(f"  Running total      : ${running_total:.4f}   (from {args.cost_log})")
    print("=" * 64 + "\n")
    return 0
