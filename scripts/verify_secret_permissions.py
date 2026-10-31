"""Verify runtime secret permissions against a built Linux image.

Run: python scripts/verify_secret_permissions.py choplab:check
Only disposable Docker volumes and synthetic credentials are used. No ports are
published, no real secret files are read, and nothing is added to the image.
"""
import argparse
import json
import subprocess
import time
import uuid

SETUP = """
import hashlib, os, secrets
from pathlib import Path

root = Path("/fixtures")
os.chmod(root, 0o755)
salt = secrets.token_bytes(16)
digest = hashlib.pbkdf2_hmac(
    "sha256", b"synthetic-secret-permission-check", salt, 600000)
content = f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}"
grouped = root / "grouped"
grouped.mkdir()
os.chown(grouped, 0, 1000)
os.chmod(grouped, 0o550)
for name, gid, mode in [
    ("readable", 1000, 0o440),
    ("unreadable", 0, 0o400),
    ("grouped/readable", 1000, 0o440),
    ("compose", 0, 0o444),
]:
    path = root / name
    path.write_text(content)
    os.chown(path, 0, gid)
    os.chmod(path, mode)
    stat = path.stat()
    assert (stat.st_uid, stat.st_gid, stat.st_mode & 0o777) == (0, gid, mode)
print("Synthetic fixtures: root:1000 0440, directory 0550; root:root 0400/0444.")
"""

IDENTITY = """
import json, os
from pathlib import Path
assert not Path("/etc/secrets").exists(), "Secret files unexpectedly baked into image"
assert not Path("/run/secrets").exists(), "Secret files unexpectedly baked into image"
assert "CHOPLAB_PASSWORD_HASH_FILE" not in os.environ
assert "CHOPLAB_PASSWORD_HASH" not in os.environ
print(json.dumps({"uid": os.getuid(), "gid": os.getgid(), "groups": os.getgroups()}))
"""

PROBE = """
import base64, json, os, urllib.error, urllib.request
assert os.getuid() == 10001 and os.getgid() == 10001
assert 1000 in os.getgroups()
assert json.load(urllib.request.urlopen("http://127.0.0.1:10000/api/health"))["ok"]
if os.environ.get("CHOPLAB_MODE") == "hosted":
    url = "http://127.0.0.1:10000/api/projects"
    try:
        urllib.request.urlopen(urllib.request.Request(
            url, headers={"X-Forwarded-Proto": "https"}))
    except urllib.error.HTTPError as e:
        assert e.code == 401
    else:
        raise AssertionError("Hosted startup accepted anonymous project access")
    auth = base64.b64encode(b"owner:synthetic-secret-permission-check").decode()
    response = urllib.request.urlopen(urllib.request.Request(url, headers={
        "X-Forwarded-Proto": "https", "Authorization": "Basic " + auth}))
    assert response.status == 200
"""


def docker(*args, stdin=None):
    result = subprocess.run(
        ["docker", *args], input=stdin, text=True, capture_output=True, check=True,
        timeout=180)
    return (result.stdout + (result.stderr if args[0] == "logs" else "")).strip()


def check_startup(image, volume, secret, *, user=None, local=False, error=None):
    name = "choplab-secret-" + uuid.uuid4().hex
    command = [
        "run", "-d", "--name", name, "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--tmpfs", "/tmp:uid=10001,gid=10001",
        "--tmpfs", "/data:uid=10001,gid=10001",
        "--env", "CHOPLAB_MODE=" + ("local" if local else "hosted"),
        "--env", "CHOPLAB_PUBLIC_URL=https://localhost",
        "--env", "CHOPLAB_PROXY_IPS=*",
        "--env", "PORT=10000", "--env", "CHOPLAB_PORT=10000",
        "--mount", f"type=volume,src={volume},dst=/etc/secrets,readonly",
    ]
    if secret is not None:
        path = "/run/secrets/compose" if secret == "compose" else "/etc/secrets/" + secret
        command += ["--env", "CHOPLAB_PASSWORD_HASH_FILE=" + path]
    if secret == "compose":
        command += ["--mount", f"type=volume,src={volume},dst=/run/secrets,readonly"]
    if user:
        command += ["--user", user]
    try:
        docker(*command, image)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = json.loads(docker("inspect", "--format", "{{json .State}}", name))
            if not state["Running"]:
                assert error, "Application exited before readiness"
                logs = docker("logs", name)
                assert state["ExitCode"] != 0, "Invalid secret startup returned success"
                assert error in logs, "Startup did not report the expected failure"
                if secret is not None:
                    assert "Invalid password hash file" in logs, "Hosted startup did not fail closed"
                assert "pbkdf2_sha256$" not in logs, "Startup leaked a password hash"
                assert "synthetic-secret-permission-check" not in logs, "Startup leaked a password"
                print(f"PASS: {secret or 'unset secret'} ({user or 'image user'}): {error}")
                return
            health = subprocess.run(
                ["docker", "exec", name, "python", "-c",
                 "import urllib.request; urllib.request.urlopen('http://127.0.0.1:10000/api/health', timeout=2)"],
                capture_output=True, timeout=10)
            if health.returncode == 0:
                assert error is None, "Application started despite an inaccessible or missing secret"
                docker("exec", name, "python", "-c", PROBE)
                print(f"PASS: {'local' if local else 'hosted'} startup with {secret or 'no secret'}")
                return
            time.sleep(1)
        raise TimeoutError("Application neither became ready nor failed within 120 seconds")
    finally:
        docker("rm", "-f", name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    args = parser.parse_args()
    identity = json.loads(docker("run", "--rm", "--entrypoint", "python",
                                 args.image, "-c", IDENTITY))
    assert identity["uid"] == identity["gid"] == 10001, identity
    assert 1000 in identity["groups"], identity
    print(f"Image runtime identity: {identity}")
    old_identity = json.loads(docker(
        "run", "--rm", "--user", "10001:10001", "--entrypoint", "python",
        args.image, "-c", IDENTITY))
    assert old_identity["uid"] == old_identity["gid"] == 10001, old_identity
    assert 1000 not in old_identity["groups"], old_identity
    print(f"Reproduced original identity without supplementary access: {old_identity}")
    volume = "choplab-secret-fixtures-" + uuid.uuid4().hex
    docker("volume", "create", volume)
    try:
        print(docker("run", "--rm", "-i", "--user", "0:0",
                     "--mount", f"type=volume,src={volume},dst=/fixtures",
                     "--entrypoint", "python", args.image, "-", stdin=SETUP))
        # Explicit uid:gid disables image supplementary groups, reproducing the old boundary.
        for path in ("readable", "grouped/readable"):
            check_startup(args.image, volume, path, user="10001:10001", error="PermissionError")
        check_startup(args.image, volume, None, error="requires CHOPLAB_PASSWORD_HASH_FILE")
        check_startup(args.image, volume, "missing", error="FileNotFoundError")
        check_startup(args.image, volume, "unreadable", error="PermissionError")
        check_startup(args.image, volume, "readable")
        check_startup(args.image, volume, "grouped/readable")
        check_startup(args.image, volume, "compose")
        check_startup(args.image, volume, None, local=True)
    finally:
        docker("volume", "rm", volume)


if __name__ == "__main__":
    main()
