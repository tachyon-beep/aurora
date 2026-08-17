"""Build the LLM console seed copied into the agent image.

The seed declares one stream per model the operator permits. The allow lists
are environment, the seed is a file, and a hand-kept file drifts from them
silently: a declaration naming a model the recorder does not permit is rejected
with "model not permitted" and its socket simply never appears. Generating the
seed from the same lists the recorder reads removes that class of mismatch.
"""

import json
import os
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

TOKEN_BUDGET = 2000000
BUDGET = 1200
MAX_TOKENS = 32768


def env_value(key: str, path: Path) -> str | None:
    """The value of an active assignment in an environment file.

    A commented-out line is not an assignment: the stack does not carry it, so
    reading one would declare models the recorder does not permit. A matched
    pair of surrounding quotes is stripped, as docker compose strips it before
    the recorder sees the value.
    """
    prefix = key + "="
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            value = line[len(prefix) :].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return None


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
    handle, temporary = tempfile.mkstemp(dir=str(dest.parent), prefix=dest.name, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            f.write(text)
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
