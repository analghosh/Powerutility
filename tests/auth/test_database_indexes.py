"""
Unit tests for services/auth/app/core/database_indexes.py

Why this file exists
---------------------
`_create_user_indexes(db)` used to wrap ALL of its `create_index()` calls in a
single try/except block. In the live UAT `auth_db.users` collection, 813 of
816 documents have `email: null` and two documents share the literal value
`"admin@epms.com"`. The very first attempt to build a unique index on
`email` therefore raises `pymongo.errors.OperationFailure` (duplicate key /
E11000) EVERY time the service starts. Because that call was statement #2 in
the function, the exception aborted the rest of the function and every index
listed after `email` (`is_deleted`+`is_active`, `login_attempts`,
`locked_until`, `roles`, `mfa_enabled`, `sessions.session_id`,
`password_expires_at`) silently never got created, on any deployment,
forever. The failure was logged at WARNING and swallowed, so nothing ever
surfaced this in monitoring or logs-as-errors.

This left `auth_db.users` with only `_id_` and `username_1`, which is why
notification-audience resolution (`users.find({"roles": {"$in": [...]},
"is_active": True, "is_deleted": {"$ne": True}}, {"_id": 1})`) was a
COLLSCAN over all 816 users instead of an index-served lookup.

These tests use `unittest.mock.MagicMock` for the db/collection - no real
MongoDB connection is made or required.
"""

from unittest.mock import MagicMock

import pytest
from pymongo.errors import OperationFailure

from services.auth.app.core.database_indexes import _create_user_indexes


def _index_names_and_args(mock_collection):
    """Return the list of (args, kwargs) pairs `create_index` was called with."""
    return [call.args for call in mock_collection.create_index.call_args_list]


class TestCreateUserIndexes:
    def test_one_failing_index_does_not_cancel_the_rest(self):
        """
        Reproduces the bug: the `email` unique-index build fails with
        OperationFailure (as it always does against the live UAT data, which
        has 813 null emails and a duplicate `admin@epms.com`). That single
        failure must not prevent every index declared after it - such as
        `roles` - from being attempted.
        """
        mock_collection = MagicMock()

        def create_index_side_effect(keys, *args, **kwargs):
            if keys == "email":
                raise OperationFailure("E11000 duplicate key error")
            return f"{keys}_index"

        mock_collection.create_index.side_effect = create_index_side_effect
        mock_db = MagicMock()
        mock_db.__getitem__.return_value = mock_collection

        # Should not raise, despite the "email" index failing.
        _create_user_indexes(mock_db)

        attempted_keys = [call.args[0] for call in mock_collection.create_index.call_args_list]
        assert "email" in attempted_keys, "the failing index should still have been attempted"
        assert "roles" in attempted_keys, (
            "an index declared after the failing 'email' index must still be attempted"
        )
        assert "login_attempts" in attempted_keys
        assert "locked_until" in attempted_keys
        assert "mfa_enabled" in attempted_keys
        assert "sessions.session_id" in attempted_keys
        assert "password_expires_at" in attempted_keys
        assert [
            ("is_deleted", 1),
            ("is_active", 1),
        ] in attempted_keys

    def test_the_audience_compound_index_is_created(self):
        """
        The new compound index [("roles", 1), ("is_active", 1)] must be
        created - it serves notification audience resolution, which filters
        on both fields together and cannot be served by the single-field
        `roles` index alone.
        """
        mock_collection = MagicMock()
        mock_db = MagicMock()
        mock_db.__getitem__.return_value = mock_collection

        _create_user_indexes(mock_db)

        attempted_keys = [call.args[0] for call in mock_collection.create_index.call_args_list]
        assert [("roles", 1), ("is_active", 1)] in attempted_keys

    def test_username_index_still_unique(self):
        """Guard against accidentally dropping `unique=True` while refactoring."""
        mock_collection = MagicMock()
        mock_db = MagicMock()
        mock_db.__getitem__.return_value = mock_collection

        _create_user_indexes(mock_db)

        username_call = next(
            call
            for call in mock_collection.create_index.call_args_list
            if call.args[0] == "username"
        )
        assert username_call.kwargs.get("unique") is True

    def test_email_index_still_unique_despite_always_failing(self):
        """
        Even though `email` always fails in production data, the code should
        still request it as unique=True - fixing the data (or dropping the
        uniqueness requirement) is a separate decision from fixing the
        bootstrap's error handling.
        """
        mock_collection = MagicMock()
        mock_db = MagicMock()
        mock_db.__getitem__.return_value = mock_collection

        _create_user_indexes(mock_db)

        email_call = next(
            call
            for call in mock_collection.create_index.call_args_list
            if call.args[0] == "email"
        )
        assert email_call.kwargs.get("unique") is True
