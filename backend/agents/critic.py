"""Critic agent — post-run scoring via phi4-mini — full implementation in Phase 4."""
# Phase 4: Critic calls phi4-mini after qwen2.5:3b unloads,
# validates TrustScore, persists to SQLite, emits trust_scored SSE event.
