"""Semantic retrieval from the ingested troubleshooting collection."""
import os

import tiktoken
from qdrant_client import models

from chunking import PIPELINE
from embedding_config import DIMENSIONS, MODEL, VECTOR_NAME


def retrieve_chunks(qdrant, embeddings, query, top_k=4, min_score=0.3,
                    symptoms=None, document_id=None, resource=None, max_context_tokens=6000):
    encoder = tiktoken.get_encoding('cl100k_base')
    if len(encoder.encode(query, disallowed_special=())) > 7500:
        raise ValueError('The retrieval query is too long; use fewer than 7500 tokens.')
    collection = os.environ.get('QDRANT_COLLECTION', 'kubernetes_troubleshooting')
    if not qdrant.collection_exists(collection):
        raise ValueError('The troubleshooting collection does not exist. Run ingest.py first.')
    info = qdrant.get_collection(collection)
    vectors = info.config.params.vectors
    if (not isinstance(vectors, dict) or VECTOR_NAME not in vectors
            or vectors[VECTOR_NAME].size != DIMENSIONS
            or vectors[VECTOR_NAME].distance != models.Distance.COSINE):
        raise ValueError('Collection embedding configuration does not match retrieval.')
    conditions = [
        models.FieldCondition(key='metadata.pipeline', match=models.MatchValue(value=PIPELINE)),
        models.FieldCondition(key='metadata.embedding_model', match=models.MatchValue(value=MODEL)),
        models.FieldCondition(key='metadata.embedding_dimensions', match=models.MatchValue(value=DIMENSIONS)),
    ]
    if symptoms:
        conditions.append(models.FieldCondition(key='metadata.symptoms', match=models.MatchAny(any=symptoms)))
    if document_id:
        conditions.append(models.FieldCondition(key='metadata.document_id', match=models.MatchValue(value=document_id)))
    if resource:
        conditions.append(models.FieldCondition(key='metadata.resource', match=models.MatchValue(value=resource)))
    response = embeddings.embeddings.create(model=MODEL, input=[query], dimensions=DIMENSIONS,
                                            encoding_format='float')
    vector = response.data[0].embedding
    if len(vector) != DIMENSIONS:
        raise ValueError('Query embedding has an unexpected dimension.')
    matches = qdrant.query_points(collection_name=collection, query=vector, using=VECTOR_NAME,
                                  query_filter=models.Filter(must=conditions), limit=top_k,
                                  score_threshold=min_score, with_payload=True, with_vectors=False).points
    selected = []
    used_tokens = 0
    for match in matches:
        payload = match.payload or {}
        text = payload.get('page_content', '')
        if not isinstance(text, str) or not text.strip():
            continue
        tokens = len(encoder.encode(text, disallowed_special=()))
        if used_tokens + tokens > max_context_tokens:
            continue
        selected.append({'id': str(match.id), 'score': match.score, 'page_content': text,
                         'metadata': payload.get('metadata', {})})
        used_tokens += tokens
    return {'query': query, 'collection': collection, 'matches': selected,
            'context_tokens': used_tokens,
            'filters': {'symptoms': symptoms or [], 'document_id': document_id, 'resource': resource}}
