"""Ingest structured Markdown chunks into Qdrant using OpenAI embeddings."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import models

from chunking import PIPELINE, load_documents
from qdrant_connection import get_qdrant_client

ROOT = Path(__file__).resolve().parent
from embedding_config import MODEL, DIMENSIONS, VECTOR_NAME


def ensure_collection(client, collection):
    if not client.collection_exists(collection):
        client.create_collection(collection_name=collection, vectors_config={
            VECTOR_NAME: models.VectorParams(size=DIMENSIONS, distance=models.Distance.COSINE)
        })
    info = client.get_collection(collection)
    vectors = info.config.params.vectors
    if (not isinstance(vectors, dict) or VECTOR_NAME not in vectors
            or vectors[VECTOR_NAME].size != DIMENSIONS
            or vectors[VECTOR_NAME].distance != models.Distance.COSINE):
        raise ValueError(f'Collection {collection} has incompatible vectors. Use a new collection; '
                         'existing data will not be recreated or deleted.')
    fields = ('document_id', 'source', 'pipeline', 'resource', 'symptoms', 'embedding_model')
    for field in fields:
        key = 'metadata.' + field
        if key not in info.payload_schema:
            client.create_payload_index(collection, field_name=key,
                                        field_schema=models.PayloadSchemaType.KEYWORD, wait=True)


def document_filter(document):
    return models.Filter(must=[
        models.FieldCondition(key='metadata.pipeline', match=models.MatchValue(value=PIPELINE)),
        models.FieldCondition(key='metadata.document_id', match=models.MatchValue(value=document.document_id)),
    ])


def existing_chunks(client, collection, document):
    points = []
    offset = None
    while True:
        page, offset = client.scroll(collection_name=collection,
                                     scroll_filter=document_filter(document),
                                     limit=100, offset=offset, with_payload=True, with_vectors=False)
        points.extend(page)
        if offset is None:
            return points



def print_chunks(points, label):
    """Print content and metadata, excluding the large embedding vectors."""
    ordered = sorted(points, key=lambda point: point.payload['metadata']['chunk_index'])
    for point in ordered:
        print(f"\n--- {label}: chunk {point.payload['metadata']['chunk_index'] + 1} ---")
        print(json.dumps({'id': str(point.id), **point.payload}, indent=2, ensure_ascii=True))


def ingest_document(client, embeddings, collection, document, force=False):
    previous = existing_chunks(client, collection, document)
    expected_ids = {chunk.id for chunk in document.chunks}
    unchanged = {str(point.id) for point in previous} == expected_ids and all(
        (point.payload or {}).get('metadata', {}).get('document_sha256') == document.sha256
        and (point.payload or {}).get('metadata', {}).get('embedding_model') == MODEL
        and (point.payload or {}).get('metadata', {}).get('embedding_dimensions') == DIMENSIONS
        and (point.payload or {}).get('metadata', {}).get('target_tokens') == document.chunks[0].metadata['target_tokens']
        and (point.payload or {}).get('metadata', {}).get('source') == document.source
        and (point.payload or {}).get('metadata', {}).get('source_url') == document.chunks[0].metadata['source_url']
        for point in previous
    )
    if unchanged and not force:
        print(f'Skipped unchanged: {document.source} ({len(document.chunks)} chunks)')
        print_chunks(previous, 'Stored in Qdrant')
        return 0
    # Complete embedding generation before changing this document's Qdrant points.
    vectors = []
    for start in range(0, len(document.chunks), 32):
        batch = document.chunks[start:start + 32]
        response = embeddings.embeddings.create(
            model=MODEL, input=[chunk.text for chunk in batch],
            dimensions=DIMENSIONS, encoding_format='float',
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in ordered] != list(range(len(batch))):
            raise ValueError('Embedding response is missing or duplicating input indexes.')
        if any(len(item.embedding) != DIMENSIONS for item in ordered):
            raise ValueError('Embedding response dimensions do not match the collection.')
        vectors.extend(item.embedding for item in ordered)
    timestamp = datetime.now(timezone.utc).isoformat()
    points = [models.PointStruct(
        id=chunk.id, vector={VECTOR_NAME: vector},
        payload={'page_content': chunk.text, 'metadata': {
            **chunk.metadata, 'embedding_model': MODEL,
            'embedding_dimensions': DIMENSIONS, 'ingested_at': timestamp,
        }},
    ) for chunk, vector in zip(document.chunks, vectors, strict=True)]
    for start in range(0, len(points), 32):
        client.upsert(collection_name=collection, points=points[start:start + 32], wait=True)
    # Remove obsolete chunks only for this pipeline and document, after upserts succeed.
    stale_ids = [point.id for point in previous if str(point.id) not in expected_ids]
    for start in range(0, len(stale_ids), 100):
        client.delete(collection_name=collection,
                      points_selector=models.PointIdsList(points=stale_ids[start:start + 100]), wait=True)
    stored = client.count(collection_name=collection, count_filter=document_filter(document), exact=True).count
    if stored != len(document.chunks):
        raise RuntimeError('Stored chunk count does not match. Avoid simultaneous ingestion runs and rerun.')
    print(f'Ingested: {document.source} ({stored} chunks; {len(stale_ids)} obsolete chunks removed)')
    print_chunks(points, 'Written to Qdrant')
    return len(points)


def main():
    load_dotenv(ROOT / '.env', override=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docs-dir', type=Path, default=ROOT / 'docs' / 'troubleshooting')
    parser.add_argument('--collection', default=os.environ.get('QDRANT_COLLECTION', 'kubernetes_troubleshooting'))
    parser.add_argument('--target-tokens', type=int, default=1000)
    parser.add_argument('--dry-run', action='store_true', help='Preview chunks without API calls or database writes.')
    parser.add_argument('--force', action='store_true', help='Re-embed unchanged documents.')
    args = parser.parse_args()
    documents = load_documents(args.docs_dir.resolve(), ROOT, args.target_tokens,
                               os.environ.get('SOURCE_BASE_URL', 'https://github.com/kube-pilot-ai/RAG/blob/main'))
    for document in documents:
        print(f'{document.source}: {len(document.chunks)} chunks')
        if args.dry_run:
            for chunk in document.chunks:
                print(f"\n--- Preview: chunk {chunk.metadata['chunk_index'] + 1} ---")
                print(json.dumps({'id': chunk.id, 'page_content': chunk.text,
                                  'metadata': {**chunk.metadata, 'embedding_model': MODEL,
                                               'embedding_dimensions': DIMENSIONS}},
                                 indent=2, ensure_ascii=True))
    if args.dry_run:
        print('Dry run complete. No embeddings requested or Qdrant data changed.')
        return
    if not os.environ.get('OPENAI_API_KEY'):
        raise ValueError('Set OPENAI_API_KEY in the RAG .env before ingestion.')
    client = get_qdrant_client()
    try:
        ensure_collection(client, args.collection)
        with OpenAI(timeout=60, max_retries=3) as embeddings:
            changed = sum(ingest_document(client, embeddings, args.collection, doc, args.force)
                          for doc in documents)
        print(f'Finished: {len(documents)} documents, {changed} chunks written to {args.collection}.')
    finally:
        client.close()


if __name__ == '__main__':
    main()
