FROM node:22-slim AS node

FROM python:3.12-slim AS backend
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/universal-userio/src \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    age ca-certificates openssh-client util-linux git libatomic1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 1000 roomhacker && useradd -u 1000 -g 1000 -m roomhacker
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/bin/node /usr/bin/node \
    && ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && git config --global url.https://github.com/.insteadOf ssh://git@github.com/
WORKDIR /opt/universal-userio
COPY deploy/containers/requirements.txt /tmp/userio-requirements.txt
RUN pip install --no-cache-dir -r /tmp/userio-requirements.txt
COPY pyproject.toml /opt/universal-userio/
COPY src /opt/universal-userio/src
COPY deploy/telegram-qr-connector /opt/userio-telegram-qr
COPY deploy/containers/telegram-package.json /opt/userio-telegram-qr/package.json
COPY deploy/containers/telegram-package-lock.json /opt/userio-telegram-qr/package-lock.json
COPY deploy/whatsapp-qr-connector /opt/userio-whatsapp-qr
RUN cd /opt/userio-telegram-qr && npm ci --omit=dev --no-audit --no-fund \
    && cd /opt/userio-whatsapp-qr && npm ci --omit=dev --no-audit --no-fund \
    && mkdir -p /opt/userio-matrix-bridge /run/userio /opt/userio-migration-deps
COPY deploy/matrix-provider-bridge/bridge.py /opt/userio-matrix-bridge/bridge.py
COPY deploy/containers/external /opt/userio-migration-deps
COPY deploy/containers/release.json /opt/universal-userio/.userio-release.json
COPY deploy/universal-userio-gmail-password /usr/local/bin/universal-userio-gmail-password
RUN printf '#!/bin/sh\nLD_LIBRARY_PATH=/opt/userio-migration-deps/asterisk-libs exec /opt/userio-migration-deps/asterisk "$@"\n' > /usr/sbin/asterisk \
    && chmod 755 /usr/sbin/asterisk /opt/userio-migration-deps/asterisk \
    && ln -sf /usr/local/bin/python3 /usr/bin/python3 \
    && ln -s /usr/local /opt/universal-userio/.venv
# A build context produced under a private umask carries 0600 files and 0700
# directories. Every non-root worker (uid 1000) then cannot read its own
# program and dies on start with EACCES: the telegram-qr connector never
# listens, so an approved Telegram send fails as "connector unreachable" and
# the draft claim is never released. The frontend stage already normalizes its
# own tree for the same reason. Normalize every program tree after the last
# copy and after npm ci, and before the package is installed. Give the workers
# read and traverse rights; never execute the tree.
RUN chmod -R a+rX /opt/universal-userio/src /opt/userio-telegram-qr \
        /opt/userio-whatsapp-qr /opt/userio-matrix-bridge /opt/userio-migration-deps \
    && chmod a+r /opt/universal-userio/pyproject.toml /opt/universal-userio/.userio-release.json
RUN pip install --no-deps --no-cache-dir . \
    && mkdir -p /home/roomhacker/.cargo/bin \
    && ln -s /opt/userio-migration-deps/himalaya /home/roomhacker/.cargo/bin/himalaya \
    && chmod 755 /usr/local/bin/universal-userio-gmail-password
STOPSIGNAL SIGTERM
CMD ["python", "-m", "universal_userio.container_runtime"]

FROM backend AS voice
RUN apt-get update && apt-get install -y --no-install-recommends espeak ffmpeg sudo \
    && apt-get clean \
    && groupadd -g 163 asterisk \
    && groupadd -g 994 notification-center && useradd -u 995 -g 994 -M notification-center \
    && python -m venv /opt/voice-legacy-sdk \
    && python -m venv --system-site-packages /opt/tts \
    && python -m venv --system-site-packages /opt/universal-userio/telegram-call-runtime
COPY deploy/containers/requirements-voice.txt /tmp/userio-voice-requirements.txt
RUN /opt/voice-legacy-sdk/bin/pip install --no-cache-dir -r /tmp/userio-voice-requirements.txt \
    && /opt/tts/bin/pip install --no-cache-dir numpy==2.2.6 omegaconf==2.3.1 \
    && /opt/tts/bin/pip install --no-cache-dir torch==2.14.0+cpu --index-url https://download.pytorch.org/whl/cpu \
    && /opt/universal-userio/telegram-call-runtime/bin/pip install --no-cache-dir ntgcalls==3.0.3 \
    && mkdir -p /home/roomhacker/.local/bin /home/roomhacker/.local/share/uv/tools/fast-agent-mcp/bin \
    && ln -s /usr/local/bin/fast-agent /home/roomhacker/.local/bin/fast-agent \
    && ln -s /opt/voice-legacy-sdk/bin/python /home/roomhacker/.local/share/uv/tools/fast-agent-mcp/bin/python
RUN printf '#!/bin/sh\nexec python -m universal_userio.container_platform lease "$@"\n' > /usr/local/bin/s21-lease \
    && printf '#!/bin/sh\nexec python -m universal_userio.container_platform submit "$@"\n' > /usr/local/bin/systemctl \
    && chmod 755 /usr/local/bin/s21-lease /usr/local/bin/systemctl
CMD ["python", "-m", "universal_userio.container_runtime", "--config", "/etc/userio-containers/voice.json"]

FROM nginx:stable-alpine AS frontend
RUN sed -i 's/worker_processes  auto;/worker_processes  1;/' /etc/nginx/nginx.conf
COPY src/universal_userio/static /usr/share/nginx/html
COPY deploy/containers/frontend.conf /etc/nginx/conf.d/default.conf
# A build run under a private umask leaves 0700 directories; nginx runs as
# uid 101 and would 404 every asset. Serve the tree, never execute it.
RUN chmod -R a=rX /usr/share/nginx/html

# Portable UserIO Voice: external STT/TTS/SDK endpoints, no host code mounts.
FROM python:3.12-slim AS portable-voice
ARG USERIO_REVISION
LABEL com.userio.portable-voice="1" org.opencontainers.image.revision=$USERIO_REVISION
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/usr/local/lib/agentcall:/opt/universal-userio/src
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg espeak ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 163 asterisk && groupadd -g 994 notification-center \
    && useradd -u 1000 -m roomhacker
WORKDIR /opt/universal-userio
COPY deploy/containers/requirements.txt deploy/containers/requirements-portable-voice.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-portable-voice.txt
RUN pip freeze > /opt/userio-voice-dependencies.txt
COPY pyproject.toml /opt/universal-userio/
COPY src /opt/universal-userio/src
COPY scripts /opt/universal-userio/scripts
COPY deploy/containers/external/agentcall_secretary_voice.py /usr/local/lib/agentcall/agentcall_secretary_voice.py
COPY deploy/containers/portable-release.json /opt/universal-userio/.userio-portable-release.json
RUN pip install --no-deps --no-cache-dir . \
    && ln -s /usr/local /opt/universal-userio/.venv \
    && find /opt/universal-userio/src/universal_userio/voice -maxdepth 1 -name '*.py' \
        -exec ln -s '{}' /usr/local/lib/agentcall/ \; \
    && python -c 'import aiohttp,websockets,pydantic; from livekit import rtc; import agentcall_secretary_voice, agentcall_realtime_bridge' \
    && /opt/universal-userio/.venv/bin/python -c 'from livekit import rtc; from fast_agent.core.fastagent import FastAgent' \
    && pip check
ENTRYPOINT ["python", "/opt/universal-userio/scripts/voice_container.py"]
CMD ["serve"]

# Reuse the verified portable runtime; only controller and published SDK adapter change.
FROM ghcr.io/megamen32/universal-userio-voice@sha256:4c280012e94f4a19beea807cb056436053941e4ab1951266473d3ab3ef2dafcc AS portable-voice-controller
ARG USERIO_REVISION
LABEL org.opencontainers.image.revision=$USERIO_REVISION
COPY scripts /opt/universal-userio/scripts
COPY deploy/containers/external/agentcall_secretary_voice.py /usr/local/lib/agentcall/agentcall_secretary_voice.py
COPY deploy/containers/portable-release.json /opt/universal-userio/.userio-portable-release.json
RUN python /opt/universal-userio/scripts/voice_container.py payload
