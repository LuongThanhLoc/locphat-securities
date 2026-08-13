# LP-RRG V2 rollout runbook

LP-RRG V2 is fail-closed and remains behind `RRG_DATA_V2_ENABLED` until the
durable PostgreSQL universe passes audit. The daily Render cron runs at 15:30
Asia/Ho_Chi_Minh (08:30 UTC) on weekdays.

## Initial rollout

1. Configure `DATABASE_URL` and deploy with `RRG_DATA_V2_ENABLED=false`.
2. Run `python rrg_sync.py backfill`. This creates the idempotent schema,
   synchronises the effective-dated security master and corporate actions,
   stores five years of raw/canonical history, then builds market-score
   snapshots for periods 10/14/20.
3. Run `python rrg_sync.py audit` and require `audit_passed=true`, no
   `adjustment_pending`, and 100% eligible coverage.
4. Check `/api/rrg/health`: provider circuits closed, snapshot session current,
   fallback/quarantine rates acceptable and no pending adjustment.
5. Set `RRG_DATA_V2_ENABLED=true` and deploy. Keep the PostgreSQL data when
   rolling back; disable only the flag.

## Operating rules

- Do not manually edit canonical bars. Provider observations are immutable;
  corrections create revisions and a new snapshot fingerprint.
- Never confirm a rights issue without both ratio and subscription price.
- A complete server snapshot may be served for at most three official trading
  sessions. Older snapshots produce HTTP 503.
- Rotation Score is relative market rotation, not a buy/sell recommendation.

