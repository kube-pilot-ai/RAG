"""Independent authenticated RAG retrieval API."""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from secrets import compare_digest

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from openai import OpenAI
from pydantic import BaseModel, Field, field_validator

from qdrant_connection import get_qdrant_client
from retrieval import retrieve_chunks


@asynccontextmanager
async def lifespan(app):
    load_dotenv(Path(__file__).resolve().parent / '.env', override=True)
    if not os.environ.get('OPENAI_API_KEY') or not os.environ.get('RAG_AUTH_TOKEN'):
        raise RuntimeError('Set OPENAI_API_KEY and RAG_AUTH_TOKEN in the RAG .env.')
    with OpenAI(timeout=60, max_retries=3) as embeddings:
        qdrant = get_qdrant_client()
        try:
            qdrant.get_collections()
            app.state.qdrant = qdrant
            app.state.embeddings = embeddings
            yield
        finally:
            qdrant.close()


def authenticate(authorization: str = Header(default='')):
    expected = 'Bearer ' + os.environ.get('RAG_AUTH_TOKEN', '')
    if not compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(401, 'Unauthorized', headers={'WWW-Authenticate': 'Bearer'})


app = FastAPI(title='Kubernetes RAG retrieval', lifespan=lifespan,
              dependencies=[Depends(authenticate)])


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10000)
    top_k: int = Field(default=4, ge=1, le=10)
    min_score: float = Field(default=0.3, ge=-1, le=1)
    symptoms: list[str] = Field(default_factory=list, max_length=10)
    document_id: str | None = Field(default=None, max_length=300)
    resource: str | None = Field(default=None, max_length=100)
    max_context_tokens: int = Field(default=6000, ge=100, le=16000)

    @field_validator('query')
    @classmethod
    def reject_blank(cls, value):
        if not value.strip():
            raise ValueError('Enter a retrieval query.')
        return value.strip()


@app.get('/health')
def health(request: Request):
    request.app.state.qdrant.get_collections()
    return {'status': 'ok'}


@app.post('/retrieve')
def retrieve(body: RetrievalRequest, request: Request):
    try:
        return retrieve_chunks(request.app.state.qdrant, request.app.state.embeddings,
                               **body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        logging.getLogger(__name__).exception('Retrieval failed')
        raise HTTPException(502, 'Retrieval failed. Check the RAG server terminal.') from exc
