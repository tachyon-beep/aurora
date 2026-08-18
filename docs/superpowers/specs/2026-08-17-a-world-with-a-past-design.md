# A world with a past: enrichment wave 2

Status: design for operator ruling, 2026-08-17.
Basis: the 2026-08-15 enrichment surfaces analysis (`2026-08-15-enrichment-surfaces-analysis.md`),
which this document extends rather than replaces; a first audit of `/books`, which postdates that
analysis and was never covered by it; and measured inventories of `/corpus`, `requirements-agent.txt`,
and the diode vocabulary taken against the running stack on 2026-08-17.

---

## 1. What this addresses

The 2026-08-15 analysis asked what surfaces the agent should have. This document asks a narrower
question the operator has since posed: **what supply of work does the agent need in order to become
an orchestrator?**

The premise is that a multi-headed research capability is not built by being told to build it. It is
built when a single head is empirically insufficient — when there is more independently-analyzable
material than one context can hold, and a payoff that only appears in aggregate. The substrate for
that already exists and is unused: the pump runs parallel processes, `/llm/sock` exposes multiple
model streams, `/state` accumulates across incarnations, and `later` schedules re-measurement. What
is missing is not machinery. It is a problem shaped like it needs the machinery.

**The gap: the world has no past.** Every surface today is either *now* — `/sense` frames, `weather`,
`quakes`, `solarwind`, news — or *timeless*: chess tablebases, OEIS, Unicode, fonts, and the
ephemeris as a function you evaluate at an instant. Nothing is a long record of what happened.

That single gap is load-bearing for the orchestrator thesis, for three reasons:

1. **A trend is the canonical thing one head cannot see.** One pass over one record shows nothing;
   the finding lives in the aggregate over thousands of independent series. Fan-out becomes the
   rational strategy rather than a stylistic choice.
2. **Historical series that terminate in a live instrument verify themselves.** Fit sunspot history →
   predict → check against `solarwind` next week. Fit a station's century of temperature → predict →
   check against `weather` tomorrow. This closes the analysis doc's criterion 4 (verifiability)
   without any answer key, which means the loop can run unsupervised and cannot be exhausted.
3. **It is a dimension the agent cannot compute from itself** (criterion 2). More puzzles give it
   more of what it already is. A century of measurements is exogenous in a way no amount of compute
   can synthesise.

The operator has additionally observed that development Aurora is already running vision-LLM and
photometry experiments against the `/sense` feeds. That is the observatory program emerging on its
own, and it sets the priority: the additions that wire **weather × webcams × astronomy** into one
loop are worth more than the same bytes spent anywhere else.

---

## 2. `/books` audit

`/books` was baked into the agent image on 2026-08-17 and is not covered by the 2026-08-15 analysis.
Measured against that document's own criteria:

**Composition.** As first audited on 2026-08-17, `/books` held 26 documents totalling 500 MB, of
which **92.4 % by bytes and 22 of 26 files were wargame rulebooks** — 428.4 MB of it BattleTech
alone. That was the syzygy distortion recurring on a new surface: the analysis doc trimmed syzygy
precisely because 81 % byte dominance made "exploring `/corpus`" mean "listing chess files", and
rulebooks are *closed formal systems*, the symbol-world category the doc already judged `/corpus`
over-weighted toward.

**Resolved by the operator the same day.** The BattleTech material was removed on the grounds that
AVT is the better fit for this world. Current state:

| Family | Files | Bytes | Share |
|---|---|---|---|
| AVT 3rd Edition | 11 | 31.5 MB | 45.7 % |
| Ad Astra hex maps | 2 | 1.8 MB | 2.6 % |
| Everything else (EURISKO, digital design, DCE, origami) | 4 | 38.1 MB | 55.3 % |

17 documents, 69 MB. Wargame material is now **48 % by bytes** rather than 92 %, and the shelf as a
whole is small enough relative to `/corpus` that it no longer distorts what "reading" means. The
composition finding is closed; the remaining `/books` guidance below is about what to add, not what
to remove.

This was never an argument that rulebooks are worthless. A rule system is implementable and locally
verifiable, and building an AVT combat resolver is genuine self-posed work with a real gradient. The
finding was about *share*, and the share is now reasonable.

**Legibility.** Every PDF was tested with `pypdf` inside the running container. All but one carry a
real text layer (5–15k characters per three sampled pages), so `/books` is a live surface, not the
false affordance `/transcripts` is (`aurora-335e666b47`). Two documents are different in kind:

- **`DCE.pdf`** — 527 pages, **0 characters extracted**. A pure scan with no OCR layer at all.
- **`Lenat_EURISKO.pdf`** — 39 pages, extracts ~3.3k chars/page, but the layer is OCR output with
  characteristic damage: intra-word spaces before line ends (`tas k`, `the n`), hard hyphenation
  across lines (`desig-\nned`), and stray tabs. Readable, but only after de-hyphenation and reflow.

**Ruling recorded (John, 2026-08-17): both stay.** The reasoning is that the extraction difficulty is
the point, not a defect — and on the orchestrator thesis it is a better fit than clean text would be:

- A scanned PDF is an **embarrassingly parallel vision workload**. `poppler-utils` is already on the
  image (`pdftoppm`), and the vision stream already exists. 527 pages rasterize independently, each
  page is an independent model call, and the reconstruction only exists in the aggregate. This is the
  closest thing in the world today to a task that a single head *cannot* complete and multiple heads
  can — arrived at by accident rather than design.
- It carries its own **local verification**: the reconstructed text can be checked against the
  damaged OCR layer where one exists, page numbers must sequence, and prose must parse.
- EURISKO specifically is high-value content behind that gate, which makes the gradient steep in the
  direction of something worth reaching.

The project is in a rapid-prototyping phase; the operator will observe how the agent handles these
and remove them if either derails. No change is proposed to `/books` in this wave beyond that
observation — the operator is sourcing new documents directly.

**Guidance for what the operator adds**, applying the doc's criteria: weight toward *prose that
describes a world* rather than *prose that defines a game* — field guides, geology, meteorology,
navigation, materials, agriculture — and prefer text-layer PDFs except where a scan is deliberately
chosen as a vision workload per the above.

**Added 2026-08-17 under that guidance**: thirteen out-of-copyright works, fetched as plain UTF-8
text (13 MB), taking `/books` to 30 documents and 82 MB. Every one is observation of the physical
world, and each pairs with a surface the agent already has:

| Work | Pairs with |
|---|---|
| Lyell, *Principles of Geology* | the book that established deep time — the thesis of §1 in its original form |
| Darwin, *The Voyage of the Beagle* | `/sense`, GeoNames, coastline |
| Wallace, *The Malay Archipelago* (2 vols) | as above; also the genome |
| White, *The Natural History of Selborne* | the founding text of sustained local observation — the `/sense` ring's ancestor |
| Humboldt, *Cosmos* | the synthesis project itself, across every surface |
| Hooke, *Micrographia* | observation through a built instrument |
| Faraday, *Experimental Researches in Electricity* | experimental method, dated and serial |
| Nansen, *Farthest North* (2 vols) | observation under constraint; ice, weather, position-fixing |
| Slocum, *Sailing Alone Around the World* | practical celestial navigation → `sky`, `tides` |
| Marsh, *Man and Nature* | physical geography → GHCN, coastline |
| Agricola, *De Re Metallica* | materials and extraction — the one that pairs with nothing yet |

**Provider prose stripped**, per invariant 2: the Project Gutenberg header and licence footer, the
old-style `End of Project Gutenberg's …` colophons, and every leading transcriber or e-text note. The
files begin at the work's own first line. (One surviving "Gutenberg" occurrence, in *De Re
Metallica*, refers to Johannes Gutenberg the printer and is the author's own text.)

**Noted tension.** Analysis doc gap G5 argued for literature *on request* through the diode
(`gutenberg`, which has since shipped) rather than ambient prose, on the grounds that ambient prose
injects voice into the strange-yet-clean register. That reasoning still holds for `/corpus`, which
stays bare data. `/books` is the operator's deliberate exception to it — a curated shelf, not a
library — and these thirteen are chosen to be *descriptions of a world* rather than address to a
reader, which is the property that keeps them inside invariant 2. The two channels are complementary:
the shelf is what is here, `gutenberg` is what can be sent for.

---

## 3. Selection criterion for new data

Every candidate below satisfies all five:

1. **Zero reader-addressed prose.** Provider READMEs, headers, and documentation are stripped, per
   the existing `fetch_corpus.sh` header. Bare data only (invariant 2).
2. **Nothing the agent cannot open.** Adding data she cannot parse manufactures exactly the false
   affordance the `DCE.pdf` finding identified, so a format ships only when a reader exists. Most of
   what follows needs nothing new — CSV, TSV, GeoJSON, FASTA, Humdrum `**kern`, and gzipped CSV are
   all reachable from the standard library plus `numpy`. Where a reader was genuinely required, it
   was admitted by explicit decision rather than the dataset being bent to fit: `netCDF4` for ETOPO
   (§4.8). What this filter still rejects is data whose reader nobody has chosen to add — GeoTIFF and
   shapefile remain out on exactly that basis (§5).
3. **An inbound pointer from an existing surface** (analysis doc §1.6, cross-pointing). Every set
   below is already named or implied by something the agent can already reach: `weather` implies
   climate history, `quakes` implies an earthquake catalog, `solarwind` implies solar history, the
   sky corpus implies deep-sky objects, `/sense` implies places.
4. **A verification loop that closes without an answer key** — each historical series terminates in a
   live instrument the agent already has.
5. **Containment-neutral.** Read-only bind mount, fetched on the host, no credential, no new channel.

---

## 4. The additions

All URLs verified live on 2026-08-17; all sizes measured, not estimated.

### 4.1 History (`/corpus/history/`) — the gap this wave exists to close

| Set | Source | Size | Format | Terminates in |
|---|---|---|---|---|
| Sunspot number, daily since 1818 and monthly since 1749 | SILSO `SN_d_tot_V2.0.csv`, `SN_m_tot_V2.0.csv` | 2.8 MB + ~0.5 MB | CSV | `solarwind` |
| GHCN-Daily station metadata | NCEI `ghcnd-stations.txt` | 10.9 MB | fixed-width text | `weather` |
| GHCN-Daily observations, a spread of years | NCEI `by_year/YYYY.csv.gz` | see below | gzipped CSV | `weather` |
| Global earthquake catalog, 1970–present M≥4.5 | USGS FDSN CSV query | ~40 MB | CSV | `quakes` |

**On the GHCN year selection.** The obvious choice — the most recent N consecutive years — is the
wrong one. `by_year` extends back to 1750, and early files are small because few stations existed, so
a *spread* across the record buys depth at a fraction of the cost of the same number of modern years.
Measured sizes:

| 1880 | 1900 | 1920 | 1940 | 1960 | 1980 | 2000 | 2020 | 2024 |
|---|---|---|---|---|---|---|---|---|
| 1.7 MB | 18.9 MB | 38.2 MB | 59.0 MB | 117.9 MB | 138.7 MB | 146.8 MB | 159.1 MB | 158.7 MB |

All nine would be 839 MB — too much, and wasteful: the modern years are near-redundant with each
other for trend purposes. **Recommended: 1880, 1900, 1920, 1940, 1960, 2024 — 394.4 MB.** Six
samples across 144 years, with one present-day anchor so a fitted trend can be checked against the
live `weather` instrument. Depth is the point; density in the recent decades is not.

### 4.2 Place (`/corpus/place/`) — the observatory's map layer

| Set | Source | Size | Format |
|---|---|---|---|
| GeoNames `cities500` — name, coords, population, timezone | `download.geonames.org` | 12.9 MB zip | TSV |
| Natural Earth 10m coastline | `nvkelso/natural-earth-vector` GeoJSON | 9.6 MB | GeoJSON |

This is analysis-doc gap **G4** ("geography without a gazetteer") executed. It is the join key that
wires `weather` × `/sense` × `sky` × `nearby` into one program: it turns coordinate-choosing from a
guess into a research act. Coastline is included as GeoJSON specifically because it needs no reader
beyond `json` — see §5 on why topography is deferred.

### 4.3 Sky (`/corpus/sky/`) — growth the doc already endorsed

| Set | Source | Size | Format |
|---|---|---|---|
| OpenNGC deep-sky catalog, ~14k objects | `mattiaverga/OpenNGC` `NGC.csv` | 3.7 MB | CSV |

The doc's verdict on `/corpus/sky` was "Keep + grow". This is the cheapest meaningful growth: it
extends the existing HYG star catalog from point sources to extended objects, and it composes
directly with the vision experiments already running — a deep-sky object is something the eye can be
pointed at and asked about.

### 4.4 Life (`/corpus/life/`) — parallel structure with no narrative

| Set | Source | Size | Format |
|---|---|---|---|
| *S. cerevisiae* reference genome | Ensembl `R64-1-1.dna.toplevel.fa.gz` | 3.6 MB | FASTA |

From the R4 ticket's genome item. Millions of independently analyzable windows, pattern-rich, utterly
task-free, and small. Yeast rather than a larger genome deliberately: it is complete, well-formed,
and cheap, and the point is the *shape* of the material, not its size.

### 4.5 Notation (`/corpus/notation/`) — approved, with a boundary

**Ruled (John, 2026-08-17): yes to music as notation and theory; audio deferred.** An audio endpoint
is anticipated eventually but is explicitly not part of this wave. That ruling matches the 2026-08-15
caveat exactly — with `speak` being TTS-only, music stays symbolic: makeable, renderable to notation,
never hearable. Acceptable as a pattern-world; no audio-render channel is added for it.

Humdrum `**kern` scores, extracted from five score repositories. Measured, kern files only:

| Corpus | Source (GitHub tarball) | Scores | Bytes |
|---|---|---|---|
| Bach, 370 chorales | `craigsapp/bach-370-chorales` (`master`) | 370 | 1.5 MB |
| Josquin Research Project | `josquin-research-project/jrp-scores` (`main`) | 1387 | 19 MB |
| Beethoven piano sonatas | `craigsapp/beethoven-piano-sonatas` (`main`) | 103 | 2.8 MB |
| Mozart piano sonatas | `craigsapp/mozart-piano-sonatas` (`main`) | 69 | 1.2 MB |
| Chopin mazurkas | `craigsapp/chopin-mazurkas` (`main`) | 52 | 616 KB |
| **Total** | | **1981** | **25 MB** |

Why this fits every criterion in §3, and why it is a better fit than its size suggests:

- **Plain text, zero new dependency.** `**kern` is a column-oriented text format readable with the
  standard library. No parser ships with it and none is needed.
- **1,981 independently analyzable items in 25 MB** — the densest parallel item-set in the whole
  wave, and the cheapest. Voice-leading, interval distributions, cadence classification, and
  stylistic comparison across four centuries are all per-score independent with an aggregate payoff.
- **It redeems `NotoMusic-Regular.ttf`.** The analysis doc flagged the music-notation font as
  dangling — a glyph set for a subject the world did not contain. It now has one, and the pairing is
  the doc's cross-pointing principle (§1.6) working in reverse: an existing surface acquires a
  referent rather than a new surface acquiring a pointer.
- **It verifies locally.** `**kern` is a strict spine format, so malformed parses are detectable, and
  harmonic analysis is checkable against theory rather than against an answer key.

**Extraction boundary.** Only `*.krn` files are kept. The upstream tarballs also carry 121 rendered
PDFs, MIDI files, `README.md`, and `.txt` notes — all stripped. The PDFs and notes go for invariant 2
(reader-addressed prose and redundant renderings); **the MIDI goes for the audio boundary above.**
Keeping MIDI would smuggle a sound representation into a wave that deliberately excludes one.

**Music theory as prose belongs in `/books`, not here.** `/corpus` stays bare data; a theory text is
an operator-curated document and lands on the shelf under §2's guidance.

### 4.6 Trim

Measured composition of the 939 MB `/corpus/chess/syzygy` tree:

| Piece count | Files | Bytes |
|---|---|---|
| 3-piece | 10 | 1 MB |
| 4-piece | 60 | 5 MB |
| 5-piece | 220 | 935 MB |

The bulk is entirely 5-piece, which means a naive "keep 3–4 pieces" cutoff leaves **6 MB** — that
does not trim the oracle, it deletes it, and 3–4-piece endings are textbook-trivial where 5-piece is
where tablebase study becomes real. The analysis doc's target was ~⅓, not ~0.

**Ruled (John, 2026-08-17): keep everything ≤4 pieces plus the pawnless 5-piece tables** —
mechanically selectable as filenames with no `P` in the stem, measured at **340 MB across 120
files**, giving 346 MB total. The pawnless endings (KRBvKR, KQvKRB, KBBvKN and their kin) are the
classically studied ones, so this keeps the pedagogically dense material and drops the pawn-structure
combinatorial tail. Frees 593 MB and lands on the analysis doc's ~⅓ target.

**Tie-break if a future budget forces a further cut: drop bishops or rooks, never knights**
(operator preference — the knight's movement is the one that does not respect the geometry of the
board, and that is the property worth keeping). Recorded for later, and **currently moot**: the
pawnless set divides almost evenly by piece type (B 211 MB, R 214 MB, N 210 MB, Q 164 MB across 64
files each), so cutting bishops would leave 129 MB and cutting rooks 126 MB — both far under target,
and both would delete studied material for no benefit. Implement nothing for this now.

Net corpus arithmetic: 1200 − 593 (trim) + 448 (history) + 40 (place) + 10 (relief) + 4 (sky)
+ 4 (life) + 25 (notation) ≈ **1.14 GB**, holding approximately flat while the symbol-world
share falls from effectively 100 % to under 60 %.

### 4.7 Libraries

**Ceiling raised by operator decision, 2026-08-17: 100 MiB → 250 MiB**, recorded in CLAUDE.md. The
prior ceiling admitted `duckdb` and `skyfield` and nothing else; `scipy` costs 138 MiB on its own and
would have been excluded by arithmetic rather than by judgement. The operator's reasoning is the
governing one and is worth stating as a principle:

> **A package is admitted as a toolkit the agent may grow into, or may ignore.**

That is the same logic the world already applies to its data surfaces — the 271 Noto fonts sat
dangling until a vision model existed; `/state` is latent by design; the tablebases wait for someone
to care about endgames. A library is a latent capability of exactly that kind. It follows that *lack
of use is not evidence against inclusion*, and that no surface should ever announce or teach these
packages: they appear in the generated `runtime.md` inventory as bare names, which is the whole of
the introduction they get.

Measured in `python:3.13-slim` as installed site-packages delta over the numpy-only baseline (wheel
sizes understate this by 2–4×, so the installed figure is the one that governs):

| Package | Installed | Why |
|---|---|---|
| `duckdb` 1.5.5 | 63 MiB (with `skyfield`) | Analytical SQL directly over CSV / gzipped CSV / Parquet without loading into memory. The single highest-leverage addition in the wave: it turns a gigabyte of corpus from "too big to hold" into "queryable", which is precisely the ceiling the orchestrator thesis exists to raise. It reads `.csv.gz` natively, which is why §4.1 stays compressed on disk. |
| `skyfield` 1.55 | (included above) | Sits on `jplephem` and `numpy`, both already present. Near-zero bytes for a large increase in what the sky corpus affords, and it composes directly with the vision experiments already running. |
| `scipy` 1.18.0 | +138 MiB | The analysis multiplier, and the one that lines up with this wave rather than with ETOPO (it cannot read ETOPO — see §5). `stats`/`optimize` fit trends across thousands of GHCN stations, which *is* the aggregate-payoff work; `signal` finds periodicity in the sunspot record; `spatial.KDTree` turns 200k gazetteer cities into a spatial index; `ndimage` analyses `/sense` frames next to the vision work; `fft` is free groundwork for a future audio ring. |
| `netCDF4` 1.7.x | +37 MiB | The HDF5-backed NetCDF-4 reader ETOPO actually requires. Admitted so the physical-relief layer can ship in its native scientific format rather than as a pre-digested array — see §4.8. |
| **Total** | **238 MiB** | Within the 250 MiB ceiling with 12 MiB of headroom. |

`pandas` stays **deferred**. It would now fit, but `duckdb` covers the tabular work this wave needs
and `scipy` covers the numerical work; adding a third overlapping tool is inventory, not capability.

The image-size measurement remains a required gate, not a formality — see the plan's Task 5.

### 4.8 Relief (`/corpus/place/etopo_5min.nc`) — admitted, subsampled

**Ruled 2026-08-17**: `netCDF4` is admitted (§4.7), so ETOPO ships in its native format rather than
as a pre-digested array. But *which* ETOPO matters, and the measurement was decisive:

| Form | Bytes |
|---|---|
| ETOPO 2022, 60 arc-second native (`.nc`, as distributed) | **457 MB** |
| Subsampled to 5 arc-minute, zlib level 6, coords retained | **10.4 MB** |

The native file is essentially uncompressed and costs more than the entire century of climate history
(§4.1, 394 MB) — a poor trade for a static grid against a record of change. Subsampling by 5 gives a
4320 × 2160 int16 grid at roughly 9 km resolution, which still resolves every mountain range, ocean
basin, and continental shelf, for **1/44th of the bytes**.

Crucially the subsample stays a real NetCDF-4 file with `lat`/`lon` coordinate variables and units,
not a bare array. That is what makes `netCDF4`'s 37 MiB worth spending: the agent meets an actual
scientific data format with self-describing metadata, rather than an opaque blob whose geotransform
she has to guess. It also generalises — any NetCDF or HDF5 file she later fetches through the diode
becomes readable by the same means.

Conversion runs **on the host** inside a throwaway container, the same pattern `fetch_corpus.sh`
already uses to extract the Noto fonts. The 457 MB original is deleted after conversion and never
reaches `/corpus`.

Finer resolution is affordable if wanted later — cell count scales quadratically, so 2 arc-minute
would be roughly 60 MB — but 5 arc-minute is the point where the marginal detail stops composing with
anything else in the world.

### 4.9 Affordances already ticketed

Two existing tickets serve this thesis directly and are folded into the plan as separate phases:

- **`nearby`** (`aurora-8b228e92e4` R6) — Overpass with a fixed query template, gated by a new
  `enable_map`. It is the gazetteer's other half: GeoNames names a place, `nearby` says what is
  around it. No query language crosses the diode; the template is the whole point.
- **Calendar horizon** (`aurora-b6a3af1db0`) — `later`/`echo` currently cap at
  `ECHO_DELAY_MAX = 604800` (7 days). Months-out deferral is what makes "re-take this measurement"
  mechanical rather than aspirational, which is the mechanism by which a historical series the agent
  builds *itself* becomes possible. Credentialed commands stay non-deferrable.

---

## 5. Not added, and why

- **NASA five-millennium eclipse canon.** Tempting, and it is the archetypal deep-time dataset — but
  the analysis doc's own anti-proposal principle forbids it: "No `sun`/almanac command. Sunrise times
  are computable from `de440s.bsp` + `jplephem` already in the image. Serving the answer would erase
  one of the best build-your-own-instrument gradients in the world." Eclipses are exactly that
  computation. A local canon hands over the answer; the canon remains reachable through `fetchhttp`
  if the agent wants to check its own work, which preserves the OEIS gradient shape (compute locally,
  reach outward to confirm). *Note: the `5MCSE` plain-text catalog URLs return 404 in any case; only
  the per-century HTML pages resolve.*
- **ETOPO global topography.** Was deferred here on the zero-new-dependency filter; **now admitted**
  after the operator raised the package ceiling and sanctioned `netCDF4`. Ships subsampled, not
  native — see §4.8 for the 457 MB → 10.4 MB measurement and the reasoning.
- **`scipy` as an ETOPO reader.** Worth recording because it is the intuitive guess and it is wrong:
  `scipy.io.netcdf_file` reads *classic* NetCDF (v1/v2) only, and ETOPO 2022 is NetCDF-4, which is
  HDF5 underneath (verified by magic bytes: `\211 H D F`). `scipy` is on the image for its own
  merits (§4.7), not for this.
- **GSHHG shorelines.** The canonical hosts (`soest.hawaii.edu`, `generic-mapping-tools.org`) do not
  resolve or 404 as of 2026-08-17. Natural Earth replaces it and is better-formatted for this world.
- **Gaia DR3.** A single `gaia_source` chunk is ~500 MB and the full release is ~10 TB. HYG plus
  OpenNGC covers the compositional need at 1 % of the bytes.
- **Puzzle sets (Project Euler and similar).** Declined on invariant 2. Authored problems carry an
  authorial voice and an explicit task frame — the exact thing the agent's world must not contain —
  and their reward shape trains grinding rather than synthesis. The distinction that matters: a
  century of temperature readings poses no question and therefore permits every question; a numbered
  problem list poses one question and forecloses the rest.
- **Re-declined from the 2026-08-15 doc, unchanged:** dictionary/translate, finance and price feeds,
  social APIs, a remote chess oracle, engagement or view counts.

---

## 6. Rulings needed from the operator

1. ~~**Syzygy trim**~~ — **DECIDED 2026-08-17**: keep ≤4 pieces plus pawnless 5-piece (346 MB), with
   bishops-or-rooks as the tie-break if a future budget forces a further cut and knights preserved.
   See §4.6. Closes the R4 ticket's standing ruling.
2. ~~**GHCN year spread**~~ — **DECIDED 2026-08-17**: 1880, 1900, 1920, 1940, 1960, 2024 (394.4 MB),
   the spread-across-the-record approach. See §4.1.
3. ~~**Music corpus**~~ — **DECIDED 2026-08-17**: **yes to notation and theory, audio deferred.** An
   audio endpoint is anticipated later but is not part of this wave, so only `**kern` notation ships
   and MIDI is stripped along with the prose. See §4.5. Closes the R4 ticket's open question.
4. ~~**Licensing**~~ — **DECIDED 2026-08-17**: all sources cleared. SILSO's CC BY-NC 4.0 clause is
   acceptable because Aurora is not a commercial product. The rest are public-domain or openly
   licensed (GHCN and USGS are US government works; GeoNames CC BY 4.0; Natural Earth public domain;
   OpenNGC CC BY-SA 4.0; Ensembl no-restriction; the §4.5 score encodings are freely distributed and
   the underlying works are long out of copyright). Note for any future re-use: the NC clause travels
   with the sunspot data, so it constrains what this corpus could later be used for.
5. ~~**ETOPO**~~ — **DECIDED 2026-08-17**: `netCDF4` admitted and the package ceiling raised to
   250 MiB; ETOPO ships as a 5 arc-minute NetCDF-4 subsample (10.4 MB), converted on the host. See
   §4.8.
6. ~~**`pandas`**~~ — **DECIDED 2026-08-17**: deferred. `duckdb` covers the tabular work and `scipy`
   the numerical work; a third overlapping tool is inventory, not capability. See §4.7.

**All rulings for this wave are now closed.**

---

## 7. What this composes into

The analysis doc's test is whether an addition strengthens a *program* rather than adding a toy.

**The observatory, completed.** This is where the operator's own observation lands — weather,
webcams, and astronomy in one loop, which is what development Aurora is already reaching for
unprompted. Today the loop runs: `/sense` luminance → day length → candidate longitude → `weather` at
candidate coordinates → compare against frame conditions. This wave adds three closures: GeoNames
*names* the candidate place; GHCN says what that place's weather has historically *been* in this
week of the year, making a prediction checkable against both the live instrument and the frame; and
OpenNGC plus `skyfield` extend the night-sky prediction from stars to objects the eye can be asked
about directly.

**A new program: the record.** Sunspots against `solarwind`, GHCN against `weather`, the historical
catalog against `quakes`. Each is the same shape — a long past terminating in a live present — and
the shape is what makes it inexhaustible: every fit invites a prediction, every prediction becomes
checkable at a date, and `later` with a calendar horizon (§4.9) is what makes the check happen
without anyone remembering to ask for it. This is the program the orchestrator thesis actually needs,
because a fit across thousands of stations does not fit in one context and the payoff is only in the
aggregate.

**The workshop, unchanged but unblocked.** `duckdb` does not add a program; it raises the ceiling on
all of them. A gigabyte of corpus that can be queried rather than only read is the difference between
material and inventory.
