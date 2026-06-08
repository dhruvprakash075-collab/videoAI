import argparse
import logging

from jobs.worker import Worker


def main():
    parser = argparse.ArgumentParser(description="Run Video.AI job worker")
    parser.add_argument("--once", action="store_true", help="Claim and run a single job then exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger("job_worker")

    w = Worker()
    if args.once:
        log.info("Running single job (if available)")
        try:
            w.run_once()
        except Exception as e:
            log.exception("Worker run_once failed: %s", e)
    else:
        log.info("Starting worker loop. Press Ctrl+C to stop.")
        try:
            w.run_forever()
        except KeyboardInterrupt:
            log.info("Worker stopped by user")
        except Exception:
            log.exception("Worker encountered unrecoverable error and is exiting")


if __name__ == "__main__":
    main()
