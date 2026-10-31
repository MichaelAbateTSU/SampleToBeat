"""Offline metadata migration and coordinated backup/restore. Never accepts credentials on the command line."""
import argparse
import json
import sys

from . import store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["migrate-sqlite", "backup", "restore", "check"])
    args = parser.parse_args()
    try:
        if args.operation == "migrate-sqlite":
            store.migrate_sqlite()
            print("SQLite metadata copied to PostgreSQL. Original SQLite and audio were retained.")
        elif args.operation == "backup":
            print(json.dumps(store.export_snapshot()))
        elif args.operation == "restore":
            store.restore_snapshot(json.load(sys.stdin))
            print("Metadata restored. Verify its matching audio volume before starting ChopLab.")
        else:
            print(json.dumps(store.check_ready()))
    except (store.StoreConfigurationError, store.StoreUnavailable) as e:
        parser.exit(1, f"{e}\n")


if __name__ == "__main__":
    main()
