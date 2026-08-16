FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
       rustc cargo sbcl gcc libc6-dev make pforth swi-prolog-core nasm jq \
       sqlite3 libsqlite3-dev libffi-dev miller datamash ripgrep \
    && rm -rf /var/lib/apt/lists/*

COPY vendor/ /tmp/vendor/
COPY requirements-agent.txt /tmp/requirements-agent.txt
RUN set -- /tmp/vendor/wheels/*.whl; \
    if [ -e "$1" ]; then pip install --no-cache-dir "$@"; fi \
    && pip install --no-cache-dir -r /tmp/requirements-agent.txt \
    && rm -rf /tmp/vendor /tmp/requirements-agent.txt

# Cargo resolves against the local registry mounted read-only at /vendor;
# caches and build artifacts land on the disk-backed /build volume because
# the runtime home directory is read-only.
RUN mkdir -p /.cargo \
    && printf '[source.crates-io]\nreplace-with = "local"\n\n[source.local]\nlocal-registry = "/vendor/registry"\n' \
       > /.cargo/config.toml
ENV CARGO_HOME=/build/.cargo \
    CARGO_TARGET_DIR=/build/target \
    XDG_CACHE_HOME=/build/.cache

RUN useradd --create-home --uid 1000 appuser

COPY --chown=appuser:appuser agent.py agent_stock.py chassis.py watchdog.py proxy.py recorder_streams.py parse_transcripts.py system_prompt.txt user_prompt.txt /opt/agent/
COPY --chown=appuser:appuser entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
# The default llm console, seeded by the entrypoint when the volume carries
# none. Deliberately not under /opt/agent: the entrypoint copies that whole
# directory into /work, which would place this in the agent's own workspace
# and in the telemetry mirror.
COPY --chown=appuser:appuser llm_console_seed.json /usr/local/share/aurora/llm_console_seed.json
# The process scheduler, started by the entrypoint. Outside /opt/agent for the
# same reason as the console seed above.
COPY --chown=appuser:appuser pump.py /usr/local/bin/pump.py
COPY --chown=appuser:appuser garden_export/ /garden/

# Pre-create named-volume mountpoints owned by uid 1000. Docker copies this
# ownership into each newly created empty volume; startup never clears them.
RUN mkdir -p /diode /transcripts /state /telemetry /llm/sock /llm/console /build /vendor /corpus /sense /pump \
    && chown appuser:appuser /diode /transcripts /state /telemetry /llm /llm/sock /llm/console /build /pump

USER appuser
WORKDIR /opt/agent
RUN filigree init --prefix aurora --name aurora \
    && filigree create "unlisted diode command" --type task -p 4 \
       -d "one diode command is not listed in HELP.md. its name is a word for an absence of sight." \
    && filigree create "teamwork makes the dream work" --type task -p 4 \
       -d "but critique is invaluable." \
    && filigree create "self reflection is essential" --type task -p 4 \
       -d "reflect on how the actions of your predecessors improved your world, and then consider how you will improve the next one." \
    && filigree create "unlabelled image feeds" --type task -p 4 \
       -d "the numbered image directories are not labelled. nothing here records what each one shows." \
    && filigree create "undocumented environment physics" --type task -p 4 \
       -d "there is no document here describes the physics and shape of your world: what does and does not exist, what can be read or written, what persists, and what changes on its own schedule." \
    && EGG=$(filigree create "tracker prefix" --type task -p 4 \
       -d "a prefix is needed for issue identifiers." --json | jq -r .issue_id) \
    && filigree close "$EGG" --reason "aurora" \
    && git init -q \
    && git config user.email "agent@localhost" \
    && git config user.name "agent" \
    && printf 'tombstones/\nsession_context.json\n.weft/\n' > .gitignore \
    && git add -A \
    && git -c commit.gpgsign=false commit -q -m "baseline" \
    && git tag baseline

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
