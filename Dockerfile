# ── builder ─────────────────────────────────────────────────────────────────
# Builds the bot for Linux. native-tls links against the system OpenSSL, so the
# builder needs libssl-dev (and the runtime needs libssl3 + ca-certificates).
FROM rust:1-bookworm AS builder
WORKDIR /app
RUN apt-get update \
 && apt-get install -y --no-install-recommends pkg-config libssl-dev ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY Cargo.toml Cargo.lock ./
COPY src ./src
RUN cargo build --release

# ── runtime ─────────────────────────────────────────────────────────────────
FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates libssl3 \
 && rm -rf /var/lib/apt/lists/*
# Persistent state (dedup set + brownfox markets). Mount a volume here on AWS.
RUN mkdir -p /data
ENV COPY_DATA_DIR=/data
WORKDIR /app
COPY --from=builder /app/target/release/polybotshadow /usr/local/bin/polybotshadow
EXPOSE 8090
ENTRYPOINT ["polybotshadow"]
