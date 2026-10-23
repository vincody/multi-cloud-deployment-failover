CREATE TABLE IF NOT EXISTS issues (
    id UUID PRIMARY KEY, number BIGSERIAL UNIQUE NOT NULL,
    title TEXT NOT NULL, description TEXT NOT NULL, author TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('open','closed')),
    version INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS comments (
    id UUID PRIMARY KEY, issue_id UUID NOT NULL REFERENCES issues(id),
    author TEXT NOT NULL, body TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS comments_issue_idx ON comments(issue_id, created_at);
CREATE TABLE IF NOT EXISTS issue_history (
    id BIGSERIAL PRIMARY KEY, issue_id UUID NOT NULL REFERENCES issues(id),
    request_id UUID NOT NULL UNIQUE, action TEXT NOT NULL,
    before_data JSONB, after_data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS issue_history_issue_idx ON issue_history(issue_id, id);
CREATE TABLE IF NOT EXISTS save_requests (
    request_id UUID PRIMARY KEY, payload JSONB NOT NULL, result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS probe_pings (
    run_id TEXT NOT NULL, seq INTEGER NOT NULL, environment TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (run_id, seq));
