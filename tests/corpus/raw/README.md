# Real camera RAW samples (local-only)

`manifest.txt` pins eight CC0 files from raw.pixls.us — one per RAW container
family — as `local-name|url|sha256`. They are 14–35 MB each and are **not
committed**; fetch them to `~/metadata-research/raw/` and verify each checksum:

```
while IFS='|' read -r n u s; do curl -sfL -o "$n" "$(python3 -c 'import sys,urllib.parse as p;print(p.quote(sys.argv[1],safe=":/"))' "$u")"; echo "$s  $n" | shasum -a 256 -c; done < manifest.txt
```

What they were used to measure is `docs/p4_media_plan.md` §13. CI does not use
them; CI builds its own DNG (Phase 4 RAW M1).
