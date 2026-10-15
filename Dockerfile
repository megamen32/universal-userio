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
RUN printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d \
    && chmod 755 /usr/sbin/policy-rc.d \
    && apt-get update && apt-get install -y --no-install-recommends asterisk \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/local/bin/python3 /usr/bin/python3
RUN pip install --no-deps --no-cache-dir . \
    && mkdir -p /home/roomhacker/.cargo/bin \
    && ln -s /opt/userio-migration-deps/himalaya /home/roomhacker/.cargo/bin/himalaya \
    && chmod 755 /usr/local/bin/universal-userio-gmail-password
STOPSIGNAL SIGTERM
CMD ["python", "-m", "universal_userio.container_runtime"]

FROM nginx:stable-alpine AS frontend
RUN sed -i 's/worker_processes  auto;/worker_processes  1;/' /etc/nginx/nginx.conf
COPY src/universal_userio/static /usr/share/nginx/html
COPY deploy/containers/frontend.conf /etc/nginx/conf.d/default.conf
