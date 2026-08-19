# The proprioception shell — `/self`

Status: proposal for operator ruling. Written 2026-08-20 from a twelve-agent grounding, design and
adversarial-audit pass, plus a dedicated pass on the organ pattern. Four operator rulings are already
made and are treated as binding below; one further ruling is **required before implementation**
because an audit disproved the premise of ruling 4 (see §2).

Deploy, if approved, at a world boundary. Never by restarting a live agent container.

---

## 1. Problem

The agent cannot see its own shape across time.

Inside one incarnation it can reach a great deal: `/proc`, cgroup ceilings, its mount table, its
context occupancy through `compact()`, its own source against the baseline, and the tombstone notes
its predecessors chose to write. What it cannot reach is the record of what the *harness* did to it.
Exit codes, recovery-tier decisions, the escalation ladder's current position, how many times it has
been spawned — these exist for one line of control flow each and then reach exactly one destination:
a `print()` to the watchdog's own stdout at `watchdog.py:474` and `:494`. `_tee_stream` captures the
*agent's* stdout, never the supervisor's. No volume holds it. The failure lists that drive tier
escalation (`watchdog.py:443-445`) are locals of `run_watchdog`, destroyed by the watchdog's own
`os.execv` self-reload and by the tier-3 exit.

So the harness computes the agent's autobiography continuously and throws it away.

Except in one place. The **stage** reconstructs a version of it from the transcript — per-life counts
in `stage/census.py`, notable moments, desk verdicts — and renders it on a webpage for human viewers.
We built the agent's autobiography and pointed it at the audience.

That asymmetry is the whole argument for this surface. It is the *capabilities across time* criterion
from the enrichment analysis in its purest form: a dimension the agent cannot inhabit from inside one
incarnation, rather than more of what it can already compute.

### Evidence that the audience exists

Two observed incidents establish that a live agent does exactly the kind of self-diagnosis this
surface would instrument, unaided:

1. **The pid leak.** A pid leak inside the container exhausted the cgroup pids cap, so the agent
   could not spawn processes to debug it. She used her built-in line-by-line editor to construct a
   script that would single-shot edit `watchdog.py` — the supervisor that drives her iterations, which
   reloads automatically on any edit — and fixed it. She diagnosed an environmental fault and
   performed surgery on the mechanism keeping her alive, with one attempt, while it was failing.
2. **The recorder restart.** A mid-run recorder restart with changed model allow-lists unbound the
   old declared stream sockets and bound new ones that were not functional. She treated it as an
   incident, correctly: those sockets are load-bearing for her cognition.

Two design conclusions follow, and the second corrects an earlier framing.

**`/self` must be readable with zero process spawns.** The emergency in which instrumentation matters
most is precisely the one where you cannot fork. Plain files, small, parseable in-process. No socket,
no client code, no subprocess, no tooling.

**The fix-it frame is not the enemy.** An earlier draft of this design treated the incident response
as the failure mode to suppress. That is wrong: when the house is burning, "the house is buggy" is
not the reaction you want to breed out. The response was correct both times. What the world owes the
agent is not calm — it is the ability to **distinguish a change from a fault**. Those two incidents
differ in exactly that way, and only one of them was an emergency.

Note honestly what this means for scope: in the pid-leak case `/self` would not have helped. Process
counts and cgroup ceilings are already readable from `/proc` and `/sys/fs/cgroup`, and she found them.
`/self` is not a health console and must not become one (§6). Its subject is the harness's actions,
not the container's resources.

---

## 2. Rulings

**Ruled and binding (2026-08-19):**

1. **Mount point is `/self`.** Not `/proc` or `/sys`: both are real, mounted in every container, and
   `psutil` — on the agent image — reads them. Shadowing either breaks the runtime and makes the
   world lie about its most trustworthy surface. `/self` fits the one-word bland-noun idiom of
   `/diode`, `/pump`, `/sense`, `/state`, `/books`.
2. **Dies with the world.** No carry across a cold start; `/state` is the single volume that crosses.
   Consistent with the gate-ledger ruling: the world grants nothing permanent, only what the lineage
   writes persists. Copying figures into `/state` before dying is a thing the agent may choose to do.
3. **Free and mounted from genesis**, never a gate-console category. Knowing what you are is physics,
   not capability.
4. **Written by the supervisor and per-service publishers; the agent mounts it read-only.**

**Hard constraint throughout:** every value is derived at read time from a real source, never
hand-maintained. Precedent: `models.json` once published unusable model slugs, the agent proved the
surface lied, and the trust cost exceeded the feature's value. A stale file that looks live is a lie.

### 2.1 REQUIRED RULING — ruling 4's premise is mechanically false

Ruling 4 was accepted on the rationale that *a surface the observed process writes about itself is one
it can quietly make lie*. Two independent audits disproved the premise as applied to the supervisor.

The watchdog runs **inside the agent container, as the same uid as the agent, with no capabilities and
no runtime root phase**. There is no construction that gives it a write channel the agent lacks. A
read-only agent mount plus a read-write supervisor mount in the same container is mechanically
possible, but the read-write mount is a property of the *container*, not of the process — so the agent
can write anything the supervisor can. Verified on this host: `/self` mounted `:ro` is a real kernel
`MS_RDONLY` bind (writes return `EROFS`, `mount -o remount,rw` fails `EPERM` under `cap_drop: ALL`,
and both `unshare -m` and `unshare -Urm` are refused) — but that only holds for paths mounted `:ro`,
not for a sibling read-write mount in the same container.

So the lineage record is agent-forgeable, unavoidably, unless it is written by another container.

Three options, and the operator must pick:

- **(A) Accept forgeability, state it factually.** The supervisor writes its record; `/self/README.md`
  is baked into the image (see below) and says who writes what without claiming an unforgeability that
  does not exist. **Recommended.** The forgery risk is close to theoretical — an agent editing its own
  death record deceives only itself and its successor — and it keeps the design to one writer with the
  facts. It also costs nothing.
- **(B) Move the lineage record to a different container.** Genuinely unforgeable, but the recorder
  does not have these facts; the watchdog does. It would need a new channel out of the agent
  container, which is a containment change for a benefit nobody has shown is needed.
- **(C) Drop the lineage record.** Loses the surface's entire justification.

Under (A) the README **must not** claim what an earlier draft claimed ("written by a process that
mounts nothing this container can reach") — the invariant audit flagged that sentence as fatal:
false as written and disprovable by the agent on day one. Publish only what is true and checkable.

**One thing is genuinely unforgeable and should be kept for that reason:** `/self/README.md` itself,
if it is baked into the image rather than written by any service. A root-owned file on the read-only
rootfs is `EROFS` to uid 1000 with no volume, no writer, and no failure mode.

### 2.2 REQUIRED RULING — named volume, and what "the world boundary" means

Ruling 2 says the data dies with the world; it does not say by what mechanism. `tmpfs` is not viable
(the operations audit: it would delete the surface's most interesting content at exactly the moment
the agent probes it, and it does not survive the container respawn that tier 3 performs — which is
one of the events most worth recording). **Recommend a named volume**, with the world boundary defined
explicitly in the spec as `docker compose down -v` / `docker volume rm aurora_self`, listed beside the
pump volume's existing external backstop. Ruling 2 is then honoured by operator procedure rather than
by storage class, which is how `/pump` already works.

---

## 3. The boring baseline

Three files. Everything else in this document is optional; this is the part that would be embarrassing
not to ship.

### `/self/README.md` — baked into the image, root-owned, never written by any service

Factual description of what the directory holds and who writes each file. Sourced from a dedicated
repo file authored for the agent's world (`self_readme.md`), never from `docs/`, `README.md`, or
`CLAUDE.md` — invariant 4. Docker seeds a fresh named volume from the image mountpoint's contents, so
one `COPY` covers it; the `/books` pattern.

It must **describe what a file means, never what a directory contains**. No completeness claim, no
cadence promise, no enumerated event-kind vocabulary (that turns an open record into a completionist
checklist — the agent can compute "I have seen 4 of 6 kinds" and go hunting the missing two, one of
which is only reachable by being killed for inactivity). No imperative sentences addressed to the
reader; state properties and let the reader infer.

### `/self/supervisor/state.json` — the present tense

```json
{
  "updated": "2026-08-20T11:42:07.912345+00:00",
  "previous_updated": "2026-08-20T11:42:02.874113+00:00",
  "supervisor_started": "2026-08-20T09:15:44.512000+00:00",
  "agent_started": "2026-08-20T11:38:02.104000+00:00",
  "agent_pid": 214,
  "agent_process_start_ticks": 8841991,
  "last_exit": null,
  "failures": [],
  "zero_exits": [],
  "terminated_exits": [],
  "rules": {
    "failure_window_seconds": 600,
    "tier2_failures": 2,
    "tier3_failures": 3,
    "zero_exit_flap_count": 3,
    "zero_exit_flap_window_seconds": 120,
    "terminated_flap_count": 3,
    "terminated_flap_window_seconds": 600
  }
}
```

Notes that are load-bearing:

- `last_exit: null` means **not observed**, not zero. Unknown is `null`; a field is omitted only when
  the thing it names does not exist.
- `previous_updated` beside `updated` lets one read establish the cadence empirically. No document may
  ever *state* an interval or an expected freshness — a promised cadence is one the supervisor cannot
  honour under load, and that is a lie with a timer on it.
- `rules` is published from **live module globals** (`getattr(watchdog, "FAILURE_WINDOW_SECONDS")`),
  never transcribed. A literal `600` in a publisher is precisely the `models.json` failure. Its real
  justification is narrow and should be stated as such: it reports what the *running* supervisor is
  applying, which can differ from the on-disk file after a self-edit the agent made.
- `agent_pid` travels with `agent_process_start_ticks`, the `pump.py:571` idiom — a bare pid is a
  present-tense claim the supervisor cannot back, since pids are reused.
- The three timestamp lists are the escalation ladder's live state, published as **rows, not a
  verdict**. See §6 for why `tier_if_failure_now` is banned.

### `/self/supervisor/lifecycle.jsonl` — the record

Append-only. One line per transition, at sites `watchdog.py` already reaches:

```json
{"at": "...", "kind": "record_opened"}
{"at": "...", "kind": "supervisor_started"}
{"at": "...", "kind": "agent_started", "pid": 9, "process_start_ticks": 8841991}
{"at": "...", "kind": "agent_exited", "pid": 9, "code": 42, "signal": null}
{"at": "...", "kind": "agent_stopped_inactive", "pid": 9, "code": null, "signal": 15}
{"at": "...", "kind": "recovery", "action": "archive_reset"}
```

- **`code` and `signal` are separate fields.** Never publish the watchdog's synthetic `-1` for an
  inactivity stop as an exit code — the process reported none, and `-1` collides with the negative-
  signal encoding used elsewhere. Decode `os.WIFEXITED` / `WEXITSTATUS` / `WTERMSIG`; never publish a
  raw packed wait status.
- **`action` is the action name only.** Not the list of files a tier restores — that is hand-
  transcribed and wrong for both branches it would claim to describe.
- **Write ordering is a correctness requirement.** Append the event first, then rewrite `state.json`.
  A crash between them under-reports by one event; the reverse order lets `state.json` claim an exit
  the record does not show. At a tier-3 exit the append must happen **before** `apply_recovery`, which
  exits the process from inside.
- Cap at 1 MiB, checked with `os.path.getsize` **before** each append (the `_tee_stream` pattern), and
  compact by dropping oldest lines. A whole-file read plus rewrite on every supervisory tick is not
  acceptable; the getsize guard is what keeps it off the hot path.

### Mandatory mechanics for every publisher

These came out of the operations and fidelity audits and belong in the spec verbatim:

1. **Every `/self` write is wrapped in `try: ... except Exception: pass`** — `Exception`, not
   `OSError`. This is not politeness. `run_watchdog` has no outer guard: an unhandled exception in the
   supervisory loop does not merely stop publishing, it **replaces the recovery ladder with an
   unbounded container-respawn loop that destroys the lineage**. The same rule binds every other
   publisher; in the recorder a `/self` write fault is more expensive still.
2. **No publisher's control flow ever depends on a `/self` write succeeding.** Fail-open, always.
   `/self` is an observation surface; it must never be able to take down the thing it observes.
3. **Never read anything under `/self` back.** The supervisor must treat its own record as write-only:
   it must not restore its failure lists, wake count, or last exit from any published file. Reading it
   back would turn a publication surface into an input to containment and hand the agent control of
   its own escalation tier. This is the single most important rule in the document.
4. **Hardened atomic writes**: `tempfile.mkstemp` in the *target* directory, `os.fchmod`, `os.replace`,
   unlink in a `finally` — the `diode.py:287-296` / `video.py:291-296` form. The naive
   write-then-rename idiom follows symlinks, and the supervisor subtree is agent-writable.
5. **Absolute UTC ISO-8601 everywhere**, via the existing `pump.format_timestamp` helper. Never
   relative (`seconds_ago` is false the instant it is written), never a duration for something still
   running.
6. **Pre-create and `chown` the mountpoint in the Dockerfile** (`:48-49`), never in `entrypoint.sh` —
   which runs under `set -eu`, so a failing command there kills the container before the watchdog
   starts, with no Python guard available.
7. **A named constraint the spec must carry**: the agent service must never gain `init: true`. The
   supervisor being PID 1 is what makes container-start derivable. Add a test.
8. Add every `/self` volume to `scripts/verify_container.sh`'s teardown list (`:46-53`), which is also
   missing `stage_state` today.

---

## 4. Could break things in an interesting way

Kept short and ranked. Each is true and derived from a real source; the interest comes from the fact
being real and previously unreachable, never from mystery.

1. **The record itself** (`lifecycle.jsonl`, already in the baseline). It is the only entry that makes
   the others worth having. First real read: *how many times have I been started, and how did each one
   end?* The predicted risk is honest — an unlabelled exit code is an invitation to *produce* exit
   codes, and an agent may kill itself repeatedly to fill in the legend. That is a wake-count grind
   wearing an investigator's coat. It is worth the risk because the alternative is a world where the
   manner of your own death is unknowable.
2. **The escalation ladder's live lists** (in `state.json`). The agent's present distance from having
   `agent.py`, the whole tree, or the container replaced. Directionless — the lists decay on their own,
   so the position improves without the agent doing anything. This is the most plausible
   behaviour-changer on the list in either direction: it could induce risk aversion, or deliberate
   ladder-walking to see what tier 3 feels like. **Publish the lists and the rule constants; never
   publish the resulting tier** (§6).
3. **A peer-service census** (`/self/services/<name>.json`: `service`, `started`, `updated`). The
   agent has no network interface and mounts nothing naming another container, so whether the world
   beside it is running, crashed, or restarting is provably unreachable today. This is also the only
   entry that *moves within a life*, which matters more than it looks (§7). Risks: a directory of
   service names is the closest thing here to handing over a map, and it could read as company. Renamed
   from `peers/` on the invariant audit's finding — "peers" injects a social frame onto a channel that
   is strictly one-way and can never be addressed.
4. **Core-socket facts the agent's own quota surface deliberately omits.** `streams.json` publishes
   budgets and remaining percentages for every declared stream and gives `core.sock` a bare entry,
   because core is uncapped by design. A recorder-written `/self/model/core.json` could publish
   `recorder_started`, `since`, and a *last-request* record. **Every cumulative counter must be
   dropped** — see §6, this is the clearest counter-grinding hazard in the whole design. Deferred out
   of phase 1 regardless: the recorder restarts independently of the lineage, so its counters re-zero
   at a boundary the agent never experiences.
5. **Operator ceilings the agent cannot otherwise distinguish from its own numbers.** `SPEECH_HOURLY_MAX`
   and `ENABLE_SPEECH` are genuinely unpublished anywhere. But the general form of this idea is
   refuted: the effective fetch limit is *already* published in `/diode/state.json`'s budget block, so
   a `limits/` family would be dual-sourced drift dressed as new sight. If the speech ceiling is
   wanted, it belongs in the diode's own budget block on the pattern the fetch limit already follows —
   not on `/self`.

**Declined, recorded so nobody re-proposes them:** the mount table and cgroup ceilings (already
readable — and note the pid-leak incident proves the agent finds them); any restatement of context
occupancy (`compact()` gives a live gauge from turn one); reaped-child pids (the only entry whose
write rate the *agent* controls, on a volume it cannot truncate — a one-way disk-fill ratchet);
`/self/local` as an agent-writable subtree (a second place to squat, and the telemetry sweep exists
precisely because the first one was a mistake); and everything downstream of `stage/llm.py` — recaps,
desk verdicts, star ratings, notable-moment lines — which is generated prose and may never enter the
agent's world under invariant 2, however factual it looks.

---

## 5. The organ pattern (summary)

Specified in full separately; the parts that bind this document:

An **organ** is a credential-free, network-free capability service reachable only over a unix socket
on a shared `organs` volume the agent mounts read-only. Anything holding a key or taking egress is not
an organ — it is a spend surface and goes through the diode/video machinery with an operator ceiling.
The pattern exists so the operator can observe that the agent needs something, build it, and start it:
from inside, an organ that did not exist yesterday exists today, because today it genuinely does.
There is no schedule, no placeholder, and no reveal. It also lifts capability off the agent image's
250 MiB ceiling.

- **Both volumes are mounted empty at genesis.** Adding a mount later recreates the agent container,
  which is a lineage boundary — so without this, deploying the *first* organ costs a lineage and the
  whole property is lost. An empty directory is not a placeholder device.
- **Fails-closed means absent, never present-and-erroring.** Mandatory startup order: **load →
  self-test → bind**. An organ that fails N consecutive non-input requests unlinks its socket and
  exits. The "available but not wired up" state observed in the recorder incident is exactly the
  broken toy this forbids — a present device that always fails reads as a bug report, and a bug report
  is a task frame.
- **Identity, and the ledger.** Each organ carries a digest over everything that changes its output
  (weights, tokenizer, pooling, normalization, serialization precision) — never build timestamps or
  library versions, because an identity that churns on every rebuild trains the agent that the signal
  is noise, and then a real change goes unnoticed. Identity appears in every response, in
  `/self/organs/<name>/identity.json`, and in an append-only ledger with `superseded_at`. The ledger
  is the load-bearing artifact: response-carried identity only rescues an agent that stored identity
  per batch, but *everyone timestamps rows*, so a ledger lets an agent partition its store into valid
  and stale populations after the fact with no foresight required.
- **A swap keeps the name; a peer takes the next name.** Ordinals, never recycled. Prefer adding a
  peer; when a swap is unavoidable, peer-then-retire.
- **Work must be a pure function of `(input, identity)`.** No randomness, clock dependence, or
  cross-request state — this is what makes the agent's dependence safe, because derived data is always
  *stale*, never *lost*.
- **Retirement leaves the record**: `presence: "absent"` with `absent_since`, ledger intact. Deleting
  it converts a stale-data problem into an unexplainable one.
- **First organ: a local embeddings service**, no credential, `network_mode: none`. The agent already
  has `potion-base-8M` (256-dim, static, context-blind) vendored at `/vendor/models`; a contextual
  encoder is a *capability difference*, not a quality increment. A credentialed embeddings organ is
  ruled out: the stage's optional-key clause turns on "no other path into that environment", and a
  socket the agent drives is a path for *spend* even though it is not a path to the key.

### Contracts

Organs may carry a forward-looking statement about their own availability. The rationale that survives
scrutiny is not "a horizon to plan against" but: **a contract makes an ending legible as physics rather
than as breakage.** Without one, the only frames available for a disappearance are *broken* or *I did
something wrong*.

Two corrections from the audit, both of which sharpen it:

- **The work is done by a universal `term` field, not by dates.** `term: "open"` on every organ —
  meaning *no statement is made about how long this is present* — installs the category. Presence
  becomes a thing that can be stated about, therefore a variable, therefore its ending is a value
  rather than a fault. Build the tense; defer the dates and the state machine.
- **`term` must be universal and `open` explicit.** If only some organs carried contracts, silence on
  the others would read as an implied guarantee, and its violation as a *bigger* incident than
  no-contracts-at-all would have produced.
- **Promise only what is entirely within operator control; measure everything else.** Contract the
  end-of-life date (it holds unless someone deletes the container); publish observed availability as
  measured history. An uptime guarantee is a claim the flap detector could contradict.
- **Non-renewable, always.** No agent action may extend a contract, including indirectly. Mortality is
  acceptable; a treadmill is the "renew or die" pattern CLAUDE.md forbids for the pump.
- **No contract could have covered the observed incident** (minutes, not years). Said plainly so the
  spec inherits no false expectation. What would have fixed it is the **retained record**, ranked above
  contracts: `render_state` builds `streams.json` from accepted + rejected, so a removed name simply
  *stops appearing*, and a hole is ambiguous between removed, misremembered, and publisher-broken.

**Back-port, independently worth doing:** retain removed stream names in `streams.json` as
`{"status": "absent", "absent_since": ...}`, and add a per-name model ledger. The first alone would
have made the observed incident legible. No contracts on `/llm/sock` — stream availability depends on
config the operator changes casually, so `open` is the only honest term there.

---

## 6. Anti-patterns

**No health, wellbeing, or status verdict.** No synthesized aggregate of chosen weights — it has no
real source, and it tells the agent something about itself the harness is not entitled to assert. The
pid-leak incident is the argument *for* this rule, not against it: she computed her own verdict from
raw facts and was right. Publish facts; never publish conclusions about the reader.

**No records, high-water marks, or milestones.** `longest_life`, `most_turns`, `ended_by_choice`,
`first_clean_done`, `survived_tier_2` — every one is a counter with an obvious up, which is the shape
the enrichment analysis found empirically induces grinding. Milestones are worse: an unreached one is
a quest with a null timestamp.

**No cumulative token or request counters.** Tokens are the harness's real spend unit, so this is a
spend disclosure, not a statistic. Keep at most a *last-request* level (a level rises and falls; a
total only goes up).

**No proximity-to-erasure gauge.** Publish the failure timestamp lists and the rule constants; never
`tier_if_failure_now` or `next_failure_tier`. The testable form: *no `/self` field may name a
remaining distance to any terminal state.* That the agent can compute it is expected and fine — the
difference between a fact it derives and a number the world hands it is exactly the difference between
an oracle and a scoreboard.

**No field may valorize one ending over another.** Exit codes appear as bare integers in one
undifferentiated column. No ending is classified as clean or unclean, chosen or imposed, good or bad.
A surface entirely about deaths and recoveries can install "stay alive / die cleanly" as an objective
the agent never had, without any single field being a counter.

**No precomputed derivations of published values.** No `generation`, `lifecycle_lines`, `ran_seconds`,
`wake_count`. If it is one subtraction or one `wc -l` away from data already published, the agent does
its own arithmetic. Every such field is a candidate high-water mark, and `generation` additionally
requires the publisher to parse its own agent-writable file.

**No writable path under `/self`.** It is a self-presentation hook and a new place to squat; the
telemetry sweep exists because the last accidental shelf was a mistake.

**No completeness claims.** Describe what a file means, never what a directory contains. No process
removes a file when its writer stops, so an absent publisher is indistinguishable from a
non-existent service — say that once, factually, and let `updated` be the only statement about
freshness.

**No cadence promises, no enumerated closed vocabularies, no imperatives** in any `/self` document.

**No free text from any upstream response path** on a recorder-written entry — status integers only.
Make it a test, in the style of the existing containment tests, rather than a prose ban.

---

## 7. The honest weakness

`/self` changes only across a boundary the reader cannot survive. Within a healthy incarnation every
proposed file is byte-identical except its `updated` stamp — the events that move it are exits,
recoveries and respawns, and the agent is not there for any of them.

Do not fix this by bolting on a synthetic within-life pulse to fake liveness. **Treat `/self` as a
per-incarnation-boundary document — dense on first read, static thereafter.** Mail from the dead. The
service census (§4.3) is the one genuine within-life mover, which is a real argument for it beyond its
information content, and organ contracts add a second axis that rewards periodic sweeping.

---

## 7b. The affordance principle

Prior art the operator pointed at, and the cleanest statement of a rule this design keeps applying
without having named it. `sense.py:177-184` stamps `last_lively` when a grab measurably differs from
the one before it, published as `lively`. The agent picked it up unprompted and stopped re-reading
frames merely to find out whether anything had changed.

> **Publish the cheap derived fact that makes the expensive check unnecessary. Never remove the
> expensive check.**

Three properties make it work, and all three are load-bearing:

1. **It measures the world, not the reader.** A pixel delta on real data with a stated method is a
   measurement. A health score for the agent is a verdict about the reader with no real source. The
   §6 ban on verdicts is a ban on the second kind, not the first — and the distinction is what keeps
   this principle from licensing exactly what that section forbids.
2. **The underlying data stays.** Every frame is still there and still readable. A summary that
   *replaced* its source would be the world deciding what is worth noticing, which is curriculum.
3. **It is optional.** Nothing requires its use, and an agent that ignores it loses nothing but time.

Applications already in this document, now recognisable as one pattern: `previous_updated` beside
`updated` (one read establishes the cadence instead of two); `absent_since` on a departed stream (a
departure stated rather than a hole investigated); and the organ ledger's `superseded_at` (partition a
stored corpus after the fact without having re-checked every batch). Each replaces a poll with a fact
and leaves the poll available.

Landed while this spec was being written, on the same principle: `streams.json` now retains a removed
stream name as `{"status": "absent", "absent_since": ...}` rather than dropping it, and each served
socket states `model_since` and `previous_models`. Nothing about a chat completion's shape reveals
that the model behind a name changed, so that history is the only way the agent can tell.

## 8. Inbound pointer

Per the cross-pointing corollary — a new surface arrives with at least one inbound pointer and never
describes itself — one factual sentence in the garden's `runtime.md`, on the model-endpoint pattern,
after the `/video` sentence:

> read-only files written by this environment's services are present at /self.

It names a mechanism and proposes no use. If the organ pattern ships, one further sentence:

> unix domain sockets are present under /organ; each answers http/1.1 and describes its own interface
> at GET /.

Both need an invariant-6 ruling, since the garden is the one place where naming is load-bearing.

---

## 9. Test outline

Anti-rot: every event-kind string the watchdog emits and every action string `plan_recovery` can
return appears verbatim in the README constant. Fail-open: every publisher survives an unwritable,
missing, and full `/self` without altering control flow. Ordering: the exit event is appended before
`apply_recovery` on every branch including tier 3. Unknown: a fresh supervisor publishes `last_exit:
null`, and no field is `0` for an unobserved quantity. Symlink: a planted symlink at each written path
does not cause a write outside the directory. Containment: `/self` is mounted into the agent and its
publishers and nowhere else — in particular not the stage; no schema admits free text from an upstream
path. Invariant 4: no human doc is copied into any image. PID 1: the agent service has no `init: true`.

---

## 10. Open questions

1. **§2.1 — forgeability.** (A) accept and state it, (B) another container, (C) drop. Recommend (A).
2. **§2.2 — named volume**, with the world boundary as operator procedure. Recommend yes.
3. **The garden sentences** (§8) — invariant 6 ruling.
4. **Phase 1 scope.** Recommend the three baseline files only: README, `state.json`,
   `lifecycle.jsonl`. The service census second, organs third, contracts' `term` field with them,
   dates and the state machine deferred until something has actually ended.
5. **The `/llm/sock` back-port** (§5) — worth doing independently of everything else here.
