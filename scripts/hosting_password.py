"""Create the owner password hash without printing or storing the password."""
import argparse
import getpass
import hashlib
import os
import secrets
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    password = getpass.getpass("Owner password (at least 16 characters): ")
    if len(password) < 16:
        parser.error("Use a unique password with at least 16 characters.")
    if password != getpass.getpass("Confirm password: "):
        parser.error("Passwords do not match.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}\n")
    print("Password hash saved. Keep this file private; the password was not saved.")


if __name__ == "__main__":
    main()
