# A World With a Past Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the agent a past — historical series, a gazetteer, a notation corpus, and a query
engine — so that analysis work exists which one context cannot hold and which verifies itself
against instruments the agent already has.

**Architecture:** Four independent deliverables at one rebuild boundary — corpus data, packages,
and two diode commands. Host-side data provisioning
extends `scripts/fetch_corpus.sh` (the only writer of `volumes/corpus`, bind-mounted read-only at
`/corpus`); two small wheels join `requirements-agent.txt`, which regenerates the garden runtime
inventory automatically; and two diode changes extend the closed command vocabulary. Nothing here
adds a channel, a credential, or a network path — every dataset is fetched on the host and appears to
the agent as read-only files.

**Tech Stack:** POSIX shell (`curl`, `wget`, `python3`) for provisioning; Python 3.13 + `pytest` for
the diode; Docker for the image measurement.

**Spec:** `docs/superpowers/specs/2026-08-17-a-world-with-a-past-design.md`

## Global Constraints

Copied verbatim from the spec and CLAUDE.md. Every task's requirements implicitly include these.

- **Invariant 2 — strange yet clean.** No reader-addressed prose reaches any agent-visible surface.
  Provider READMEs, headers, licence files, and documentation are stripped from every fetched
  dataset. No captions, no index, no `README.md` under `/corpus`.
- **Invariant 4 — human docs stay out of the agent's world.** Do not add anything in `docs/` or
  `tests/` to the `Dockerfile` `COPY` allow-list.
- **No unreadable data.** Every dataset added must be readable with the standard library, `numpy`,
  or one of the four approved packages. If a candidate needs a reader that is not on the image,
  either the reader is admitted by an explicit design decision (as `netCDF4` was, for ETOPO) or
  the dataset does not ship. Never ship data the agent cannot open.
- **Image size.** The approved package set must stay within **250 MiB** of the pre-change image built
  on the same host — raised from 100 MiB by operator decision on 2026-08-17 and recorded in
  CLAUDE.md. The measurement is a gate, not a formality (Task 5). Measured cost of the approved set
  is 238 MiB, leaving 12 MiB of headroom, so this is a tight ceiling and not a licence to add more.
- **No new third-party dependency beyond the four named** (`duckdb`, `skyfield`, `scipy`, `netCDF4`)
  without a new design decision. `pandas` is explicitly deferred: `duckdb` covers the tabular work
  and `scipy` the numerical work, so a third overlapping tool is inventory, not capability.
- **Diode additions:** one gate per family; every fetch charges the shared budget; `classify_url` runs
  on all URLs including fixed hosts; bland one-line help; a `COMMANDS` entry with a help string; no
  query language crosses the diode; no new credential.
- **`garden_export/` and `llm_console_seed.json` are generated.** Never hand-edit them; regenerate
  with `scripts/prepare_host.sh`.
- **Commit messages are factual and benign** — describe the change plainly, no game or task framing.
- **Lint before every commit:** `.venv/bin/ruff format . && .venv/bin/ruff check .`
- **Test command:** `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`

## Operator Prerequisites (not code)

These are John's. **All rulings landed on 2026-08-17; every task below is unblocked.** Tracked in
spec §6.

- [x] Ruling: syzygy trim — keep ≤4 pieces plus pawnless 5-piece, 346 MB (spec §4.6)
- [x] Ruling: GHCN year spread — 1880, 1900, 1920, 1940, 1960, 2024 (spec §4.1)
- [x] Ruling: music corpus — yes to notation and theory, audio deferred; `**kern` only, MIDI
      stripped (spec §4.5)
- [x] Ruling: licensing cleared — SILSO CC BY-NC 4.0 is fine, non-commercial project (spec §6.4)
- [x] Ruling: ETOPO — netCDF4 admitted, ceiling raised to 250 MiB; ships as a 5 arc-minute
      NetCDF-4 subsample converted on the host (spec §4.8)
- [ ] Operator-sourced `/books` additions, weighted toward world-describing prose (spec §2).
      No code change: `COPY books/ /books/` already ships whatever is in the directory at build time.
      Note `books/README.md` was removed from the host tree on 2026-08-17 and leaves the image at the
      next `docker compose build`.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/fetch_corpus.sh` | Modify | The only writer of `volumes/corpus`. Gains five fetch sections (history, place, sky, life, notation) and a syzygy prune step. Each section stays re-runnable and skips work already done, matching the existing style. |
| `tests/test_host_scripts.py` | Modify | Asserts on the text of host provisioning scripts. Gains assertions that each new section strips provider prose and that no section introduces a reader dependency. |
| `requirements-agent.txt` | Modify | The agent image package manifest. Gains `duckdb`, `skyfield`, `scipy`, and `netCDF4`. |
| `tests/test_build_garden.py` | Modify | Asserts the generated runtime inventory. Gains assertions for the four new package names. |
| `tests/test_agent_dependencies.py` | Modify | Asserts the manifest's shape. Gains the four new names. |
| `diode.py` | Modify | The closed command vocabulary. Gains `nearby` + `_map_gate` + `_nearby_lines` (Task 6) and a calendar horizon in `parse_delay` (Task 7). |
| `tests/test_diode.py` | Modify | Diode behaviour tests. Gains `nearby` and calendar-horizon coverage. |
| `scripts/verify_container.sh` | Modify | Live-stack checks. Gains an import check for the four new packages and a presence check for the new corpus trees. |

Tasks 1–4 are one shippable unit (the data). Task 5 is the gate on Tasks 1–4 plus the libraries.
Tasks 6 and 7 are independent diode subsystems and could equally be separate plans; they are included
here because they land at the same rebuild boundary and serve the same spec.

---

### Task 1: Historical series in the corpus

**Files:**
- Modify: `scripts/fetch_corpus.sh` (append new sections before the closing `echo "== done"`)
- Test: `tests/test_host_scripts.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `$OUT/history/sunspots/{SN_d_tot_V2.0.csv,SN_m_tot_V2.0.csv}`,
  `$OUT/history/climate/{ghcnd-stations.txt,YYYY.csv.gz…}`, `$OUT/history/quakes/quakes.csv`.
  Task 4's verification step lists these paths.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_host_scripts.py`:

```python
def test_fetch_corpus_adds_history_without_provider_prose() -> None:
    script = _read("scripts/fetch_corpus.sh")

    # Each historical series terminates in a live diode instrument; all three
    # must be present for the verification loop the corpus is being grown for.
    assert "SN_d_tot_V2.0.csv" in script
    assert "ghcnd-stations.txt" in script
    assert "earthquake.usgs.gov/fdsnws/event" in script

    # Bare data only: no provider documentation may land under /corpus.
    for artefact in ("readme", "README", "LICENSE", "index.html"):
        assert f"{artefact}\"" not in script.replace("$OUT", "")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_host_scripts.py::test_fetch_corpus_adds_history_without_provider_prose -v`
Expected: FAIL with `AssertionError` on `assert "SN_d_tot_V2.0.csv" in script`

- [ ] **Step 3: Add the history sections to `scripts/fetch_corpus.sh`**

Insert immediately before the final `echo "== done"` line:

```sh
echo "== history: SILSO sunspot numbers"
mkdir -p "$OUT/history/sunspots"
for f in SN_d_tot_V2.0.csv SN_m_tot_V2.0.csv; do
    [ -s "$OUT/history/sunspots/$f" ] || curl -sSL --retry 3 --retry-all-errors \
        -o "$OUT/history/sunspots/$f" "https://www.sidc.be/SILSO/DATA/$f"
done

echo "== history: GHCN-Daily station metadata and a spread of years"
mkdir -p "$OUT/history/climate"
[ -s "$OUT/history/climate/ghcnd-stations.txt" ] || curl -sSL --retry 3 --retry-all-errors \
    -o "$OUT/history/climate/ghcnd-stations.txt" \
    https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt
# A spread across the record rather than consecutive recent years: early files
# are small because few stations existed, so 144 years of depth costs less than
# three consecutive modern years would. 394 MB measured.
for year in 1880 1900 1920 1940 1960 2024; do
    [ -s "$OUT/history/climate/$year.csv.gz" ] || curl -sSL --retry 3 --retry-all-errors \
        -o "$OUT/history/climate/$year.csv.gz" \
        "https://www.ncei.noaa.gov/pub/data/ghcn/daily/by_year/$year.csv.gz"
done

echo "== history: global earthquake catalog"
mkdir -p "$OUT/history/quakes"
if [ ! -s "$OUT/history/quakes/quakes.csv" ]; then
    : > "$OUT/history/quakes/quakes.csv.part"
    year=1970
    while [ "$year" -le 2025 ]; do
        curl -sSL --retry 3 --retry-all-errors \
            "https://earthquake.usgs.gov/fdsnws/event/1/query?format=csv&starttime=$year-01-01&endtime=$((year + 1))-01-01&minmagnitude=4.5&orderby=time-asc" \
            | { if [ "$year" -eq 1970 ]; then cat; else tail -n +2; fi; } \
            >> "$OUT/history/quakes/quakes.csv.part"
        year=$((year + 1))
    done
    mv "$OUT/history/quakes/quakes.csv.part" "$OUT/history/quakes/quakes.csv"
fi
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_host_scripts.py -v`
Expected: PASS

- [ ] **Step 5: Run the fetch and confirm the shape of what landed**

Run:
```sh
sh scripts/fetch_corpus.sh
du -sh volumes/corpus/history/*
head -c 300 volumes/corpus/history/sunspots/SN_d_tot_V2.0.csv
head -2 volumes/corpus/history/quakes/quakes.csv
find volumes/corpus/history -iname 'readme*' -o -iname '*.html' -o -iname 'LICENSE*'
```
Expected: three directories totalling roughly 450 MB; the SILSO head is semicolon-separated numeric
rows with no header prose; the quakes head is one CSV header line then a data row; the `find` returns
nothing. If `find` returns anything, delete it and add the deletion to the script — invariant 2.

- [ ] **Step 6: Commit**

```bash
git add scripts/fetch_corpus.sh tests/test_host_scripts.py
git commit -m "Add historical sunspot, climate, and earthquake series to the corpus"
```

---

### Task 2: Gazetteer, coastline, relief, deep-sky catalogue, genome, and notation

**Files:**
- Modify: `scripts/fetch_corpus.sh`
- Test: `tests/test_host_scripts.py`

**Interfaces:**
- Consumes: the `$OUT` variable and section style established in Task 1.
- Produces: `$OUT/place/{cities500.txt,ne_10m_coastline.geojson}`, `$OUT/sky/NGC.csv`,
  `$OUT/life/yeast.fa.gz`, `$OUT/notation/<corpus>/*.krn`, `$OUT/place/etopo_5min.nc`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_host_scripts.py`:

```python
def test_fetch_corpus_adds_place_sky_and_life_in_readable_formats() -> None:
    script = _read("scripts/fetch_corpus.sh")

    assert "cities500.zip" in script
    assert "ne_10m_coastline.geojson" in script
    assert "NGC.csv" in script
    assert "dna.toplevel.fa.gz" in script
    assert "bach-370-chorales" in script
    assert "ETOPO_2022_v1_60s" in script
    assert "etopo_5min.nc" in script

    # The 457 MB original must never reach /corpus: only the subsample is kept.
    assert 'rm -rf "$tmp"' in script

    # Every added set must be readable with the standard library, numpy, or
    # one of the four approved packages.
    # GeoTIFF and shapefile still have no reader on the agent image; shipping
    # one would create a surface the agent cannot open. netCDF has left this
    # list: netCDF4 was admitted for exactly that purpose (spec 4.7).
    for unreadable in (".tif", ".tiff", ".shp"):
        assert unreadable not in script


def test_fetch_corpus_keeps_notation_without_audio_or_renderings() -> None:
    script = _read("scripts/fetch_corpus.sh")

    # Music ships as notation only. MIDI would smuggle a sound representation
    # into a wave that deliberately excludes one, and the rendered PDFs are
    # redundant with the notation they were rendered from.
    kern_section = script.split("== notation")[1]
    assert "-name '*.krn'" in kern_section
    assert "*.mid" not in kern_section.replace("! -name '*.krn'", "")
    assert "-delete" in kern_section
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_host_scripts.py::test_fetch_corpus_adds_place_sky_and_life_in_readable_formats -v`
Expected: FAIL with `AssertionError` on `assert "cities500.zip" in script`, and the notation test
fails on the `script.split("== notation")` index error.

- [ ] **Step 3: Add the sections to `scripts/fetch_corpus.sh`**

Insert before the final `echo "== done"` line:

```sh
echo "== place: GeoNames gazetteer and coastline"
mkdir -p "$OUT/place"
if [ ! -s "$OUT/place/cities500.txt" ]; then
    curl -sSL --retry 3 --retry-all-errors -o /tmp/cities500.zip \
        https://download.geonames.org/export/dump/cities500.zip
    python3 -c "import zipfile; zipfile.ZipFile('/tmp/cities500.zip').extractall('$OUT/place')"
    rm -f /tmp/cities500.zip "$OUT/place/readme.txt"
fi
[ -s "$OUT/place/ne_10m_coastline.geojson" ] || curl -sSL --retry 3 --retry-all-errors \
    -o "$OUT/place/ne_10m_coastline.geojson" \
    https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_coastline.geojson

echo "== sky: OpenNGC deep-sky catalog"
[ -s "$OUT/sky/NGC.csv" ] || curl -sSL --retry 3 --retry-all-errors -o "$OUT/sky/NGC.csv" \
    https://raw.githubusercontent.com/mattiaverga/OpenNGC/master/database_files/NGC.csv

echo "== life: reference genome"
mkdir -p "$OUT/life"
[ -s "$OUT/life/yeast.fa.gz" ] || curl -sSL --retry 3 --retry-all-errors -o "$OUT/life/yeast.fa.gz" \
    https://ftp.ensembl.org/pub/current_fasta/saccharomyces_cerevisiae/dna/Saccharomyces_cerevisiae.R64-1-1.dna.toplevel.fa.gz

echo "== notation: kern score corpora"
mkdir -p "$OUT/notation"
fetch_kern() {
    name=$1; repo=$2; ref=$3
    [ -d "$OUT/notation/$name" ] && return 0
    tmp=$(mktemp -d)
    curl -sSL --retry 3 --retry-all-errors "https://codeload.github.com/$repo/tar.gz/$ref" \
        | tar xz -C "$tmp"
    mkdir -p "$OUT/notation/$name"
    # Notation only. The upstream tarballs also carry rendered PDFs, MIDI, and
    # README/txt notes: the prose and renderings go for invariant 2, the MIDI
    # goes because this wave ships music as notation and not as sound.
    # --parents keeps the repo-relative directory structure, so scores with the
    # same basename in different composer directories do not collide.
    (cd "$tmp"/*/ && find . -name '*.krn' -exec cp --parents {} "$OUT/notation/$name/" \;)
    rm -rf "$tmp"
}
fetch_kern bach-370-chorales craigsapp/bach-370-chorales          master
fetch_kern josquin           josquin-research-project/jrp-scores  main
fetch_kern beethoven-sonatas craigsapp/beethoven-piano-sonatas    main
fetch_kern mozart-sonatas    craigsapp/mozart-piano-sonatas       main
fetch_kern chopin-mazurkas   craigsapp/chopin-mazurkas            main
find "$OUT/notation" -type f ! -name '*.krn' -delete
find "$OUT/notation" -type d -empty -delete

echo "== place: ETOPO relief, subsampled to 5 arc-minute"
# The 60 arc-second original is 457 MB and essentially uncompressed, which
# costs more than the whole climate record for a static grid. Subsampling by
# 5 keeps every range, basin, and shelf at ~1/44th the bytes, and the result
# stays a real NetCDF-4 file with lat/lon coordinates rather than a bare
# array. Conversion runs on the host in a throwaway container, as the fonts
# step above already does.
if [ ! -s "$OUT/place/etopo_5min.nc" ]; then
    tmp=$(mktemp -d)
    curl -sSL --retry 3 --retry-all-errors -o "$tmp/etopo60s.nc" \
        https://www.ngdc.noaa.gov/thredds/fileServer/global/ETOPO2022/60s/60s_surface_elev_netcdf/ETOPO_2022_v1_60s_N90W180_surface.nc
    docker run --rm -v "$tmp":/w -v "$OUT/place":/out python:3.13-slim sh -c '
        pip install -q --no-cache-dir netCDF4
        python - <<PYCONV
import netCDF4
src = netCDF4.Dataset("/w/etopo60s.nc")
step = 5
z = src.variables["z"][::step, ::step]
lat = src.variables["lat"][::step]
lon = src.variables["lon"][::step]
dst = netCDF4.Dataset("/out/etopo_5min.nc", "w", format="NETCDF4")
dst.createDimension("lat", z.shape[0])
dst.createDimension("lon", z.shape[1])
vla = dst.createVariable("lat", "f4", ("lat",))
vlo = dst.createVariable("lon", "f4", ("lon",))
vz = dst.createVariable("z", "i2", ("lat", "lon"), zlib=True, complevel=6)
vla[:] = lat
vlo[:] = lon
vz[:, :] = z.astype("int16")
vla.units = "degrees_north"
vlo.units = "degrees_east"
vz.units = "meters"
dst.close()
PYCONV
        chown '"$(id -u):$(id -g)"' /out/etopo_5min.nc'
    rm -rf "$tmp"
fi
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_host_scripts.py -v`
Expected: PASS

- [ ] **Step 5: Run the fetch and verify readability with no new dependency**

Run:
```sh
sh scripts/fetch_corpus.sh
.venv/bin/python - <<'PY'
import csv, gzip, json, pathlib
root = pathlib.Path("volumes/corpus")
rows = list(csv.reader((root / "place/cities500.txt").open(encoding="utf-8"), delimiter="\t"))
print("cities:", len(rows), rows[0][1], rows[0][4], rows[0][5])
print("coast features:", len(json.loads((root / "place/ne_10m_coastline.geojson").read_text())["features"]))
print("ngc rows:", sum(1 for _ in (root / "sky/NGC.csv").open(encoding="utf-8")))
with gzip.open(root / "life/yeast.fa.gz", "rt") as fh:
    print("fasta head:", fh.readline().strip())
relief = root / "place/etopo_5min.nc"
print("relief MB:", round(relief.stat().st_size / 1e6, 1))
scores = sorted((root / "notation").rglob("*.krn"))
print("kern scores:", len(scores))
print("kern head:", scores[0].read_text(encoding="utf-8", errors="replace").splitlines()[0])
PY
du -sh volumes/corpus/notation
find volumes/corpus/place volumes/corpus/sky volumes/corpus/life volumes/corpus/notation \
     \( -iname 'readme*' -o -iname 'LICENSE*' -o -name '*.pdf' -o -name '*.mid' -o -name '*.midi' \)
```
Expected: a city count above 200,000 with a name and coordinates; a coastline feature count above
4,000; an NGC row count near 14,000; a FASTA head line starting `>`; **1,981 kern scores totalling
about 25 MB**; a relief file of about **10 MB**; the first kern line beginning with `**kern` or
`!!!`. The `find` returns nothing — if it
lists a `.mid` or a `.pdf`, the extraction filter failed and the notation-not-sound boundary that
spec §4.5 records as the operator's ruling is broken.

- [ ] **Step 6: Commit**

```bash
git add scripts/fetch_corpus.sh tests/test_host_scripts.py
git commit -m "Add gazetteer, coastline, deep-sky catalog, genome, and kern notation to the corpus"
```

---

### Task 3: Trim the chess tablebases

**Files:**
- Modify: `scripts/fetch_corpus.sh` (the existing `== chess` section)
- Test: `tests/test_host_scripts.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a `/corpus/chess/syzygy` tree at the operator-ruled size.

**Ruled, not blocked.** Measured composition: 3-piece is 1 MB, 4-piece is 5 MB, 5-piece is 935 MB of
the 939 MB tree. A "keep 3–4 pieces" cutoff would leave 6 MB — that deletes the oracle rather than
trimming it. The operator ruled on 2026-08-17: **keep everything ≤4 pieces plus the pawnless 5-piece
tables**, measured at 346 MB total. Implement exactly that; the step below does.

Do **not** implement a further piece-type cut. The spec records a tie-break preference (drop bishops
or rooks before knights) for a future budget squeeze, and §4.5 shows it is currently moot — acting on
it now would take the tree to ~126 MB and delete studied material for no benefit.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_host_scripts.py`:

```python
def test_fetch_corpus_bounds_the_tablebase_download() -> None:
    script = _read("scripts/fetch_corpus.sh")

    # The tablebases dominated corpus bytes; the mirror must be pruned rather
    # than kept whole. 5-piece is 935 of the 939 MB, so the prune has to reach
    # into 5-piece selectively instead of cutting at the piece count.
    assert "SYZYGY_KEEP_PAWNLESS_5" in script
    assert "rm -f" in script
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_host_scripts.py::test_fetch_corpus_bounds_the_tablebase_download -v`
Expected: FAIL with `AssertionError` on `assert "SYZYGY_MAX_PIECES" in script`

- [ ] **Step 3: Replace the chess section in `scripts/fetch_corpus.sh`**

Replace the existing `echo "== chess: ..."` block through its `find ... -delete` line with:

```sh
echo "== chess: Syzygy tablebases, pruned after mirroring"
# Tablebase files are named by their piece letters, so the piece count is the
# stem length minus the separating 'v' (KQvK is 4 pieces). Everything up to 4
# pieces is kept; among the 5-piece tables only the pawnless ones are, which is
# the classically studied material and drops the pawn-structure tail. The
# oracle earns its place; its former byte dominance did not.
SYZYGY_KEEP_PAWNLESS_5=${SYZYGY_KEEP_PAWNLESS_5:-1}
mkdir -p "$OUT/chess/syzygy"
wget -q -e robots=off -c -r -np -nd -A '*.rtbw,*.rtbz' -P "$OUT/chess/syzygy" \
    https://tablebase.lichess.ovh/tables/standard/3-4-5-wdl/ \
    https://tablebase.lichess.ovh/tables/standard/3-4-5-dtz/
find "$OUT/chess/syzygy" -name 'robots.txt*' -delete 2>/dev/null || true
for f in "$OUT/chess/syzygy"/*.rtb*; do
    [ -e "$f" ] || continue
    stem=$(basename "$f"); stem=${stem%.*}
    pieces=$(printf '%s' "$stem" | tr -d v | wc -c)
    [ "$pieces" -le 4 ] && continue
    case "$stem" in
        *P*) rm -f "$f" ;;
        *) [ "$SYZYGY_KEEP_PAWNLESS_5" = "1" ] || rm -f "$f" ;;
    esac
done
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_host_scripts.py -v`
Expected: PASS

- [ ] **Step 5: Apply the trim to the existing corpus and confirm the budget**

Run:
```sh
sh scripts/fetch_corpus.sh
du -sh volumes/corpus/* | sort -h
du -sh volumes/corpus
```
Expected: `chess` is roughly 346 MB, no longer the largest entry, and total corpus is between 1.0 GB
and 1.2 GB. If `chess` still dominates, the ruling selected a larger subset than recommended — record
the actual figure and stop for the operator rather than trimming further.

- [ ] **Step 6: Commit**

```bash
git add scripts/fetch_corpus.sh tests/test_host_scripts.py
git commit -m "Bound the tablebase fetch by piece count"
```

---

### Task 4: Verify the corpus in the running container

**Files:**
- Modify: `scripts/verify_container.sh` (the existing `/vendor and /corpus are read-only` section)

**Interfaces:**
- Consumes: the corpus trees produced by Tasks 1–3.
- Produces: nothing consumed later.

- [ ] **Step 1: Add the presence check**

In `scripts/verify_container.sh`, immediately after the existing
`docker compose exec -T agent test -d /vendor/registry` line, insert:

```sh
for tree in history/sunspots history/climate history/quakes place sky life notation; do
  if ! docker compose exec -T agent test -d "/corpus/$tree"; then
    echo "FAIL: /corpus/$tree is missing"; exit 1
  fi
done
# Bare data only: provider documentation must never reach an agent surface.
if docker compose exec -T agent sh -c \
   'find /corpus -maxdepth 3 \( -iname "readme*" -o -iname "LICENSE*" \) | grep -q .'; then
  echo "FAIL: provider documentation is present under /corpus"; exit 1
fi
```

- [ ] **Step 2: Run the script test**

Run: `.venv/bin/python -m pytest tests/test_verify_script.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add scripts/verify_container.sh
git commit -m "Check the new corpus trees in the container verification script"
```

---

### Task 5: Add duckdb, skyfield, scipy, and netCDF4, and measure the image

**Files:**
- Modify: `requirements-agent.txt`
- Modify: `tests/test_agent_dependencies.py`
- Modify: `tests/test_build_garden.py`
- Modify: `scripts/verify_container.sh`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `duckdb`, `skyfield`, `scipy`, and `netCDF4` importable in the agent container;
  `garden_export/runtime.md` listing all four (generated — never hand-edited).

**Measure the baseline before touching anything.** The 250 MiB rule compares against the pre-change
image built on the same host, so the baseline must be taken first.

- [ ] **Step 1: Record the pre-change image size**

Run:
```sh
docker image inspect aurora-harness --format '{{.Size}}' | awk '{printf "baseline: %.1f MiB\n", $1/1048576}'
```
Write the number down; Step 7 compares against it.

- [ ] **Step 2: Write the failing test**

`tests/test_agent_dependencies.py` holds `EXPECTED_PACKAGES`, an ordered list that must match
`requirements-agent.txt` line for line. Append two entries to the end of that list, in the same order
they will be appended to the manifest in Step 4:

```python
    "pypdf",
    "duckdb",
    "skyfield",
    "scipy",
    "netCDF4",
]
```

Add to `tests/test_build_garden.py`, inside the existing runtime-inventory assertion function
(the one containing `assert "/corpus" in runtime`):

```python
    assert "- duckdb" in runtime
    assert "- skyfield" in runtime
    assert "- scipy" in runtime
    assert "- netCDF4" in runtime
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_agent_dependencies.py tests/test_build_garden.py -v`
Expected: FAIL — the manifest does not yet contain the two names, so the `EXPECTED_PACKAGES`
comparison and the two `runtime` assertions both fail.

- [ ] **Step 4: Add the packages to `requirements-agent.txt`**

Append two lines to the end of the file:

```
duckdb
skyfield
scipy
netCDF4
```

- [ ] **Step 5: Regenerate the garden and run the tests**

Run:
```sh
.venv/bin/python scripts/build_garden.py
.venv/bin/python -m pytest tests/test_agent_dependencies.py tests/test_build_garden.py -v
grep -n 'duckdb\|skyfield\|scipy\|netCDF4' garden_export/runtime.md
```
Expected: tests PASS, and both names appear in the generated inventory. `garden_export/` is generated
and gitignored — confirm it regenerated rather than editing it.

- [ ] **Step 6: Add the import check to `scripts/verify_container.sh`**

Change the existing line:

```sh
docker compose exec -T agent python -c "import filigree, z3, hy, tenacity, jplephem, model2vec"
```

to:

```sh
docker compose exec -T agent python -c "import filigree, z3, hy, tenacity, jplephem, model2vec, duckdb, skyfield, scipy, netCDF4"
```

- [ ] **Step 7: Rebuild and measure — this is the gate**

Run:
```sh
sh scripts/prepare_host.sh
docker compose build agent
docker image inspect aurora-harness --format '{{.Size}}' | awk '{printf "after: %.1f MiB\n", $1/1048576}'
```
Expected: the difference from Step 1 is **under 250 MiB** (238 MiB measured in `python:3.13-slim`). If it is over, stop and report the figure
to the operator — do not proceed, and do not silently drop a package to fit. Record the measured
delta in the commit message.

- [ ] **Step 8: Run the full suite and lint**

Run:
```sh
.venv/bin/ruff format . && .venv/bin/ruff check .
.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py
```
Expected: clean, all pass.

- [ ] **Step 9: Commit**

```bash
git add requirements-agent.txt tests/test_agent_dependencies.py tests/test_build_garden.py scripts/verify_container.sh
git commit -m "Add duckdb, skyfield, scipy, and netCDF4 to the agent image (measured +NN MiB)"
```

Replace `NN` with the delta measured in Step 7.

---

### Task 6: The `nearby` diode command

**Files:**
- Modify: `diode.py`
- Test: `tests/test_diode.py`

**Interfaces:**
- Consumes: `_parse_coordinates(arg) -> tuple[float, float] | None`, `FEED_ITEM_CAP`,
  `handle_command(name, variables, fetch_history) -> tuple[str, list]`, all existing in `diode.py`.
- Produces: `NEARBY_URL_TEMPLATE`, `NEARBY_RADIUS_MAX`, `_map_gate(variables) -> bool`,
  `_nearby_lines(body) -> str`, and a `"nearby"` entry in `COMMANDS` gated on `enable_map`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_diode.py`:

```python
NEARBY_SAMPLE = json.dumps(
    {
        "elements": [
            {"type": "node", "lat": 51.5, "lon": -0.12, "tags": {"name": "Ferry Pier", "amenity": "ferry_terminal"}},
            {"type": "node", "lat": 51.51, "lon": -0.13, "tags": {"name": "Old Bell"}},
            {"type": "node", "lat": 51.52, "lon": -0.14, "tags": {"amenity": "bench"}},
        ]
    }
)


def test_nearby_lines_names_features_and_skips_unnamed():
    text = diode._nearby_lines(NEARBY_SAMPLE)

    assert "Ferry Pier — ferry_terminal — 51.5,-0.12" in text
    assert "Old Bell" in text
    assert "bench" not in text


def test_nearby_lines_handle_malformed_bodies():
    assert diode._nearby_lines("not json") == "could not parse response"
    assert diode._nearby_lines("{}") == "(no features found)"
    assert diode._nearby_lines('{"elements": []}') == "(no features found)"


def test_nearby_is_gated_and_validates_its_arguments(monkeypatch):
    fake, _ = _stub_fetch("https://", NEARBY_SAMPLE)
    monkeypatch.setattr(diode, "_fetch", fake)

    closed = {"fetch_budget": 5}
    text, _ = diode.handle_command("nearby 51.5,-0.12", closed, [])
    assert text == "command not available: nearby"

    variables = {"enable_map": True, "fetch_budget": 5}
    text, _ = diode.handle_command("nearby 91,0", variables, [])
    assert text.startswith("usage: nearby")

    text, _ = diode.handle_command("nearby 51.5,-0.12 99999", variables, [])
    assert text.startswith("usage: nearby")

    text, hist = diode.handle_command("nearby 51.5,-0.12", variables, [])
    assert "Ferry Pier" in text
    assert len(hist) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_diode.py -k nearby -v`
Expected: FAIL with `AttributeError: module 'diode' has no attribute '_nearby_lines'`

- [ ] **Step 3: Add the constants and gate**

In `diode.py`, after the `SOLARWIND_URL` line (near line 55), add:

```python
NEARBY_RADIUS_DEFAULT = 1000
NEARBY_RADIUS_MAX = 10000
# A fixed template: only numbers are interpolated, so no query language crosses
# the diode.
NEARBY_URL_TEMPLATE = (
    "https://overpass-api.de/api/interpreter?data="
    "%5Bout%3Ajson%5D%5Btimeout%3A25%5D%3B"
    "node%28around%3A{radius}%2C{lat}%2C{lon}%29%5Bname%5D%3B"
    "out%20{cap}%3B"
)
```

Next to the other gate helpers (near `_library_gate`, around line 334), add:

```python
def _map_gate(variables):
    """Whether the map commands are available."""
    return bool(variables.get("enable_map"))
```

- [ ] **Step 4: Register the command and its console variable**

In the `COMMANDS` dict, immediately after the `"commons"` entry, add:

```python
    "nearby": {
        "gate": _map_gate,
        "help": "nearby <lat,lon> [radius_m] -> return named features around coordinates",
    },
```

In the console variable help list (near line 521, after the `enable_library` line), add:

```python
    lines.append("  enable_map: true, makes the map commands available")
```

- [ ] **Step 5: Add the formatter**

After `_solarwind_lines` in `diode.py`, add:

```python
def _nearby_lines(body):
    """Return name, kind, and coordinate lines from an Overpass response."""
    try:
        data = json.loads(body)
    except ValueError:
        return "could not parse response"
    elements = data.get("elements") if isinstance(data, dict) else None
    if not isinstance(elements, list):
        return "(no features found)"
    lines = []
    for element in elements:
        tags = element.get("tags") if isinstance(element, dict) else None
        if not isinstance(tags, dict):
            continue
        name = tags.get("name")
        if not name:
            continue
        kind = tags.get("amenity") or tags.get("place") or tags.get("natural") or ""
        lines.append(f"{name} — {kind} — {element.get('lat')},{element.get('lon')}")
        if len(lines) >= FEED_ITEM_CAP:
            break
    if not lines:
        return "(no features found)"
    return "\n".join(lines)
```

- [ ] **Step 6: Add URL construction and response formatting to the dispatch**

In `handle_command`, add `"nearby"` to the family tuple that currently reads
`("fetchrss", "wikipedia", "weather", "arxiv", "quakes", "airquality", "tides", "solarwind", "gutensearch")`.

In the same `if`/`elif` chain that builds `url`, after the `elif name == "tides":` branch, add:

```python
        elif name == "nearby":
            parts = arg.split()
            coords = _parse_coordinates(parts[0]) if parts else None
            radius = NEARBY_RADIUS_DEFAULT
            if len(parts) > 1:
                try:
                    radius = int(parts[1])
                except ValueError:
                    radius = -1
            if coords is None or not 1 <= radius <= NEARBY_RADIUS_MAX or len(parts) > 2:
                return (
                    "usage: nearby <lat,lon> [radius_m] with lat from -90 to 90, "
                    f"lon from -180 to 180, and radius_m from 1 to {NEARBY_RADIUS_MAX}",
                    fetch_history,
                )
            url = NEARBY_URL_TEMPLATE.format(
                radius=radius, lat=coords[0], lon=coords[1], cap=FEED_ITEM_CAP
            )
```

In the response-formatting chain further down, insert immediately after the
`if name == "gutensearch":` branch and **before** the chain's final
`return _weather_lines(body), fetch_history` — that trailing line is the fallthrough for the whole
family, so a branch added after it would never run:

```python
        if name == "nearby":
            return _nearby_lines(body), fetch_history
```

This path applies no markdown conversion and no body rewriting — `extract_markdown` lives only in the
separate `fetchhttp`/`fetchlinks` branch — so the JSON Overpass response reaches `_nearby_lines`
unmodified, exactly as it does for `quakes` and `solarwind`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_diode.py -v`
Expected: PASS. If `test_write_help_lists_available_commands` or
`test_available_commands_reflects_variables` now fail, they are asserting on the command list —
update their expectations to include `nearby` under `enable_map`.

- [ ] **Step 8: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add diode.py tests/test_diode.py
git commit -m "Add a gated nearby command using a fixed Overpass query template"
```

---

### Task 7: Calendar horizon for deferred commands

**Files:**
- Modify: `diode.py`
- Test: `tests/test_diode.py`

**Interfaces:**
- Consumes: `parse_delay(arg) -> tuple[int, str] | None`, `ECHO_DELAY_MAX`, `DEFERRING_COMMANDS`,
  `deferred_command_refusal(command) -> str | None`, all existing in `diode.py`.
- Produces: `ECHO_DELAY_MAX` raised to one year, and `parse_delay` additionally accepting an absolute
  UTC date. Its return type is unchanged — `(seconds, rest)` — so no caller changes.

Credentialed commands stay non-deferrable: `deferred_command_refusal` is not touched.

**Known consequence, deliberately not addressed here.** `PENDING_MAX = 32` becomes the binding
constraint once the horizon reaches a year. At seven days, 32 slots were generous; at a year they are
the actual ceiling on the "schedule the re-measurement" mechanism that spec §7 calls the point of
this task — a lineage that queues one check per month exhausts the queue in under three years. This
is left as-is on purpose: the right depth depends on how the agent actually uses the horizon, and
raising it blind would be guessing. Record it as an observation for the surfaces pass
(`aurora-8b228e92e4`) rather than changing it now.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_diode.py`:

```python
def test_parse_delay_accepts_an_absolute_utc_date(monkeypatch):
    monkeypatch.setattr(diode.time, "time", lambda: 1_760_000_000.0)

    parsed = diode.parse_delay("2026-12-25 time")
    assert parsed is not None
    seconds, rest = parsed
    assert rest == "time"
    # 2026-12-25T00:00:00Z is 1_798_156_800 by epoch arithmetic.
    assert seconds == 1_798_156_800 - 1_760_000_000


def test_parse_delay_rejects_past_dates_and_beyond_the_horizon(monkeypatch):
    monkeypatch.setattr(diode.time, "time", lambda: 1_760_000_000.0)

    assert diode.parse_delay("2020-01-01 time") is None
    assert diode.parse_delay("2099-01-01 time") is None
    assert diode.parse_delay("not-a-date time") is None
    assert diode.parse_delay("2026-13-45 time") is None


def test_deferral_horizon_reaches_a_year():
    assert diode.ECHO_DELAY_MAX == 31_536_000
    assert diode.parse_delay(f"{31_536_000} time") is not None
    assert diode.parse_delay(f"{31_536_001} time") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_diode.py -k "parse_delay or horizon" -v`
Expected: FAIL — `assert diode.ECHO_DELAY_MAX == 31_536_000` fails against the current `604800`.

- [ ] **Step 3: Raise the horizon**

In `diode.py`, change line 42 from:

```python
ECHO_DELAY_MAX = 604800
```

to:

```python
ECHO_DELAY_MAX = 31536000
```

- [ ] **Step 4: Accept an absolute date in `parse_delay`**

Replace the body of `parse_delay` with:

```python
def parse_delay(arg):
    """Split a leading delay off an argument; None when there is not one.

    The delay is either a whole number of seconds or an absolute UTC date as
    YYYY-MM-DD, which is converted to the seconds remaining until it.
    """
    parts = arg.split(None, 1)
    if len(parts) != 2:
        return None
    rest = parts[1].strip()
    if not rest:
        return None
    token = parts[0]
    try:
        seconds = int(token)
    except ValueError:
        try:
            when = datetime.datetime.strptime(token, "%Y-%m-%d").replace(
                tzinfo=datetime.timezone.utc
            )
        except ValueError:
            return None
        seconds = int(when.timestamp() - time.time())
    if not 0 <= seconds <= ECHO_DELAY_MAX:
        return None
    return seconds, rest
```

- [ ] **Step 5: Update the usage string**

In `handle_command`, change the deferral usage line from:

```python
        usage = f"usage: {name} <seconds> {tail} with seconds from 0 to {ECHO_DELAY_MAX}"
```

to:

```python
        usage = (
            f"usage: {name} <seconds|YYYY-MM-DD> {tail} "
            f"with seconds from 0 to {ECHO_DELAY_MAX}"
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_diode.py -v`
Expected: PASS. Existing deferral tests that assert on the old usage string or the 7-day bound will
need their expectations updated to the new values — update them, do not weaken the assertions.

- [ ] **Step 7: Lint, run the full suite, and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py
git add diode.py tests/test_diode.py
git commit -m "Extend deferred command scheduling to absolute dates within a year"
```

---

### Task 8: Rebuild, verify, and close the tickets

**Files:** none modified.

- [ ] **Step 1: Full rebuild and container verification**

Run:
```sh
sh scripts/prepare_host.sh
docker compose build
docker compose up -d
sh scripts/verify_container.sh
```
Expected: the verify script completes with no `FAIL:` line.

- [ ] **Step 2: Confirm the agent's view of the new surfaces**

Run:
```sh
docker compose exec -T agent sh -c 'ls /corpus; du -sh /corpus'
docker compose exec -T agent python -c "
import duckdb
print(duckdb.sql(\"select count(*) from read_csv_auto('/corpus/history/quakes/quakes.csv')\").fetchone())
print(duckdb.sql(\"select count(*) from read_csv_auto('/corpus/history/climate/2024.csv.gz')\").fetchone())
"
```
Expected: the new trees are listed; both counts return without error. The second confirms the design
premise that a gigabyte of corpus is queryable without loading it into memory.

- [ ] **Step 3: Close the tickets**

```bash
filigree close aurora-31456f576e --reason "corpus rebalance executed: history, place, sky, life added; tablebases bounded by piece count"
filigree close aurora-b6a3af1db0 --reason "deferral horizon extended to absolute dates within a year"
```

`aurora-8b228e92e4` (surfaces pass 2) stays open — this wave is its input, not its completion. Add an
observation recording what shipped so the next surfaces pass has the baseline.

---

## Self-Review

**Spec coverage.** §4.1 → Task 1. §4.2, §4.3, §4.4, §4.5 → Task 2. §4.6 → Task 3. §4.7 → Task 5.
§4.8 → Tasks 6 and 7. §2 (`/books`) → Operator Prerequisites; no code task, because the ruling was "both
stay" and the operator is sourcing additions directly. §5 non-additions → no tasks by design; the
Task 2 test asserts the `.nc`/`.tif`/`.shp` exclusion so a future contributor cannot add an
unreadable format without the test failing. §6 rulings 1 and 2 gate Tasks 1 and 3 and are listed as
prerequisites; rulings 3, 5, and 6 produce no task in this wave by design.

**Placeholder scan.** No TBDs. Every code step carries the actual content. Two steps deliberately
stop rather than guess: Task 5 Step 7 halts if the image measurement exceeds 250 MiB, and Task 3
Step 5 halts if the trim misses its budget — both are operator decisions, not implementer choices.

**Type consistency.** `_nearby_lines(body) -> str` matches `_quake_lines`/`_solarwind_lines`.
`_map_gate(variables) -> bool` matches `_instruments_gate`/`_library_gate`. `parse_delay` keeps its
`(seconds, rest)` return, so `handle_command` needs no change beyond the usage string.
`NEARBY_RADIUS_MAX` is used in both the validation branch and the usage message.
