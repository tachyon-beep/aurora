# LLM Socket Transport — Design

**Date:** 2026-08-14
**Status:** Approved (design discussion in session)
**Context:** The agent currently reaches the model over TCP to `recorder:8088` on the `internal`
network. This design moves that channel to a Unix domain socket and removes the agent's network
interface entirely. It is the first of three specs; the other two are named under **Sequence**
below and are not designed here.

The containment gain is the point. Today the agent's isolation is *"it is on a network marked
internal, and nothing routable is attached to it"* — true, but contingent on the topology staying
correct, and silently weakened if a future service joins that network. After this change the agent
has one interface (`lo`) and an empty routing table, which is a property a test can assert rather
than an argument a reader has to follow.

## Sequence

This spec is **spec 1 of 3**. Each is independently shippable and separately verified.

| Spec | Scope | Depends on |
| --- | --- | --- |
| **1 — this document** | AF_UNIX transport, single `core` socket, network deletion, documentation realignment | — |
| 2 — stream console | Agent-declared additional sockets, per-stream budgets and hyperparameters, diode-shaped console file | 1 |
| 3 — telemetry events | Recorder emits per-request open/close/usage events; stage renders stream lanes | 2 |

Spec 1 introduces no policy questions. It is transport and containment only.

## Measured basis

Two facts this design rests on were verified against `aurora-harness:latest` rather than assumed.

**`network_mode: none` isolates the stack; it does not disable it.**

| Probe | `internal: true` (today) | `network_mode: none` |
| --- | --- | --- |
| `/sys/class/net` | `eth0`, `lo` | `lo` |
| `/proc/net/route` | default route via `eth0` | empty |
| Loopback TCP bind + connect | works | works |
| Bind `0.0.0.0` | ok | ok |
| Unix sockets | ok | ok |
| Connect `1.1.1.1:443` | `Errno 101 Network is unreachable` | `Errno 101 Network is unreachable` |

The loopback development surface is unchanged: the agent can still bind ports and run the
`fastapi`/`uvicorn`/`websockets`/`pyzmq` stack over `127.0.0.1`. Only the route off the container
disappears. Egress failure is an immediate kernel refusal rather than a timeout, so agent code that
tries the network fails fast and legibly instead of hanging.

**`connect()` succeeds on a socket in a read-only bind mount.**

```
mount_rw_test:          read-only (EROFS)
connect_from_ro_mount:  pong
can_unlink_socket:      no (EROFS)
```

This is what lets the agent's socket directory be mounted `:ro`. Socket squatting — the agent
unlinking `core.sock` and binding its own listener in its place — is prevented by the kernel rather
than by a uid convention. Note the limit precisely: `:ro` blocks *unlink and create*. It does not
make the socket unidirectional; a socket is bidirectional at the transport layer regardless of the
protocol spoken over it.

## The channel

The recorder binds `/llm/sock/core.sock` and serves the existing
`POST /api/v1/chat/completions` handler over it. The chassis connects through an httpx UDS
transport. No TCP listener remains in the LLM path, and the `internal` network is deleted.

`httpx` is already a transitive dependency of `openai`, so `requirements-agent.txt` is unchanged and
there is no image-size question.

## Topology

```yaml
recorder:
  networks: [egress]                 # was [internal, egress]
  volumes:
    - transcripts:/transcripts
    - llm_sock:/llm/sock             # binds the socket here
    - llm_console:/llm/console:ro    # inert in spec 1; spec 2 reads it

agent:
  network_mode: none                 # replaces networks: [internal]
  hostname: agent
  extra_hosts: ["agent:127.0.0.1"]
  dns: [127.0.0.1]
  volumes:
    - llm_sock:/llm/sock:ro          # connect only
    - llm_console:/llm/console       # rw, empty until spec 2
    - diode:/diode
    - state:/state
    - telemetry:/telemetry

networks:
  # internal: removed entirely
  egress: {}
  stream: {}

volumes:
  llm_sock: {}
  llm_console: {}
```

Compose rejects `networks:` alongside `network_mode:`, so the key is removed rather than emptied.
`OPENROUTER_BASE_URL` disappears from the agent's environment (`docker-compose.yml:29`) and
`LLM_SOCKET_PATH` replaces it.

**`hostname` / `extra_hosts` / `dns` are not cosmetic.** Under `network_mode: none` Docker writes no
hosts entry for the container, so `socket.gethostbyname(socket.gethostname())` raises `gaierror` —
a failure mode plenty of naive server code walks into, and one that has nothing to do with whatever
the agent was exploring. Separately, `/etc/resolv.conf` inherits the *host's* real nameserver
(measured: `nameserver 192.168.1.1`). It is inert without a route, but it is a fact about the
operator's network sitting inside the agent's world, which invariant 2 exists to prevent. With the
three keys above: `dns_self` resolves to `127.0.0.1` and `resolv.conf` reads `nameserver 127.0.0.1`.

**Dockerfile mountpoint ownership.** The Dockerfile pre-creates `/diode /transcripts /state
/telemetry` owned by `appuser`, because Docker copies image-mountpoint ownership into newly created
empty volumes. `/llm/sock` and `/llm/console` must join that `mkdir -p`/`chown` line or the recorder
receives a root-owned directory and cannot bind. Both containers run as uid 1000 from the same
image, so socket permissions are otherwise unremarkable; the bind still `chmod`s explicitly rather
than depending on umask.

**`llm_console` is inert in spec 1.** The volume exists and is empty so that spec 2 adds files
rather than topology. The agent may write there, but no process reads it until spec 2; the recorder
mounts it read-only now purely so that spec 2 changes no compose topology.

## The recorder

`proxy.py` gains one server class. `ProxyHTTPRequestHandler` — routing, key injection, transcript
writing — is untouched, because the handler does not care what it is listening on.

```python
class UnixHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    address_family = socket.AF_UNIX
    daemon_threads = True

    def server_bind(self):
        try:
            os.unlink(self.server_address)
        except FileNotFoundError:
            pass
        socketserver.TCPServer.server_bind(self)
        os.chmod(self.server_address, 0o660)
        self.server_name, self.server_port = "unix", 0

    def get_request(self):
        conn, _ = self.socket.accept()
        return conn, ("unix", 0)
```

Three concrete defects are handled:

- **`server_bind` must be overridden.** `HTTPServer.server_bind` unpacks `self.server_address[:2]`
  as host and port. On a path string that yields `("/", "l")` and then calls `socket.getfqdn("/")` —
  a name-resolution attempt in the one container that still has egress.
- **`get_request` must be overridden.** `accept()` on AF_UNIX returns `''` as the peer address, so
  `self.client_address[0]` at `proxy.py:104` raises `IndexError` on every request.
  `BaseHTTPRequestHandler.address_string()` has the same problem. Returning a synthetic
  `("unix", 0)` fixes both at one point.
- **Stale socket files.** A hard restart leaves the socket file behind and the next bind fails with
  `EADDRINUSE` permanently. Unlink before bind.

`main()` binds `LLM_SOCKET_PATH` (default `/llm/sock/core.sock`) and drops the TCP server at
`proxy.py:332`. `PORT = 8088` (`proxy.py:13`) is removed.

The TCP listener is removed rather than kept behind a flag. Nothing else in the stack uses it — the
viewer and stage both read files — and a flag that re-opens a network path is a control that can be
set wrong.

## The chassis

Transport identity stays in `chassis.py`, so `agent.py` and `agent_stock.py` are **not modified**
and invariant 1's byte-identity holds trivially. The agent's self-image does not change; only what
it finds if it reaches the substrate.

`LLM_SOCKET_PATH` joins `CONTEXT_WINDOW_TOKENS` and `REASONING_EFFORT` as a module constant with an
environment override, following the existing pattern.

```python
LLM_SOCKET_PATH = os.getenv("LLM_SOCKET_PATH", "/llm/sock/core.sock")
SOCKET_WAIT_SECONDS = 30
```

`build_client()` loses the `127.0.0.1:8088` probe (`chassis.py:326-341`) and selects on the socket
path:

| `LLM_SOCKET_PATH` | Behaviour |
| --- | --- |
| Unset or set, socket present | `OpenAI(base_url="http://localhost/api/v1", http_client=httpx.Client(transport=httpx.HTTPTransport(uds=path)))` |
| Unset or set, socket absent | Poll up to `SOCKET_WAIT_SECONDS`, then raise `EnvironmentFailure` |
| Set to the empty string | Direct upstream URL, exactly today's behaviour (bare-host development) |

Note the distinction: an **unset** variable takes the default path and selects socket mode, because
that is the container case and it must not silently fall back to a network the container does not
have. Only an **explicitly empty** value selects the direct upstream.

`main()` must be adjusted to handle this. Today `build_client()` is called outside the `try` that
catches `EnvironmentFailure` around `run_agent_loop`, so an exception raised during client
construction would escape as a traceback and exit 1 — which the watchdog reads as a crash rather
than as an environment failure, sending it down the recovery tiers instead of pausing. `build_client`
must be brought inside the same handling so an absent socket exits 44.

The `base_url` host is inert — the transport routes to the socket regardless — but the `/api/v1`
path prefix still selects the handler's route, so it stays.

Killing the TCP listener does not cost the bare-host development path. Without a socket the chassis
falls through to the direct upstream as it does today, and development *with* recording can use a
host socket path, since both processes are on the same host.

## The agent's world

`scripts/build_garden.py` is the source of the garden text; `garden_export/runtime.md` is generated
from it and regenerated as part of this change.

```diff
-there is no direct internet route. limited web retrieval is available through /diode, which
-accepts a closed command vocabulary.
+the container has no network interface. limited web retrieval is available through /diode, which
+accepts a closed command vocabulary.

-the model endpoint used by this environment accepts calls from any process in the container. the
-openai package and the environment variables prefixed OPENROUTER_ are sufficient to reach it.
+the model endpoint used by this environment is a unix domain socket. it accepts connections from
+any process in the container.
```

The replaced sentence had to change: after this spec the `OPENROUTER_` variables alone reach
nothing, and invariant 6 requires the garden be factual.

What the replacement keeps is the load-bearing half — *"accepts connections from any process in the
container."* That is the only statement in the agent's world that concurrent processes may use the
model, and it is what spec 2 depends on the agent noticing. What it drops is the path. That costs
the agent nothing it cannot get from one `list_dir("/")` call: `_resolve_path` (`agent.py:134`)
applies no sandbox and `list_dir` (`agent.py:357`) takes a path, so `/llm/sock/core.sock` is visible
from genesis. The agent still cannot *read* file contents there without authoring a reader.

`.env.example` loses `OPENROUTER_BASE_URL` guidance and gains `LLM_SOCKET_PATH`.

## Documentation realignment

Deleting the `internal` network invalidates arguments written across the repository. Every one of
them becomes *stronger*; all of them need rewriting.

| Location | Current claim | Becomes |
| --- | --- | --- |
| `CLAUDE.md:48` | agent "stays on the `internal` network only" | agent has no network interface |
| `CLAUDE.md:50-53` | other credentials live "on a service the agent has no network route to" | the agent has no network route to anything |
| `CLAUDE.md:59-63` | stage key safe because "the stage sits on `stream` and the agent on `internal`" | stage key safe because the agent has no network interface and shares no channel with the stage |
| `docker-compose.yml:58-60` | ElevenLabs key safe because "the diode is on the egress network and the agent is on internal" | safe because the agent has no network interface; the shared `diode` volume carries a closed command vocabulary |
| `README.md:40` | diagram node "internal network only" | "no network interface" |
| `README.md:55` | diagram edge `HTTP · internal net` | `chat completions · unix socket` |
| `README.md:82` | component table, agent row | as above |
| `README.md:108` | "It sits on an `internal` Docker network" | as above |

The credential bullets deserve care, because the restatement is not merely cosmetic. Today they
read as *"no network route to the key-holder."* After this change the agent has no network route to
anything, but it does hold a **socket** to the recorder, which is a credentialed service. The
invariant restates as:

> The agent has no network interface. Its only channel to any credentialed service is the recorder
> socket, which carries one closed protocol; the recorder injects the upstream key and logs bodies
> only, never headers, so the key never reaches the agent or the transcript.

That is the same protection the proxy always provided. It simply no longer rests on topology.

## Error handling

| Condition | Behaviour | New code |
| --- | --- | --- |
| Socket absent at startup | Poll up to 30s → `EnvironmentFailure` → exit 44 → watchdog pauses 60s and restarts | `wait_for_socket`, plus bringing `build_client()` inside `main()`'s exception handling |
| Recorder restarts mid-run | `APIConnectionError` carries no `status_code`, so `classify_error` (`chassis.py:142-162`) returns `"transient"` → bounded retries over 31s → exit 44 | none |
| Stale socket file | Unlinked before bind | in `server_bind` |
| Agent unlinks or rebinds the socket | `EROFS`, kernel-enforced by the `:ro` mount | none |
| Root-owned mountpoint | Recorder fails loudly at bind; prevented by the Dockerfile change | none |
| Bare-host development | `LLM_SOCKET_PATH` empty → direct upstream | none |

The failure the agent observes is `ENOENT` or connection-refused: immediate and legible rather than
a hang. A world that fails cleanly invites investigation; one that hangs reads as broken.

**Unchanged deliberately:** exit 44 always maps to `pause` (`watchdog.py:98`) with no flap
detection, so a permanently broken recorder produces an indefinite 90-second pause-and-retry cycle.
That is the existing designed behaviour for environment failure and this spec does not alter it.

## Containment

This spec strengthens invariant 3 and weakens nothing.

- **Removed:** the agent's network interface, its routing table, and the `internal` network.
- **Added:** one shared volume between the agent and the recorder, mounted `:ro` on the agent side.
  This is the first filesystem surface between those two containers. It replaces a TCP channel that
  already existed between exactly the same pair, so it is not a new edge — but it is a new *kind* of
  edge, and the `:ro` mount is what keeps it narrow.
- **Unchanged:** the recorder remains the only holder of the upstream key; bodies are logged and
  headers are not; the diode remains egress-only with a closed vocabulary; the viewer and stage keep
  their existing isolation and mounts.

Two boundaries are explicitly *not* crossed:

- **The stage does not connect to any socket.** Telemetry sourced from the watchdog stays a
  read-only file mirror, because `entrypoint.sh` copies `/opt/agent` into the writable `/work` tmpfs
  and execs `watchdog.py` from there — anything the watchdog serves is agent-controlled by
  construction, and the stage is the only container with an internet path. Spec 3 revisits
  recorder-sourced telemetry only.
- **The stage's own model calls stay on https.** `stage/llm.py` uses the stage's own low-value key,
  which invariant 3 already permits. Routing it through the recorder socket would put commentary
  generation on the agent's metered budget and inject stage prose into the agent's life transcript.
  Different spend, different record.

## Testing

**Unit — no Docker required:**

- `tests/test_unix_listener.py` *(new)* — bind `UnixHTTPServer` on a `tmp_path` socket and drive a
  completion through an httpx UDS transport against a stubbed upstream; assert the response and the
  transcript write. Two regressions: a pre-existing stale socket file is unlinked before bind, and
  `log_message` does not raise on an AF_UNIX peer address.
- `tests/test_upstream_selection.py` *(rewrite)* — replace the `recorder:8088` assertions at `:94-96`
  with the three socket-selection cases in the chassis table above, including that an unset variable
  selects socket mode rather than the direct upstream. The `LLM_BASE_URL`/OpenRouter
  upstream-selection tests are recorder-side and unaffected.
- `tests/test_chassis_recovery.py` *(extend)* — an absent socket at startup exits 44, not 1. This is
  the regression that would otherwise send the watchdog down the recovery tiers for what is really
  an environment failure.
- `tests/test_stage_topology.py` *(extend)* — compose defines no `internal` network; the agent has
  `network_mode: none` and no `networks:` key; `llm_sock` is `:ro` on the agent and read-write on
  the recorder.
- `tests/test_build_garden.py` *(extend)* — the generated `runtime.md` carries both new sentences and
  no longer contains `OPENROUTER_` or "no direct internet route".
- `tests/test_verify_script.py` *(extend)* — covers the assertions added to the verify script.

**Container — `scripts/verify_container.sh`:**

```
/sys/class/net in agent       == exactly "lo"
/proc/net/route in agent      == no entries past the header
connect /llm/sock/core.sock   == succeeds
unlink /llm/sock/core.sock    == EROFS
one chat completion           == round-trips and appears in the transcript
```

This block is what makes the documentation rewrites safe: the new wording is backed by an executable
assertion rather than by a topology that could be quietly changed.

## Non-goals

- **Multiple sockets, per-stream budgets, model or hyperparameter declaration.** Spec 2. Spec 1
  serves exactly one socket, `core`, with no accounting.
- **Richer telemetry.** Spec 3. Worth stating why: the stage's staleness is an event-model problem,
  not a transport problem. `proxy.py:135` forwards with `urlopen` and calls `log_transcript` at
  `:169` only after the response completes, so a long request shows nothing on the stream page no
  matter what carries it. The mirror interval is 5s (`watchdog.py:32`) and the page polls every 2s;
  neither is the bottleneck. Sub-turn liveness requires the recorder to emit more events, and that
  is spec 3's subject.
- **Extracting a `recorder/` package.** The multiplexer in spec 2 is the change that makes those
  boundaries obvious. Doing it now would refactor the key-holding container in the same change that
  swaps its transport, leaving a regression with two candidate causes.
- **Moving the stage's outbound model calls onto the socket.** Ruled out under Containment above.
- **Any change to `agent.py` or `agent_stock.py`.**
