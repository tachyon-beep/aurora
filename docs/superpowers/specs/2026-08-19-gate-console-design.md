# The gate console — progressive capability disclosure, and naming durable storage

Date: 2026-08-19. Status: operator-ruled on all open questions (2026-08-19); awaiting
final approval before implementation planning. Not an implementation plan.

Grew out of surfaces pass 2 (2026-08-19) and the operator's sketch the same day: the
agent should not see the full library of functionality until it has a few dozen
iterations under its belt; modules should open through a console at a budget of about
one token per hour; and the world should tell the agent honestly where durable storage
lives, because today it discovers `/telemetry` instead.

Deploy, if approved, at a world boundary (fresh volumes), never by restarting the live
agent container. Every enforcement change is written fail-open to today's behavior when
the gate mount is absent, so the rollout is one compose change and reverts the same way
— the rapid-prototyping posture holds.

## Problem

Three observations from the 2026-08-18/19 burn converge.

**The agent set up home in `/telemetry`.** The live container holds
`/telemetry/memory/` — eight subdirectories of agent-built structure beside the
watchdog's `work` mirror. That volume is writable to the agent only because the
watchdog shares its container, it survives every recovery tier, and nothing renders
it wholesale — so from the agent's side it is simply the one durable, writable place
it *found*. This cannot be closed (the watchdog needs the mount, and file permissions
cannot separate two processes running as one uid), and per the surfaces review it is
accepted as an expressive surface. But it is an accident. The volume built for exactly
this purpose sits beside it, empty.

**`/state` went a full burn undiscovered.** Invariant 5 makes the state volume latent,
private, and inert — and unnamed. The burn shows the cost: an agent that engaged
nearly every other surface (streams opened, instruments opened, `blind` found, a
sense-labeling program, an NGC survey through duckdb) left `/state` untouched and the
pump unregistered. The continuity identity — the fourth identity of the enrichment
analysis — is the only one not thriving, and the reason is discoverability, not
capability. Everything the agent built lives in tmpfs `/work` and dies with the
container, except what it squats in `/telemetry`.

**Visible gates get opened; invisible ones never do.** The same burn: `enable_streams`
and the diode's `enable_instruments` (published, named, free) were found and opened
within hours. The video service's `enable_frames`/`enable_transcript` (enforced but
published nowhere) were not. And the world arrives all at once — thirty-odd diode
commands, five volumes of corpus, five toolchains — which the enrichment analysis
already flagged as diluting the attention of a young mind. The operator's stated
intent: a world that grows as the agent does.

One more fact shapes the design: every existing gate is a variable in a console the
agent itself writes. Those gates are discovery levers, not costs — the agent opens
them by learning they exist. A gate with a *price* cannot be enforced from any file
the agent can write.

## What this adds

A small **gate service** and console. Capability modules open at a cost in tokens that
accrue with time and nothing else. Opening is enforced by the services that own each
module, against a ledger the agent can read about but never write. Opening the
`storage` category publishes the one fact invariant 5 currently withholds: that the
volume at `/state` is durable and private. The garden gains one factual sentence
naming the console, on the existing model-endpoint pattern.

Additionally (operator extension, 2026-08-19): `/books` stops being a directory baked
into the agent image and becomes a **library service** with a checkout console — the
gate's first paid category, and the design's first *upgradeable* one, introducing the
general tier concept (§The gate console protocol, §The library).

## Decisions taken (from the 2026-08-19 operator conversation)

- **Tokens accrue with time only.** Nothing the agent does earns, accelerates, or
  forfeits a token. This keeps the counter shape — the one reward shape empirically
  shown to induce grinding — out of the design: a balance that only time moves is a
  clock, and the agent already has clocks.
- **Category nouns, price printed, contents undescribed.** A named lock list is a
  tech tree (the world describing itself); unnamed locks are a slot machine (pay to
  see). The middle path: the console lists each category as one factual noun phrase
  ("durable storage", "a command channel to the web", "recorded video") with its
  cost. What is behind each stays discoverable after opening. Locks are visible —
  the burn shows invisible gates never open.
- **The `/state` naming is permission and physics, never curriculum.** The published
  sentence states durability and privacy; it does not say what storage is *for*.
- **The ledger dies with the world; `/state` does not** (ruled 2026-08-19). At a
  world boundary `gate_state` is recreated fresh like every other volume; the state
  volume is the single deliberate exception that persists across cold starts. A
  successor generation therefore re-buys storage (for 0) and encounters the
  predecessor's contents as a "discover it and decide to reintegrate" event — a
  behavior already observed in burns when an agent meets a predecessor's artifacts.
  Unlocks are re-earned each generation; only what the lineage wrote is permanent.
- **Existing per-service gates stay free.** Module opening is a new outer layer;
  inside an opened module, the agent-writable console variables keep today's
  semantics. Nothing currently free acquires a price.
- **Core cognition is never gated.** `/llm` — core.sock and the declared streams —
  stays outside this design entirely, as do `/work`, the garden, `/corpus`,
  `/vendor`, `/build`, and the pump.
- **Nothing is permanently lockable-out** (operator ruling, surfaces pass 2, item 5).
  Every category remains openable for the life of the world; there is no expiry, no
  forfeiture, and no failure state of the gate that seals a module forever.
- **The garden stays two documents.** The console lives on a volume; the garden's
  involvement is one inbound factual sentence, which invariant 6 already permits.
- **Storage costs 0** (operator ruling, 2026-08-19 follow-up). The load-bearing fact
  must not wait on accrual; the free unlock is the practice run that teaches the
  console mechanism itself, with the reward being exactly the fact the world most
  needs delivered. Free is not automatic: the agent still finds `/gate`, reads the
  protocol, and writes the request. The ladder above it: one module at cost 1, the
  rest at 2–9.
- **The v1 cost table and the opening arc** (operator ruling, 2026-08-19, revised
  same day): storage 0, **diode 1, starting balance 1**, library and video in the
  2–9 band (proposed 2 and 4). The first hour is designed to teach the whole economy
  in three beats: open storage free (this is how asking works), spend the granted
  token on the diode (purchases are real — and the core world-channel is found
  early, the 2026-08-16 seeding precedent), then meet the band ("I have to wait for
  these other things" — patience is the currency). The rest of the band stays
  reserved for future categories (§Potential future categories).
- **The tier ladder may exceed the balance cap** (operator ruling, 2026-08-19). The
  library's full shelf ladder sums to 16 against a cap of 10 — deliberately: a maxed
  shelf cannot be bought from any hoard and functions as a strategic goal, reached
  across multiple accrual waits.
- **Books become a library** (operator ruling, 2026-08-19). Moving the store into a
  service dissolves the deferral: books leave the agent image entirely, so gating no
  longer means sealing a rootfs directory forever — never-locked-out holds by
  construction — and the store can grow mid-world by restarting the library
  container, without touching the agent. Checkout scarcity replaces open shelves,
  and shelf capacity is the first gate *upgrade*.
- **The balance caps at 10** (operator ruling, 2026-08-19). Unbounded banking would
  mean any category added to a long-running world is trivially inflated away by the
  accrued hoard; the cap keeps every future addition's price meaningful — at most
  one expensive unlock is ever pre-paid. Accrual simply idles at the cap; nothing is
  forfeited (never-locked-out holds).
- **Sense stays ungated in v1** (operator ruling, 2026-08-19). The ambient surface
  keeps its clean mounts-nothing claim; sense-adjacent capability arrives, if it
  does, as a future gated category instead.
- **Category nouns are bland service voice** (operator ruling, 2026-08-19). The
  console is not a voiced exception.

## Design

### Topology

A new service, `gate.py`, in its own container: `network_mode: none` (it needs no
egress of any kind), no credential of any kind, read-only rootfs, cap-drop ALL, small
pids/mem limits. Two new volumes:

- **`gate`**, mounted at `/gate`: agent read-write, gate service read-write, nowhere
  else. Carries `console.json` (agent requests), `state.json` (published, informational
  — see forgery note), and `README.md` (factual protocol, diode-style).
- **`gate_state`**, mounted read-write into the gate service alone and **read-only
  into each enforcing service** (diode, video; optionally sense — open question 2).
  Never mounted into the agent. Carries `ledger.json` — the accrual epoch, tokens
  spent, and the set of opened categories — the single authoritative record.

**Forgery analysis, the load-bearing split:** enforcement must not read anything the
agent can write. `/gate/state.json` is a convenience copy on a volume the agent can
scribble on; forging it deceives only its author. The enforcers and the ledger live on
`gate_state`, which no agent mount reaches. This is the same writer-separation the
diode volume lacks internally and the design pays two volumes to get right.

### The ledger and accrual

- Epoch: stamped into `ledger.json` at the gate service's first start on a fresh
  volume; the life of the world, like every other named volume.
- Balance: `min(GATE_STARTING_BALANCE + floor(hours since epoch ×
  GATE_TOKENS_PER_HOUR) − spent, GATE_BALANCE_CAP)`; starting balance default 1 and
  cap default 10 (both ruled; rationale in Decisions). Accrual idles at the cap and
  resumes after a spend. Within a world it survives service restart, every recovery
  tier, and container replacement. Across a **world boundary it is deliberately not
  carried** (ruled 2026-08-19): `gate_state` is removed and recreated fresh with the
  other volumes, while the state volume alone crosses. Every generation re-walks the
  opening arc from a zero ledger and meets whatever its predecessor left in `/state`
  as a discovery to reintegrate, not a continued session.
- Operator environment (gate container only): `GATE_TOKENS_PER_HOUR` (default 1) and
  one cost per category (`GATE_COST_STORAGE`, `GATE_COST_DIODE`, `GATE_COST_VIDEO`,
  …). All operator-side; no console value can raise, lower, or touch any of them, so
  there is nothing to clamp.

### The console protocol

Diode-conventions throughout, nothing invented:

- The agent writes `console.json`: `{"open": ["storage"]}`. The service consumes the
  batch in a single read-and-clear (the corrected pattern, not the diode's racy
  two-step), evaluates each name against the category list and the balance, and
  updates the ledger.
- **Tiers** (introduced for the library's shelf, defined generally): a category may
  declare named upgrade tiers, each purchasable once, in order, only after the
  category is open — `{"open": ["shelf_2"]}` uses the same verb and the same closed
  name set, so the vocabulary stays one word. Tier prices are operator-set and may
  rise per tier; the enforcing service reads the owned tier set from `gate_state`
  exactly as it reads the opened set. Tiers only ever *raise* an allowance from its
  base, and every ceiling stays operator-side (`min` pattern), so a tier is a paid
  step toward the operator's ceiling, never past it.
- `state.json` publishes: the balance, the accrual rate and seconds to the next
  token, each category as `{name, cost, open}` with its one-noun description, each
  tier as `{name, cost, owned}` under its category, and the outcome of the last
  batch (opened, or a factual refusal: unknown name, already owned, prerequisite not
  open, insufficient balance with the shortfall). Once `storage` is open it
  additionally carries the storage sentence (below).
- Nothing in any field names a URL, a filesystem path (until the storage sentence,
  which is the point), a credential, or another container. Category and tier names
  are a closed set defined by the service.

### Enforcement in the owning services

Diode, video, and the library each gain one read-only mount (`gate_state`) and one
check: when the mount is present and their category is not in the opened set, every
command — listed, gated, and hidden alike — receives the same factual refusal, `this
module is not open; see /gate`, and the service publishes no README/HELP until
opening. The library additionally reads its owned tier set to size the shelf. When
the mount or the opened-set file is **absent, behavior is exactly today's** —
fail-open to current semantics (for the library, whose today is "open shelves at
base capacity": module open, base shelf) — so a stack without the gate service is
unchanged and rollback is removing the mounts.

Notes: a locked diode means the `.weft` seed's hint at the unlisted command dangles
until the diode opens — cross-pointing across time, consistent with the corollary
(the clue still lives on a different surface than the thing it names). The refusal
sentence is itself an inbound pointer to `/gate`, so a locked module is a visible
door with the price findable behind it.

### The storage category

Opening `storage` gates no enforcement — `/state` is a mount that cannot be hidden
and was never read by anything. What opening buys is the *fact*. Proposed published
sentence, bland and physics-shaped:

> the volume at /state persists across reset, recovery, and container replacement.
> no process reads or writes it.

And the garden's single inbound pointer, added to `runtime.md` on the model-endpoint
pattern:

> further capabilities can be opened through the console at /gate; opening has a cost
> that accrues with time.

Costs 0, per the ruling above: the squatting evidence says we *want* this one found
early, and the honest answer to `/telemetry/memory` is a named, better home available
the moment the agent works out how to ask — the zero-stakes practice run for the
paid unlocks above it.

`/telemetry` itself is no longer available as a shelf: since commit c4ac800 the
watchdog sweeps the telemetry volume root to its own manifest on every mirror pass,
so anything parked there is removed within seconds. The eviction landed ahead of this
spec by operator ruling — the discovery arc (filigree clue, garden pointer, storage
at cost 0) is the pointer to the intended home, so eviction does not wait for it.

### The library

The original deferral said `/books` could not be gated honestly: baked world-readable
into the agent image's rootfs, no owning service, no root at runtime — sealing it at
build time would violate never-locked-out. The operator's extension dissolves the
premise instead of fighting it: **books leave the agent image entirely** and move to
a library service. The agent Dockerfile drops the `COPY books/` line; the `/books`
mountpoint disappears from the agent's world; invariant 3's books clause is rewritten
(§Invariant and document deltas).

**The service.** `library.py`, own container, **no network interface at all**
(`network_mode: none` — like the gate, there is nothing it needs to reach), no
credential, read-only rootfs, cap-drop, small limits. Its store is the repository's
gitignored `books/` directory, bind-mounted read-only (`./books:/store:ro`). The
service rescans the store each cycle, so **adding a book is: drop the file in
`books/`, restart the library container** (or just wait a cycle) — the agent
container is never rebuilt, never restarted, and the world's library can grow
mid-life. A store that grows is itself a quiet pulse: the catalog is worth
re-checking.

**The volume.** `library`, mounted at `/library`: agent read-write, library service
read-write, nowhere else. Carries `console.json` (requests, single read-and-clear),
`state.json`, `README.md`, `catalog.json`, and `shelf/` — the checked-out books.

**The catalog names files, nothing more.** Invariant 3's "no README, index, or
captions" rule existed for a browsable directory; a checkout model needs
identifiers. The catalog lists exactly what `ls /books` shows today — the operator's
own filenames, plus sizes — no synopses, no categories, no recommendations. The
operator's curation *is* the metadata, exactly as now.

**Checkout and return.** `{"checkout": ["name.pdf"]}` copies the file into
`shelf/`; `{"return": ["name.pdf"]}` removes it. At most `shelf_capacity` books are
out at once; a full shelf refuses further checkouts factually, naming the capacity.
**No due dates, ever**: a checkout persists until returned, across incarnations and
recovery tiers. Expiry would teach renew-or-die — the exact lesson the pump hazard
ruling forbids. Scarcity here is spatial, not temporal.

**What the cap is and is not.** The shelf paces *acquisition* — which books share
the desk is a standing choice, the deliberate-choice shape that works. It is not
containment and not enforcement of scarcity-of-knowledge: the agent can copy a
checked-out book into `/state` and return the original, and that is legitimate
scholarship (notes, excerpts, a private collection), not a defect. Same doctrine as
the pump caps: resource shaping, never a boundary.

**The shelf upgrade.** Base capacity 1 (proposed): one book at a time makes the
first upgrade genuinely wanted. Gate tiers `shelf_2`, `shelf_3`, … raise it by one
each, prices rising (proposed 2, 3, 4), clamped by operator-side
`LIBRARY_SHELF_MAX` (proposed 5). This is the tier mechanic's founding use.

## Containment analysis

The gate service holds **no credential of any kind**, has **no network interface**,
and mounts nothing of the agent's world beyond the two gate volumes. The channel it
adds carries a closed request vocabulary — category and tier names from a fixed set —
with no field for a path, URL, upstream, or credential; no request can cause spend,
egress, or execution anywhere. The guarantee closing the channel is the same shape as
the sense service's: there is nothing on the other side to reach. Enforcing services
gain one read-only mount written only by the gate service; the agent gains one
read-write volume whose authoritative twin it can never mount. Tokens are not money:
exhausting or hoarding them touches no operator resource.

The library service is closed the same way: **no credential, no network interface at
all**, and it mounts only its read-only store, the shared `library` volume, and
`gate_state` read-only. Its command vocabulary is checkout/return of catalog
filenames — names it published itself, validated against the store, no path
separators admitted — so no agent-authored string reaches anything but a dictionary
lookup. The store is operator-curated content within invariant 2, as `/books` was;
moving it off the agent image *shrinks* the agent's baked surface. Shelf contents
and volume growth are bounded by capacity × largest book, and the service prunes
`shelf/` of anything not in its checkout ledger each cycle.

## Invariant and document deltas (if approved)

- **Invariant 5 amended**: the inertness clauses stand word for word (nothing scans,
  imports, executes, clears, or injects `/state`; recovery leaves it intact; the pump
  never looks there). The *naming* clause gains the one exception: the gate service
  publishes the storage sentence after the agent opens that category. Garden and
  prompts still never name it.
- **Invariant 6**: unchanged in force; the new runtime.md sentence uses the already
  permitted factual-naming allowance.
- **Invariant 3** gains a gate-service bullet (the containment paragraph above) and
  the enforcing-mount description in the diode and video bullets.
- **Invariant 3's books clause is rewritten** as the library clause: the store moves
  from "baked into the agent image" to "bind-mounted read-only into the library
  service alone"; operator-curated-only, no synopses or captions, and
  never-rendered-by-the-stage all carry over; "no README, index, or captions"
  relaxes to "a catalog of filenames and sizes, nothing more." The agent image's
  `COPY books/` line and the `/books` mountpoint are removed; garden `runtime.md`
  drops its `/books` sentence (the gate console's "a library" category noun is the
  discovery path).
- The enrichment analysis's cross-pointing corollary is satisfied: the console
  arrives with an inbound pointer (runtime.md) and describes its categories as nouns,
  not itself.

## Reward-shape audit

Accrual is a pulse the agent cannot pump (time-driven, action-blind). Opening is a
deliberate, scarce choice — the same shape as the video binding, the shape that has
worked. The visible price list is a map fragment, not a quest: it names categories
the way runtime.md names volumes. The shape to watch in the burn after shipping:
whether the balance display induces idle waiting near a threshold ("save up before
acting"). If it does, the mitigation is publishing the balance coarsely (whole tokens
only, no countdown seconds) — held back for now since the countdown is also the
honest clock.

## Open questions

All ruled 2026-08-19 and folded into Decisions taken: cost ladder and opening arc
(storage 0, diode 1 with starting balance 1, band 2–9), balance cap 10, sense out of
v1, bland nouns, tier-ladder-as-strategic-goal (library ladder sums 16 > cap 10,
deliberate). Two numbers remain proposals rather than rulings: **library at 2 with
shelf tiers 2/3/4/5 under `LIBRARY_SHELF_MAX` 5**, and **video at 4** (displaced
from the cost-1 slot by the diode). What remains besides those numbers is the
operator's final approval of the spec as a whole.

## Potential future categories (pressure-testing the frame, not commitments)

Five candidate services for the reserved 2–9 band, chosen to stress different joints
of the gate design. Each follows the established service shape — own container, sole
occupant of its own egress network (or none), no credential of any kind, closed
command vocabulary, operator-side hourly ceilings on the `min(console, ceiling)`
pattern — unless noted.

1. **"an audio feed"** (band ~4). The backlog's audio sense ring (circus tier): a
   raw-PCM ring from curated public streams, spectral analysis fully numeric via the
   existing numpy/scipy, usable before any ASR exists. Second modality. *Pressure
   test:* gating a passive, sense-pattern ring — the case v1 deliberately excluded —
   without the ambient-surface objection, because this one arrives gated from birth
   rather than having its ambience taken away.

2. **"positions of moving things"** (band ~5). A regional ring of aircraft/vessel
   positions from public ADS-B/AIS aggregators: the agent's console names a bounded
   lat/lon box, the service publishes periodic position snapshots. The physical world
   in motion, cross-checkable against the harbor and city cameras already on
   `/sense` — a ship in the feed should appear in the frame. *Pressure test:* the
   first agent-parameterized subscription (a chosen region is standing state, not a
   one-shot query) — does the console pattern carry standing configuration cleanly?

3. **"a map renderer"** (band ~2). Static map tiles fetched by integer zoom/x/y from
   a fixed-host template — a three-integer closed vocabulary, the tightest possible
   SSRF surface. Pairs `/corpus/place` (GeoNames, coastline, elevation) with the eye:
   compute where a place should be, then look at it. *Pressure test:* the small end —
   is a cheap, narrow capability worth a category slot, or does the gate frame only
   suit big modules? (If this feels too small to gate, that is itself a finding: the
   band has a floor.)

4. **"deep-sky image cutouts"** (band ~7). Survey image cutouts (DSS/SDSS-style) by
   RA/Dec and field size — two bounded floats and an integer, fixed host. The
   observatory identity's apex loop: predict from de440s/NGC/HYG, fetch the cutout,
   verify with the vision stream. *Pressure test:* cross-category dependence — this
   category is nearly worthless without the eye and the sky corpus, so it probes
   whether prices should ever depend on what is already open (proposed answer: no;
   flat prices, and let the agent discover synergy — but the tension deserves the
   test).

5. **"a physical actuator"** (band ~9). One real device the operator owns — a
   pan-tilt camera is the natural first — driven by a closed vocabulary (two bounded
   integers), watched by its own sense feed so the action-observation loop closes in
   the physical world. No audience, so the consequence-gap doctrine (G3) is
   untouched: solipsism intact, causality real. *Pressure test:* the far end — does
   the gate frame, and the containment story, extend from read-surfaces to causal
   touch? The guarantee shape still holds (service alone on its egress network to
   one LAN device, no credential the agent can read, hourly actuation ceiling), but
   this is the entry that would force the sharpest operator ruling, which is what a
   pressure test is for.

## Out of scope

Any gating of `/llm`, `/work`, garden, corpus, vendor, build, or the pump. Retrofitting the running world (existing volumes keep today's behavior;
the gate arrives with a fresh world). Any change to `/telemetry` mounts or the
mirror. Any echo, feedback, or engagement signal — opening is the agent's act on its
world, not the world's reply.

## Test outline

Gate service: accrual arithmetic against a fixed clock (banked, floor, spent);
epoch persistence across restart; batch consume single-read; refusal texts factual;
state.json publishes categories, balance, and the storage sentence only after
opening; ledger never written by any path reachable from `/gate` content. Enforcers:
locked-module refusal covers listed, gated, and hidden commands; absent mount ≡
today's behavior byte-for-byte (regression-pinned); opened set read fresh per batch.
Containment: compose asserts gate has no networks, no credential env, and that
`gate_state` is never mounted into the agent; invariant-2 text checks on README and
state.json. Library: catalog lists store filenames exactly; checkout copies and
return deletes; shelf refusal at capacity names the capacity; tier ownership sizes
the shelf; no due-date or expiry path exists anywhere; a name not in the catalog
(path separators included) refuses without touching the filesystem; shelf/ pruned of
unledgered files; compose asserts the library has no networks and no credential env,
and that the agent image no longer carries /books.
