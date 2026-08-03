# Master Architecture & Deep Audit Report
**Project:** VPN Manager V2 Ecosystem
**Generated:** 2026-06-25
**Scope:** 4 Servers (Helsinki, Moscow, Frankfurt, Amsterdam)

---

## 📌 1. Introduction & AI Agent Context
This document is the **single source of truth** for the VPN infrastructure. 
**For future AI Agents:** Read this file carefully before making any architectural or code changes. It contains the exact mappings of network ports, systemd services, and data flows. Pay particular attention to the **Network & Ports** section to avoid conflicts.

---

## 🔑 2. Server Credentials

| Location | IP Address | User | Password |
| :--- | :--- | :--- | :--- |
| **Helsinki** (Main) | `150.251.152.174` | `root` | `Mn52YqRYF88Aa` |
| **Moscow** (Edge) | `132.243.230.173` | `root` | `gwYCjg3WSTvNj` |
| **Frankfurt** (Edge) | `132.243.194.119` | `root` | `Q6umygDjKnjvA` |
| **Amsterdam** (Edge)| `194.50.94.177` | `root` | `Ke5fcHqd7SaDE` |

---

## 🌍 3. Global Architecture Overview

The system operates in a **Hub-and-Spoke (Main-to-Edge) topology**.

*   **Helsinki (Main/Brain Node):** Handles all business logic, the Telegram Bot, REST API, Billing (YooKassa/CryptoBot), Postgres Database, Redis cache, and user synchronization logic. It also acts as an Xray node (with Cloudflare WARP proxying).
*   **Edge Nodes (Moscow, Frankfurt, Amsterdam):** Act purely as traffic transit nodes. They run Xray and an active receiver API (`xray-node-receiver`) that listens for configuration updates (new users, deleted users) pushed from Helsinki.

### 🔄 State Synchronization Flow
1. User buys a subscription via the Telegram Bot (handled by `vpn-bot` and `vpn-api` on Helsinki).
2. The Database (Postgres on Helsinki) is updated.
3. The `vpn-xray-sync` daemon (on Helsinki) detects the change and pushes the updated client list over HTTP/gRPC.
4. The `xray-node-receiver` (port 8090 on Edge nodes) receives the update and injects the client into the local `xray` process via the Xray gRPC API (port 10085).

---

## 🧠 4. Central Node: Helsinki (150.251.152.174)

The Helsinki server (`neurosmmai.ru`) is the core of the project.

### 4.1. Systemd Services (Helsinki)
| Service Name | Description | State | Port / Interface |
| :--- | :--- | :--- | :--- |
| `nginx.service` | Reverse proxy (Stream & HTTP) | **Active** | `80`, `443`, `7443` |
| `docker.service` | Runs Postgres & Uptime Kuma | **Active** | `5433` (DB), `3001` (Kuma)|
| `pgbouncer.service` | Postgres connection pooler | **Active** | `127.0.0.1:6432` |
| `redis-server.service` | Redis Cache | **Active** | `127.0.0.1:6379` |
| `vpn-api.service` | FastAPI Backend (Gunicorn/Uvicorn) | **Active** | `0.0.0.0:8001` |
| `vpn-bot.service` | Telegram Bot daemon | **Active** | N/A (Webhooks/Polling) |
| `vpn-worker.service` | Background tasks daemon | **Active** | N/A |
| `vpn-xray-sync.service` | Pushes configs to Edge nodes | **Active** | N/A |
| `xray.service` | Main Xray daemon | **Active** | `10085` (API), multiple VLESS |
| `warp-svc.service` | Cloudflare WARP proxy | **Active** | `127.0.0.1:40000` (SOCKS) |

*(Note: `vpn-alert`, `vpn-backup`, and `vpn-billing` timers/services are present but were in a failed state during audit).*

### 4.2. Network & Port Routing (Helsinki)
Helsinki uses an advanced **Nginx Stream SNI Routing** to multiplex traffic on port `443` before passing it to Xray or the Web API.

**Nginx Stream Routing (`/etc/nginx/nginx.conf`):**
*   SNI `wikipedia.org` ➔ Xray `127.0.0.1:443`
*   SNI `yahoo.com` ➔ Xray `127.0.0.1:8446`
*   SNI `vk.com` ➔ Xray `127.0.0.1:10445`
*   SNI `ya.ru` ➔ Xray `127.0.0.1:10444`
*   SNI `samsung.com` ➔ Xray `127.0.0.1:10446` *(Note: Xray actually listens on `40443`, `20443`, `30443` for WARP routes, potential misconfig here)*
*   `default` ➔ Nginx HTTP `127.0.0.1:7443`

**Nginx HTTP Routing (`127.0.0.1:7443` -> `neurosmmai.ru`):**
*   `/webhook/`, `/api/`, `/setup`, `/admin`, `/cabinet` ➔ `127.0.0.1:8001` (`vpn-api`)
*   `/vpn-admin/` ➔ `127.0.0.1:8010`
*   `/vpn-user-.../` ➔ `127.0.0.1:8011`
*   `/` ➔ `127.0.0.1:8000`

**IPTables:**
*   Port `20443` redirects to `443` (`REDIRECT --to-ports 443`).

### 4.3. Xray Configuration (`/usr/local/etc/xray/config.json`)
*   **Inbounds:**
    *   `10085`: `dokodemo-door` (API)
    *   `443`: `vless-smart` (SNI: `wikipedia.org`)
    *   `8446`: `vless-euro2` (SNI: `yahoo.com`)
    *   `10444`: `vless-ru-clean` (SNI: `ya.ru`)
    *   `10445`: `vless-ru-whitelist` (SNI: `vk.com`)
    *   `40443`: `vless-warp-se` (SNI: `samsung.com`)
    *   `20443`: `vless-warp-yt` (SNI: `yahoo.com`)
    *   `30443`: `vless-warp-gpt` (SNI: `wikipedia.org`)
*   **Outbounds:**
    *   `direct`: `freedom`
    *   `proxy-ipv6`: `socks` targeting `127.0.0.1:40000` (Cloudflare WARP)
    *   `block`: `blackhole`
*   **Routing Rules:** Traffic to ChatGPT, Gemini, Claude, OpenAI is routed through the `proxy-ipv6` (WARP) outbound.

### 4.4. Application Structure (`/root/vpn-manager-v2`)
The backend is a modern Python stack using FastAPI, SQLAlchemy (Async), and Aiogram 3.
*   `app/api/`: FastAPI web server.
*   `app/bot/`: Telegram Bot (Aiogram).
*   `app/db/`: SQLAlchemy AsyncPG models and Repositories.
*   `app/grpc/`: Xray gRPC client for manipulating users.
*   `.env`: Holds all credentials (DB, Telegram, CryptoBot, YooKassa, Xray Reality keys).

---

## 🛡️ 5. Edge Nodes (Moscow, Frankfurt, Amsterdam)

The Edge nodes are streamlined. They do not run the database or Telegram bots.

### 5.1. Edge Services & Ports
| Service Name | Description | Port |
| :--- | :--- | :--- |
| `xray.service` | Main Xray daemon | `443`, `10444`, `10445` |
| `xray-node-receiver.service`| Uvicorn API receiving pushes from Helsinki| `0.0.0.0:8090` |
| `hysteria-server.service` | **(Amsterdam only)** Hysteria protocol | `34512` (UDP) |

### 5.2. Xray Configuration (Edge Nodes)
Edge nodes receive connections and either route them directly or load balance them back to the EU infrastructure.
*   **API:** `10085` (`dokodemo-door`)
*   **Inbounds:**
    *   `443`: `vless-smart-transit` (SNI: `samsung.com`)
    *   `10444`: `vless-ru-clean` (SNI: `ya.ru`)
    *   `10445`: `vless-ru-whitelist` (SNI: `vk.com`)
*   **Outbounds:**
    *   `eu-fin` (150.251.152.174)
    *   `eu-ger` (132.243.194.119)
    *   `eu-nl` (194.50.94.177)
*   **Routing:**
    *   Russian domains/IPs (`geoip:ru`, `domain:ru`, etc.) are routed `direct`.
    *   Other traffic hits an `eu-balancer` strategy, proxying traffic randomly across the EU outbounds (Finland, Germany, NL).

### 5.3. The Receiver Daemon (`xray-node-receiver`)
Runs via Systemd. Located within `/root/vpn-manager-v2/` utilizing the `deploy.active_push.xray_node_receiver` module.
It listens on port `8090` and requires the `SYNC_NODES_TOKEN` defined in Helsinki's `.env`.

---

## 🔑 6. Critical Environment Variables (`.env`)
Found in `/root/vpn-manager-v2/.env` on Helsinki:
*   `DATABASE_URL=postgresql+asyncpg://vpn_user:...@localhost:5433/vpn_v2`
*   `REDIS_URL=redis://127.0.0.1:6379/0`
*   `XRAY_GRPC_PORT=10085`
*   `SYNC_NODES_TOKEN` = Used to authenticate pushes to Edge nodes.
*   `SYNC_PUSH_NODES` = JSON array containing Edge node IP and port 8090 endpoints.
*   `VLESS_SNI`, `VLESS_PUBLIC_KEY`, `VLESS_SHORT_ID`, `XRAY_REALITY_PRIVATE_KEY` = Security keys for the Reality protocol.

---

## 🛠️ 7. Guidelines for AI Agents modifying this repo
1. **Network Changes:** Any new service must be mapped in the Nginx Stream configuration on Helsinki if it requires port 443. Check for port conflicts using this document.
2. **Xray Modifications:** Be aware of the distinction between the Helsinki Xray config (contains WARP outbounds) and Edge Xray configs (contains balancer outbounds).
3. **Database Changes:** The database is in Docker (`vpn_v2_db` running on port 5433), but accessed via PgBouncer (`6432`) locally. Migrations must use Alembic (`alembic upgrade head`).
4. **Synchronization:** Adding or removing a user must happen through the DB *and* trigger an Xray Sync. Do not manually edit `config.json` for user management. Use the gRPC API or rely on the `vpn-xray-sync` worker.
5. **Restarts:** If modifying Python backend code, restart `vpn-api.service`, `vpn-bot.service`, or `vpn-worker.service` accordingly. If modifying edge receiver code, restart `xray-node-receiver.service`.
