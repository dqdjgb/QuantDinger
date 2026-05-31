# QuantDinger Deploy Context

This project is deployed to a cloud server via SSH.

## Server

- Host: `101.96.238.238`
- SSH port: `22`
- SSH user: `root`
- App directory: `/opt/quantdinger-app/src/QuantDinger`
- Frontend source directory: `/opt/quantdinger-app/src/QuantDinger-Vue`

Do not store the SSH password in this repository. Ask the operator for the secret when needed.

## Compose

Run deployment from the server app directory:

```bash
cd /opt/quantdinger-app/src/QuantDinger
python3 -m py_compile \
  backend_api_python/app/services/trading_executor.py \
  backend_api_python/app/services/pending_order_worker.py

docker compose -f docker-compose.yml -f docker-compose.upload.yml up -d --build
```

## Verification

```bash
cd /opt/quantdinger-app/src/QuantDinger
docker compose -f docker-compose.yml -f docker-compose.upload.yml ps
curl -sS http://127.0.0.1:5000/api/health
docker logs quantdinger-backend --tail 120
docker logs quantdinger-frontend --tail 80
```

## Cloud Broker Setting

This is a cloud deployment. Keep local desktop brokers disabled unless TWS/IB Gateway or MT5 is actually installed and reachable from the server:

```env
ALLOW_LOCAL_DESKTOP_BROKERS=false
```
