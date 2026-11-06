"""Heading-first Markdown chunking that never cuts a top-level syntax block."""
import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import tiktoken
import yaml
from markdown_it import MarkdownIt

PIPELINE = 'kubernetes-markdown-v1'
MAX_INPUT_TOKENS = 7500


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict


@dataclass
class Document:
    source: str
    document_id: str
    sha256: str
    chunks: list[Chunk]


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def chunk_document(path: Path, repo_root: Path, target_tokens: int = 1000,
                   source_base_url: str = '') -> Document:
    if not 100 <= target_tokens <= MAX_INPUT_TOKENS:
        raise ValueError(f'target_tokens must be between 100 and {MAX_INPUT_TOKENS}.')
    raw = path.read_text(encoding='utf-8-sig')
    lines = raw.splitlines(keepends=True)
    frontmatter = {}
    offset = 0
    if lines and lines[0].strip() == '---':
        closing = next((i for i in range(1, len(lines)) if lines[i].strip() == '---'), None)
        if closing is None:
            raise ValueError(f'Unclosed front matter: {path.name}')
        frontmatter = yaml.safe_load(''.join(lines[1:closing])) or {}
        if not isinstance(frontmatter, dict):
            raise ValueError(f'Front matter must be a mapping: {path.name}')
        offset = closing + 1
    source = path.resolve().relative_to(repo_root.resolve()).as_posix()
    document_id = str(frontmatter.get('document_id') or source)
    title = str(frontmatter.get('title') or path.stem)
    symptoms = frontmatter.get('symptoms') or []
    if not isinstance(symptoms, list) or not all(isinstance(item, str) for item in symptoms):
        raise ValueError(f'symptoms must be a list of strings: {source}')
    body_lines = lines[offset:]
    tokens = MarkdownIt('commonmark').enable('table').parse(''.join(body_lines))
    encoder = tiktoken.get_encoding('cl100k_base')
    count = lambda text: len(encoder.encode(text, disallowed_special=()))
    headings = []
    heading_line = None
    pending = []
    chunks = []
    file_hash = digest(raw)

    def render(blocks):
        body = ''.join(body_lines[blocks[0][0]:blocks[-1][1]]).strip()
        section_path = ' > '.join(text for _, text in headings) or title
        return f'{title}\nSection: {section_path}\n\n{body}'

    def flush():
        if not pending:
            return
        text = render(pending)
        token_count = count(text)
        if token_count > MAX_INPUT_TOKENS:
            raise ValueError(f'{source}:{pending[0][0] + offset + 1}: a structural block exceeds '
                             f'{MAX_INPUT_TOKENS} tokens. Split that section/block in the source document.')
        index = len(chunks)
        content_hash = digest(text)
        point_id = str(uuid5(NAMESPACE_URL, f'{PIPELINE}:{document_id}:{index}:{content_hash}'))
        metadata = {
            'pipeline': PIPELINE, 'source': source, 'document_id': document_id,
            'document_title': title, 'document_sha256': file_hash,
            'heading_path': [text for _, text in headings],
            'section_title': headings[-1][1] if headings else title,
            'heading_line': heading_line,
            'line_start': pending[0][0] + offset + 1,
            'line_end': pending[-1][1] + offset,
            'chunk_index': index, 'chunk_sha256': content_hash,
            'token_count': token_count, 'target_tokens': target_tokens,
            'oversized_block': token_count > target_tokens,
            'resource': str(frontmatter.get('resource') or ''),
            'symptoms': symptoms,
            'reviewed_on': str(frontmatter.get('reviewed_on') or ''),
            'source_format': 'markdown', 'chunking_method': 'heading-and-block',
            'source_url': source_base_url.rstrip('/') + '/' + source if source_base_url else None,
        }
        chunks.append(Chunk(point_id, text, metadata))
        pending.clear()

    for i, token in enumerate(tokens):
        # Top-level maps span whole paragraphs, lists, tables, fences, or quotes.
        # Nested headings (including text inside fenced code) are not boundaries.
        if token.level != 0 or token.map is None or token.nesting == -1:
            continue
        if token.type == 'heading_open':
            flush()
            level = int(token.tag[1:])
            heading = tokens[i + 1].content
            while headings and headings[-1][0] >= level:
                headings.pop()
            headings.append((level, heading))
            heading_line = token.map[0] + offset + 1
            if level == 1 and not frontmatter.get('title'):
                title = heading
            continue
        block = tuple(token.map)
        if pending and count(render(pending + [block])) > target_tokens:
            flush()
        pending.append(block)
    flush()
    if not chunks:
        raise ValueError(f'No document content to ingest: {source}')
    for chunk in chunks:
        chunk.metadata['chunk_count'] = len(chunks)
    return Document(source, document_id, file_hash, chunks)


def load_documents(directory: Path, repo_root: Path, target_tokens: int = 1000,
                   source_base_url: str = '') -> list[Document]:
    if not directory.is_dir():
        raise ValueError(f'Document directory does not exist: {directory}')
    paths = sorted(directory.rglob('*.md'))
    if not paths:
        raise ValueError('No Markdown documents found.')
    documents = [chunk_document(path, repo_root, target_tokens, source_base_url) for path in paths]
    ids = [doc.document_id for doc in documents]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate document_id values found; give each document a unique ID.')
    return documents
