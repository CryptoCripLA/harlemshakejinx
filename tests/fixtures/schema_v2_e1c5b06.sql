-- JINX store schema v2 as of commit e1c5b06 (after review round 2), extracted with git show.

CREATE TABLE IF NOT EXISTS events (
    idempotency_key TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    recv_ns INTEGER NOT NULL,
    event_time_ns INTEGER NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    mint TEXT NOT NULL,
    signature TEXT NOT NULL,
    trader TEXT,
    side TEXT,
    sol_lamports INTEGER,
    token_amount INTEGER,
    v_sol_lamports INTEGER,
    v_tokens INTEGER,
    slot INTEGER,
    block_time INTEGER,
    raw_json TEXT NOT NULL,
    applied INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_unapplied ON events(applied, event_time_ns, seq);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_ns INTEGER NOT NULL,
    updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    changed_ns INTEGER NOT NULL,
    changed_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tokens (
    mint TEXT PRIMARY KEY,
    creator TEXT,
    buys INTEGER NOT NULL DEFAULT 0,
    sells INTEGER NOT NULL DEFAULT 0,
    unique_traders INTEGER NOT NULL DEFAULT 0,
    v_sol_lamports INTEGER,
    v_tokens INTEGER,
    peak_v_sol_lamports INTEGER,
    latest_time_ns INTEGER,
    latest_seq INTEGER,
    first_time_ns INTEGER,
    first_seq INTEGER
);
CREATE TABLE IF NOT EXISTS token_traders (
    mint TEXT NOT NULL,
    trader TEXT NOT NULL,
    PRIMARY KEY (mint, trader)
);
CREATE TABLE IF NOT EXISTS wallets (
    address TEXT PRIMARY KEY,
    buys INTEGER NOT NULL DEFAULT 0,
    sells INTEGER NOT NULL DEFAULT 0,
    buy_lamports INTEGER NOT NULL DEFAULT 0,
    sell_lamports INTEGER NOT NULL DEFAULT 0,
    first_time_ns INTEGER,
    last_time_ns INTEGER
);

CREATE TABLE IF NOT EXISTS positions (
    position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_intent_id TEXT NOT NULL UNIQUE,
    entry_budget_lamports INTEGER CHECK (entry_budget_lamports IS NULL OR entry_budget_lamports > 0),
    exit_intent_id TEXT,
    mint TEXT NOT NULL,
    status TEXT NOT NULL,
    token_amount INTEGER NOT NULL DEFAULT 0 CHECK (token_amount >= 0),
    cost_lamports INTEGER NOT NULL DEFAULT 0 CHECK (cost_lamports >= 0),
    proceeds_lamports INTEGER NOT NULL DEFAULT 0 CHECK (proceeds_lamports >= 0),
    protocol_fees_lamports INTEGER NOT NULL DEFAULT 0 CHECK (protocol_fees_lamports >= 0),
    network_fees_lamports INTEGER NOT NULL DEFAULT 0 CHECK (network_fees_lamports >= 0),
    realized_pnl_lamports INTEGER,
    simulated INTEGER NOT NULL,
    opened_ns INTEGER NOT NULL,
    closed_ns INTEGER,
    note TEXT
);
CREATE INDEX IF NOT EXISTS positions_active ON positions(status);
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    position_id INTEGER NOT NULL REFERENCES positions(position_id),
    side TEXT NOT NULL,
    token_amount INTEGER NOT NULL CHECK (token_amount > 0),
    sol_lamports INTEGER NOT NULL CHECK (sol_lamports >= 0),
    protocol_fee_lamports INTEGER NOT NULL CHECK (protocol_fee_lamports >= 0),
    network_fee_lamports INTEGER NOT NULL CHECK (network_fee_lamports >= 0),
    simulated INTEGER NOT NULL,
    ts_ns INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS exit_intents (
    intent_id TEXT PRIMARY KEY,
    position_id INTEGER NOT NULL REFERENCES positions(position_id),
    token_amount INTEGER CHECK (token_amount IS NULL OR token_amount > 0),
    created_ns INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_wallet (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    starting_lamports INTEGER NOT NULL,
    balance_lamports INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_wallet_entries (
    entry_id TEXT PRIMARY KEY,
    delta_lamports INTEGER NOT NULL,
    reason TEXT NOT NULL,
    ts_ns INTEGER NOT NULL
);
