CREATE EXTENSION IF NOT EXISTS pg_search CASCADE;

CREATE TABLE IF NOT EXISTS documents (
    id BIGSERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    section TEXT,
    content TEXT NOT NULL,
    url TEXT NOT NULL,
    obds_version TEXT
);

COMMENT ON TABLE documents IS
'Searchable sections extracted from official oBDS documentation.';
COMMENT ON COLUMN documents.id IS
'Request-local row identifier and ParadeDB BM25 key field.';
COMMENT ON COLUMN documents.source_type IS
'Source family used to scope synchronization and search.';
COMMENT ON COLUMN documents.title IS
'Title of the source page or document.';
COMMENT ON COLUMN documents.section IS
'Optional heading that identifies the extracted section.';
COMMENT ON COLUMN documents.content IS
'Plain-text section content searched for grounded answers.';
COMMENT ON COLUMN documents.url IS
'Public URL of the canonical source.';
COMMENT ON COLUMN documents.obds_version IS
'Optional oBDS version; NULL marks version-independent content.';

CREATE INDEX IF NOT EXISTS documents_full_text_idx
ON documents
USING bm25 (
    id,
    (source_type::pdb.literal),
    (title::pdb.simple('stemmer=german')),
    (section::pdb.simple('stemmer=german')),
    (content::pdb.simple('stemmer=german')),
    (obds_version::pdb.literal)
)
WITH (key_field = 'id');

COMMENT ON INDEX documents_full_text_idx IS
'ParadeDB BM25 index for German full-text retrieval and literal filters.';
