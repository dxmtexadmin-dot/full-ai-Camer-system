"""Inventory ledger: append-only semantics, pending candidates, balances."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models import InventoryTransaction
from app.models.enums import EventType, TransactionType
from app.services import event_service, inventory_service


def test_signed_quantity_normalises_signs():
    assert inventory_service.signed_quantity(TransactionType.INITIAL, 5) == 5
    assert inventory_service.signed_quantity(TransactionType.RECEIVED, 2) == 2
    assert inventory_service.signed_quantity(TransactionType.REMOVED, 3) == -3
    assert inventory_service.signed_quantity(TransactionType.REMOVED, -3) == -3
    assert inventory_service.signed_quantity(TransactionType.RETURNED, 1) == 1
    assert inventory_service.signed_quantity(TransactionType.TRANSFER, 4) == 0
    # Corrections keep whatever sign the operator typed.
    assert inventory_service.signed_quantity(TransactionType.CORRECTION, -2) == -2


def test_balance_counts_only_confirmed_rows(session):
    inventory_service.record(session, category_code="monitor", transaction_type=TransactionType.INITIAL, quantity=10)
    inventory_service.record(session, category_code="monitor", transaction_type=TransactionType.RECEIVED, quantity=2)
    inventory_service.record(session, category_code="monitor", transaction_type=TransactionType.REMOVED, quantity=3)
    session.commit()

    balance = inventory_service.balance_for(session, "monitor")
    assert balance.initial == 10
    assert balance.expected == 9
    assert balance.pending_in == 0
    assert balance.pending_out == 0
    assert balance.projected == 9


def test_pending_candidate_does_not_move_balance(session):
    inventory_service.record(session, category_code="laptop", transaction_type=TransactionType.INITIAL, quantity=4)
    inventory_service.record(
        session, category_code="laptop", transaction_type=TransactionType.REMOVED, quantity=1, is_confirmed=False
    )
    session.commit()

    balance = inventory_service.balance_for(session, "laptop")
    assert balance.expected == 4
    assert balance.pending_out == 1
    assert balance.projected == 3
    assert balance.has_issue is True


def test_vision_movement_creates_unconfirmed_candidate(session):
    event, created = event_service.create_event(
        session,
        event_type=EventType.EQUIPMENT_TAKEN_OUT,
        camera_code="CAM-01",
        equipment_category="monitor",
        object_track_id=7,
        person_track_id=1,
    )
    session.commit()
    assert created is True

    rows = inventory_service.event_transactions(session, event.id)
    assert len(rows) == 1
    assert rows[0].is_confirmed is False
    assert rows[0].quantity == -1
    assert rows[0].source == "vision"
    session.refresh(rows[0])
    assert inventory_service.balance_for(session, "monitor").expected == 0


def test_confirm_event_promotes_candidate(session):
    inventory_service.record(session, category_code="monitor", transaction_type=TransactionType.INITIAL, quantity=6)
    event, _ = event_service.create_event(
        session,
        event_type=EventType.EQUIPMENT_TAKEN_OUT,
        camera_code="CAM-01",
        equipment_category="monitor",
        object_track_id=3,
        person_track_id=2,
    )
    session.commit()

    inventory_service.confirm_event(session, event)
    session.commit()

    balance = inventory_service.balance_for(session, "monitor")
    assert balance.expected == 5
    assert balance.pending_out == 0
    rows = inventory_service.event_transactions(session, event.id)
    assert all(row.is_confirmed for row in rows)


def test_reject_event_keeps_balance_untouched(session):
    inventory_service.record(session, category_code="monitor", transaction_type=TransactionType.INITIAL, quantity=6)
    event, _ = event_service.create_event(
        session,
        event_type=EventType.EQUIPMENT_TAKEN_OUT,
        camera_code="CAM-01",
        equipment_category="monitor",
        object_track_id=3,
        person_track_id=2,
    )
    session.commit()

    inventory_service.reject_event(session, event, reason="Noto‘g‘ri aniqlangan")
    session.commit()

    balance = inventory_service.balance_for(session, "monitor")
    assert balance.expected == 6
    assert balance.pending_out == 0
    rows = inventory_service.event_transactions(session, event.id)
    assert rows and not rows[0].is_confirmed
    assert "Rad etildi" in (rows[0].reason or "")


def test_correction_is_a_new_row_not_an_edit(session):
    inventory_service.record(session, category_code="printer", transaction_type=TransactionType.INITIAL, quantity=3)
    inventory_service.record(session, category_code="printer", transaction_type=TransactionType.CORRECTION, quantity=-1)
    session.commit()

    total = session.scalar(select(func.count(InventoryTransaction.id)))
    assert total == 2
    assert inventory_service.balance_for(session, "printer").expected == 2


def test_transfer_changes_location_without_changing_count(session):
    asset = None
    from app.services import equipment_service

    asset = equipment_service.create_asset(
        session, category_code="monitor", location="Ombor", status="IN_STOCK"
    )
    inventory_service.record(session, category_code="monitor", transaction_type=TransactionType.INITIAL, quantity=1)
    session.commit()

    inventory_service.record(
        session,
        category_code="monitor",
        transaction_type=TransactionType.TRANSFER,
        quantity=1,
        asset_id=asset.id,
        location_from="Ombor",
        location_to="201-xona",
    )
    session.commit()

    session.refresh(asset)
    assert asset.location == "201-xona"
    assert inventory_service.balance_for(session, "monitor").expected == 1


def test_pending_movement_totals(session):
    inventory_service.record(
        session, category_code="monitor", transaction_type=TransactionType.RECEIVED, quantity=2, is_confirmed=False
    )
    inventory_service.record(
        session, category_code="monitor", transaction_type=TransactionType.REMOVED, quantity=1, is_confirmed=False
    )
    session.commit()

    totals = inventory_service.pending_movement_totals(session)
    assert totals == {"pending_in": 2, "pending_out": 1}


def test_discrepancy_compares_expected_with_registered_assets(session):
    from app.services import equipment_service

    inventory_service.record(session, category_code="monitor", transaction_type=TransactionType.INITIAL, quantity=3)
    equipment_service.create_asset(session, category_code="monitor", status="IN_STOCK")
    session.commit()

    balance = inventory_service.balance_for(session, "monitor")
    assert balance.registered_assets == 1
    assert balance.discrepancy == 2


@pytest.mark.parametrize(
    ("event_type", "expected_quantity"),
    [
        (EventType.EQUIPMENT_BROUGHT_IN, 1),
        (EventType.EQUIPMENT_RETURNED, 1),
        (EventType.EQUIPMENT_TAKEN_OUT, -1),
    ],
)
def test_candidate_direction_follows_event_type(session, event_type, expected_quantity):
    event, _ = event_service.create_event(
        session,
        event_type=event_type,
        camera_code="CAM-01",
        equipment_category="keyboard",
        object_track_id=11,
        person_track_id=4,
    )
    session.commit()
    rows = inventory_service.event_transactions(session, event.id)
    assert [row.quantity for row in rows] == [expected_quantity]
