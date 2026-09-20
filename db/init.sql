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

CREATE TABLE IF NOT EXISTS sources (
    id UUID PRIMARY KEY,
    source_family TEXT NOT NULL CHECK (
        source_family IN ('umsetzungsleitfaden', 'manual_plus')
    ),
    origin TEXT NOT NULL CHECK (length(origin) > 0),
    native_kind TEXT NOT NULL CHECK (native_kind IN ('page', 'attachment')),
    native_id TEXT NOT NULL CHECK (length(native_id) > 0),
    original_content BYTEA NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (
        content_sha256 = encode(sha256(original_content), 'hex')
    ),
    media_type TEXT NOT NULL CHECK (media_type IN ('text/html', 'application/pdf')),
    source_revision TEXT,
    title TEXT NOT NULL CHECK (length(title) > 0),
    url TEXT NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    source_modified_at TIMESTAMPTZ,
    publication_edition TEXT,
    decision_date DATE,
    applicability_date DATE,
    metadata JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(metadata) = 'object'),
    UNIQUE (source_family, origin, native_kind, native_id)
);

CREATE TABLE IF NOT EXISTS evidence_blocks (
    id UUID PRIMARY KEY,
    source_id UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    extraction_version TEXT NOT NULL CHECK (length(extraction_version) > 0),
    local_key TEXT NOT NULL CHECK (length(local_key) > 0),
    kind TEXT NOT NULL CHECK (kind IN ('heading', 'paragraph', 'table', 'diagram')),
    content TEXT NOT NULL CHECK (length(content) > 0),
    locator JSONB NOT NULL CHECK (jsonb_typeof(locator) = 'object'),
    structure JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(structure) = 'object'),
    UNIQUE (source_id, local_key)
);

COMMENT ON TABLE sources IS
'Current original and metadata per native page or attachment; replaced in place.';
COMMENT ON TABLE evidence_blocks IS
'Current extracted evidence; replacement uses new IDs and removes old blocks.';
