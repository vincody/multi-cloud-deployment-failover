CREATE TABLE IF NOT EXISTS app_control (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    writes_enabled BOOLEAN NOT NULL DEFAULT TRUE
);
INSERT INTO app_control(singleton,writes_enabled) VALUES(TRUE,TRUE)
ON CONFLICT(singleton) DO NOTHING;
