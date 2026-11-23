FROM node:24.18.0-trixie-slim@sha256:ae91dcc111a68c9d2d81ff2a17bda61be126426176fde6fe7d08ab13b7f50573 AS build

# lbug@0.14.3 ships a prebuilt addon requiring GLIBC_2.38; Debian bookworm
# provides 2.36 and fails at CLI startup. Trixie keeps build and runtime ABI aligned.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential ca-certificates cmake git python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/specd
COPY package.json package-lock.json ./
RUN npm ci --strict-allow-scripts \
    && npm cache clean --force \
    && ./node_modules/.bin/specd --version

FROM node:24.18.0-trixie-slim@sha256:ae91dcc111a68c9d2d81ff2a17bda61be126426176fde6fe7d08ab13b7f50573

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/specd /opt/specd

ENV PATH="/opt/specd/node_modules/.bin:${PATH}"
WORKDIR /workspace
ENTRYPOINT ["specd"]
