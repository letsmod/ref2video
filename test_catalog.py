"""Offline checks: no network, no API keys, no cost.

Runs every model x provider pair through argument validation, payload building
and cost estimation using real local files, so the shape of every request is
verified before any money is spent. Also asserts the catalog is internally
consistent (defaults are legal values, every offering has a price, and so on).

    python test_catalog.py
"""

from __future__ import annotations

import json
import sys

from r2v import catalog, media
from r2v.cli import (apply_defaults, build_payload, build_parser, cite_references,
                     prompt_length_problem, validate)
from r2v.errors import R2VError
from r2v.providers import PROVIDER_CLASSES

REFS = r"D:\Work\PathLLM\POC\product-dataset-studio\output"
CLIP = REFS + r"\dolly_in.mp4"
CLIP2 = REFS + r"\helix.mp4"
IMG = REFS + r"\png\frame ({}).png"

failures: list[str] = []
checks = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if condition:
        print(f"  ok   {name}")
    else:
        failures.append(f"{name}: {detail}")
        print(f"  FAIL {name}: {detail}")


def parse(argv: list[str]):
    return build_parser().parse_args(argv)


def run(name: str, model: str, provider: str, argv: list[str],
        expect_error: str | None = None, show: bool = False) -> None:
    """Validate + build a payload, asserting success or an expected rejection."""
    global checks
    checks += 1
    args = parse(argv)
    try:
        offering = catalog.get_offering(catalog.resolve_model_key(model), provider)
        videos, images = validate(args, offering)
    except R2VError as exc:
        if expect_error and expect_error.lower() in str(exc).lower():
            print(f"  ok   {name}  (rejected: {str(exc)[:90]})")
        else:
            failures.append(f"{name}: {exc}")
            print(f"  FAIL {name}: {exc}")
        return

    if expect_error:
        failures.append(f"{name}: expected rejection containing '{expect_error}'")
        print(f"  FAIL {name}: expected a rejection, got none")
        return

    prompt, _ = cite_references(offering, "test prompt", len(images), len(videos))
    payload = build_payload(
        offering, prompt,
        [f"https://example.com/i{i}.png" for i in range(len(images))],
        [f"https://example.com/v{i}.mp4" for i in range(len(videos))],
        args)
    ref_seconds = media.total_reference_seconds(videos)
    estimate, rate, billed = catalog.estimate_cost(
        offering, args.quality, args.aspect_ratio, args.duration, ref_seconds,
        len(images), args.audio_on, bool(videos))

    print(f"  ok   {name}")
    if show:
        print(f"       {offering.endpoint_slug(args.quality)}")
        print(f"       {json.dumps(payload)[:190]}")
        print(f"       estimate " + (f"${estimate:.4f} ({billed:.1f}s x ${rate:.4f}/s)"
                                     if estimate else "unavailable"))


# --------------------------------------------------------------------------- #
print("\nCatalog consistency\n")

for offering in catalog.OFFERINGS:
    tag = f"{offering.model}/{offering.provider}"
    check(f"{tag}: model exists", offering.model in catalog.MODELS)
    check(f"{tag}: provider exists", offering.provider in PROVIDER_CLASSES)
    check(f"{tag}: default quality is legal",
          offering.default_quality in offering.quality_choices,
          f"{offering.default_quality} not in {offering.quality_choices}")
    check(f"{tag}: default aspect is legal",
          not offering.aspect_param or offering.default_aspect in offering.aspect_choices,
          f"{offering.default_aspect} not in {offering.aspect_choices}")
    check(f"{tag}: default tier is priced",
          catalog.price_per_second(offering, offering.default_quality,
                                   offering.default_aspect, False, False) is not None)
    check(f"{tag}: accepts some reference",
          offering.supports_images() or offering.supports_videos()
          or offering.needs_source_video())
    check(f"{tag}: slug_by_quality covers every tier",
          not offering.slug_by_quality
          or set(offering.slug_by_quality) == set(offering.quality_choices))
    check(f"{tag}: tier is either a field or in the URL",
          bool(offering.quality_param) or bool(offering.slug_by_quality))

check("every model has at least one provider",
      all(catalog.PROVIDERS_FOR[m] for m in catalog.MODELS))
check("aliases resolve", catalog.resolve_model_key("seedance") == "seedance-2.5"
      and catalog.resolve_model_key("h3") == "minimax-h3")

# --------------------------------------------------------------------------- #
print("\nEvery model x provider, happy path\n")

for offering in catalog.OFFERINGS:
    name = f"{offering.model} on {offering.provider}"
    argv = ["--prompt", "p"]
    if offering.needs_source_video():
        argv += ["--ref", CLIP]
    if offering.supports_images():
        argv += ["--ref-image", IMG.format(1)]
    elif offering.supports_videos():
        argv += ["--ref", CLIP]
    argv += ["--duration", str(max(offering.duration_range[0], 5))]
    run(name, offering.model, offering.provider, argv, show=True)

# --------------------------------------------------------------------------- #
print("\nClips where they are supported\n")

run("seedance-2.5 segmind, images + clip", "seedance-2.5", "segmind",
    ["--prompt", "p", "--ref", CLIP, "--ref-image", IMG.format(1)], show=True)
run("seedance-2.5 replicate, images + clip", "seedance-2.5", "replicate",
    ["--prompt", "p", "--ref", CLIP, "--ref-image", IMG.format(1)], show=True)
run("seedance-2.5 fal, images + clip", "seedance-2.5", "fal",
    ["--prompt", "p", "--ref", CLIP, "--ref-image", IMG.format(1)], show=True)
run("minimax-h3 fal, images + clip", "minimax-h3", "fal",
    ["--prompt", "p", "--ref", CLIP, "--ref-image", IMG.format(1)], show=True)

# --------------------------------------------------------------------------- #
print("\nGuard rails\n")

run("unknown model", "seedance-9", "segmind", ["--prompt", "p", "--ref-image", IMG.format(1)],
    expect_error="unknown model")
run("kling-o3 is not on replicate", "kling-o3", "replicate",
    ["--prompt", "p", "--ref-image", IMG.format(1)], expect_error="not available on replicate")
run("minimax segmind refuses clips", "minimax-h3", "segmind",
    ["--prompt", "p", "--ref", CLIP], expect_error="images only")
run("kling fal refuses clips", "kling-o3", "fal",
    ["--prompt", "p", "--ref", CLIP, "--ref-image", IMG.format(1)],
    expect_error="images only")
run("kling segmind needs exactly one clip", "kling-o3", "segmind",
    ["--prompt", "p", "--ref", CLIP, CLIP2], expect_error="exactly one source clip")
run("kling segmind needs a clip at all", "kling-o3", "segmind",
    ["--prompt", "p", "--ref-image", IMG.format(1)], expect_error="exactly one source clip")
run("minimax segmind caps images at 5", "minimax-h3", "segmind",
    ["--prompt", "p", "--ref-image", *[IMG.format(i) for i in range(1, 8)]],
    expect_error="at most 5")
run("minimax segmind needs an image", "minimax-h3", "segmind",
    ["--prompt", "p", "--ref", CLIP], expect_error="images only")
run("seedance-2.0 caps clips at 3", "seedance-2.0", "segmind",
    ["--prompt", "p", "--ref", REFS + r"\*.mp4"], expect_error="at most 3")
run("duration out of range", "minimax-h3", "fal",
    ["--prompt", "p", "--ref-image", IMG.format(1), "--duration", "30"],
    expect_error="5-15 seconds")
run("auto duration where unsupported", "minimax-h3", "fal",
    ["--prompt", "p", "--ref-image", IMG.format(1), "--duration", "-1"],
    expect_error="no automatic duration")
run("resolution not on this provider", "seedance-2.5", "replicate",
    ["--prompt", "p", "--ref-image", IMG.format(1), "--resolution", "1080p"],
    expect_error="does not support")
run("aspect not on this provider", "kling-o3", "segmind",
    ["--prompt", "p", "--ref", CLIP, "--aspect-ratio", "21:9"],
    expect_error="does not support")
run("seedance-2.5 segmind 30.2s combined cap", "seedance-2.5", "segmind",
    ["--prompt", "p", "--ref-image", IMG.format(1)], show=False)

# Prompt ceilings, which providers only enforce after every upload has landed.
rep_25 = catalog.get_offering("seedance-2.5", "replicate")
rep_20 = catalog.get_offering("seedance-2.0", "replicate")
over = prompt_length_problem(rep_25, "x" * 4722)
check("replicate seedance-2.5 stops a 4722-char prompt", over is not None)
check("the overrun says how much to trim", "Trim 2722" in (over or ""), over or "")
check("it names the providers that would take it",
      "fal" in (over or "") and "segmind" in (over or ""), over or "")
check("a prompt at the limit passes",
      prompt_length_problem(rep_25, "x" * 2000) is None)
check("seedance-2.0 on replicate allows 4000",
      prompt_length_problem(rep_20, "x" * 4000) is None
      and prompt_length_problem(rep_20, "x" * 4001) is not None)
check("undeclared limits are not enforced",
      prompt_length_problem(catalog.get_offering("seedance-2.5", "fal"), "x" * 50000) is None)

# --------------------------------------------------------------------------- #
print("\nPer-provider request shapes\n")

def payload_for(model, provider, argv):
    args = parse(argv)
    offering = catalog.get_offering(catalog.resolve_model_key(model), provider)
    videos, images = validate(args, offering)
    return offering, build_payload(offering, "p", ["https://i/1.png"],
                                   ["https://v/1.mp4"] if videos else [], args)

_, seg = payload_for("seedance-2.5", "segmind",
                     ["--prompt", "p", "--ref-image", IMG.format(1)])
check("segmind seedance uses aspect_ratio + int duration",
      seg["aspect_ratio"] == "16:9" and seg["duration"] == 5, str(seg))

_, rep = payload_for("minimax-h3", "replicate",
                     ["--prompt", "p", "--ref-image", IMG.format(1)])
check("replicate minimax uses ratio + reference_image_urls",
      "ratio" in rep and "reference_image_urls" in rep, str(rep))

_, fal = payload_for("seedance-2.5", "fal",
                     ["--prompt", "p", "--ref-image", IMG.format(1)])
check("fal seedance uses image_urls + string duration",
      fal["image_urls"] == ["https://i/1.png"] and fal["duration"] == "5", str(fal))

offering_std, kling = payload_for("kling-o3", "fal",
                                  ["--prompt", "p", "--ref-image", IMG.format(1),
                                   "--resolution", "std"])
check("fal kling puts the tier in the URL, not the body",
      "mode" not in kling and "resolution" not in kling
      and offering_std.endpoint_slug("std").endswith("standard/reference-to-video"),
      str(kling))

_, kling_seg = payload_for("kling-o3", "segmind", ["--prompt", "p", "--ref", CLIP])
check("segmind kling sends video_url + mode",
      kling_seg.get("video_url") == "https://v/1.mp4" and kling_seg.get("mode") == "pro",
      str(kling_seg))

args_auto = parse(["--prompt", "p", "--ref-image", IMG.format(1), "--duration", "-1"])
off_fal = catalog.get_offering("seedance-2.5", "fal")
validate(args_auto, off_fal)
auto_payload = build_payload(off_fal, "p", ["https://i/1.png"], [], args_auto)
check("fal auto duration is the string 'auto'", auto_payload["duration"] == "auto",
      str(auto_payload))

args_auto_seg = parse(["--prompt", "p", "--ref-image", IMG.format(1), "--duration", "-1"])
off_seg = catalog.get_offering("seedance-2.5", "segmind")
validate(args_auto_seg, off_seg)
check("segmind auto duration is -1",
      build_payload(off_seg, "p", ["https://i/1.png"], [], args_auto_seg)["duration"] == -1)

# --------------------------------------------------------------------------- #
print("\nAudio defaults and pricing\n")

args_kling = parse(["--prompt", "p", "--ref-image", IMG.format(1)])
off_kling = catalog.get_offering("kling-o3", "fal")
validate(args_kling, off_kling)
check("audio defaults off where it costs extra", args_kling.audio_on is False)

args_seed = parse(["--prompt", "p", "--ref-image", IMG.format(1)])
validate(args_seed, catalog.get_offering("seedance-2.5", "segmind"))
check("audio defaults on where it is free", args_seed.audio_on is True)

check("kling audio-on rate is higher",
      catalog.price_per_second(off_kling, "pro", "16:9", False, True) == 0.14
      and catalog.price_per_second(off_kling, "pro", "16:9", False, False) == 0.112)

# Seedance on Segmind meters reference clips; on Replicate it does not.
seg_est, _, seg_billed = catalog.estimate_cost(
    catalog.get_offering("seedance-2.5", "segmind"), "720p", "16:9", 5, 28, 0, True, True)
rep_est, _, rep_billed = catalog.estimate_cost(
    catalog.get_offering("seedance-2.5", "replicate"), "720p", "16:9", 5, 28, 0, True, True)
check("segmind bills output + reference seconds", seg_billed == 33, str(seg_billed))
check("replicate bills output seconds only", rep_billed == 5, str(rep_billed))
check("the known $4.68 invoice reproduces", abs(seg_est - 4.7157) < 0.01, f"${seg_est:.4f}")

fal_est, _, _ = catalog.estimate_cost(
    catalog.get_offering("minimax-h3", "fal"), "2K", "16:9", 5, 0, 8, False, False)
check("fal minimax adds the per-image surcharge past 5",
      abs(fal_est - (0.13 * 5 + 3 * 0.08)) < 1e-9, f"${fal_est:.4f}")

# --------------------------------------------------------------------------- #
print("\nSubmit responses that mean 'accepted'\n")

# Replicate answered a real submission with 202 and the tool called it a failure,
# abandoning a prediction that ran to completion and was billed anyway. Every 2xx
# has to be treated as a live, billable job.

class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self.payload = payload
        self.text = json.dumps(payload)
        self.content = b"fake-mp4-bytes"
        self.headers = {}

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self.payload

    def raise_for_status(self):
        pass


class FakeHTTP:
    """Stands in for the requests module inside one provider adapter."""

    class RequestException(Exception):
        pass

    def __init__(self, response):
        self.response = response

    def post(self, *args, **kwargs):
        return self.response

    def get(self, *args, **kwargs):
        return self.response


def submit_with_status(status: int) -> bytes:
    """Run Replicate's generate() against a canned submit response."""
    from r2v.providers import base, replicate as replicate_mod

    # `output` is already present so the poll loop exits without sleeping.
    response = FakeResponse(status, {"id": "abc123", "status": "processing",
                                     "output": "https://cdn.example/out.mp4",
                                     "urls": {"get": "https://api.example/p/abc123"},
                                     "metrics": {}})
    saved = (replicate_mod.requests, base.requests)
    replicate_mod.requests = base.requests = FakeHTTP(response)
    try:
        provider = replicate_mod.ReplicateProvider("token")
        result = provider.generate(catalog.get_offering("seedance-2.5", "replicate"),
                                   "720p", {"prompt": "p"}, lambda message: None)
        return result.video_bytes
    finally:
        replicate_mod.requests, base.requests = saved


for accepted in (200, 201, 202):
    try:
        check(f"replicate submit accepted on HTTP {accepted}",
              submit_with_status(accepted) == b"fake-mp4-bytes")
    except R2VError as exc:
        check(f"replicate submit accepted on HTTP {accepted}", False, str(exc)[:90])

try:
    submit_with_status(500)
    check("replicate submit still fails on HTTP 500", False, "no error raised")
except R2VError:
    check("replicate submit still fails on HTTP 500", True)

# --------------------------------------------------------------------------- #
print()
if failures:
    print(f"{len(failures)} of {checks} checks FAILED\n")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print(f"All {checks} checks passed.")
