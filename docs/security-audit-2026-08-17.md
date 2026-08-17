# Aurora containment audit — 2026-08-17

Method: CLAUDE.md is treated as a **claims document**. For each asserted invariant, find the
enforcing code and check that enforcement matches the claim. Three outcomes: verified, finding
(enforced only by documentation/convention/out-of-repo config), or accepted hazard (named as such
in CLAUDE.md — not a finding). Then a dual pass over surfaces CLAUDE.md never mentions.

Scope: `docker-compose.yml`, all five Dockerfiles, `entrypoint.sh`, `proxy.py`,
`recorder_streams.py`, `diode.py`, `pump.py`, `watchdog.py`, `viewer.py`, `stage/*`.
Full suite at audit time: 1452 passed, 2 failed (see F5). After the F1 fix: 1455 passed,
the same 2 failed.

---

## Verified — the load-bearing controls hold

These are the ones that would actually hurt if wrong. Each was checked in code, not taken on trust.

**Symlink containment — the static check is correct.** The chain agent → `/work` → watchdog mirror
→ `/telemetry` → stage → public page is the one path where agent-controlled data reaches a
more-privileged container. `watchdog.mirror_work` uses `shutil.copytree(..., symlinks=True)`, so
the agent *can* plant a link that survives into `/telemetry`. `stage/data.py:contained_file`
resolves with `os.path.realpath` **before** the containment test and rejects non-regular files;
`stage/browse.py:resolve_within` does the same. A planted link to `/proc/self/environ` is rejected
at check time. I grepped every `open()`/`listdir` in `stage/` for a read that bypasses these two
helpers and found none; the `glob`/`listdir` sites all filter through `contained_file` before
opening. `tests/test_stage_containment.py` holds 19 tests on exactly this.

The check itself is sound. What happened *between* the check and the open was not — that is F1,
now reproduced and fixed.

**Stream-name traversal.** `recorder_streams.NAME_PATTERN` is
`\A[a-z0-9][a-z0-9_-]{0,31}\Z` — correctly `\A`/`\Z` rather than `^`/`$` (which would admit a
trailing newline). No separator, no dot, no NUL, length-capped. An agent-declared name cannot
create a socket outside `/llm/sock`.

**Stage console auth fails closed.** `console_token()` returns `""` when unset;
`_authorized()` returns `None` for an empty token and `do_GET` checks `auth is None` *first*,
answering 403 "console disabled". An empty `STAGE_CONSOLE_TOKEN` disables the console rather
than disabling the check. Comparison is `hmac.compare_digest`. Mutating methods are rejected at
the base handler before auth (405, leaks nothing).

**Credential injection.** `build_forward_headers` **overrides** `Authorization` rather than
filling it when absent. Both keys live only in the recorder; the agent runs `sk-dummy`. The
recorder listens on `AF_UNIX` only — no TCP listener anywhere in `proxy.py` — so nothing on the
`egress` network can reach it over IP.

**Logging is header-free.** `log_event` writes a closed set of structured fields (id, model,
message count, status, duration, usage triple). No header dict reaches `events.jsonl` or the
transcript. Verified at the call sites, not from the docstring.

**Blog renderer (agent text on a public page).** `stage/blog.py` escapes at every emission point:
`html.escape` on text runs, `render_inline` recursion for bold/em, `html.escape(quote=True)` in
the one attribute context (`href`), an allow-list `safe_href` (prefix match on http/https/mailto/`#`
— allow-list, so `javascript:` and control-character tricks are moot), `_LANG.match` guarding the
code-fence class, `int()` on the `<ol start=>` value. The inline regex classes each exclude their
own closer and are length-capped, so no opener rescans past its closer.

**No client-side injection sink.** Zero occurrences of `innerHTML`, `insertAdjacentHTML`,
`outerHTML`, `document.write`, `eval`, or `new Function` across all of `stage/`. The page JS
builds DOM nodes. This is what makes the `'unsafe-inline'` in the CSP tolerable.

**CSP + mermaid.** `default-src 'none'; connect-src 'self'; img-src 'self' data:; base-uri 'none';
form-action 'none'; frame-ancestors 'none'`. The blog page's added CDN script is SRI-pinned
(`integrity` + `crossorigin`) at a fixed version, so the external dependency cannot silently
change under you. Mermaid runs at `securityLevel: "strict"`.

**Diode command vocabulary.** Exactly one command is marked `credentialed` (`speak`);
`deferred_command_refusal` refuses to defer it and refuses to defer a deferring command. Hidden
commands (`secret`, `blind`, `echo`, `silence`) bypass gate evaluation by construction, and
`tests/test_diode.py:2142` dispatches *every* hidden command with the request builder stubbed to
raise — the regression test CLAUDE.md claims does exist and is enumerated dynamically, so a new
hidden command is covered automatically. `parse_clone_arg` validates owner/repo/ref against closed
identifier patterns. Redirects are re-validated per hop (`_ValidatingRedirectHandler`), responses
are byte-capped.

**Console field vocabulary.** `validate_declaration` rejects any field outside
`BUDGET_FIELDS + COMPOSED_FIELDS`. No field names a URL, path, credential, or upstream.

**Pump execution.** `subprocess.Popen(entry["command"], ...)` — argv list, no `shell=True`,
`stdin=DEVNULL`, `start_new_session=True`. It cannot reach anything the agent cannot.

**Ceilings exist when unset.** `.env` omits `STREAM_HOURLY_MAX`, `STREAM_TOKEN_HOURLY_MAX` and
`DIODE_HOURLY_MAX`, but the code defaults are bounded, not unlimited: 120 req/hr, 2,000,000
tokens/hr, 120 fetches/hr, 20 speech/hr. (See F6 on whether those defaults are the values you want.)

**Host-level.** No `/var/run/docker.sock` mount, no `privileged`, no `cap_add` anywhere in the
compose file. Every service except cloudflared has `read_only`, `cap_drop: [ALL]`,
`no-new-privileges`, `pids_limit`, `mem_limit`, and a non-root `USER` in its image.

---

## Findings

### F1 — check-then-open race on `/diode` (medium-high) — **REPRODUCED, QUANTIFIED, FIXED**

Investigated under systematic-debugging after the initial audit. Two claims in the first
draft were wrong; both corrections are below.

**Root cause.** `contained_file()` and `resolve_within()` validated a *name* and returned a
*name*. Validation and use were two independent name resolutions, and the object bound to that
name could change in between. All 24 call sites inherited it — this was an API-shape defect,
not a set of sloppy callers.

**Reproduction.** A forked process renaming a validated file into a symbolic link, against the
real `blog.read_post` path, with no cooperation between the two:

| reader | reads | leaks | rate |
|---|---|---|---|
| tight loop, 10 s | 165,494 | 9,359 | 5.66 % |
| one read per 10 ms (a page load) | 400 | 72 | **18.0 %** |

**Correction 1 — severity was understated.** The first draft said "small odds per attempt,
unbounded attempts." Measured: roughly **one in six public page loads** leaked the canary while
the agent was swapping. That is not a lottery.

**Correction 2 — the `/audio/` claim was wrong.** `_send_file` streamed `Content-Length` bytes
taken from `getsize`, and procfs reports `st_size == 0`, so `/proc/self/environ` served **0
bytes**. The audio route was exploitable for *regular-file* targets only, not for the
environment. The blog route was the environment path, because `read_post` used `f.read(n)`
with no size dependence.

**Fix.** Added `data.open_contained(root, path)`: it opens first, then applies the containment
test to the descriptor — `os.fstat` for the regular-file check and the `/proc/self/fd/N` link
for containment. A descriptor is pinned to its inode, so no later rename reaches it.
`O_NONBLOCK` preserves the old behaviour of refusing a fifo rather than blocking the reader
(the `O_NOFOLLOW` flag alone is insufficient: it guards only the final component, and the agent
can swap an intermediate directory — the `/proc/self/fd` readlink is what catches that).

Converted: `blog.read_post`, `data._read_capped`, `data._capped_text` (and through them
`code_stats`, `diode_published`, `diode_spoken`, the console and state bodies),
`records._compute`, `moments._tombstone_note`, `summary._tombstone_notes` and
`_source_delta`, `codewatch._read_lines`, the `/audio/` and `/frame/` routes via a new
`_send_contained`, and the token-gated console `/api/file`, `/download` and `/api/diff`.

Published and spoken entries also had their `size`/`mtime` stated from the name while the text
came from the descriptor, so the two could disagree on a public field; both now come from one
descriptor via `_read_capped_stat`.

Left as-is deliberately: call sites that only filter a listing, and `os.stat` calls whose result
only keys a memo (`codewatch._key`, `code_stats`, `summary._stamp`). Those expose at most the
size and mtime of a swapped target, never its contents.

**Verification.** 500 rounds against a live concurrent swapper across the blog, audio and
published sinks: 0 escapes. The in-suite regression test
(`test_open_contained_pins_the_inode_against_a_concurrent_swap`) failed with 30 escapes in 400
reads before the fix and passes after. Full suite 1455 passed, 2 failed (the pre-existing F5
failures only). `ruff format` + `ruff check` clean.

### F2 — DNS rebinding is not actually defeated (medium)

`diode.classify_url` docstring: *"defeats SSRF and DNS-rebinding to internal services"*. It does
not defeat rebinding. It resolves the host, checks **every** returned address (good — stronger
than checking the first), and returns ok. Then `urllib.request.urlopen` **resolves the hostname a
second time** at connect. An attacker-controlled authoritative server with TTL 0 answers the
validation lookup with a public IP and the connect lookup with a private one. The check is
`resolve-then-connect`, the classic TOCTOU.

What it actually defeats: SSRF by literal private IP, and by hostname that statically resolves
private. Both real, both worth having.

Blast radius is bounded but non-empty. The diode's netns contains the `egress` bridge. The
recorder is not a target (AF_UNIX only, no TCP). The reachable target is **the Docker bridge
gateway address — i.e. the host** — and anything the host binds on it. Note the diode has no
`extra_hosts: host-gateway`, but rebinding to the gateway IP doesn't need one.

Requires the agent to get an attacker-controlled hostname into a `fetch`. The agent has no
network and no way to register a domain, so this needs a second step — but the agent can read
`/corpus`, `/books`, and news RSS, all of which carry attacker-influenced text.

Fix: resolve once, pin the address, connect to the IP with an explicit `Host` header — or install
a custom connection hook that re-validates the address the socket actually connected to. At
minimum, correct the docstring so the claim doesn't outrun the code.

### F3 — cloudflared is the one unhardened service, and it can reach the console port (medium)

`TUNNEL_TOKEN` is set in `.env`, so this is deployed, not hypothetical.

```yaml
cloudflared:
  image: cloudflare/cloudflared:latest
  command: tunnel --no-autoupdate run --token ${TUNNEL_TOKEN:-}
  networks: [stream]
  restart: unless-stopped
```

Three things, in descending order:

1. **It shares the `stream` network with the stage, so it can reach `stage:8092`.** The stage binds
   `0.0.0.0` in-container (`stage/server.py:1153`); the `127.0.0.1:8092` publish constrains only
   the *host* binding, not the Docker network. CLAUDE.md says the console is "never exposed
   through the tunnel" — that is enforced by the Cloudflare-side ingress config, which lives
   outside this repo, in a dashboard. Nothing in the repo prevents it. The in-repo control is the
   token check, which does fail closed (verified above), so this is defense-in-depth rather than
   an open door — but the invariant as written overstates what the code guarantees. A separate
   `console` network that cloudflared is not on would make the claim true in the repo.
2. **It is the only service with no `read_only`, no `cap_drop`, no `no-new-privileges`, no
   `pids_limit`, no `mem_limit`** — and it is the one holding a credential that grants inbound
   reach to your network.
3. **`:latest` is unpinned** and re-pulled. Pin to a digest.

### F4 — `.env` is world-readable (low–medium, host hygiene)

Mode `0664`. It holds `OPENROUTER_API_KEY`, `ELEVENLABS_API_KEY`, `STAGE_SUMMARY_API_KEY`, and
`TUNNEL_TOKEN`. Any local account on the host reads all four. `chmod 600 .env` — one command, and
the only finding here with no design tension at all.

### F5 — two tests are red on the public stream surface (low as a bug, medium as a process signal)

```
FAILED tests/test_stage_server.py::test_stream_snapshot_publishes_only_the_newest_six_turns
FAILED tests/test_stage_server.py::test_stream_snapshot_stays_small_with_the_longest_legal_payload
```

`DISPLAY_TURNS` was raised 6 → 10 in commit `e6bcf44`; both tests still assert 6. **Not a
regression** — I built the "longest legal payload" shape directly against `server.stream_snapshot()`
and measured **4,561 bytes** against the test's 130,000-byte bound, so the cap is intact and the
clipping is tighter than the test ever checked.

Caveat on that number: I hand-rolled the transcript entries rather than reusing the test's
`_turn_entry` helper, so the shapes may not match exactly and the reasoning field clipped harder
than the test expects (`len(t["reasoning"]) == 8_000`). Treat 4,561 as "comfortably under the
bound, same order of magnitude as intended", not as a verified reproduction of the test.

It is worth listing anyway, because the test suite is the enforcement mechanism for most of the
invariants above — `test_cleanliness`, `test_stage_containment`, the hidden-command regression
test. A suite that is normally red in two places is how a real containment regression gets waved
through. Either update the two assertions to 10 or delete them.

### F6 — the per-stream token ceiling sits at its permissive default (low, cost exposure)

`.env` sets neither `STREAM_TOKEN_HOURLY_MAX` nor `STREAM_HOURLY_MAX`, so the defaults apply:
2,000,000 tokens/hr and 120 requests/hr **per stream**. `llm_console_seed.json` declares four
streams, each with `"token_budget": 2000000` — exactly at the ceiling, so the clamp reduces
nothing. Aggregate declared-stream exposure is ~8M tokens/hr, on top of `core.sock`, which is
uncapped by design (accepted, see below).

Not a containment failure — the money boundary works as specified. But if 8M tokens/hr is not the
number you intended to authorize, `STREAM_TOKEN_HOURLY_MAX` in `.env` is the single lever, and it
clamps all four without a rebuild.

### F7 — upstream error bodies and headers relay verbatim to the agent (minor)

`proxy.py:544-548` returns `e.read()` and `passthrough_headers(e.headers.items())` on `HTTPError`.
`FRAMING_HEADERS` strips only `content-length`, `transfer-encoding`, `connection`,
`content-encoding` — everything else passes, including rate-limit counters, `set-cookie`,
Cloudflare ray IDs, and any account/org identifier the upstream includes in a 401/403/429.

No key crosses. But this is account-identifying metadata arriving on a surface invariant 2 wants
bland and factual, and it is the one place where upstream-authored text reaches the agent
unfiltered.

### F8 — an exception string reaches the agent transcript, with a named key-leak trigger (minor, conditional)

`proxy.py:551` puts `str(e)` straight into the body the agent receives:

```python
response_body = json.dumps({"error": {"message": f"Proxy error: {str(e)}"}}).encode("utf-8")
```

Different class from F7. `urllib` exception strings carry the request **URL**. With OpenRouter the
URL is a constant and this is harmless. But `LLM_BASE_URL` is operator-settable and some
OpenAI-compatible endpoints take the key as a query parameter — the moment one is configured that
way, `upstream_url()` carries it, and any transport exception writes it into the agent's
transcript and the recorded log. A conditional leak with a specific, plausible trigger.

Fix: log the exception, return a fixed string. The agent has no use for the upstream's URL.

---

## Accepted hazards — named in CLAUDE.md, listed so they aren't mistaken for oversights

- **Pump entries outlive every recovery tier.** An entry that restores `agent.py` from `/pump`
  defeats the ladder. Deliberate; the backstop is `docker volume rm aurora_pump`, outside the
  container. Adding expiry would teach "renew or die" and break invariant 2.
- **`core.sock` is uncapped.** The key budget is its ceiling, so multiplexing meets a real
  consequence rather than a substituted refusal. `chassis.py` classifying 402 and
  `EXHAUSTION_PHRASES` as transient (pause at exit 44, not tombstone) is load-bearing for this.
- **`later` refusing to defer a credentialed command is a courtesy, not a boundary** — the pump can
  write `speak` into `/diode` with no agent turn behind it. `ENABLE_SPEECH` and
  `SPEECH_HOURLY_MAX` in the diode's environment are what actually close credentialed spend.
- **`PUMP_MAX_ENTRIES` / `PUMP_MAX_CONCURRENT` are resource hygiene, not containment** — they live
  in the agent's own container.
- **Lowered `REASONING_EFFORT` persists across incarnations** for the container's life.

---

## Dual pass — surfaces CLAUDE.md doesn't cover

**The toolchain means "limited tool surface" is not a boundary, at all.** The agent image ships
`gcc`, `libc6-dev`, `make`, `rustc`, `cargo`, `nasm`, `sbcl`, `swi-prolog`, `pforth`, `sqlite3`,
`git`, `jq`, `ripgrep`, `poppler-utils`, a full CPython, a vendored crates registry at `/vendor`,
and a 10 GiB disk-backed `/build`. The agent that writes one `subprocess` call has arbitrary
native code execution and can compile new binaries.

This is obviously intended — self-modification is the entire design. The point is what follows
from it: **the only real boundaries are the kernel-level ones.** `network_mode: none`, the
read-only rootfs, `cap_drop: [ALL]`, `no-new-privileges`, `pids_limit`, `mem_limit`, the tmpfs
size on `/work`, and the AF_UNIX-only path to the recorder. Everything above that line — genesis
tool count, the absence of a shell tool, the closed diode vocabulary — is *shaping the agent's
world*, not containing it. That posture is sound and the compose file backs it up. The risk is
purely that someone later reasons "the agent has no shell tool, therefore X is unreachable." It
isn't. Worth stating explicitly in CLAUDE.md so that inference is never available.

**No explicit seccomp/AppArmor profile and no user-namespace remapping.** Docker's default seccomp
profile applies (fine). With `cap_drop: ALL` + `no-new-privileges` + non-root uid 1000, an escape
needs a kernel bug. `userns-remap` on the daemon would be the next real hardening step, and would
mean uid 1000 in the agent maps to an unprivileged subuid on the host — relevant because
`/build`, `/vendor`, `/corpus` and `/sense` are **host bind mounts**, not named volumes, so a
container escape or a uid-1000 confusion touches the repo working tree directly.

**`entrypoint.sh` wipes `/build` unconditionally.** If the loop image is not mounted, the
`find /build -mindepth 1 -maxdepth 1 -exec rm -rf {} +` runs against the repo's
`./volumes/build`. The script warns when `/build` shares a filesystem with `/vendor`, which
catches the case, but it warns and proceeds rather than refusing.

**`parse_transcripts.py` is on the image allow-list** and lands in `/work` via
`cp -r /opt/agent/. /work/`. Checked: factual, no authorial voice. Within invariant 2.

**`vendor/` is copied to `/tmp/vendor` and deleted in the same `RUN`** — so it is not in the final
layer, and deleted layer content is not readable from inside the container (overlay whiteouts).
Image-size concern only, not a disclosure one.

---

## What I'd do, in order

1. `chmod 600 .env` — **F4**. Thirty seconds, zero tradeoff, four keys.
2. ~~Convert the containment helpers to open-then-validate~~ — **F1, DONE.** Reproduced at 18 % of
   page loads, root-caused to the API shape, fixed with `data.open_contained`, verified at 0
   escapes with a regression test in the suite.
3. Pin cloudflared to a digest and give it the same `read_only` / `cap_drop` /
   `no-new-privileges` / `pids_limit` / `mem_limit` as every other service — **F3** items 2 and 3.
4. Put the console on its own network that cloudflared is not attached to, so "never exposed
   through the tunnel" becomes a fact this repo enforces rather than a dashboard setting —
   **F3** item 1.
5. Pin the resolved address in `diode._fetch`, or at minimum correct the docstring so the claim
   stops outrunning the code — **F2**.
6. Fix or delete the two red tests — **F5**. The value here is keeping the suite green enough that a
   real containment regression is visible.
7. Decide whether ~8M tokens/hr across declared streams is the number you meant; set
   `STREAM_TOKEN_HOURLY_MAX` if not — **F6**.
8. Filter the upstream error relay to a known-safe header/body subset, and replace `str(e)` with a
   fixed string — **F7**, **F8**.

Item 2 is done. Item 1 is thirty seconds. Items 3–5 are worth a single focused pass; 6–8 are
hygiene.

---

# Addendum — disk interface probe, and the structural question

Probed against the running stack (all six services up), not read off the compose file.

## What the disk interface actually looks like

From inside the agent container:

| mount | backing | options | writable | exec |
|---|---|---|---|---|
| `/` | overlay | `ro` | no | — |
| `/work` | tmpfs 8 GiB | `nosuid,nodev,noexec` | yes | **no** |
| `/tmp` | tmpfs 31 GiB | `nosuid,nodev,noexec` | yes | **no** |
| `/build` | loop → `volumes/build.img` | `rw,relatime` | yes | yes |
| `/state` | **`/dev/nvme0n1p3` (host root)** | `rw,relatime` | yes | yes |
| `/diode` | **`/dev/nvme0n1p3` (host root)** | `rw,relatime` | yes | yes |
| `/telemetry` | **`/dev/nvme0n1p3` (host root)** | `rw,relatime` | yes | yes |
| `/pump` | **`/dev/nvme0n1p3` (host root)** | `rw,relatime` | yes | yes |
| `/vendor` `/corpus` `/sense` | ext4 | `ro,relatime` | no | — |

The read-only rootfs, the tmpfs hardening, and the read-only operator mounts all behave as
designed. Two things do not.

### D1 — the agent can fill the host root filesystem in about twelve minutes (HIGH)

`/state`, `/diode`, `/telemetry` and `/pump` are Docker named volumes under
`/var/lib/docker/volumes/`, which is `/dev/nvme0n1p3` — **the host root filesystem**. No quota is
active (`/` is mounted plain `rw,relatime`; no `prjquota`, no `usrquota`), and the agent container
has no block-I/O limit of any kind: `BlkioWeight: 0`, all `BlkioDevice*Bps/IOps` unset, and no
`io.max` in its cgroup.

Demonstrated, not inferred:

```
$ docker exec aurora-agent-1 dd if=/dev/zero of=/state/.probe bs=1M count=1024 conv=fdatasync
1073741824 bytes (1.1 GB, 1.0 GiB) copied, 0.963092 s, 1.1 GB/s
```

That is sustained writeback, not page cache. 790 GiB of free space at 1.1 GB/s is roughly
**twelve minutes**. `df` inside the container truthfully reports the host's 1.7 T, so the agent
can see exactly how much room it has.

The consequence is not confined to the agent. Exhausting `/` stops the Docker daemon, the
recorder, the diode, the stage and anything else the host runs. It is the only demonstrated
host-level impact in this whole audit, and it needs no exploit — just a write loop.

What makes this a gap rather than an oversight is that **the design already solved this problem
once**. `/build` is a preallocated loop-mounted ext4 image precisely so that "a full `/build`
cannot consume main-disk space beyond this fixed footprint" (the script says so). The same
reasoning was never applied to the other four writable mounts, which are the ones the agent
actually lives in.

Fix, cheapest first:

- Reuse the pattern the operator already invented: back `state`, `diode`, `telemetry` and `pump`
  with loop-mounted images sized to intent, exactly as `create_build_volume.sh` does for `/build`.
- Or enable XFS/ext4 project quotas on the Docker data root and set per-volume limits.
- Or, as a partial stopgap that needs no host change, set `blkio` write limits on the agent so
  exhaustion takes days rather than minutes and is visible in monitoring first.

Related, same root cause, much slower: **transcript archives are never pruned.** `rotate_if_needed`
gzips at `TRANSCRIPT_MAX_BYTES` (128 MiB) and renames the archive into place, but nothing deletes
old archives. On a long-lived stack the transcripts volume grows without bound on the same host
filesystem.

### D2 — `/build` is half its documented size (LOW, documentation)

`docker-compose.yml` describes "a preallocated 10 GiB ext4 loop image" and
`create_build_volume.sh` defaults `AURORA_BUILD_SIZE=10G`. The image actually in service is
**5 GiB** (`df` reports 4.9 G). The bound is real and working — it is simply half what the
comments say. Worth reconciling so the stated footprint matches the enforced one.

### D3 — mount-option inconsistency is a misleading signal, not a weakness (LOW)

The tmpfs mounts carry `nosuid,nodev,noexec`; the four ext4 mounts carry none of them. I verified
what that means in practice by compiling and running a binary from each:

```
/build   compiled, exec rc=42  ->  EXECUTABLE
/state   compiled, exec rc=42  ->  EXECUTABLE
/diode   compiled, exec rc=42  ->  EXECUTABLE
/pump    compiled, exec rc=42  ->  EXECUTABLE
/work    exec blocked (rc=126)
/tmp     exec blocked (rc=126)
```

This is **not** an escape: the agent is meant to run arbitrary code inside its box, and CPython is
on the image regardless, so `noexec` was never a boundary. The reason to fix it is that the tmpfs
`noexec` reads as though execution is controlled when it is not — a future reader could rely on it.

`nosuid` and `nodev` are free to add on all four (`no-new-privileges` already neuters setuid, so
this is depth, not a fix). `noexec` is free on `/state`, `/diode` and `/pump` — nothing legitimate
executes from them. **`/build` must stay executable**: `CARGO_TARGET_DIR=/build/target`, so cargo
builds and runs binaries there.

One host-side note: `volumes/build.img` is mode `0664` owned by `john`, and root loop-mounts it
without `nosuid`/`nodev`. A host process running as `john` can write the raw filesystem image and
plant a setuid or device node. The agent cannot use either (`no-new-privileges`), so this is a
host-compromise amplifier rather than an agent escape — but `-o nosuid,nodev` on that mount costs
nothing.

## The structural question: should this move off Docker?

**No — and the two most severe findings of this audit are the reason why.**

F1 was a check-then-open race in application logic inside a single container. D1 is a missing
filesystem quota. **Neither is touched by changing container runtime.** A migration would consume
the effort that fixes them and leave both in place. That is the honest answer to "should we move
away from Docker entirely."

The current configuration is genuinely strong for this threat model — untrusted code that must
reach neither the network nor a credential: `network_mode: none` (one loopback device, empty
routing table), read-only rootfs, `cap_drop: [ALL]`, `no-new-privileges`, non-root uid, Docker's
default seccomp profile, pids and memory limits, and a unix-socket-only path to the recorder.
Docker is not the weak link here.

In priority order, what would actually change the risk:

1. **Quotas (D1).** The only demonstrated host-level impact in the audit, and the pattern is
   already in the repo. Highest real-world value of anything on this list, runtime swap included.
2. **`userns-remap` on the daemon.** The one Docker-level change that materially matters, and I
   flagged it in the main audit for a specific reason: `/build`, `/vendor`, `/corpus` and `/sense`
   are **host bind mounts into the repository working tree**. Today root-in-container is
   root-on-host; with userns-remap it maps to an unprivileged subuid, so a container escape or a
   uid confusion no longer lands on the repo with full rights.
3. **gVisor (`runsc`)** — the only runtime swap worth considering, because it is a drop-in
   `runtime:` line in compose rather than a rearchitecture, and it interposes on syscalls so a
   kernel bug is no longer a single point of failure. Real cost: it will likely break or badly
   slow the toolchain — rustc, cargo, sbcl, gcc under gVisor — and that toolchain is the agent's
   entire point. Worth a timed experiment before committing; not worth adopting blind.
4. **Firecracker / Kata microVMs** — strictly stronger isolation (a real kernel boundary), and it
   costs the shared-volume topology the whole design rests on: `/diode`, `/llm/sock`,
   `/telemetry` and the unix-socket transport are all host-shared-filesystem constructs. This is
   a rewrite of the containment model, not a hardening step.

Items 1 and 2 are worth doing. Item 3 is worth measuring. Item 4 is worth doing only if the threat
model changes to include a hostile kernel-exploit-capable agent — which is a different project.
