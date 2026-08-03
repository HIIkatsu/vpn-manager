# 🛡️ AnKo VPN — VLESS/REALITY Subscription Router & Node Manager

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi&logoColor=white)
![Xray-core](https://img.shields.io/badge/Xray--core-VLESS--REALITY-green?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-blue?style=for-the-badge)

**AnKo VPN** is an enterprise-grade backend management service and subscription router for **Xray-core** (VLESS + XTLS-Vision + REALITY protocol). 

It provides automated client config generation, rate-limited subscription webhooks, multi-node synchronization via gRPC/SSH, Telegram bot integration, and web administration panels.

---

## 🌟 Key Features

* **🔑 VLESS / REALITY Protocol Generator**: Dynamic generation of VLESS + REALITY obfuscated configs with custom SNI masking (`www.samsung.com`), short IDs, and XTLS-Vision flow.
* **📱 Universal Client Deeplinks**: Automatic Base64 subscription headers and deeplinks compatible with **Hiddify**, **V2RayN/V2RayNG**, **Sing-Box**, **NekoBox**, and **Shadowrocket**.
* **⚡ Rate-Limited Async Router**: High-throughput FastAPI backend (`subscription_router.py`) featuring HMAC-signed subscription tokens and shared sliding-window rate limiting.
* **🌐 Automated Multi-Node Sync**: Real-time Xray node state synchronization (`node_sync.py`, `xray_manager.py`) across distributed server instances via gRPC.
* **🤖 Telegram & Billing Integration**: Direct integration with Telegram bots for automated user onboarding, subscription renewal, traffic quotas, and payment processing.

---

## 📂 Project Architecture

```
vpn-manager/
├── subscription_router.py      # Core FastAPI Subscription Webhook Router
├── security.py                 # Cryptographic security, token signing & rate limiter
├── bootstrap.html              # Management Web UI template
├── deploy/                     # Infrastructure deployment & systemd service scripts
└── remote_app/                 # Backend system & microservices
    ├── grpc/                   # Xray gRPC API protocol buffer definitions
    ├── runtime/                # Background sync workers & entrypoints
    ├── services/               # Node synchronization, billing & user lifecycle
    ├── static/                 # Cabinet & Admin panel CSS/JS assets
    └── templates/              # Admin dashboard & User Cabinet templates
```

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.10+
- Xray-core installed on target server nodes

### 2. Install Dependencies
```bash
pip install fastapi uvicorn sqlalchemy aiohttp aiogram jinja2 python-dotenv
```

### 3. Launch Router
```bash
uvicorn subscription_router:router --host 0.0.0.0 --port 8000
```

---

## 📄 License
Distributed under the MIT License. See `LICENSE` for details.
