"""
VAPT F3 — deleting a price group must not orphan the plants that reference it.

The reference is by NAME (`plant_master.price_group` holds "A"/"B"/"C"), not by id, so
MongoDB enforces nothing here. Before this, `delete()` called `repo.delete(_id)` and
returned, leaving every referencing plant pointing at a group that no longer existed.
"""

import pytest

from services.processing.app.v2.services.price_group_service import (
    PriceGroupInUseError,
    PriceGroupService,
)


class _Repo:
    def __init__(self, group=None, referencing=0):
        self._group = group
        self._referencing = referencing
        self.deleted = []

    async def get(self, _id):
        return self._group

    async def count_referencing_plants(self, name):
        self.counted = name
        return self._referencing

    async def delete(self, _id):
        self.deleted.append(_id)
        return True


def build(group=None, referencing=0):
    svc = PriceGroupService.__new__(PriceGroupService)
    svc.repo = _Repo(group, referencing)
    svc.audit_logger = None          # _safe_audit tolerates this
    return svc


GROUP = {"price_group_id": "abc123", "price_group": "B", "price_from": 1, "price_to": 2}


class TestDeleteRefusesWhileInUse:
    @pytest.mark.asyncio
    async def test_refuses_when_plants_still_reference_the_group(self):
        svc = build(GROUP, referencing=12)
        with pytest.raises(PriceGroupInUseError) as exc:
            await svc.delete("abc123", None, {})
        assert exc.value.plant_count == 12
        assert exc.value.price_group == "B"
        assert svc.repo.deleted == [], "must not delete while referenced"

    @pytest.mark.asyncio
    async def test_error_message_names_the_group_and_the_count(self):
        svc = build(GROUP, referencing=3)
        with pytest.raises(PriceGroupInUseError) as exc:
            await svc.delete("abc123", None, {})
        assert "'B'" in str(exc.value) and "3 plant" in str(exc.value)

    @pytest.mark.asyncio
    async def test_counts_against_the_group_NAME_not_its_id(self):
        """Counting by id would always return 0 and silently restore the old behaviour."""
        svc = build(GROUP, referencing=1)
        with pytest.raises(PriceGroupInUseError):
            await svc.delete("abc123", None, {})
        assert svc.repo.counted == "B"


class TestDeleteStillWorks:
    @pytest.mark.asyncio
    async def test_deletes_when_nothing_references_it(self):
        svc = build(GROUP, referencing=0)
        assert await svc.delete("abc123", None, {}) is True
        assert svc.repo.deleted == ["abc123"]

    @pytest.mark.asyncio
    async def test_missing_group_reports_not_found_rather_than_raising(self):
        """The route turns False into 404; a raise here would become a 500."""
        svc = build(None, referencing=0)
        assert await svc.delete("nope", None, {}) is False
        assert svc.repo.deleted == []
