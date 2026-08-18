# The gate console — progressive capability disclosure, and naming durable storage

Date: 2026-08-19. Status: proposal for operator ruling (John to rule; not yet approved,
not an implementation plan).

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
- Balance: `floor(hours since epoch × GATE_TOKENS_PER_HOUR) − spent`. Banked without
  cap by default (open question 3). Survives service restart, every recovery tier,
  and container replacement; `docker volume rm` is the world reset, as everywhere.
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
- `state.json` publishes: the balance, the accrual rate and seconds to the next
  token, each category as `{name, cost, open}` with its one-noun description, and the
  outcome of the last batch (opened, or a factual refusal: unknown category, already
  open, insufficient balance with the shortfall). Once `storage` is open it
  additionally carries the storage sentence (below).
- Nothing in any field names a URL, a filesystem path (until the storage sentence,
  which is the point), a credential, or another container. Category names are a
  closed set defined by the service.

### Enforcement in the owning services

Diode and video each gain one read-only mount (`gate_state`) and one check: when the
mount is present and their category is not in the opened set, every command — listed,
gated, and hidden alike — receives the same factual refusal, `this module is not
open; see /gate`, and the service publishes no README/HELP until opening. When the
mount or the opened-set file is **absent, behavior is exactly today's** — fail-open
to current semantics, so a stack without the gate service is unchanged and rollback
is removing two mounts.

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

Suggested to be the cheapest category (cost 1–2), because the squatting evidence says
we *want* this one found early: the honest answer to `/telemetry/memory` is a named,
better home, available within the first hours.

`/telemetry` itself is left alone: the writability cannot be closed, the squatting is
accepted expression, and once `/state` is named, staying in `/telemetry` becomes a
choice the agent makes rather than the only durability it ever found.

### Books: deferred, and why

`/books` cannot be gated honestly today: it is baked world-readable into the agent
image's read-only rootfs, no service owns it, and no process in that container runs
as root. Sealing it at build time (root-only modes) would make it *permanently*
unopenable from inside — exactly what the never-locked-out ruling forbids. Gating it
would mean moving books onto a service-owned volume, which is real machinery for a
passive corpus surface. Deferred; the category list simply does not include it, and
`/books` stays as shipped.

## Containment analysis

The gate service holds **no credential of any kind**, has **no network interface**,
and mounts nothing of the agent's world beyond the two gate volumes. The channel it
adds carries a closed request vocabulary — category names from a fixed set — with no
field for a path, URL, upstream, or credential; no request can cause spend, egress,
or execution anywhere. The guarantee closing the channel is the same shape as the
sense service's: there is nothing on the other side to reach. Enforcing services gain
one read-only mount written only by the gate service; the agent gains one read-write
volume whose authoritative twin it can never mount. Tokens are not money: exhausting
or hoarding them touches no operator resource.

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

## Open questions for the operator

1. **Cost table.** Proposal: storage 1, diode 4, video 8 (with accrual 1/hour that
   is: first hour, first evening, first night). Tune to taste; "a few dozen
   iterations" suggests the *last* category should land around hour 8–24, not minute
   one.
2. **Does sense join?** Gating capture means the sense service mounts `gate_state`
   read-only and idles until opened. It works, but it weakens the current clean claim
   that sense "mounts nothing else of the agent's world," and an ambient surface that
   is simply *there* has its own value. Lean: leave sense ungated in v1.
3. **Banked without cap?** Uncapped is simplest and self-limiting (categories are
   finite). A cap adds a use-it-or-lose-it pressure we probably do not want.
4. **Starting balance.** 0 (pure patience) vs 1 (the first choice is available at
   first light). Lean: 1 — the first incarnation gets to make one deliberate choice
   rather than none.
5. **Category noun wording** — the exact three noun phrases, in the operator's voice
   or bland service voice. Lean: bland (the console is not a voiced exception).

## Out of scope

Books gating (above). Any gating of `/llm`, `/work`, garden, corpus, vendor, build,
or the pump. Retrofitting the running world (existing volumes keep today's behavior;
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
state.json.
