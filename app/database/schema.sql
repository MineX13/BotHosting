-- ============================================================
-- MineNodes Bot Hoster — PostgreSQL Schema
-- ============================================================
-- Run once on fresh database. Idempotent (IF NOT EXISTS).
-- ============================================================

-- Status enum for bots
DO $$ BEGIN
    CREATE TYPE bot_status AS ENUM ('building', 'running', 'stopped', 'crashed', 'error');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- Runtime enum
DO $$ BEGIN
    CREATE TYPE bot_runtime AS ENUM ('python', 'node');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- ── Users ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              BIGINT PRIMARY KEY,          -- Discord user ID
    suspended       BOOLEAN NOT NULL DEFAULT FALSE,
    max_bots        INTEGER NOT NULL DEFAULT 3,  -- Max bots allowed
    max_ram_mb      INTEGER NOT NULL DEFAULT 512,-- Max RAM per bot (MB)
    max_cpu         REAL NOT NULL DEFAULT 0.5,   -- Max CPU per bot
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Bots ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            VARCHAR(128) NOT NULL,
    container_name  VARCHAR(256) NOT NULL UNIQUE,
    encrypted_token BYTEA NOT NULL,
    runtime         bot_runtime NOT NULL DEFAULT 'python',
    status          bot_status NOT NULL DEFAULT 'building',
    bot_path        TEXT NOT NULL,
    ram_limit_mb    INTEGER NOT NULL DEFAULT 512,
    cpu_limit       REAL NOT NULL DEFAULT 0.5,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_bots_user_id ON bots(user_id);
CREATE INDEX IF NOT EXISTS idx_bots_status ON bots(status);
CREATE INDEX IF NOT EXISTS idx_bots_container_name ON bots(container_name);

-- ── Trigger: auto-update updated_at ─────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$ BEGIN
    CREATE TRIGGER trg_users_updated_at
        BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION update_updated_at();
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TRIGGER trg_bots_updated_at
        BEFORE UPDATE ON bots
        FOR EACH ROW EXECUTE FUNCTION update_updated_at();
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;
