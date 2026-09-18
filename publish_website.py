"""Export the existing cache without starting Telegram or writing Google Sheets."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from site_publisher import SitePublisher, public_snapshot, series_from_env, write_snapshot


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write a local preview instead of pushing to GitHub")
    args = parser.parse_args()
    cache_path = Path(os.getenv("TALKS_CACHE_FILE", "talks-cache.json"))
    try:
        snapshot = public_snapshot(json.loads(cache_path.read_text()), series_from_env())
        if args.output:
            write_snapshot(args.output, snapshot)
            print(f"Exported {len(snapshot['events'])} events to {args.output}")
        else:
            repository = os.getenv("WEBSITE_REPO_URL", "").strip()
            if not repository:
                parser.error("Set WEBSITE_REPO_URL in .env or use --output")
            changed = SitePublisher(repository).publish(snapshot)
            print("Published website schedule" if changed else "Website schedule is already up to date")
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        parser.exit(1, f"Website export failed: {exc}\n")


if __name__ == "__main__":
    main()
