# Deploying latebutfast (Rust copytrader) on AWS

## ⚠️ READ FIRST — the region decision determines if it works at all

Polymarket **geoblocks order placement by IP**. Datacenter IPs (which is what AWS
gives you) are subject to the same regional block your Railway deploy hit:

- **US AWS regions (us-east-1, us-west-2, …) are blocked** — order POSTs return
  `403 Trading restricted in your region`. The bot will detect trades and try to
  place, but every order is rejected.
- **Reads always work** (Data API, CLOB book/tick) from any region — so you'll see
  trade detection and "DRY: would place …" even in a blocked region. That does NOT
  mean live orders will go through.
- You must deploy in a **region Polymarket allows for trading**, or route the bot's
  outbound traffic through a residential/allowed-region proxy.

The bot uses **REST polling** for the target (`DATA_SOURCE=rest`), so the WebSocket
`429` rate-limit on datacenter IPs does **not** apply here — only the order-POST
geoblock does.

**Before committing to a region: launch a tiny instance there, set `DRY_RUN=false`
+ `LIVE_SIGNING_VALIDATED=true`, and place ONE small real order. If it fills, the
region is good. If it 403s, pick another region or add a proxy.**

---

## Option A — EC2 + systemd (simplest)

### 1. Launch an instance
- AMI: **Ubuntu 22.04** or **Amazon Linux 2023**, `t3.small` is plenty.
- **Region: an allowed one** (see above).
- Security group: allow SSH (22) from your IP. Only open 8090 if you want the
  dashboard reachable (better: keep it private, view via SSH tunnel).

### 2. Install the toolchain + build deps
Ubuntu:
```bash
sudo apt update && sudo apt install -y build-essential pkg-config libssl-dev git curl
curl https://sh.rustup.rs -sSf | sh -s -- -y && source "$HOME/.cargo/env"
```
Amazon Linux 2023:
```bash
sudo dnf install -y gcc gcc-c++ openssl-devel pkgconfig git
curl https://sh.rustup.rs -sSf | sh -s -- -y && source "$HOME/.cargo/env"
```

### 3. Build (no MinGW/dlltool needed on Linux — the standard GNU toolchain works)
```bash
git clone <your-repo-url> latebutfast && cd latebutfast
cargo build --release          # produces target/release/latebutfast
```

### 4. Validate signing on the box (do this once, live)
```bash
PRIVATE_KEY=<key> FUNDER_ADDRESS=<proxy> ./target/release/latebutfast selftest
```
Run the SAME fixed inputs through py-clob-client-v2 (`ExchangeOrderBuilderV2`) and
confirm the order hash matches. Only then set `LIVE_SIGNING_VALIDATED=true`.

### 5. Install as a service
```bash
sudo useradd -r -s /usr/sbin/nologin botuser || true
sudo mkdir -p /opt/latebutfast/data
sudo cp target/release/latebutfast /opt/latebutfast/
sudo cp deploy/latebutfast.service /etc/systemd/system/
# create /opt/latebutfast/.env from .env.example (see env list below), then:
sudo chown -R botuser:botuser /opt/latebutfast
sudo chmod 600 /opt/latebutfast/.env
sudo systemctl daemon-reload
sudo systemctl enable --now latebutfast
sudo journalctl -u latebutfast -f      # watch logs
```

### 6. Secrets
Put `PRIVATE_KEY` in `/opt/latebutfast/.env` (chmod 600), **or** pull it at start
from AWS SSM Parameter Store / Secrets Manager. Never bake it into the AMI or
commit it.

---

## Option B — Docker (reproducible; ECS/Fargate or EC2)

```bash
docker build -t latebutfast .
docker run -d --name latebutfast --restart unless-stopped \
  --env-file .env \
  -v /opt/latebutfast/data:/data \
  -p 8090:8090 \
  latebutfast
```
The included `Dockerfile` builds on Linux (OpenSSL via native-tls) and ships a
slim runtime with `ca-certificates` + `libssl3`. For ECS/Fargate, push the image
to ECR, set the env vars as task definition variables (PRIVATE_KEY from Secrets
Manager), and mount an EFS volume at `/data` so state survives restarts.

**ECS/Fargate region caveat:** same geoblock rule — the task's egress region must
be allowed, or use a NAT/residential proxy.

---

## Go-live checklist
1. Region allows Polymarket trading (placed one test order — it filled).
2. pUSD funded in `FUNDER_ADDRESS`; V2 exchange allowances set.
3. `selftest` order hash == py-clob-client-v2 → `LIVE_SIGNING_VALIDATED=true`.
4. Ran with `DRY_RUN=true` first and watched the logs detect the target + log
   would-be brownfox orders correctly.
5. `/data` is on persistent storage (EBS/EFS) so dedup + brownfox state survive
   restarts.
6. Flip `DRY_RUN=false`, restart, watch `journalctl`.
