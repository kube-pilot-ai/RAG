# Kubernetes RAG foundations

This repository owns Qdrant, its reusable Python connection, and the
ImagePullBackOff and OOMKilled guides in docs/troubleshooting/.
A standalone ingestion pipeline chunks Markdown by structure, generates OpenAI
embeddings, and stores them with provenance metadata in Qdrant. Retrieval is served through an independent HTTP API for the chat application. Nothing here imports code from the
MCP server or chat app.

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
not this repository. Ingestion requires OPENAI_API_KEY in this repository's Git-ignored .env.


## Structure-based ingestion

The embedding model is `text-embedding-3-small` (the official OpenAI API ID),
using 1536 dimensions and cosine distance. The Qdrant collection defaults to
`kubernetes_troubleshooting`, with a named vector `text-embedding-3-small`.
Set `QDRANT_COLLECTION` in `.env` to choose another collection. Add
`OPENAI_API_KEY` to `.env`; an existing local project key has been copied there
for this setup. No credential values belong in Git.

Preview the chunks without calling OpenAI or changing Qdrant:

```powershell
.\.venv\Scripts\python.exe ingest.py --dry-run
```

Ingest the troubleshooting guides:

```powershell
.\.venv\Scripts\python.exe ingest.py
```

Run commands from this RAG repository. Defaults for documents and configuration
are resolved relative to the source files, so the pipeline also works when
launched by an absolute script path. An explicit `--docs-dir` is resolved from
the current working directory and must point inside this repository.

### Chunk boundaries

- YAML front matter becomes document metadata and is not embedded as prose.
- Markdown headings define section boundaries and retain their hierarchy.
- Large sections are grouped at top-level paragraph, list, quote, table, or code
  block boundaries, targeting 1000 tokens per chunk.
- Code fences, tables, and lists stay intact. Heading-like text in fenced code
  does not start a new section. Each chunk includes its title and heading path
  as context for its embedding.
- A single block can exceed the target to preserve its structure, but any chunk
  above 7500 tokens fails before ingestion; edit that oversized source block
  instead of silently truncating it. `--target-tokens` changes the target.
- Markdown body line ranges refer to the original source file, including its
  front matter offset. No arbitrary character overlap is added.

### Qdrant payload

Each point contains `page_content` (the text that was embedded) and `metadata`:

| Metadata | Purpose |
| --- | --- |
| `source`, `source_url`, `document_id`, `document_title` | Source provenance and citations |
| `heading_path`, `section_title`, `heading_line`, `line_start`, `line_end` | Section hierarchy and original locations |
| `resource`, `symptoms`, `reviewed_on` | Front matter tags for filtering |
| `chunk_index`, `chunk_count`, `token_count`, `target_tokens`, `oversized_block` | Chunk organization and size |
| `document_sha256`, `chunk_sha256` | Change detection and reproducibility |
| `embedding_model`, `embedding_dimensions` | Embedding space identity |
| `pipeline`, `chunking_method`, `source_format`, `ingested_at` | Processing provenance; timestamp uses UTC |

Keyword payload indexes are created for document ID, source, pipeline, resource,
symptoms, and embedding model. `SOURCE_BASE_URL` can override the default
GitHub source URL prefix used in citations.

### Rerunning and failures

Unchanged documents are skipped without new embedding requests. Stable UUID
point IDs prevent duplicate chunks on rerun. Changed documents are embedded
before their points are written; obsolete chunks for that same document and
pipeline are removed only after successful upserts. Other documents and points
are not deleted. Files removed from disk are not automatically purged.
Use `--force` to re-embed unchanged files. Run one ingestion process at a time.

Qdrant updates are not a cross-batch transaction: an interrupted write can leave
mixed chunks until a successful rerun. An incompatible collection vector schema
raises an error; the pipeline never recreates or wipes a collection to fix it.
The OpenAI client retries transient API errors, but account access, billing, or
quota problems must be resolved before ingestion can finish. Embedding requests
send the document chunk text to OpenAI.

References: [OpenAI embedding model](https://developers.openai.com/api/docs/models/text-embedding-3-small),
[vector embeddings](https://developers.openai.com/api/docs/guides/embeddings),
and [Qdrant payload indexes](https://qdrant.tech/documentation/concepts/payload/).


### View chunks and metadata

`python ingest.py` prints each chunk's ID, complete `page_content`, and formatted
JSON metadata. For skipped documents, these details come from the existing
Qdrant payload, including the original `ingested_at` timestamp. Successful
writes print the payload that was stored. Embedding vectors are not printed.
`python ingest.py --dry-run` prints the local chunk previews and metadata without
calling OpenAI or Qdrant; previews have no ingestion timestamp.


## Retrieval API

Start the service in its own terminal (Qdrant must be running):

```powershell
.\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8003
```

RAG_AUTH_TOKEN in .env authenticates requests via Authorization: Bearer <token>.
The chat application uses the same token and RAG_SERVER_URL. Matching values
have been generated locally. Use HTTPS or a private encrypted network if hosting
on another machine. Bind to localhost for this local setup.

POST /retrieve accepts a JSON body, for example:

```json
{
  "query": "Why is my pod OOMKilled?",
  "top_k": 4,
  "min_score": 0.3,
  "symptoms": ["OOMKilled"],
  "resource": "Pod",
  "max_context_tokens": 6000
}
```

Optional document_id narrows retrieval to one guide. Multiple symptoms use OR;
document_id, resource, embedding identity, and symptom conditions combine with
AND. An empty symptoms list does not restrict symptoms. Filters match stored
metadata values exactly. The query is embedded using the same model, 1536
 dimensions, and named vector as ingestion. The collection is checked for a
compatible schema, and retrieval does not modify it.

The response includes query, collection, applied filters, context_tokens, and
matches containing id, similarity score, page_content, and metadata. Vectors
are excluded. Cosine similarity ranks candidates, top_k caps results, and
min_score excludes lower-ranked matches. The 0.3 default is a starting point,
not a calibrated confidence score. A total token budget limits returned text;
entire chunks are preserved and oversized ones can be omitted. No match returns
an empty matches list. GET /health checks Qdrant connectivity and also requires
the bearer token. Query text is sent to OpenAI for embedding generation.

The chat app invokes this API before the LLM and after tool results, passes
selected excerpts as reference data, and asks the LLM to cite source metadata.
It never imports RAG Python code directly. Retrieval itself never calls the
chat LLM or Kubernetes MCP server.
