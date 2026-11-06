# Kubernetes RAG foundations

This repository owns Qdrant, its reusable Python connection, and the
ImagePullBackOff and OOMKilled guides in docs/troubleshooting/.
Document ingestion, embedding generation, retrieval, and a RAG API have not
been implemented yet. Nothing here requires the MCP server or chat app.

## Local setup (PowerShell)

```powershell
cd C:\Users\kusum\Documents\Final_Project\RAG
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Fresh clone only: Copy-Item .env.example .env, then set your API key.
docker compose up -d qdrant
.\.venv\Scripts\python.exe qdrant_connection.py
```

Your local .env has already been migrated and remains Git-ignored. Qdrant REST
is at http://127.0.0.1:6333 and the dashboard at http://127.0.0.1:6333/dashboard.
Use QDRANT_API_KEY from .env when prompted. API requests use the api-key header.
get_qdrant_client() loads this repository's .env. Close the returned client
after use. The connection check reads the collection list without writing data.

## Persistent storage

The ignored compose.override.yaml on this machine reuses the pre-migration
Qdrant storage and snapshot volumes. Fresh clones create new named volumes.
The old volume names do not depend on the old checkout remaining present.
`docker compose stop qdrant` retains data. Avoid deleting database volumes.
Only the localhost REST port is published. PostgreSQL belongs to kube-autopilot,
not this repository. This project has no OpenAI key until embedding ingestion
is explicitly added later.
