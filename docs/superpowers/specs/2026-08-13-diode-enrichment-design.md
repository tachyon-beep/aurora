# Diode Enrichment — Design

**Date:** 2026-08-13
**Status:** Approved (design discussion in session)
**Context:** Phase 4a of the stream-demonstration project
(`docs/superpowers/specs/2026-08-13-stream-demonstration-design.md`, "Diode enrichment").
The diode's closed command vocabulary grows from four commands to ten, giving agents more
of the outside world to reach — through the same egress-only, gated, budgeted channel.

## Commands

Six new entries in `COMMANDS` in `diode.py`, in the existing gated-variable style. The agent
opens gates by setting variables in `console.json`; `HELP.md` lists every gate name factually.

| Command | Gate variable | Behavior |
| --- | --- | --- |
| `fetchrss <url>` | `enable_feeds` | Fetch the URL via the SSRF-checked `_fetch`; parse RSS/Atom; write up to 20 `title — link` lines |
| `wikipedia <title>` | `enable_reference` | Fetch `https://en.wikipedia.org/api/rest_v1/page/summary/<quoted title>`; write the extract as markdown |
| `weather <lat,lon>` | `enable_weather` | Fetch open-meteo current conditions (keyless); write factual lines (temperature, wind, code) |
| `arxiv <query>` | `enable_papers` | Fetch `https://export.arxiv.org/api/query?search_query=all:<quoted>&max_results=5` (Atom); write title/summary/link per entry |
| `abc` | `enable_news` | Fetch `https://www.abc.net.au/news/feed/51120/rss.xml`; write `headline — link` lines via the feed parser |
| `entropy <n>` | `enable_entropy` | Write `os.urandom(n)` as hex; `n` capped to 1–256; no network |

## Shared mechanics

- The five fetching commands (`fetchrss`, `wikipedia`, `weather`, `arxiv`, `abc`) consume the
  existing `fetch_budget` rate limit (`check_rate_limit`) and pass `classify_url` plus redirect
  revalidation, exactly like `fetchhttp`. Fixed-host URLs are still classified — no exemptions.
- `entropy` consumes no budget (no egress).
- All results land in `output/` via `write_output`, so they surface on the stream page and
  operator console with no stage changes.
- Argument validation:
  - `weather`: argument split on one comma; both parts parsed as floats; latitude in [-90, 90],
    longitude in [-180, 180]; otherwise a factual usage line is written to output.
  - `wikipedia` / `arxiv`: argument URL-quoted; length capped at 200 characters.
  - `entropy`: integer parse; out-of-range or non-integer writes a factual usage line.
  - `fetchrss`: URL handled exactly as `fetchhttp` handles its URL.

## Feed parsing and untrusted XML

One parser, `parse_feed(text) -> list[dict]` with keys `title`, `link`, `summary` (each possibly
empty), used by `fetchrss`, `arxiv`, and `abc`. `fetchrss` and `abc` write `title — link` lines;
`arxiv` also writes the summary (capped at 500 characters) beneath each entry:

- stdlib `xml.etree.ElementTree` only (no new dependencies).
- **Entity-expansion defense**: before parsing, reject any document whose first 4096 characters
  contain `<!DOCTYPE` or `<!ENTITY` (case-insensitive); the command writes a factual
  "unsupported feed" output. stdlib ElementTree is otherwise exposed to billion-laughs /
  quadratic-blowup inputs and `defusedxml` is not an approved dependency.
- Handles RSS 2.0 (`channel/item/title|link`) and Atom (`entry/title`, `entry/link[@href]`),
  namespace-tolerant (match on local tag names).
- Item count capped at 20; title length capped at 300 characters per line.
- Parse failures write a factual "could not parse feed" output — never a traceback.

## Agent-visible text

- `HELP.md` (via `write_help`) gains one usage line per command (gated commands appear only when
  their gate is open, as today) and one factual line per new gate variable in the
  "set variables in console.json" section, alongside `enable_fetchlinks`.
- All help text is bland and factual, matching the existing register
  (e.g. `entropy <n> -> return n random bytes as hex`). No suggested uses, no personas.
- `README.md` in the diode volume (`write_readme`) is unchanged.

## Error handling

- Gate closed → existing behavior (command not in `available_commands`, factual "unknown or
  unavailable command" output path).
- Budget exhausted → existing rate-limit output path.
- Fetch failures (network, HTTP error, classify rejection) → existing factual failure outputs.
- Malformed arguments → factual usage line in `output/`, never an exception escaping
  `handle_command`.

## Testing

In `tests/test_diode.py`, following its existing stubbed-`_fetch` pattern:

- Per command: gate-closed unavailability; gate-open happy path with a stubbed fetch/urandom;
  output file content shape.
- `parse_feed`: RSS sample, Atom sample, DOCTYPE/ENTITY rejection, malformed XML, item and
  title caps.
- `weather`: bounds rejection (lat 91, lon 200, non-numeric), valid parse.
- `entropy`: n=1, n=256, n=0 rejected, n=1000 rejected, non-integer rejected, hex output length
  = 2n, budget NOT consumed.
- Budget: each fetching command consumes one budget slot; `entropy` does not.
- `write_help`: new gate names present when closed (variables section) and usage lines present
  when open.
