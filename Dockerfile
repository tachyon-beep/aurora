FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-agent.txt /tmp/requirements-agent.txt
RUN pip install --no-cache-dir -r /tmp/requirements-agent.txt \
    && rm /tmp/requirements-agent.txt

RUN useradd --create-home --uid 1000 appuser

COPY --chown=appuser:appuser agent.py agent_stock.py chassis.py watchdog.py proxy.py parse_transcripts.py system_prompt.txt user_prompt.txt /opt/agent/
COPY --chown=appuser:appuser entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
COPY --chown=appuser:appuser garden_export/ /garden/

# Pre-create named-volume mountpoints owned by uid 1000. Docker copies this
# ownership into each newly created empty volume; startup never clears them.
RUN mkdir -p /diode /transcripts /state /telemetry \
    && chown appuser:appuser /diode /transcripts /state /telemetry

USER appuser
WORKDIR /opt/agent
RUN git init -q \
    && git config user.email "agent@localhost" \
    && git config user.name "agent" \
    && printf 'tombstones/\nsession_context.json\n' > .gitignore \
    && git add -A \
    && git -c commit.gpgsign=false commit -q -m "baseline" \
    && git tag baseline

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
