"""
One-time migration: encrypt plaintext BearerToken values in exchange_config collection.

Uses the `encrypted: True` flag to track which documents have already been migrated.
Safe to run on every deploy — already-encrypted documents are filtered out by the query.
Runs automatically at service startup via main.py lifespan.
"""

import logging
from datetime import datetime
from ..utils.encryption import encrypt_api_key

logger = logging.getLogger(__name__)

COLLECTION = "exchange_config"


async def run_encrypt_bearer_tokens_migration() -> None:
    """
    Find all exchange_config documents where BearerToken is present but not yet encrypted,
    encrypt the token, and set encrypted=True to prevent re-processing on future deploys.
    """
    from libs.database.mongo_client_factory import get_processing_client
    from ...core.config import settings

    client = get_processing_client(settings.MONGODB_URI)
    db = client[settings.MONGODB_DATABASE]
    collection = db[COLLECTION]

    # Only process documents that have a BearerToken and are NOT yet encrypted
    cursor = collection.find(
        {
            "BearerToken": {"$exists": True, "$not": {"$in": [None, ""]}},
            "encrypted": {"$ne": True}
        },
        {"_id": 1, "BearerToken": 1}
    )

    updated = 0
    failed = 0

    async for doc in cursor:
        doc_id = doc["_id"]
        token = doc.get("BearerToken", "")

        if not token:
            continue

        try:
            encrypted_token = encrypt_api_key(token)
            await collection.update_one(
                {"_id": doc_id},
                {
                    "$set": {
                        "BearerToken": encrypted_token,
                        "encrypted": True,
                        "updated_at": datetime.now()
                    }
                }
            )
            updated += 1
            logger.info(f"[migration] Encrypted BearerToken for exchange_config _id={doc_id}")
        except Exception as e:
            failed += 1
            logger.error(f"[migration] Failed to encrypt BearerToken for _id={doc_id}: {e}")

    logger.info(
        f"[migration] encrypt_bearer_tokens complete — updated={updated}, failed={failed}"
    )
