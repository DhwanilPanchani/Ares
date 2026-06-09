-- =============================================================================
-- Project Ares — SQLite Schema
-- =============================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- runs
-- Tracks every goal submission from creation through completion.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
    id           TEXT PRIMARY KEY,                -- UUID stored as TEXT
    goal         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending', -- pending|compiling|running|completed|failed
    dag_json     TEXT,                            -- Compiled DAG as JSON string (nullable until compiled)
    created_at   TEXT NOT NULL,                   -- ISO 8601
    completed_at TEXT,                            -- ISO 8601, nullable
    error        TEXT                             -- Error message if status=failed
);

CREATE INDEX IF NOT EXISTS idx_runs_status     ON runs(status);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at DESC);

-- ---------------------------------------------------------------------------
-- nodes
-- One row per DAG node per run.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nodes (
    id           TEXT NOT NULL,                   -- Compiler-generated name (e.g. research_openai)
    run_id       TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,                   -- Short human-readable name
    description  TEXT NOT NULL,                   -- Actionable one-sentence task description
    status       TEXT NOT NULL DEFAULT 'pending', -- pending|running|success|failed
    depends_on   TEXT NOT NULL DEFAULT '[]',      -- JSON array of node IDs
    prompt       TEXT,                            -- System prompt sent to the worker agent
    output       TEXT,                            -- Raw LLM output
    tool_calls   TEXT NOT NULL DEFAULT '[]',      -- JSON array of tool call records
    started_at   TEXT,
    completed_at TEXT,
    error        TEXT,
    PRIMARY KEY (id, run_id)                      -- Unique within a run, not globally
);

CREATE INDEX IF NOT EXISTS idx_nodes_run_id ON nodes(run_id);
CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);

-- ---------------------------------------------------------------------------
-- spans
-- OpenTelemetry spans persisted by the custom SQLite exporter.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS spans (
    id          TEXT PRIMARY KEY,                -- OTel span ID (hex string)
    trace_id    TEXT NOT NULL,                   -- Equals run_id
    parent_id   TEXT,                            -- Parent span ID, nullable
    run_id      TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    node_id     TEXT,                            -- Compiler-generated node name (informational, not FK)
    name        TEXT NOT NULL,                   -- e.g. "llm.call" or "tool.web_search"
    kind        TEXT NOT NULL,                   -- llm|tool|agent|system
    attributes  TEXT NOT NULL DEFAULT '{}',      -- JSON object
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    status_code TEXT NOT NULL DEFAULT 'OK'       -- OK|ERROR
);

CREATE INDEX IF NOT EXISTS idx_spans_run_id   ON spans(run_id);
CREATE INDEX IF NOT EXISTS idx_spans_node_id  ON spans(node_id);
CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id);

-- ---------------------------------------------------------------------------
-- scores
-- Trust scores produced by the Critic agent after each run.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scores (
    id                TEXT PRIMARY KEY,          -- UUID
    run_id            TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    factual_grounding REAL NOT NULL,             -- 0.0 to 1.0
    goal_completion   REAL NOT NULL,             -- 0.0 to 1.0
    tool_error_rate   REAL NOT NULL,             -- 0.0 to 1.0 (lower is better)
    trust_score       REAL NOT NULL,             -- Composite 0.0 to 1.0
    critique_text     TEXT NOT NULL,             -- Natural language explanation
    flagged_span_ids  TEXT NOT NULL DEFAULT '[]',-- JSON array of suspicious span IDs
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scores_run_id ON scores(run_id);
