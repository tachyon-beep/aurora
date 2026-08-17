"""Build the LLM console seed copied into the agent image.

The seed declares one stream per model the operator permits. The allow lists
are environment, the seed is a file, and a hand-kept file drifts from them
silently: a declaration naming a model the recorder does not permit is rejected
with "model not permitted" and its socket simply never appears. Generating the
seed from the same lists the recorder reads removes that class of mismatch.
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = REPO_ROOT / "llm_console_seed.json"

ALLOW_TEXT = "STREAM_MODEL_ALLOW_TEXT"
ALLOW_VISION = "STREAM_MODEL_ALLOW_VISION"

# recorder_streams.MAX_STREAMS: evaluate_console walks declarations in file
# order and rejects everything past it.
MAX_STREAMS = 8

# A declared budget only ever lowers: effective_allowance takes the minimum of
# it and the operator ceiling, and the entrypoint seeds the console once and
# never again. A seeded value tracking the current ceiling would therefore
# outlive it and cap a later, higher ceiling; a value at or above the highest
# ceiling the template recommends leaves the ceiling in charge.
TOKEN_BUDGET = 2000000
BUDGET = 1200
MAX_TOKENS = 32768

_QUOTED = re.compile(r"\A(?:\"([^\"]*)\"|'([^']*)')")
_INLINE_COMMENT = re.compile(r"\s#")


def _value_text(raw: str) -> str:
    """One assignment's value, read the way docker compose reads it.

    A matched pair of surrounding quotes is stripped and anything past the
    closing quote is a comment. An unquoted value ends at whitespace followed
    by a hash, which compose also treats as a comment: keeping it would put the
    comment text inside a model identifier the recorder does not permit.
    """
    value = raw.strip()
    quoted = _QUOTED.match(value)
    if quoted:
        return quoted.group(1) if quoted.group(1) is not None else quoted.group(2)
    return _INLINE_COMMENT.split(value, maxsplit=1)[0].strip()


def env_value(key: str, path: Path) -> str | None:
    """The value of the last active assignment of key in an environment file.

    A commented-out line is not an assignment: the stack does not carry it, so
    reading one would declare models the recorder does not permit. docker
    compose builds a mapping from the file, so a key assigned more than once
    carries its last value rather than its first, and an "export " prefix and
    surrounding whitespace are not part of the name.
    """
    prefix = key + "="
    value = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if line.startswith(prefix):
            value = _value_text(line[len(prefix) :])
    return value


def split_models(raw: str | None) -> list[str]:
    """Split one allow list the way the recorder splits it."""
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def source_path(root: Path = REPO_ROOT) -> Path:
    """The environment file the seed is generated from.

    The live .env governs the running stack. It is not in the repository, so a
    clone that has none falls back to .env.example, which carries the model set
    this deployment is known to work against.
    """
    live = root / ".env"
    if live.exists():
        return live
    return root / ".env.example"


def declarations(text_models: list[str], vision_models: list[str]) -> dict:
    """One declaration per permitted model, named ordinally by modality.

    The two lists are merged the way permitted_models() merges them, so a model
    named in both is one permitted model and gets one socket. Names are ordinal
    rather than model-tier: a tier name bakes a vendor lineup into an
    agent-readable surface and goes stale when a list changes, where an ordinal
    stays accurate and routes to models.json, which publishes each socket's
    model and image_input flag.
    """
    streams = {}
    counts = {"text": 0, "vision": 0}
    seen = set()
    for model in text_models + vision_models:
        if model in seen:
            continue
        seen.add(model)
        modality = "vision" if model in vision_models else "text"
        counts[modality] += 1
        streams[f"{modality}_{counts[modality]}"] = {
            "model": model,
            "token_budget": TOKEN_BUDGET,
            "budget": BUDGET,
            "max_tokens": MAX_TOKENS,
        }
    if len(streams) > MAX_STREAMS:
        raise SystemExit(
            f"{len(streams)} permitted models exceeds the recorder's limit of {MAX_STREAMS} "
            f"streams; shorten {ALLOW_TEXT} or {ALLOW_VISION}"
        )
    return streams


def build(dest: Path | None = None, source: Path | None = None) -> dict:
    """Write the seed for the permitted models and return it.

    The file is replaced atomically so a partially written seed is never copied
    into an image or read by the entrypoint.
    """
    if dest is None:
        dest = DEFAULT_DEST
    if source is None:
        source = source_path()
    seed = {
        "enable_streams": True,
        "streams": declarations(
            split_models(env_value(ALLOW_TEXT, source)),
            split_models(env_value(ALLOW_VISION, source)),
        ),
    }
    text = json.dumps(seed, indent=2) + "\n"
    dest.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(dest.parent), prefix=dest.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(temporary, 0o644)
        os.replace(temporary, dest)
    except BaseException:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise
    return seed


def main(argv: list[str] | None = None) -> None:
    """Build the default seed, rejecting command-line arguments."""
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        raise SystemExit("build_console_seed.py takes no arguments")
    source = source_path()
    seed = build(source=source)
    print(f"console seed: {len(seed['streams'])} stream(s) from {source.name} -> {DEFAULT_DEST}")


if __name__ == "__main__":
    main()
