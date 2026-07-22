# E3 peer corpus — DQT producer-fingerprint experiment

This directory holds the peer corpus for experiment **E3** (reproduce Kornblum:
the JPEG quantization tables classify the *producer* and survive F1/F2 scrubbing).
The runner is [`tests/scrub/e3_dqt.py`](../../scrub/e3_dqt.py); it reads this
directory by default.

## Layout

```
<producer>__scene<i>.jpg
```

- **producer** = which JPEG encoder compressed the file (the fingerprint axis).
  Current set: `apple_sips`, `libjpegturbo_cjpeg`, `ffmpeg_mjpeg`.
- **scene<i>** = which source image (the repeat axis; content held ~constant per
  scene across all producers).

So `apple_sips__scene0.jpg` and `ffmpeg_mjpeg__scene0.jpg` are the **same picture**
compressed by **different encoders** — the pair that should look identical yet
carry different DQT fingerprints.

## The image files are NOT committed

The `.jpg` binaries are git-ignored (they may be real photos with visible personal
content). Only this README is tracked. Track generators/manifests, not binaries —
the project rule. `git add -f` a file only if you have deliberately vetted it.

## Rebuilding the corpus

The set was built from HEIC source photos, decoded once to a lossless base and
re-encoded through each installed encoder (identical pixels in, distinct encoder
out). To regenerate, run the build over your own source images with the three
encoders:

- `sips -s format jpeg -s formatOptions 90 base.png --out apple_sips__sceneN.jpg`
- `cjpeg -quality 90 base.ppm > libjpegturbo_cjpeg__sceneN.jpg`
- `ffmpeg -noautorotate -i base.png -q:v 3 -map_metadata -1 ffmpeg_mjpeg__sceneN.jpg`

## Running the experiment

```
./.venv/bin/python -m tests.scrub.e3_dqt          # summary across raw / F1 / F2
./.venv/bin/python -m tests.scrub.gen_matrix      # fold the A2 verdict into the matrix
```

A valid corpus has the **same content repeated across producers** but a **distinct
DQT per producer**. Verify before trusting a run:

```
./.venv/bin/python -c "from tests.scrub.e3_dqt import group_corpus; \
from tests.harness.plugins.jpeg import JpegPlugin as P; g=group_corpus(); p=P(); \
[print(k, {p.structural_features(x)['dqt'] for x in v}) for k,v in g.items()]"
```

Each producer should print exactly **one** DQT value, and the three values should
all differ.
