# Server Deployment

Production target:

- Ubuntu 24.04 LTS
- Docker Compose
- App on port `8765`
- PostgreSQL 16 provisioned locally for migration readiness
- Current app data remains SQLite at `data/racing.db`

## Deploy

```bash
git clone --branch codex/horse-racing-model https://github.com/harryheung19951212-sketch/HKJC_Statistical_Model.git /opt/hkjc-model
cd /opt/hkjc-model
cp .env.production.example .env.production
openssl rand -hex 24
# Put the generated value into POSTGRES_PASSWORD in .env.production
cp .env.production .env
docker compose -f docker-compose.prod.yml up -d --build
```

Open:

```text
http://SERVER_IP:8765/
```

## Notes

- Do not commit `.env.production`, `data/racing.db`, `data/raw`, `reports`, or `models/*.json`.
- PostgreSQL is installed and running, but the app still uses SQLite until the storage adapter is upgraded.
- Codex CLI is installed in the app image. Authentication must be configured on the server/container before AI iteration can run.
