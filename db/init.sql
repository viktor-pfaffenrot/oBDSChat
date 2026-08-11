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
USING GIN (
    (
        setweight(to_tsvector('german', title), 'A')
        || setweight(to_tsvector('german', COALESCE(section, '')), 'B')
        || setweight(to_tsvector('german', content), 'C')
    )
);
