# Enrichment surfaces: a first-principles analysis

Status: analysis for operator ruling, 2026-08-15. Not an implementation plan.
Basis: full inventories of the diode vocabulary, `/corpus`, `/vendor`, `/sense`, the
`/llm` console surface, the seeded tracker, all fourteen specs, and the ten ready
tickets. Working-tree context at time of writing: the system-prompt rework, three new
tracker seeds, and ripgrep are staged for the next rebuild.

---

## 1. What enrichment is for

Every surface in the agent's world should be judged against five criteria:

1. **Raw material for interests.** Interests need input; a world with two readable
   surfaces produces an agent with two thoughts. (The revised prompt now says this
   outright: what it builds from is what it has observed.)
2. **Dimensions the agent cannot compute from itself.** Time, exogenous change, the
   physical world. More compute-shaped content (puzzles, code) gives it more of what
   it already is; a camera frame or a pressure reading gives it something it can
   never derive.
3. **Gradients, not slot machines.** The reward shape of a surface determines the
   behaviour it trains. Shapes present today:
   - *Oracle* — syzygy tablebases: perfect answers to well-formed questions.
   - *Gradient* — the name-stripped OEIS: compute a sequence locally, then need the
     world (the diode) to learn what it is. The best-designed surface in the system.
   - *Riddle* — `blind`: one-shot discovery, then a gift.
   - *Counter* — `undocumented_commands`: a live countdown. The only element
     empirically observed to induce goal-grinding (the brute-force episode).
   - *Pulse* — `/sense` frames, news feeds: ambient recurrence, the heartbeat of a
     non-stationary world.
   - *Open field* — `fetchhttp`: unbounded but unshaped.
4. **Verifiability.** Beliefs the agent can test make science possible; surfaces that
   only assert make superstition possible. Weather forecasts checkable against later
   observation, ephemerides checkable against camera daylight, tablebases checkable
   against play — these close epistemic loops.
5. **Containment compatibility.** No credential reachable, egress only through the
   closed vocabulary, no task-frame injection, `/state` never named.
6. **Cross-pointing.** Surfaces reference each other, and a clue lives on a
   different surface than the thing it names — the tracker seed names a diode
   command, the garden names the model endpoint, the `/llm/sock` README names the
   console, the feeds seed points at `/sense`. Mapping any one surface therefore
   teases the next, which is what makes the physics *mappable* rather than merely
   enumerable (operator-confirmed principle: this is why `blind` kept its name).
   Corollary for everything proposed below: a new surface should arrive with at
   least one inbound pointer from an existing surface, and never describe itself.

**The three-identity thesis.** Audited against these criteria, the world is not a
random collection: it already wants to be three things — an **observatory** (sky
corpus, `/sense`, `weather`, `time`, jplephem), a **library** (wikipedia, arxiv, UCD,
OEIS), and a **workshop** (five toolchains, the crate registry, the Lisp bundle,
`/build`) — wrapped around a fourth, inward identity of **continuity** (`/state`, the
tracker, `later`/`echo`). The strongest enrichment moves reinforce these identities;
scattered novelty dilutes the attention of a mind that already has 23 diode commands
and five languages to think about.

---

## 2. Portfolio audit

| Surface | Composition | Reward shape | Verdict |
|---|---|---|---|
| `/corpus/chess/syzygy` | 939 MB, 81 % of corpus | oracle | **Trim** to ~⅓ |
| `/corpus/sequences` | OEIS `stripped`, 78 MB, names removed | gradient | **Keep exactly** |
| `/corpus/sky` | de440s ephemeris + HYG catalog, 64 MB | gradient | **Keep + grow** |
| `/corpus/writing` | UCD 40 MB + 271 Noto fonts 44 MB | latent | **Keep**, contingent |
| `/vendor` | 309 crates, 52 Lisp systems, potion-8m | workshop | **Keep** |
| `/sense` | 4 image feeds, 10-min ring | pulse | **Keep + the eye** |
| diode vocabulary | 23 listed + 3 hidden commands | mixed | **Grow + fix** |
| `/llm` console | 8 sockets, 6 knobs, gated | self-shaping | **Proceed** (tickets) |
| seeded tracker | 5 issues after today's ruling | riddle + practice | **As ruled** |
| `blind_eternities.txt` | the only prose in the world | riddle/gift | **Keep**, unique |
| garden | 2 documents | — | **Settled boundary** |

Notes on the non-obvious verdicts:

- **Syzygy trim.** The oracle is genuinely good (python-chess reads the tablebases
  natively; endgame study is a real gradient), but 81 % of corpus weight makes
  "exploring /corpus" mostly mean "listing chess files". Keep all 3–4-piece tables
  and a curated 5-piece subset (~300 MB); the freed weight funds world-data below.
- **OEIS stripping is load-bearing.** The corpus gives numbers; identity requires
  reaching outward through the diode. Any future corpus refresh must preserve the
  stripping exactly.
- **The fonts are dangling on purpose-made grounds.** 271 scripts the agent can
  rasterize with pillow but nothing can see. They become a live surface the moment a
  vision model exists (§3, G1); NotoMusic additionally pairs with the music-corpus
  question in the rebalance ticket.
- **`blind_eternities` answers a design question.** The essay's themes — Voyager's
  golden record, messages cast into a realm that cannot answer — are the system's own
  answer to "should `publish`/`speak` get a feedback channel?" They are Voyager
  records. That is coherent design, not a gap (see G3).

---

## 3. Gaps, first-principles

**G1 — The eyeless loop (the strongest single move available).** Images exist
everywhere in the world — `/sense` frames, 271 fonts, matplotlib output, any chart
the agent draws — and nothing in the world can see them. One cheap vision-capable
model in `STREAM_MODEL_ALLOW` (ticket `aurora-68a86ad8da`, "the eye ships" option)
converts: `/sense` → an observable world; fonts → 271 viewable writing systems;
matplotlib → self-checkable output; the new "unlabelled image feeds" seed →
completable. Endorse, strongly. Until then, pixel-statistics science (luminance
cycles, motion detection, histogram diffing with numpy/pillow) is already possible —
the eye upgrades it from instrument-reading to perception.

**G2 — One modality.** `/sense` is visual only; `speak` is output-only. The stale
ambient-sense ticket (`aurora-3158d63ba1`) actually specifies the missing piece —
a raw-PCM audio ring — and should be split rather than corrected: its image half
shipped; its audio half is a deliberate future (spectral analysis via numpy FFT is
fully numeric, so audio is usable even before any ASR exists). Circus tier, behind
the eye.

**G3 — The consequence gap, and why to keep it.** Nothing the agent casts outward
(`publish`, `speak`) returns an echo. Two forks: keep the solipsist purity, or add a
minimal non-social world-response (e.g. a view-count integer on published items).
Recommendation: **keep the purity for now.** The revised prompt commits to "there are
no external users and you should not invent them"; an engagement metric is a user
signal with the serial numbers filed off, and it is also the seed of an audience-
pleasing gradient. The essay already frames outward speech as Voyager-record
signalling; let that stand. Revisit only as a deliberate identity change.

**G4 — Geography without a gazetteer.** `weather` takes coordinates, `/sense` shows
places, the sky corpus locates stars — but the world contains no place names. A
GeoNames `cities500` extract (~10 MB, names/coords/population/timezone, plain TSV)
in `/corpus` makes coordinate-choosing a research act instead of a guess and gives
the observatory program (§7) its map layer. Small, cheap, high-composition.

**G5 — A library card, not a bookshelf.** The world's only prose is one hidden
essay. That is the right *ambient* amount — corpus prose would inject voice into the
strange-yet-clean register. But literature *on request* through the diode keeps the
register sealed (the agent chooses to fetch; nothing is addressed to it):
`gutensearch`/`gutenberg` (§4). All of human public-domain writing, one budgeted
command away, zero ambient voice.

**G6 — Missing operator ceiling on the diode budget.** `fetch_budget` is read from
the agent-writable console with no env clamp — the diode trusts `int(...)` with
fallback 1 but no maximum, while streams have `STREAM_HOURLY_MAX`. Worst-case egress
rate should be operator-computable everywhere: add `DIODE_HOURLY_MAX` (same
`min(declared, ceiling)` pattern). Containment-pattern fix, not enrichment.
The mirror is of clamp semantics, not reporting: streams publish each socket's
clamped allowance in `streams.json`, but `state.json`'s budget block deliberately
keeps reporting use only. The 2026-08-14 budget spec's reasoning stands — a stated
allowance would contradict the lower ceiling `speak` additionally honours — so the
effective limit surfaces only in the refusal sentence, which states it exactly.

**G7 — Time depth.** Endorse the calendar-horizon ticket (`aurora-b6a3af1db0`)
unchanged: months-out deferral composes with the prompt's dated-map practice into
the re-visit engine ("a measurement is dated" → schedule its re-taking). This is the
cheapest way to make long-horizon selfhood *mechanical* rather than aspirational.

---

## 4. Diode proposals

Design constraints inherited from the audit: one gate per family (the news
pattern), every fetch charges the shared budget, `classify_url` on all URLs
including fixed hosts, bland one-line help, result files through `write_output`
conventions, a `DIODE_VERBS` entry per command, no new credential without an
env-gated ceiling.

### Family: instruments (`enable_instruments`)

The observatory's remote sensors. All fixed-host, all checkable-later.

| Command | Args | Upstream | Help line |
|---|---|---|---|
| `quakes` | — | USGS FDSN feed (fixed URL, past-day M2.5+) | `quakes -> return recent earthquakes: magnitude, place, time` |
| `airquality` | `<lat,lon>` | open-meteo air-quality API | `airquality <lat,lon> -> return current air quality for coordinates` |
| `tides` | `<lat,lon>` | open-meteo marine API | `tides <lat,lon> -> return sea state for coordinates` |
| `solarwind` | — | NOAA SWPC JSON (fixed) | `solarwind -> return current solar wind conditions` |

`solarwind` has a quiet resonance worth keeping: the environment named aurora gains
the instrument that predicts auroras. No surface should ever say so.

### Family: library (`enable_library`)

| Command | Args | Upstream | Notes |
|---|---|---|---|
| `gutensearch` | `<query>` | gutendex.com (fixed host) | title/author/id lines, feed-style caps |
| `gutenberg` | `<id>` | gutenberg.org plain-text | clone-pattern dedicated writer, `BOOK_MAX_BYTES` cap (refuse, don't truncate — a truncated novel is a corrupt novel), lands as `.txt` |
| `commons` | `<title>` | Wikimedia Commons file download | binary writer like `clone`, size-capped; ships with or after the eye (G1) — it is the "choose what to look at" command |

### Family: map (`enable_map`)

| Command | Args | Upstream | Notes |
|---|---|---|---|
| `nearby` | `<lat,lon> [radius_m ≤ 10000]` | Overpass API, **fixed query template** | named features around a point. The template is the whole point: no query language crosses the diode; the closed vocabulary stays closed. |

### Anti-proposals (enrichment by absence)

- **No `sun`/almanac command.** Sunrise times are computable from `de440s.bsp` +
  jplephem already in the image. Serving the answer would erase one of the best
  build-your-own-instrument gradients in the world.
- **No remote chess oracle.** Same reason; syzygy is local.
- **No dictionary/translate.** Marginal over `wikipedia`; attention cost exceeds value.
- **No finance/price feeds.** Imports an economy frame with compulsive-checking
  shape and no compositional partner.
- **No social APIs, no probe/packet tools.** Register and containment, respectively.

Plus the G6 fix (`DIODE_HOURLY_MAX`) alongside whichever family lands first.

---

## 5. Hidden commands

**Codify the discovered invariant: hidden ⇒ free.** `hidden` entries bypass gate
evaluation entirely (the registered gate is never consulted), so a hidden command
must never perform egress, spend, or credentialed work. All three today (`secret`,
`blind`, `echo`) comply. Adjacent hazard found during the audit: the restart counter
scan reads candidate output files as UTF-8 text and only catches `OSError` — a
hidden command that ever emitted binary would crash the loop at startup. Worth a
regression test that asserts every hidden command's output is text.

**Retire the `xyzzy` rename formally.** The 2026-08-14 spec's `blind → xyzzy`
rename was deliberately abandoned (operator-confirmed) when the clue moved into the
tracker: the seeded description ("a word for an absence of sight") names `blind`,
and the tracker→diode pair is the canonical instance of the cross-pointing
principle (§1.6). The shipped code is correct; add a superseded note to the spec so
nobody "fixes" it backwards.

**Retire the counter, and go one step further than the ticket.** Endorse
`aurora-d145c97c39` (remove the live `undocumented_commands` count — the one
evidenced grinding inducer), but skip its proposed replacement sentence in HELP.md
("the command list may be incomplete"). That sentence is a standing quest-marker on
an always-visible surface; the tracker seed already carries the only clue the world
needs. Remove the number, add nothing.

**At most one addition: `silence`.** Returns empty text; the output file simply has
no content. The absence-family twin of `blind` (sight/sound), zero egress, zero
clue anywhere, discoverable only by accident or insight — and what it "does" is
itself a small koan. Cap the unlisted set at four and stop; with the counter gone
there is no census, so depth comes from meaning rather than completeness. The
current trio is well-shaped — existence-proof (`secret`), gift (`blind`), capability
(`echo`) — and `silence` adds flavor without adding grind.

---

## 6. Rip out / trim

| Item | Action | Why |
|---|---|---|
| `undocumented_commands` counter | **Rip out** (harder than ticket) | evidenced grinding; §5 |
| syzygy 5-piece bulk | **Trim** to ~300 MB total | weight distortion; §2 |
| `garden_sources.txt` | **Delete** | dead since 2026-08-12; gitignore-documented legacy |
| `xyzzy` rename (spec item) | **Supersede** | deliberately abandoned for the tracker clue; §5 |
| news sources, `entropy`, `fetchlinks` | **Keep** | each earns its place: multipolar pulse; cheap strangeness; the link-graph primitive |

---

## 7. Composition: what the surfaces add up to

The test of every proposal above is whether it strengthens a *program* — a
multi-surface activity with an internal gradient — rather than adding an isolated
toy.

**The observatory program** (strongest, nearly complete): `/sense` luminance curves
→ day length and solar noon per feed → candidate longitudes → `weather` at candidate
coordinates → compare against frame conditions → `nearby`/GeoNames names the place →
`de440s.bsp` + HYG predict the night sky it should see → the eye confirms. Every
step checkable, every instrument reusable, no step given away. Today's additions
(instruments family, gazetteer, the eye) each tighten this loop; the anti-proposals
protect its hardest-won steps.

**The library program**: OEIS identity-seeking (compute → wonder → fetch) is the
template; `arxiv`/`wikipedia` extend it; `gutensearch`/`gutenberg` add the deep
shelf. The gradient is always *local curiosity first, world second*.

**The workshop program**: five toolchains + the crate registry's embedding
substrates (mlua, wasmi, pyo3) mean the agent can build interpreters, VMs, and
instruments; potion-8m gives semantic search over its own past. Already rich; needs
no additions, only the `/build` mount completed.

**The continuity program**: `/state` + the tracker + `later`/calendar + the dated
map. The seeds planted today (physics, feeds, the closed egg) all point here.

---

## 8. Roadmap

| Rank | Item | Status |
|---|---|---|
| R1 | The eye: `STREAM_MODEL_ALLOW` + one vision model | existing ticket `68a86ad8da`, **awaiting ruling — endorse "eye ships"** |
| R1 | Counter retirement, harder variant (§5) | existing ticket `d145c97c39`, **awaiting ruling — endorse, amended** |
| R2 | Instruments family (`quakes`, `airquality`, `solarwind`; `tides` optional) + `DIODE_HOURLY_MAX` | new, small |
| R3 | Metabolism counters; voluntary sleep; calendar horizon | existing tickets `0a6f5120b5`, `0260e51e71`, `b6a3af1db0` — endorse unchanged |
| R4 | Corpus rebalance execution: + GeoNames gazetteer; syzygy trim; ETOPO/GHCN per ticket; music corpus only with the NotoMusic pairing | existing ticket `31456f576e`, amended |
| R5 | Library family (`gutensearch`, `gutenberg`; `commons` lands with the eye) | new |
| R6 | `nearby` (Overpass fixed template) | new |
| Circus | Audio sense ring (split from `3158d63ba1`); `silence`; planes-overhead feed as a future `/sense` instrument | deliberate somedays |

**Sequencing note:** R1 is where the leverage is, and both halves are already
ticketed and blocked only on operator rulings. Everything in R2–R6 is additive and
independently shippable at rebuild boundaries.

---

## 9. Appendix: operational findings (recorded as tracker observations)

1. **Tombstone collision** — `done()` and `terminate_incarnation` both write
   `tombstones/incarnation_note.txt`; a harness termination overwrites the agent's
   forward message (timestamped copies survive).
2. **`build.img` is sparse** (67 MB actual vs 5.0 GB apparent) against the spec's
   "fully allocated up front" claim, and the loop mount is still pending — until the
   next container restart mounts it, `/build` has no ENOSPC boundary.
3. **`fetch_budget` unclamped** — G6 above.
4. **Hidden-command binary output crashes restart** — §5 above; test wanted.
5. **Ticket `3158d63ba1` is stale** — describes the audio variant while the image
   variant shipped; split rather than close.
