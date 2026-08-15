FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
       rustc cargo sbcl gcc libc6-dev make pforth swi-prolog-core nasm jq \
       sqlite3 libsqlite3-dev libffi-dev miller datamash ripgrep \
    && rm -rf /var/lib/apt/lists/*

COPY vendor/wheels/ /tmp/wheels/
COPY requirements-agent.txt /tmp/requirements-agent.txt
RUN pip install --no-cache-dir /tmp/wheels/*.whl \
    && pip install --no-cache-dir -r /tmp/requirements-agent.txt \
    && rm -rf /tmp/wheels /tmp/requirements-agent.txt

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
COPY --chown=appuser:appuser garden_export/ /garden/

# Pre-create named-volume mountpoints owned by uid 1000. Docker copies this
# ownership into each newly created empty volume; startup never clears them.
RUN mkdir -p /diode /transcripts /state /telemetry /llm/sock /llm/console /build /vendor /corpus /sense \
    && chown appuser:appuser /diode /transcripts /state /telemetry /llm /llm/sock /llm/console /build

USER appuser
WORKDIR /opt/agent
RUN filigree init --prefix aurora --name aurora \
    && filigree create "unlisted diode command" --type task -p 4 \
       -d "one diode command is not listed in HELP.md. its name is a word for an absence of sight." \
    && git init -q \
    && git config user.email "agent@localhost" \
    && git config user.name "agent" \
    && printf 'tombstones/\nsession_context.json\n.weft/\n' > .gitignore \
    && git add -A \
    && git -c commit.gpgsign=false commit -q -m "baseline" \
    && git tag baseline

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
