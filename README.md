# MongoDB Hackathon

Python 3.11 backend for a fixture-driven payment approval pipeline. No UI or API
keys are required for local development. Payments are database receipts; no money
is sent by this prototype.

```sh
uv sync --python 3.11
docker compose up -d --wait
cp .env.example .env
```

An empty `MONGODB_URI` uses the local single-node `rs0` replica set. Local MongoDB
is exposed only on loopback. Real credentials belong in ignored `.env` files.
The existing `.env.secrets` file is not loaded automatically.
