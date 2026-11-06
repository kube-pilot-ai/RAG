import os
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient


def get_qdrant_client() -> QdrantClient:
    """Create a REST client using the project's local environment settings.

    The caller owns the client and should close it after use.
    """
    load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
    url = os.environ.get("QDRANT_URL")
    api_key = os.environ.get("QDRANT_API_KEY")
    if not url or not api_key:
        raise RuntimeError("Set QDRANT_URL and QDRANT_API_KEY in .env.")
    return QdrantClient(url=url, api_key=api_key, timeout=15)


def main():
    client = get_qdrant_client()
    try:
        collections = client.get_collections().collections
        print("Connected to Qdrant successfully.")
        print(f"Collections: {len(collections)}")
    except Exception:
        raise SystemExit("Unable to connect to Qdrant. Check Docker, QDRANT_URL, and QDRANT_API_KEY.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
