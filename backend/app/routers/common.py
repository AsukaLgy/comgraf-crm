"""Shared response shaping for endpoints that mutate a case."""

from ..repository import case_detail


def case_response(db, user, case):
    """Flush pending writes before projecting the updated aggregate."""
    db.flush()
    return case_detail(db, user, case)
