#!/usr/bin/env python
"""
Encrypt the TOTP secrets sitting in plaintext in `auth_db.users.mfa_secret`.

Why this exists
---------------
Every enrolled account stored its TOTP secret as plaintext base32. Anyone with a read of
that collection -- a backup, a Compass export, a `mongodump` shared over AnyDesk -- can mint
valid second factors for every one of those accounts, indefinitely, and nothing in the
system would show it had happened. This rewrites each secret as Fernet ciphertext using
`libs.crypto`, the same scheme and the same `ENCRYPTION_KEY` the processing service already
uses for the exchange bearer token.

Running it is optional, not a prerequisite
------------------------------------------
The application reads plaintext and ciphertext transparently: `decrypt_secret` returns a
value that was never encrypted unchanged. So the code can deploy first and nobody is locked
out while this waits for a maintenance window. What the migration buys is that the
plaintext stops existing -- otherwise it only disappears account by account, as each user
happens to re-enrol.

Idempotent: a secret that is already ciphertext is left alone, so re-running it is a no-op
and an interrupted run can simply be run again.

Usage
-----
    python tools/migrate_mfa_secrets.py --dry-run    # report only, change nothing
    python tools/migrate_mfa_secrets.py --apply      # encrypt, after taking a backup
    python tools/migrate_mfa_secrets.py --revert FILE  # restore secrets from a backup

`--apply` writes a timestamped JSON backup of every `_id` and its current `mfa_secret`
first. **That file contains live TOTP secrets in plaintext.** Treat it like a password
dump: keep it only until the migration is confirmed good, then destroy it.

ENCRYPTION_KEY
--------------
Set it before running, and set the same value on every service and every replica. Without
it `libs.crypto` derives a key from a password committed to this repository, which
obfuscates the secrets rather than protecting them. If the key changes after this has run,
the stored secrets become unreadable and every enrolled user is locked out -- recovery is
`--revert` with the backup file, or an admin emergency-disable per user.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bson import ObjectId
from pymongo import MongoClient

from libs.crypto import SecretCipher

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
AUTH_DB_NAME = os.getenv("AUTH_DB_NAME", "auth_db")


def _safe_url(url: str) -> str:
    """mongodb://user:secret@host/ -> mongodb://user:***@host/"""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    creds, _, host = rest.rpartition("@")
    user = creds.split(":")[0]
    return f"{scheme}://{user}:***@{host}"


def announce_target(db) -> None:
    """
    Say out loud which database is about to be changed.

    UAT and production both run Mongo on localhost with the same database name, so the only
    thing distinguishing them is which machine the shell is on. That is what makes a
    mistake here quiet.
    """
    print(f"target : {_safe_url(MONGODB_URL)}  db={AUTH_DB_NAME}")
    print(f"key    : {'ENCRYPTION_KEY is set' if os.getenv('ENCRYPTION_KEY') else 'ENCRYPTION_KEY NOT SET -- using the repository default key derivation'}")


def confirm(action: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(f"refusing to {action} without --yes when stdin is not a terminal")
        return False
    return input(f"{action} against {AUTH_DB_NAME} at {_safe_url(MONGODB_URL)}? [y/N] ").strip().lower() == "y"


def survey(db, cipher: SecretCipher):
    """Split the enrolled population into already-encrypted and still-plaintext."""
    encrypted, plaintext = [], []
    for doc in db["users"].find(
        {"mfa_secret": {"$nin": [None, ""]}},
        {"username": 1, "mfa_secret": 1, "mfa_enabled": 1, "mfa_setup_complete": 1},
    ):
        (encrypted if cipher.is_encrypted(doc["mfa_secret"]) else plaintext).append(doc)
    return encrypted, plaintext


def describe(encrypted, plaintext) -> None:
    fully_enrolled = sum(
        1 for d in plaintext if d.get("mfa_enabled") and d.get("mfa_setup_complete")
    )
    print(f"secrets already encrypted : {len(encrypted)}")
    print(f"secrets to encrypt        : {len(plaintext)}")
    print(f"  of those, fully enrolled and prompted at login: {fully_enrolled}")
    for doc in plaintext[:10]:
        state = "enrolled" if doc.get("mfa_enabled") and doc.get("mfa_setup_complete") else "partial"
        print(f"    {str(doc['_id'])}  {doc.get('username', '?'):24s} {state}")
    if len(plaintext) > 10:
        print(f"    ... and {len(plaintext) - 10} more")


def backup(db, plaintext, path) -> None:
    payload = {
        "taken_at": datetime.now().isoformat(),
        "database": AUTH_DB_NAME,
        "users": [
            {"_id": str(d["_id"]), "username": d.get("username"), "mfa_secret": d["mfa_secret"]}
            for d in plaintext
        ],
    }
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=1)
    os.chmod(path, 0o600)


def apply_plan(db, plaintext, cipher: SecretCipher) -> int:
    changed = 0
    for doc in plaintext:
        ciphertext = cipher.encrypt(doc["mfa_secret"])
        result = db["users"].update_one(
            # Match on the current value as well as the _id: if another process re-enrolled
            # this user between the survey and now, the write is skipped rather than
            # clobbering the newer secret with an encrypted copy of the stale one.
            {"_id": doc["_id"], "mfa_secret": doc["mfa_secret"]},
            {"$set": {"mfa_secret": ciphertext}},
        )
        changed += result.modified_count
    return changed


def revert(db, path) -> int:
    with open(path) as fh:
        payload = json.load(fh)
    ids = [ObjectId(d["_id"]) for d in payload["users"]]
    present = db["users"].count_documents({"_id": {"$in": ids}})
    print(f"backup taken {payload.get('taken_at')} from db={payload.get('database')}: "
          f"{len(ids)} users, {present} of them exist here")
    if present == 0:
        print("none of the backup's documents exist here -- this backup is from a "
              "different environment. Refusing.")
        return -1
    restored = 0
    for doc in payload["users"]:
        restored += db["users"].update_one(
            {"_id": ObjectId(doc["_id"])}, {"$set": {"mfa_secret": doc["mfa_secret"]}}
        ).modified_count
    return restored


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    group.add_argument("--apply", action="store_true", help="encrypt the plaintext secrets")
    group.add_argument("--revert", metavar="BACKUP", help="restore secrets from a backup file")
    parser.add_argument("--backup-dir", default=".", help="where --apply writes its backup")
    parser.add_argument("--yes", action="store_true",
                        help="skip the confirmation prompt (required for non-interactive runs)")
    args = parser.parse_args()

    db = MongoClient(MONGODB_URL)[AUTH_DB_NAME]
    announce_target(db)
    print()

    if args.revert:
        if not confirm("revert MFA secrets", args.yes):
            return 1
        restored = revert(db, args.revert)
        if restored < 0:
            return 1
        print(f"restored {restored} secrets from {args.revert}")
        return 0

    cipher = SecretCipher()
    encrypted, plaintext = survey(db, cipher)
    describe(encrypted, plaintext)

    if not plaintext:
        print("\nnothing to do -- every stored MFA secret is already encrypted")
        return 0

    if args.dry_run:
        print("\ndry run, nothing written")
        return 0

    if not confirm("encrypt these MFA secrets", args.yes):
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.abspath(os.path.join(args.backup_dir, f"mfa-secrets-{AUTH_DB_NAME}-{stamp}.json"))
    backup(db, plaintext, path)
    print(f"\nbackup written to {path} (mode 0600)")
    print("THIS FILE CONTAINS LIVE TOTP SECRETS IN PLAINTEXT. Destroy it once the "
          "migration is confirmed good.")

    changed = apply_plan(db, plaintext, cipher)
    print(f"encrypted {changed} of {len(plaintext)} secrets")
    if changed != len(plaintext):
        print("some documents changed underneath the migration and were skipped; "
              "re-run to pick them up")
    print(f"\nTo undo: python {sys.argv[0]} --revert {path}")
    print("No restart is required -- the application reads both forms.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
