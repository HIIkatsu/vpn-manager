# 🛡️ VPN Manager & Node Subscription Router

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi&logoColor=white)
![Security](https://img.shields.io/badge/Security-Hardened-red?style=for-the-badge)
![License](https://img.shields.io/badge/License-MIT-blue?style=for-the-badge)

A robust backend service and management utility for enterprise VPN infrastructure, dynamic node routing, subscriber access control, and automated server synchronization.

---

## 🌟 Features

* **🌐 Multi-Region Node Management**: Dynamic subscription generation and load balancing across multi-region server infrastructure.
* **🔒 Security & SNI Hardening**: Built-in SNI mask configuration, obfuscation tools, and security auditing (`security.py`).
* **🤖 Telegram Integration**: Automated distribution of access keys and subscriptions via Telegram Bot integrations (`update_tg_free.py`).
* **⚡ Live Synchronization**: Webhook and SSH deployment scripts (`ssh_run.py`, `upload.py`) for instantaneous config pushes across distributed server fleets.

---

## 📂 Project Overview

```
vpn-manager/
├── deploy/                     # Deployment scripts & systemd services
├── remote_app/                 # Remote application modules
├── security.py                 # Cryptographic security & audit helpers
├── subscription_router.py      # Core Subscription Router API backend
├── update_router.py            # Node configuration updater
├── ssh_run.py                  # Remote node SSH manager
├── bootstrap.html              # Management Web UI panel
└── README.md                   # Project documentation
```

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.10+

### 2. Setup Environment
```bash
pip install fastapi uvicorn requests python-dotenv paramiko
```

### 3. Run Subscription Router
```bash
python subscription_router.py
```

---

## 📄 License
Distributed under the MIT License. See `LICENSE` for details.
