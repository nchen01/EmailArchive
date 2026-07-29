"""Background job worker (S24/S25).

Claims and runs queued jobs from the Postgres-backed `job` table (S24), one at a
time, with a lease/heartbeat and expired-lease reclaim. Run this alongside the API
to execute enqueued work (e.g. `gmail_ingest_window` from S25).

Usage (blessed venv Python, never a bare `python`):
    .venv\\Scripts\\python.exe -m scripts.run_worker
    .venv\\Scripts\\python.exe -m scripts.run_worker --once      # drain, then exit
    .venv\\Scripts\\python.exe -m scripts.run_worker --poll 2.0  # seconds between empty polls

AUTH_MODE / DATABASE_URL come from the environment / .env, same as the API. This
process never handles OAuth tokens directly — the Gmail provider seam resolves
credentials, and job metadata is safe-only. Never logs job params/content.
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import time

from scripts._env import load_local_env

_log = logging.getLogger("ekc.worker")


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def main() -> int:
    load_local_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    ap = argparse.ArgumentParser(description="EKC background job worker")
    ap.add_argument("--once", action="store_true", help="drain the queue once, then exit")
    ap.add_argument("--poll", type=float, default=2.0, help="seconds to sleep when the queue is empty")
    ap.add_argument("--lease", type=int, default=120, help="job lease seconds")
    args = ap.parse_args()

    # Mirror the API's import-time redaction install so the worker satisfies the
    # shared "OAuth callback log redaction installed" invariant too (idempotent,
    # defense in depth — the worker serves no callback, but the guard is uniform).
    from services.api.log_redaction import install_access_log_redaction
    install_access_log_redaction()

    # S27 hosted-readiness guard: no-op unless EKC_DEPLOY_ENV=production, so local
    # dev workers run unchanged. In a hosted deployment the worker refuses to start
    # on unsafe config — dev auth/vault, an unreachable DB, a DB not at migration
    # head, or an unobservable job queue. The banner is safe metadata only.
    from services.hosted_readiness import HostedReadinessError, run_startup_guard

    try:
        run_startup_guard(component="worker")
    except HostedReadinessError as exc:
        _log.error(
            "HOSTED READINESS GUARD FAILED — refusing to start the worker:\n  %s",
            exc.safe_summary(),
        )
        return 1

    # Import handlers so every job type (noop, gmail_ingest_window, …) is registered.
    import services.jobs.handlers  # noqa: F401
    from services.db.engine import SessionLocal
    from services.jobs import worker

    wid = _worker_id()
    _log.info("worker %s starting (mode=%s)", wid, "once" if args.once else "loop")
    while True:
        db = SessionLocal()
        try:
            job = worker.run_once(db, worker_id=wid, lease_seconds=args.lease)
        except Exception:  # never crash the loop; log a safe marker only
            _log.exception("worker iteration error")
            job = None
        finally:
            db.close()

        if job is not None:
            _log.info("ran job %s type=%s -> %s", job.id, job.job_type, job.status)
            continue  # keep draining while there is work
        if args.once:
            _log.info("queue empty; --once exiting")
            return 0
        time.sleep(args.poll)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
