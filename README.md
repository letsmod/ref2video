# ref2video

Generate a video from a text prompt plus **local** reference images and clips.
One command in front of four reference-to-video models, each available on more
than one provider:

| Model | Segmind | Replicate | fal |
| --- | --- | --- | --- |
| Seedance 2.5 | yes | yes | yes |
| Seedance 2.0 | yes | yes | yes |
| MiniMax Hailuo H3 | yes | yes | yes |
| Kling O3 | yes | — | yes |

It uploads the references, runs the model, saves the MP4 and logs what the call
cost, with a running total.

```powershell
python ref2video.py                  # a short guide
python ref2video.py --list-models    # what exists, what it costs, where
python ref2video.py --help           # every flag
```

## The command

```powershell
python ref2video.py --model minimax-h3 --provider fal --prompt-file prompts\prompt1.txt --ref-image refs\product\
```

Leave `--model` or `--provider` off an interactive run and you'll be asked, with
prices shown. Everything else has a default: 5 seconds, 16:9, each model's usual
quality tier, audio on where it's free.

Before spending anything, check the request with `--dry-run`. It validates every
limit, prints the exact JSON body that would be sent, and stops:

```powershell
python ref2video.py --model kling-o3 --provider fal --prompt "..." --ref-image refs\hero.png --dry-run
```

## Pick the provider deliberately — it's the biggest cost lever

The same model can cost four times as much on one provider as another, and the
differences don't run in one direction. For a 5-second clip from stills only:

| Model | Segmind | Replicate | fal |
| --- | --- | --- | --- |
| Seedance 2.5 @720p | $1.19 | $1.16 | $2.37 |
| Seedance 2.0 @720p | $0.76 | $0.90 | $1.52 |
| MiniMax H3 @2K | $0.81 | $0.65 | $0.65 |
| MiniMax H3 @768P | $0.56 | $0.40 | $0.30 |
| Kling O3 @pro | $2.10 | — | **$0.56** |

Add reference clips and the ranking inverts, because providers meter them
differently. The same 5-second Seedance 2.5 render with 9 seconds of reference
clips costs **$2.00 on Segmind, $3.97 on fal and $4.84 on Replicate** — Segmind
and fal charge for the clips' own seconds but discount the rate, while Replicate
charges output seconds only at a rate four times higher.

You don't have to remember any of this. Every run prints what the same job would
cost on the other providers before it asks you to confirm:

```
Estimated cost: $2.0006  (14.0 billed s x $0.1429/s)
  5s output            $0.7145
  9.0s reference clips  $1.2861   <- metered like output here
  same run on replicate  $ 4.8380 at 720p
  same run on fal        $ 3.9732 at 720p
```

## Choosing a model

| Model | `--model` | References | Duration | Use it when |
| --- | --- | --- | --- | --- |
| Seedance 2.5 | `seedance-2.5` | 30 images + 10 clips | 4–30s | You need length, many references, or motion clips |
| Seedance 2.0 | `seedance-2.0` | 9 images + 3 clips | 4–15s | Drafting — about half the price, and it reaches 4k |
| MiniMax H3 | `minimax-h3` | 5–9 images (clips on fal/Replicate) | 4–15s | Product detail from stills; cheapest per second and highest resolution |
| Kling O3 | `kling-o3` | 4 images (+ a source clip on Segmind) | 3–15s | Cinematic motion, or restyling footage you already have |

Kling O3 is genuinely two different things depending on where you run it. On
Segmind it *transforms a source clip you supply*, so it needs exactly one
`--ref` and keeps that clip's camera move. On fal it works from stills alone and
takes no clip at all. The tool enforces whichever applies.

## Passing references

`--ref` takes clips, `--ref-image` takes stills. Both accept several paths, a
glob, or a whole folder, expanded by the tool rather than the shell so they
behave the same in PowerShell as in bash:

```powershell
python ref2video.py ... --ref a.mp4 b.mp4        # several at once
python ref2video.py ... --ref a.mp4 --ref b.mp4  # repeat the flag
python ref2video.py ... --ref refs\*.mp4         # glob
python ref2video.py ... --ref-image refs\product\ # whole folder
```

Matches are sorted by filename and duplicates dropped, so `@Video 2` always
means the same file. The numbering is printed before anything is spent:

```
  @Video 1: refs\dolly.mp4  (3.0s)
  @Image 1: refs\product\front.jpg
```

Each provider spells citations differently — `@Image 1` on Segmind, `[Image1]`
on Replicate, `@Image1` on fal, plain `Image 1` for MiniMax. If your prompt
never mentions the references, the right form for that provider is appended
automatically; `--no-auto-cite` turns that off.

Limits differ per model *and* provider, and are checked locally before any
upload, because every provider rejects an over-long reference set only after
you've spent minutes uploading it:

```
Error: Reference clips total 31.0s across 7 files; this model accepts 30.2s
combined (over by 0.8s). Drop a clip, trim one, or pass --drop-excess-refs.
```

`--drop-excess-refs` keeps clips in the order given and skips those that don't
fit, so list the important ones first.

## Setup

```powershell
pip install -r requirements.txt
Copy-Item .env.example .env
```

Fill in only the providers you use:

```
SEGMIND_API_KEY=SG_xxxx
REPLICATE_API_TOKEN=r8_xxxx
FAL_KEY=key_id:key_secret
```

An exported shell variable of the same name overrides the file. `--env-file`
points at a different one. A key is only ever required for the provider you
actually run with, and it's checked before any upload starts.

## Uploads are cached per provider

References are uploaded before generation, which is the slow part of a run.
Successful uploads are remembered in `.upload_cache.json`, keyed by provider and
by a SHA-256 of the file's **contents** — so renaming or moving a file still
hits, editing one automatically misses, and a Segmind URL is never handed to
fal. Before reusing a URL the tool checks it still resolves.

```
  reusing dolly_in.mp4 (cached upload)
```

Uploads are free, so this saves time, not money. Use `--no-upload-cache` to
force a re-upload.

## What you get

- Video: `outputs\<model>_<provider>_<timestamp>.mp4` unless you pass `--output`.
- Spend: one JSON line per successful call in `cost_log.jsonl`, with the model,
  provider, endpoint, request id, seconds generated, price per second, cost and
  a running total. Failed calls are not logged and are not billed.

Only Segmind reports what it actually charged (in an `x-cost` response header),
and that figure is logged as ground truth. Replicate reports no cost at all and
fal reports billable *units* rather than dollars, so for those two the log
records this tool's own estimate and says so in `cost_source`.

## Flags

| Flag | Default | Notes |
| --- | --- | --- |
| `--model` / `-m` | asked | `seedance-2.5`, `seedance-2.0`, `minimax-h3`, `kling-o3` |
| `--provider` / `-p` | asked | `segmind`, `replicate`, `fal` |
| `--list-models` | — | Every model, every provider, with prices |
| `--prompt` | one required | Inline prompt |
| `--prompt-file` | one required | Read the prompt from a file; `#` lines are notes |
| `--ref` | — | Reference clips: paths, globs or folders |
| `--ref-image` | — | Reference stills: paths, globs or folders |
| `--duration` | `5` | Range depends on the model; `-1` where auto is supported |
| `--resolution` / `--quality` | per model | Values depend on model and provider |
| `--aspect-ratio` | per model | Values depend on model and provider |
| `--seed` | random | Fix it to reproduce a clip (Seedance, Wan-style models) |
| `--audio` / `--no-audio` | on where free | Kling on fal charges 25% more with audio, so it defaults off there |
| `--dry-run` | off | Validate and print the request; send nothing |
| `--drop-excess-refs` | off | Trim clips to the combined-duration cap instead of stopping |
| `--no-auto-cite` | off | Never append reference tags |
| `--output` | `outputs\...` | Where to write the MP4 |
| `--cost-log` | `cost_log.jsonl` | Spend log |
| `--upload-cache` | `.upload_cache.json` | Remembered upload URLs |
| `--no-upload-cache` | off | Re-upload everything |
| `--env-file` | `.env` | Where the API keys live |
| `--yes` / `-y` | off | Skip the confirmation |

## Reference clips are billed like output (Seedance)

The biggest cost trap, and it applies on Segmind and fal but not Replicate. Both
meter **reference-clip duration at the same per-second rate as the output**, so
the billed quantity is `output seconds + total reference-clip seconds`.
Reference *images* are always free.

A real invoice: 5s of 720p Seedance 2.5 output with 28s of reference clips
billed **$4.68** on Segmind, not the $0.71 the output alone would cost. The
clips were 85% of the bill. Practical consequences:

- Clips only pay for themselves below roughly 3.4 seconds total. Past that,
  images-only is cheaper *and* usually better for product identity.
- Trimming references saves far more than dropping resolution or length.
- MiniMax H3 and Kling O3 never meter references, so they're the safe choice
  when you want many reference seconds.

## Adding a model or a provider

The layout is meant to keep each of those to one file:

```
ref2video.py          entry point
r2v/catalog.py        which models exist, on which providers, limits and prices
r2v/providers/        one file per API: auth, upload, submit, poll, download
r2v/cli.py            arguments, the help screen, the run
r2v/media.py          finding, probing and validating local references
r2v/uploads.py        the per-provider upload cache
r2v/costlog.py        the spend log
test_catalog.py       offline checks: every model x provider, no network, no cost
```

A new model on an existing provider is one `Offering(...)` entry in
`r2v/catalog.py`. A new provider is one class in `r2v/providers/` implementing
`upload()` and `generate()`. Run `python test_catalog.py` afterwards — it walks
every model × provider pair, asserts the request body matches that provider's
published schema, and checks the catalog is internally consistent.

## Gotchas

- Segmind reserves ~$1.90 of balance up front regardless of the clip's real
  cost, so a thin balance returns HTTP 406 even on a cheap request.
- Segmind generation is one long synchronous call; Replicate and fal are
  submit-and-poll. Either way expect one to four minutes.
- Replicate deletes prediction outputs after an hour. The tool downloads
  immediately, but if a run dies between submit and download the prediction
  keeps going and is still billed — find it at `replicate.com/p/<id>` within
  the hour, since the id is printed with the request.
- Replicate caps prompts at 2000 characters for Seedance 2.5 and 4000 for
  Seedance 2.0 — enough to reject a detailed product brief. Segmind and fal
  declare no limit. The tool checks this before uploading anything.
- Replicate declares MiniMax H3's reference fields as plain strings rather than
  file inputs, so it may ignore URLs hosted on Replicate's own Files API. The
  tool warns you before the run; use fal or Segmind for H3 if references seem
  to be ignored.
- Give a long clip enough prompt to fill the time — budget roughly one `Shot N:`
  block per 5 seconds, which is the other reason a prompt file helps.
