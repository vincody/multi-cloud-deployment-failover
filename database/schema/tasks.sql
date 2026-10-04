CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('todo','doing','done')),
    version INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS task_history (
    id BIGSERIAL PRIMARY KEY, task_id UUID NOT NULL REFERENCES tasks(id),
    request_id UUID NOT NULL UNIQUE, before_data JSONB, after_data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS save_requests (
    request_id UUID PRIMARY KEY, payload JSONB NOT NULL, result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now());
