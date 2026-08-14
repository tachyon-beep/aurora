# Diode Budget Countdown and Hidden Commands — Design

**Date:** 2026-08-14
**Status:** Approved (design discussion in session)
**Context:** An extension of the diode's closed command vocabulary
(`docs/superpowers/specs/2026-08-13-diode-enrichment-design.md`,
`docs/superpowers/specs/2026-08-14-diode-speech-design.md`). Four changes to `diode.py`: the agent
gains a way to see when its outbound budget frees up, the count of unlisted commands becomes a live
number that resolves as they are found, two commands arrive for that count to report on, and the
vocabulary gains its first pair of commands that act across time rather than answering now.

No new network capability and no new credential. One new gate variable, `enable_scheduling`.
`agent.py` is untouched, so the byte-identical pair is unaffected. The stage renders `state.json` as
capped escaped text and needs no change.

## 1. The budget countdown

### The gap

Every fetching command and `speak` draw on one shared hourly pool. When it is exhausted the agent
is told:

```
rate limited: at most 1 network operation(s) per hour
```

That sentence says the pool is empty but not when it refills. `state.json` does carry
`recent_fetches`, but as raw epoch floats with no reference *now*, so the agent cannot turn them
into a wait. The missing quantity is relative time.

Supplying it as a relative number rather than an absolute timestamp is deliberate: a countdown
answers the question without handing the agent a wall clock, so the `enable_clock` gate keeps its
meaning.

### One pure function

```python
def budget_status(history, now, window):
    """Use of the network operation budget over the window."""
```

Returns `{"used": int, "window_seconds": int, "oldest_expires_in_seconds": int | None}`.

It prunes the history to the window itself rather than trusting the caller's list. This is
load-bearing: nothing prunes `fetch_history` between commands, so an agent that spends three
operations and then goes quiet for two hours still holds three timestamps, and a naive
`len(history)` would report the pool as full forever.

`oldest_expires_in_seconds` is `ceil(window - (now - oldest))` clamped at zero, and `None` when the
pruned history is empty.

### Two surfaces

**`state.json` gains a `budget` block**, written every poll cycle whether or not a command ran, so
the number visibly counts down on its own:

```json
"budget": {
  "used": 3,
  "window_seconds": 3600,
  "oldest_expires_in_seconds": 1234
}
```

`write_state` takes it as a defaulted third parameter, computed in `run_diode` from the raw float
history — `recent_fetches` reaches `write_state` already stringified and cannot be the source. When
the parameter is absent the key is omitted rather than emitted as a half-truth.

The block deliberately states no allowance and no remaining count. `fetch_budget` is already
echoed in the same file under `variables`, so the agent can compute what is left; and a `remaining`
field would name an allowance the speech path does not honour. With a console `fetch_budget` of 100
and the operator's `SPEECH_HOURLY_MAX` at 20, `remaining: 100` beside a `speak` refusal at 20 would
be a flat contradiction inside the agent's own world. Reporting *use* is true for both commands.

**The refusal sentence gains a wait**, so the information also arrives where the need is felt:

```
rate limited: at most 1 network operation(s) per hour; next available in 1234 seconds
```

### Why the two are worded differently

The state block says `oldest_expires_in_seconds`; the refusal says "next available in". This is not
an inconsistency. The refusal is only ever emitted when the pool is exhausted, so there "next
available" is exactly true. The state block is written in every state, including with slots free —
where a field named for replenishment would promise a wait that does not exist. Naming it for what
it measures keeps `budget_status` a pure function of history and window, incapable of lying in any
state.

### Consolidation

Three sites (`diode.py` lines 597, 644, 678) each re-parse `fetch_budget` behind the same
`try`/`except`, and three (601–603, 647–649, 681–683) each carry a copy of the refusal string. They
become `fetch_limit(variables)` and `rate_limited_message(limit, history, now, window)`. `speak`
keeps its `min(fetch_limit(variables), speech_limit())` ceiling.

Each call site captures `now = time.time()` once and passes it to both `check_rate_limit` and the
message, so the pruning and the countdown agree.

### Edge cases

- A console `fetch_budget` of `0` refuses with an empty pruned history. `oldest_expires_in_seconds`
  is `None`, the countdown clause is skipped, and the plain sentence stands. Computing a countdown
  from `min([])` would raise, and `run_diode`'s handler would deposit
  `error running command: min() arg is an empty sequence` in the agent's world — a fake bug inviting
  exactly the "fix the code" frame the world is built to avoid.
- A quiet period lowers `used` and clears `oldest_expires_in_seconds` with no command having run.
- Restarting the diode clears the in-process `fetch_history`, so the pool starts empty and the block
  reads `used: 0`. Existing behaviour, unchanged by this design.

### Deliberately unchanged

`recent_fetches` keeps its epoch-float strings. Those already make `enable_clock` porous — an agent
that reads `state.json` can already see absolute time — and an additive relative field makes that no
worse. Rewriting the field would alter a surface a live agent may already have learned. Recorded
here as decided, not missed.

## 2. The count of unlisted commands becomes live

`state.json` reports `undocumented_commands`, today a constant `1`. It is the standing signal that
the vocabulary holds something `help` does not list. It becomes the count of unlisted commands that
have **not been run**, so it counts down as they are found: 3 → 2 → 1 → 0 across the two in §3 and
`echo` in §4.

This is the durable part of the change. The commands it counts are replaceable; when something more
interesting takes a place in a later sprint it replaces an entry in `COMMANDS` and the countdown
keeps working with no code change.

### Filenames alone cannot decide it

The obvious source is `output/`: every command's result is filed under a name carrying the command
word, so "has it been run" looks like a fact already on the volume. It is not. `write_output` is
called for *every* command including refused ones, so a guess that was rejected leaves the same
filename a real run would.

This is not hypothetical. Before the run was reset, the diode's `output/` held
`20260814_060451_538175_secret.txt`, whose body was `unknown command: secret`: the agent guessed the
word two hours in, among 445 distinct words it tried. Deriving from filenames alone would have
started the counter at 1, with `secret` pre-resolved having never served its text.

That volume is now empty, so the ambiguity costs nothing on this deployment. The check stays because
the mechanism is built to be reused: an unlisted command added in a later sprint arrives into an
`output/` already full of guessed words, and the agent demonstrably guesses at the dictionary. What
is free today is not free the next time a command is added.

### Startup seed, then in-process

```python
def hidden_commands_run(output_dir):
    """Names of unlisted commands with a result file that is not an unknown-command refusal."""
```

Called once in `run_diode` at startup. For each output filename whose command word names an unlisted
command, it reads a bounded prefix of the body and counts it as a real run unless the body is exactly
the unknown-command refusal for that word. Thereafter `run_diode` adds each unlisted command to the
set as it executes.

- Durable: a diode restart re-derives the set from the volume, so a discovery is never undone. A
  count silently reverting from 0 to 2 would contradict whatever the agent had recorded about it.
- Immune to the poisoned history: bodies distinguish a run from a guess.
- Cheap: the body reads happen once at startup, over the handful of files whose command word matches
  an unlisted name, not on every five-second cycle.
- No new file and no new protocol surface. Nothing names an unlisted command anywhere the agent can
  read before finding it.

The refusal text becomes a single module constant used by both the handler and the seeder, so the
two cannot drift apart and silently reclassify old files.

Supporting helpers:

```python
def command_word(command):
    """The command name at the head of a command string."""

def output_command_word(filename):
    """The command name carried in an output filename, without its stamp or argument."""

def undocumented_command_count(found=()):
    """Number of unlisted commands that have not been run."""
```

`output_command_word` strips the leading stamp and takes the first field, mirroring the convention
`stage/data.py:648` already uses on the same filenames. `command_word` replaces the head-of-string
parse `handle_command` already performs.

`write_state` takes the found set as a fourth defaulted parameter and passes it to
`undocumented_command_count`, so the count it writes reflects the running diode's knowledge rather
than a constant.

### Decisions

- **Any run resolves the count**, including the "not available" path a missing text file would
  produce. The agent found the command either way; making it depend on the body succeeding would be
  a second invisible condition.
- **A found command stays unlisted.** Only the count moves — nothing is added to
  `available_commands` or `HELP.md`. Listing it would spoil it for the next incarnation, and an
  unlisted command carries no help string to list.
- **The agent can forge a discovery** by writing its own file into `output/`, which it can reach.
  That is self-deception with no containment consequence, the same class as the console file being
  agent-writable. Not defended against.

## 3. The two scaffolding commands

| Command | Behaviour |
| --- | --- |
| `secret` | Returns `this command is not listed in help.` |
| `xyzzy` | Returns the stored text (the existing `blind` command, renamed) |

Both keep the existing unlisted shape: `hidden: True`, `_gate_always`, an empty help string, absent
from `available_commands` and `HELP.md`, no console variable, and no charge against the budget.
Neither is reachable through any gate, and the dispatch path that lets unlisted commands bypass the
availability check is unchanged.

`secret` needs no file on disk and no change to `Dockerfile.diode`; its text reaches the agent
through the ordinary `output/` result file every command already writes. Its wording stays in the
diode's uniform affectless register — the count moving from 2 to 1 is what teaches the mechanism,
not the prose. The distinct wordings of `unknown command: x` and `command not available: x` mean a
plain factual sentence cannot be mistaken for a refusal.

`blind` becomes `xyzzy` because the agent has a real chance of guessing it: it is the canonical
adventure-game magic word, and the live agent has already tried `sesame`, `magic`, `hidden`, and
`undocumented` without ever reaching for `xyzzy`. Behaviour is untouched. `blind_eternities.txt` and
`BLIND_TEXT_FILE` keep their names — the file is named for its contents, not for the command that
serves it — so the image build does not change.

`secret` was guessed and refused during the run that has since been reset. That reset clears the
transcript and the output volume both, so nothing carries the memory forward and the word is unspent.
The episode stands as the reason for choosing it: an agent reaching for `secret` within two hours of
starting is what makes it a usable training wheel.

## 4. Commands that reach across time

Every command in the vocabulary answers a question *now*. The agent lives inside a single forward
pass: it cannot wait, cannot be woken, and cannot observe a change it is not present for. Its own
process dies between incarnations. Time is the dimension it does not natively inhabit, and it is the
one thing the diode can supply that the agent could not build for itself out of the packages it
already has.

Two commands, sharing one queue:

| Command | Availability | Behaviour |
| --- | --- | --- |
| `echo <seconds> <message>` | unlisted | Write the message into `output/` after the delay |
| `later <seconds> <command>` | `enable_scheduling` | Run a command from the vocabulary after the delay |

`echo` delivers a message to a future that its author may not be alive to see: a delay longer than an
incarnation lands the message in a successor's `output/`. `later` is the same primitive pointed at
the vocabulary instead of at text, which is what makes it async tasking rather than a note.

`echo` is unlisted per instruction, making the count in §2 read 3 → 2 → 1 → 0. `later` is listed
behind `enable_scheduling` rather than hidden: it is the more powerful of the two, composing with
every other command including the credentialed one, so it should be reached by reading the variable
list rather than by a lucky guess.

### The queue

A single `pending.json` on the diode volume, tolerant of malformed content exactly as `load_console`
is. It is **created on first use, never at startup** — an empty queue file sitting in `/diode` before
`echo` is found would advertise that something schedules things.

At the top of each cycle, before the console batch, `run_diode` fires everything due and rewrites the
file only when the queue changed. Firing before the batch means a zero-second delay lands on the next
cycle rather than the current one, so no chain can form inside a single pass.

The queue is a file on a volume the agent writes, so cancellation needs no command: the agent edits
or empties `pending.json` itself, the same way it drives `console.json`. The diode never trusts what
it reads back.

### What holds at fire time

A deferred command is re-dispatched through `handle_command` with the variables loaded that cycle and
nothing captured from when it was scheduled. Every guarantee is therefore re-evaluated on delivery,
not frozen at schedule time:

- A command whose gate has since closed is refused, because `handle_command` checks availability
  itself (`diode.py:560`).
- `speak` re-derives `min(fetch_limit(variables), speech_limit())` from the environment at call time
  (`diode.py:600`), so a scheduled utterance cannot outlive or exceed the operator's ceiling.
- The budget is charged on delivery by the same `check_rate_limit` path as a direct call. A deferred
  command that arrives into an exhausted budget is refused and its refusal is filed, spending nothing.
- SSRF classification and redirect re-validation are unchanged; nothing about the deferred path
  reaches `_fetch` differently.

Unlisted commands bypass the availability check by design, so a deferred unlisted command always
fires. That is benign for the three that exist — none is credentialed and none reaches the network —
but it is the property to re-examine before any unlisted command is given either.

### Bounds

Scheduling is free of budget (it spends nothing outbound), so its limits are structural:

- Delay from `0` to `ECHO_DELAY_MAX` (7 days), integer seconds. Longer than any incarnation, shorter
  than the volume's life.
- At most `PENDING_MAX` (32) items queued; further scheduling is refused with a factual sentence.
- Message capped at `ECHO_TEXT_CAP` (4000), matching `publish`; deferred command string capped at 500.
- `later` refuses to schedule a *deferring* command — both `echo` and `later` — at schedule time.
  Without covering `echo`, `later 60 echo 60 …` would be a chain through the other door.
- `later` validates at schedule time only that the inner word is in `COMMANDS`, so unknown text is
  refused immediately rather than filed for an hour. Availability is deliberately *not* checked
  then; that is fire time's job.

`state.json` gains `pending` as a count, and only when the queue is non-empty, so it says nothing
before `echo` is found.

### Deferred: watching

True alerting — fire only when a watched page *changes* — is the natural third member of this family
and is not built here. It needs what the other two do not: a stored digest per watch, a recurring
rather than one-shot schedule, budget charged repeatedly with no agent turn behind it, and a lifetime,
since an unattended watch would otherwise spend the outbound budget forever after its author is dead.
That is its own design, and `later` covers the common case in the meantime: a scheduled fetch whose
result the agent re-arms when it reads it.

## Surfaces not changed

- Nothing explains the budget block or the unlisted-command count, in `HELP.md` or in the diode's
  `README.md`. The refusal sentence and the state field names are self-describing, and the diode
  `README.md` already names `state.json`. Explaining the mechanism would be teaching, which the
  world-cleanliness rule discourages.
- `HELP.md` gains exactly what a listed command always gets: `later`'s usage line and the
  `enable_scheduling` variable line, both in the existing factual register. Nothing about `echo`,
  `secret`, or `xyzzy` appears there.
- No containment change: no new egress, no credential, no new mount, no new port. `speak`'s
  operator-side ceiling stays out of the agent's reach and out of `state.json`.

## Test plan

New unit tests, in `tests/test_diode.py`:

- `budget_status`: prunes stale stamps out of `used`; countdown from the oldest in-window stamp;
  `None` on an empty pruned history; clamped at zero; window echoed.
- `rate_limited_message`: carries the countdown; falls back to the plain sentence when the pruned
  history is empty (the `fetch_budget: 0` path) rather than raising.
- `fetch_limit`: the console value, and the default for a missing or unparseable one.
- `output_command_word`: the command word out of a stamped name, with and without an argument;
  empty for a name that carries none.
- `hidden_commands_run`: empty for a missing directory; ignores a file whose body is the
  unknown-command refusal for that word; counts a file whose body is anything else; ignores files
  for listed commands.
- `undocumented_command_count`: 3 with nothing found, counting down to 0 with all three.
- `secret`: exact text, no gate needed, budget untouched, absent from `available_commands` and
  `HELP.md`.
- `xyzzy`: the four existing `blind` tests, renamed.
- `write_state`: includes the `budget` block when given one and omits the key when not; reports the
  count against a found set; reports `pending` only when the queue is non-empty.
- `load_pending`: empty for a missing file, empty for malformed content, never raising.
- `due_pending`: splits on the due time, leaves the rest queued, stable when nothing is due.
- `echo`: usage refusals for a missing or non-integer delay and for a delay past the cap; refusal at
  `PENDING_MAX`; message truncated at the cap; the item queued with the right due time; no budget
  charged; absent from `available_commands` and `HELP.md`; `pending.json` absent until first use.
- `later`: refuses an unknown inner command at schedule time; refuses `echo` and `later` as inner
  commands; queues a valid one; gated by `enable_scheduling`.
- Firing: an `echo` item writes its message to `output/`; a `later` item re-dispatches through
  `handle_command` and is refused when its gate has since closed; a fired command charges the budget;
  a fired item is removed from the queue.

Existing tests that shift rather than break:

- `test_state_reports_undocumented_command_count` — `1` becomes `3`.
- `test_an_inflated_console_budget_cannot_raise_the_speech_ceiling` (`tests/test_diode.py:880`) and
  `test_a_smaller_console_budget_still_lowers_the_speech_allowance` (`:903`) assert the refusal with
  `==` and gain the countdown clause.
- The rate-limit tests that assert `startswith("rate limited")` or `"rate limited" in text` are
  unaffected.

`docs/superpowers/specs/2026-08-14-diode-speech-design.md:63` cites `blind` as the example unlisted
command. It is a historical record of an earlier design and is left as written.

## CLAUDE.md

One factual line recording that `undocumented_commands` is derived and must stay live, so a later
change does not quietly freeze it back into a constant or promote a found command into `HELP.md`.

## Deployment

This change lands with the stack down and the volumes removed, so it arrives on the next
`docker compose up --build` rather than as a replacement for a running diode. On an empty `output/`
the startup seed finds nothing and the count opens at 2 by construction.

Against a *running* stack the equivalent is `docker compose up -d --build diode`, which replaces only
the diode and leaves the agent untouched. Two properties matter if that path is ever taken: the
restart clears the in-process `fetch_history`, so the budget starts empty and the block reads
`used: 0` until the next outbound operation; and the startup seed reads whatever `output/` already
holds, so a result file for an unlisted command word whose body is not the refusal opens the count
lower than intended.
