import argparse
import logging
import sys
from pathlib import Path

from jobs.job_store import JobStore
from jobs.worker import Worker


def main():
    parser = argparse.ArgumentParser(description="Run Video.AI job worker")
    parser.add_argument("--once", action="store_true", help="Claim and run a single job then exit")
    parser.add_argument("--db-path", type=str, default=None, help="Path to job database (default: project DB)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("job_worker")

    store = JobStore(db_path=Path(args.db_path)) if args.db_path else JobStore()
    w = Worker(store=store)
    if args.once:
        log.info("Running single job (if available)")
        try:
            job_id = w.run_once()
            if job_id is None:
                log.info("No job available")
            else:
                job = w.store.get_job(job_id)
                log.info(f"Job {job_id} finished with status: {job['status'] if job else 'unknown'}")
        except Exception as e:
            log.exception("Worker run_once failed: %s", e)
            sys.exit(1)
    else:
        log.info("Starting worker loop. Press Ctrl+C to stop.")
        try:
            w.run_forever()
        except KeyboardInterrupt:
            log.info("Worker stopped by user")
        except Exception:
            log.exception("Worker encountered unrecoverable error and is exiting")
            sys.exit(1)


if __name__ == "__main__":
    main()
