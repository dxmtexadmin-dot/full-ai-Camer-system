"""Inventory ledger.

Rules enforced here:

* The ledger is append-only: entries are never edited or deleted, corrections
  are new entries of type ``CORRECTION``.
* Official balances only include *confirmed* entries. Vision candidates are
  written with ``is_confirmed=False`` and therefore never move the balance until
  a human confirms them.
* A category count is not the same thing as a registered physical asset. Asset
  counts are reported separately and the application never claims that a
  detected monitor is a specific asset code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import EquipmentAsset, EquipmentCategory, Event, InventoryTransaction, User
from app.models.enums import Direction, EquipmentStatus, EventType, TransactionType, VerificationStatus
from app.services import audit_service
from app.utils.time_utils import utc_now

logger = logging.getLogger(__name__)

POSITIVE_TYPES = {TransactionType.INITIAL, TransactionType.RECEIVED, TransactionType.RETURNED}
NEGATIVE_TYPES = {TransactionType.REMOVED}


@dataclass
class CategoryBalance:
    category_code: str
    label_uz: str
    initial: int
    expected: int
    pending_in: int
    pending_out: int
    projected: int
    registered_assets: int
    discrepancy: int

    @property
    def has_issue(self) -> bool:
        return self.pending_out > 0 or self.discrepancy != 0


def signed_quantity(transaction_type: TransactionType | str, quantity: int) -> int:
    """Normalise the sign of a ledger delta."""
    tx_type = TransactionType(str(transaction_type))
    if tx_type == TransactionType.CORRECTION:
        return int(quantity)  # caller decides the sign
    quantity = abs(int(quantity))
    if tx_type in POSITIVE_TYPES:
        return quantity
    if tx_type in NEGATIVE_TYPES:
        return -quantity
    return 0  # transfers do not change the count


def record(
    session: Session,
    *,
    category_code: str,
    transaction_type: TransactionType | str,
    quantity: int = 1,
    asset_id: Optional[int] = None,
    event_id: Optional[int] = None,
    location_from: str = "",
    location_to: str = "",
    is_confirmed: bool = True,
    source: str = "manual",
    reason: Optional[str] = None,
    user: Optional[User] = None,
    occurred_at: Optional[datetime] = None,
) -> InventoryTransaction:
    tx_type = TransactionType(str(transaction_type))
    delta = signed_quantity(tx_type, quantity)

    transaction = InventoryTransaction(
        category_code=category_code,
        asset_id=asset_id,
        event_id=event_id,
        transaction_type=tx_type.value,
        quantity=delta,
        location_from=location_from,
        location_to=location_to,
        is_confirmed=is_confirmed,
        source=source,
        reason=reason,
        created_by_user_id=user.id if user else None,
        created_at=occurred_at or utc_now(),
    )
    session.add(transaction)
    session.flush()

    if is_confirmed and asset_id:
        _apply_asset_side_effect(session, asset_id, tx_type, location_to or location_from)

    logger.debug(
        "Ledger +%s %s (%s, confirmed=%s)",
        transaction.quantity,
        category_code,
        tx_type.value,
        is_confirmed,
    )
    return transaction


def _apply_asset_side_effect(session: Session, asset_id: int, tx_type: TransactionType, location: str) -> None:
    asset = session.get(EquipmentAsset, asset_id)
    if asset is None:
        return
    if tx_type in {TransactionType.REMOVED}:
        if asset.status != EquipmentStatus.LOST.value:
            asset.status = EquipmentStatus.IN_USE.value
    elif tx_type in {TransactionType.RECEIVED, TransactionType.RETURNED}:
        asset.status = EquipmentStatus.IN_STOCK.value
    if location:
        asset.location = location
    asset.updated_at = utc_now()


# ---------------------------------------------------------------------------
# Vision candidate -> ledger plumbing
# ---------------------------------------------------------------------------
def create_pending_movement(session: Session, event: Event) -> Optional[InventoryTransaction]:
    """Write an *unconfirmed* ledger candidate for an equipment movement event."""
    if not event.equipment_category:
        return None
    if event.event_type == EventType.EQUIPMENT_BROUGHT_IN.value:
        tx_type = TransactionType.RECEIVED
    elif event.event_type == EventType.EQUIPMENT_TAKEN_OUT.value:
        tx_type = TransactionType.REMOVED
    elif event.event_type == EventType.EQUIPMENT_RETURNED.value:
        tx_type = TransactionType.RETURNED
    else:
        return None

    return record(
        session,
        category_code=event.equipment_category,
        transaction_type=tx_type,
        quantity=1,
        asset_id=event.asset_id,
        event_id=event.id,
        is_confirmed=False,  # never trust a vision candidate
        source="vision",
        reason=f"Kamera kuzatuvi: {event.public_id}",
        occurred_at=event.occurred_at,
    )


def event_transactions(session: Session, event_id: int) -> list[InventoryTransaction]:
    return list(
        session.scalars(select(InventoryTransaction).where(InventoryTransaction.event_id == event_id)).all()
    )


def confirm_event(session: Session, event: Event, user: Optional[User] = None) -> list[InventoryTransaction]:
    rows = event_transactions(session, event.id)
    if not rows:
        row = create_pending_movement(session, event)
        rows = [row] if row else []
    for row in rows:
        row.is_confirmed = True
        row.is_rejected = False
        row.reason = (row.reason or "") + " | Tasdiqlangan"
        if row.asset_id:
            _apply_asset_side_effect(session, row.asset_id, TransactionType(row.transaction_type), row.location_to)
    audit_service.record(
        session,
        action="inventory.confirm",
        entity="event",
        entity_id=event.public_id,
        details={"ledger_rows": [row.id for row in rows]},
        user=user,
        username="operator",
    )
    return rows


def reject_event(session: Session, event: Event, reason: str, user: Optional[User] = None) -> list[InventoryTransaction]:
    """Keep the candidate rows for the audit trail but stop counting them."""
    rows = event_transactions(session, event.id)
    for row in rows:
        row.is_confirmed = False
        row.is_rejected = True
        row.reason = f"{row.reason or ''} | Rad etildi: {reason}".strip()
    audit_service.record(
        session,
        action="inventory.reject",
        entity="event",
        entity_id=event.public_id,
        details={"ledger_rows": [row.id for row in rows]},
        reason=reason,
        user=user,
        username="operator",
    )
    return rows


# ---------------------------------------------------------------------------
# Balances and reporting
# ---------------------------------------------------------------------------
def _categories(session: Session) -> list[EquipmentCategory]:
    return list(session.scalars(select(EquipmentCategory).order_by(EquipmentCategory.sort_order)).all())


def balances(session: Session) -> list[CategoryBalance]:
    categories = _categories(session)
    totals: dict[str, dict[str, int]] = {}
    rows = session.execute(
        select(
            InventoryTransaction.category_code,
            InventoryTransaction.is_confirmed,
            InventoryTransaction.quantity,
        ).where(InventoryTransaction.is_rejected.is_(False))
    ).all()
    for category_code, is_confirmed, quantity in rows:
        bucket = totals.setdefault(category_code, {"confirmed": 0, "pending_in": 0, "pending_out": 0})
        if is_confirmed:
            bucket["confirmed"] += quantity
        else:
            if quantity > 0:
                bucket["pending_in"] += quantity
            elif quantity < 0:
                bucket["pending_out"] += -quantity

    initial_rows = session.execute(
        select(InventoryTransaction.category_code, func.sum(InventoryTransaction.quantity))
        .where(
            InventoryTransaction.is_confirmed.is_(True),
            InventoryTransaction.is_rejected.is_(False),
            InventoryTransaction.transaction_type == TransactionType.INITIAL.value,
        )
        .group_by(InventoryTransaction.category_code)
    ).all()
    initial_map = {code: int(qty or 0) for code, qty in initial_rows}

    asset_rows = session.execute(
        select(EquipmentCategory.code, func.count(EquipmentAsset.id))
        .join(EquipmentAsset, EquipmentAsset.category_id == EquipmentCategory.id)
        .where(EquipmentAsset.status != EquipmentStatus.RETIRED.value)
        .group_by(EquipmentCategory.code)
    ).all()
    asset_map = {code: int(count or 0) for code, count in asset_rows}

    result: list[CategoryBalance] = []
    for category in categories:
        bucket = totals.get(category.code, {"confirmed": 0, "pending_in": 0, "pending_out": 0})
        initial = initial_map.get(category.code, 0)
        expected = bucket["confirmed"]  # INITIAL rows are part of the confirmed sum
        registered = asset_map.get(category.code, 0)
        result.append(
            CategoryBalance(
                category_code=category.code,
                label_uz=category.label_uz,
                initial=initial,
                expected=expected,
                pending_in=bucket["pending_in"],
                pending_out=bucket["pending_out"],
                projected=expected + bucket["pending_in"] - bucket["pending_out"],
                registered_assets=registered,
                discrepancy=(expected - registered) if registered else 0,
            )
        )
    return result


def balance_for(session: Session, category_code: str) -> CategoryBalance:
    for item in balances(session):
        if item.category_code == category_code:
            return item
    return CategoryBalance(category_code, category_code, 0, 0, 0, 0, 0, 0, 0)


def try_confirm_pending_for_event(session: Session, event: Event, user: Optional[User] = None) -> None:
    """Used by the manual review flow (kept tiny so callers stay readable)."""
    if event.verification_status != VerificationStatus.PENDING.value:
        return
    confirm_event(session, event, user=user)


def pending_movement_totals(session: Session) -> dict[str, int]:
    pending_in = session.scalar(
        select(func.coalesce(func.sum(InventoryTransaction.quantity), 0)).where(
            InventoryTransaction.is_confirmed.is_(False),
            InventoryTransaction.is_rejected.is_(False),
            InventoryTransaction.quantity > 0,
        )
    )
    pending_out = session.scalar(
        select(func.coalesce(func.sum(InventoryTransaction.quantity), 0)).where(
            InventoryTransaction.is_confirmed.is_(False),
            InventoryTransaction.is_rejected.is_(False),
            InventoryTransaction.quantity < 0,
        )
    )
    return {"pending_in": int(pending_in or 0), "pending_out": int(-(pending_out or 0))}


def list_transactions(
    session: Session,
    *,
    category_code: Optional[str] = None,
    confirmed: Optional[bool] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[InventoryTransaction]:
    stmt = select(InventoryTransaction).order_by(InventoryTransaction.created_at.desc(), InventoryTransaction.id.desc())
    if category_code:
        stmt = stmt.where(InventoryTransaction.category_code == category_code)
    if confirmed is not None:
        stmt = stmt.where(InventoryTransaction.is_confirmed.is_(confirmed))
    if start:
        stmt = stmt.where(InventoryTransaction.created_at >= start)
    if end:
        stmt = stmt.where(InventoryTransaction.created_at <= end)
    return list(session.scalars(stmt.offset(offset).limit(limit)).all())


def count_transactions(session: Session, *, category_code: Optional[str] = None, confirmed: Optional[bool] = None) -> int:
    stmt = select(func.count(InventoryTransaction.id))
    if category_code:
        stmt = stmt.where(InventoryTransaction.category_code == category_code)
    if confirmed is not None:
        stmt = stmt.where(InventoryTransaction.is_confirmed.is_(confirmed))
    return int(session.scalar(stmt) or 0)


def direction_for_transaction(transaction_type: str) -> Direction:
    try:
        tx_type = TransactionType(transaction_type)
    except ValueError:
        return Direction.NONE
    if tx_type in POSITIVE_TYPES:
        return Direction.IN
    if tx_type in NEGATIVE_TYPES:
        return Direction.OUT
    return Direction.NONE


def iter_balances(session: Session) -> Iterable[CategoryBalance]:
    return balances(session)
