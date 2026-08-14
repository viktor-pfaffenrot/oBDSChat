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
