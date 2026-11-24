# ⚡ Cloud Task Orchestrator

[![CI/CD Pipeline](https://github.com/ainan-codes/cloud-task-orchestrator/actions/workflows/ci.yml/badge.svg)](https://github.com/ainan-codes/cloud-task-orchestrator/actions)
[![Python 3.11](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=flat&logo=react&logoColor=black)](https://react.dev)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Supabase-3ECF8E?style=flat&logo=postgresql&logoColor=white)](https://supabase.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

**Cloud Task Orchestrator** is an enterprise-grade, event-driven distributed task execution and asset synchronization engine designed for high-throughput scheduled batch processing and fault-tolerant media workflows.

---

## 🏛 System Architecture

- **Distributed Task Scheduler**: High-throughput queue management with strict idempotency and deduplication guarantees.
- **Asynchronous Cloud Asset Ingestion**: Concurrent stream processing and chunked multipart synchronization with cloud storage.
- **Circuit Breaker & Self-Healing Watchdog**: Independent fault isolation preventing cascading failures across multi-tenant dispatch pools.
- **Encrypted Credential Vault**: Authenticated symmetric AES-128 encryption at rest for external integration secrets.
- **Live Editorial & Monitoring Dashboard**: Real-time React SPA with telemetry, queue reflow, and comprehensive audit ledger.

---

## 🚀 Quickstart Guide

### Prerequisites
- Python 3.11+
- Node.js 18+
- PostgreSQL Database (e.g. Supabase)

### 1. Clone & Environment Setup
```bash
git clone https://github.com/ainan-codes/cloud-task-orchestrator.git
cd cloud-task-orchestrator
```

Configure `.env`:
```ini
DATABASE_URL=postgresql://user:password@host:port/database
RUN_TOKEN=your_secure_run_token
```

### 2. Backend Service
```bash
cd backend
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --port 8000
```

### 3. Frontend Dashboard
```bash
cd frontend
npm install
npm run dev
```

---

## 🔐 Security & Integrity
- **Encrypted Secrets Vault**: Integration credentials encrypted at rest using Fernet AES-128 with HMAC validation.
- **Zero Leakage**: Tokens and credentials stripped from all client-facing responses and serializations.
- **Strict Deduplication**: Hash-based verification preventing duplicate task execution.

---

## 📄 License
This project is licensed under the MIT License.
