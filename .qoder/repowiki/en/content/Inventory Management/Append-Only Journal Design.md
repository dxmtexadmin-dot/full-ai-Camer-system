# Append-Only Journal Design

<cite>
**Referenced Files in This Document**
- [inventory_service.py](file://app/services/inventory_service.py)
- [ledger.py](file://app/models/ledger.py)
- [enums.py](file://app/models/enums.py)
- [equipment_service.py](file://app/services/equipment_service.py)
- [equipment.py](file://app/api/equipment.py)
- [audit_service.py](file://app/services/audit_service.py)
- [test_inventory.py](file://tests/test_inventory.py)
- [README.md](file://README.md)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)

## Introduction
This document explains the append-only inventory journal design used by the system. The core principle is simple: inventory transactions are immutable once written. They are never edited or deleted. If a mistake is discovered, the system records a new correction entry instead of changing history. This approach preserves an audit trail, guarantees data integrity, and aligns with financial auditing standards that require a complete, tamper-evident record of every change.

The design supports both manual operations and automated vision-based movement candidates. Vision candidates are stored as unconfirmed entries until an operator confirms them. Rejected candidates remain in the ledger for audit purposes but do not affect balances. Corrections use a dedicated transaction type so auditors can distinguish intentional adjustments from normal receipts, removals, returns, or transfers.

## Project Structure
The append-only journal spans models, services, API endpoints, tests, and documentation:

- Models define the immutable ledger row and related types.
- Services enforce business rules such as sign normalization, confirmation workflows, and balance calculation.
- API endpoints expose manual operations including corrections.
- Tests assert that corrections create new rows and do not edit existing ones.
- Documentation describes the expected behavior for users and auditors.

```mermaid
graph TB
subgraph "Models"
Ledger["InventoryTransaction"]
Enums["TransactionType<br/>Direction<br/>VerificationStatus"]
end
subgraph "Services"
InvSvc["inventory_service.py"]
EquipSvc["equipment_service.py"]
AuditSvc["audit_service.py"]
end
subgraph "API"
EquipApi["equipment.py"]
end
subgraph "Tests"
TestInv["test_inventory.py"]
end
subgraph "Docs"
Readme["README.md"]
end
EquipApi --> EquipSvc
EquipSvc --> InvSvc
InvSvc --> Ledger
InvSvc --> Enums
InvSvc --> AuditSvc
TestInv --> InvSvc
Readme --> InvSvc
```

**Diagram sources**
- [ledger.py:114-157](file://app/models/ledger.py#L114-L157)
- [enums.py:113-130](file://app/models/enums.py#L113-L130)
- [inventory_service.py:1-13](file://app/services/inventory_service.py#L1-L13)
- [equipment_service.py:279-298](file://app/services/equipment_service.py#L279-L298)
- [equipment.py:208-260](file://app/api/equipment.py#L208-L260)
- [audit_service.py:13-37](file://app/services/audit_service.py#L13-L37)
- [test_inventory.py:118-125](file://tests/test_inventory.py#L118-L125)
- [README.md:286-313](file://README.md#L286-L313)

**Section sources**
- [inventory_service.py:1-13](file://app/services/inventory_service.py#L1-L13)
- [ledger.py:114-157](file://app/models/ledger.py#L114-L157)
- [enums.py:113-130](file://app/models/enums.py#L113-L130)
- [equipment_service.py:279-298](file://app/services/equipment_service.py#L279-L298)
- [equipment.py:208-260](file://app/api/equipment.py#L208-L260)
- [audit_service.py:13-37](file://app/services/audit_service.py#L13-L37)
- [test_inventory.py:118-125](file://tests/test_inventory.py#L118-L125)
- [README.md:286-313](file://README.md#L286-L313)

## Core Components
The append-only journal is implemented through these key components:

- InventoryTransaction model: Represents each ledger entry. Rows are never deleted; rejected candidates stay marked rather than removed.
- TransactionType enum: Includes INITIAL, RECEIVED, REMOVED, RETURNED, TRANSFER, and CORRECTION. CORRECTION allows signed deltas chosen by the caller.
- inventory_service.record: Creates new ledger entries and normalizes their signed quantity based on transaction type.
- inventory_service.confirm_event and reject_event: Promote or mark vision candidates without editing original rows.
- equipment_service.correct: Enforces a required reason and writes a new CORRECTION entry.
- API endpoint /api/inventory/operation: Exposes manual operations including corrections to operators.
- audit_service.record: Appends administrator actions to the audit log.

These components together ensure that historical accuracy is preserved while still allowing corrections through explicit new entries.

**Section sources**
- [ledger.py:114-157](file://app/models/ledger.py#L114-L157)
- [enums.py:113-130](file://app/models/enums.py#L113-L130)
- [inventory_service.py:53-112](file://app/services/inventory_service.py#L53-L112)
- [inventory_service.py:165-205](file://app/services/inventory_service.py#L165-L205)
- [equipment_service.py:279-298](file://app/services/equipment_service.py#L279-L298)
- [equipment.py:208-260](file://app/api/equipment.py#L208-L260)
- [audit_service.py:13-37](file://app/services/audit_service.py#L13-L37)

## Architecture Overview
The append-only journal integrates event detection, operator review, manual operations, and reporting.

```mermaid
sequenceDiagram
participant Camera as "Vision Events"
participant EventSvc as "Event Creation"
participant InvSvc as "inventory_service"
participant DB as "InventoryTransaction"
participant Operator as "Operator UI"
participant Api as "/api/inventory/operation"
participant EquipSvc as "equipment_service"
participant Audit as "audit_service"
Camera->>EventSvc : Equipment movement detected
EventSvc->>InvSvc : create_pending_movement()
InvSvc->>DB : Append unconfirmed candidate
Note over InvSvc,DB : Candidate stays out of official balance
Operator->>Api : Confirm or Reject event
Api->>InvSvc : confirm_event() or reject_event()
InvSvc->>DB : Mark candidate confirmed/rejected
InvSvc->>Audit : Record operator action
Operator->>Api : Submit correction (CORRECTION)
Api->>EquipSvc : correct(category_code, delta, reason)
EquipSvc->>InvSvc : record(CORRECTION)
InvSvc->>DB : Append new correction row
InvSvc->>Audit : Record correction action
```

**Diagram sources**
- [inventory_service.py:132-156](file://app/services/inventory_service.py#L132-L156)
- [inventory_service.py:165-205](file://app/services/inventory_service.py#L165-L205)
- [equipment.py:208-260](file://app/api/equipment.py#L208-L260)
- [equipment_service.py:279-298](file://app/services/equipment_service.py#L279-L298)
- [audit_service.py:13-37](file://app/services/audit_service.py#L13-L37)

## Detailed Component Analysis

### Append-Only Ledger Model
The ledger model enforces immutability at the data layer:

- Each transaction is a separate row with a timestamp and source metadata.
- Rejection does not delete rows; it marks them as rejected so they stop counting but remain visible for audit.
- Only confirmed rows contribute to the official balance.

```mermaid
classDiagram
class InventoryTransaction {
+int id
+string category_code
+int asset_id
+int event_id
+string transaction_type
+int quantity
+string location_from
+string location_to
+bool is_confirmed
+bool is_rejected
+string source
+string reason
+int created_by_user_id
+datetime created_at
}
class TransactionType {
<<enum>>
+INITIAL
+RECEIVED
+REMOVED
+RETURNED
+TRANSFER
+CORRECTION
}
InventoryTransaction --> TransactionType : "uses"
```

**Diagram sources**
- [ledger.py:114-157](file://app/models/ledger.py#L114-L157)
- [enums.py:113-130](file://app/models/enums.py#L113-L130)

**Section sources**
- [ledger.py:114-157](file://app/models/ledger.py#L114-L157)
- [enums.py:113-130](file://app/models/enums.py#L113-L130)

### Correction Workflow
Corrections are new entries, not edits. The service normalizes signs so that most transaction types have predictable directionality, while CORRECTION lets the caller choose the sign explicitly.

```mermaid
flowchart TD
Start(["Correction Request"]) --> ValidateReason["Validate reason is present"]
ValidateReason --> ReasonValid{"Reason provided?"}
ReasonValid --> |No| Error["Raise validation error"]
ReasonValid --> |Yes| NormalizeSign["Normalize signed quantity"]
NormalizeSign --> SignType{"Transaction type?"}
SignType --> |CORRECTION| KeepSign["Keep caller-provided sign"]
SignType --> |Other| ApplyRule["Apply positive/negative rule"]
KeepSign --> CreateRow["Append new InventoryTransaction"]
ApplyRule --> CreateRow
CreateRow --> Commit["Commit transaction"]
Commit --> End(["Ledger updated with correction"])
Error --> End
```

**Diagram sources**
- [equipment_service.py:279-298](file://app/services/equipment_service.py#L279-L298)
- [inventory_service.py:53-112](file://app/services/inventory_service.py#L53-L112)

**Section sources**
- [equipment_service.py:279-298](file://app/services/equipment_service.py#L279-L298)
- [inventory_service.py:53-112](file://app/services/inventory_service.py#L53-L112)

### Vision Candidates and Confirmation
Vision-based movements are recorded as pending candidates. They do not move the official balance until an operator confirms them. Rejections keep the candidate row for audit but exclude it from pending totals.

```mermaid
sequenceDiagram
participant Vision as "Vision Pipeline"
participant InvSvc as "inventory_service"
participant DB as "InventoryTransaction"
participant Operator as "Operator"
participant Audit as "audit_service"
Vision->>InvSvc : create_pending_movement(event)
InvSvc->>DB : Append candidate with is_confirmed=False
Note over InvSvc,DB : Candidate excluded from balance
Operator->>InvSvc : confirm_event(event)
InvSvc->>DB : Set is_confirmed=True
InvSvc->>Audit : Record "inventory.confirm"
Operator->>InvSvc : reject_event(event, reason)
InvSvc->>DB : Set is_confirmed=False, is_rejected=True
InvSvc->>Audit : Record "inventory.reject"
```

**Diagram sources**
- [inventory_service.py:132-156](file://app/services/inventory_service.py#L132-L156)
- [inventory_service.py:165-205](file://app/services/inventory_service.py#L165-L205)
- [audit_service.py:13-37](file://app/services/audit_service.py#L13-L37)

**Section sources**
- [inventory_service.py:132-156](file://app/services/inventory_service.py#L132-L156)
- [inventory_service.py:165-205](file://app/services/inventory_service.py#L165-L205)
- [audit_service.py:13-37](file://app/services/audit_service.py#L13-L37)

### Manual Operations API
The API exposes manual inventory operations, including corrections. Operators submit operation forms, which are validated and routed to the appropriate service method.

```mermaid
sequenceDiagram
participant User as "Operator"
participant Api as "/api/inventory/operation"
participant EquipSvc as "equipment_service"
participant InvSvc as "inventory_service"
participant DB as "InventoryTransaction"
User->>Api : POST operation=correct, delta, reason
Api->>EquipSvc : correct(category_code, delta, reason)
EquipSvc->>InvSvc : record(CORRECTION, quantity=delta)
InvSvc->>DB : Append new correction row
Api-->>User : Redirect with success message
```

**Diagram sources**
- [equipment.py:208-260](file://app/api/equipment.py#L208-L260)
- [equipment_service.py:279-298](file://app/services/equipment_service.py#L279-L298)
- [inventory_service.py:66-112](file://app/services/inventory_service.py#L66-L112)

**Section sources**
- [equipment.py:208-260](file://app/api/equipment.py#L208-L260)
- [equipment_service.py:279-298](file://app/services/equipment_service.py#L279-L298)
- [inventory_service.py:66-112](file://app/services/inventory_service.py#L66-L112)

### Balance Calculation and Pending Totals
Balances are computed from confirmed transactions only. Pending candidates are tracked separately so operators can see incoming and outgoing movements awaiting confirmation.

```mermaid
flowchart TD
Start(["Compute Balances"]) --> LoadRows["Load non-rejected transactions"]
LoadRows --> Bucket["Bucket by category_code<br/>separate confirmed vs pending"]
Bucket --> InitialSum["Sum INITIAL rows per category"]
InitialSum --> Expected["expected = sum of confirmed rows"]
Expected --> Pending["pending_in and pending_out from unconfirmed rows"]
Pending --> Projected["projected = expected + pending_in - pending_out"]
Projected --> Registered["registered_assets from asset registry"]
Registered --> Discrepancy["discrepancy = expected - registered_assets"]
Discrepancy --> Result(["CategoryBalance object"])
```

**Diagram sources**
- [inventory_service.py:215-273](file://app/services/inventory_service.py#L215-L273)
- [inventory_service.py:290-305](file://app/services/inventory_service.py#L290-L305)

**Section sources**
- [inventory_service.py:215-273](file://app/services/inventory_service.py#L215-L273)
- [inventory_service.py:290-305](file://app/services/inventory_service.py#L290-L305)

## Dependency Analysis
The following diagram shows how the append-only journal depends on models, services, and APIs:

```mermaid
graph LR
Enums["enums.py"] --> Ledger["ledger.py"]
Enums --> InvSvc["inventory_service.py"]
Ledger --> InvSvc
InvSvc --> EquipSvc["equipment_service.py"]
EquipSvc --> Api["equipment.py"]
InvSvc --> AuditSvc["audit_service.py"]
TestInv["test_inventory.py"] --> InvSvc
Readme["README.md"] --> InvSvc
```

**Diagram sources**
- [enums.py:113-130](file://app/models/enums.py#L113-L130)
- [ledger.py:114-157](file://app/models/ledger.py#L114-L157)
- [inventory_service.py:1-13](file://app/services/inventory_service.py#L1-L13)
- [equipment_service.py:279-298](file://app/services/equipment_service.py#L279-L298)
- [equipment.py:208-260](file://app/api/equipment.py#L208-L260)
- [audit_service.py:13-37](file://app/services/audit_service.py#L13-L37)
- [test_inventory.py:118-125](file://tests/test_inventory.py#L118-L125)
- [README.md:286-313](file://README.md#L286-L313)

**Section sources**
- [enums.py:113-130](file://app/models/enums.py#L113-L130)
- [ledger.py:114-157](file://app/models/ledger.py#L114-L157)
- [inventory_service.py:1-13](file://app/services/inventory_service.py#L1-L13)
- [equipment_service.py:279-298](file://app/services/equipment_service.py#L279-L298)
- [equipment.py:208-260](file://app/api/equipment.py#L208-L260)
- [audit_service.py:13-37](file://app/services/audit_service.py#L13-L37)
- [test_inventory.py:118-125](file://tests/test_inventory.py#L118-L125)
- [README.md:286-313](file://README.md#L286-L313)

## Performance Considerations
- Append-only growth: Because rows are never deleted, the ledger grows monotonically. Queries should filter by category and confirmation status where possible.
- Indexing: The model indexes category_code, created_at, is_confirmed, and is_rejected to support efficient balance and listing queries.
- Batch operations: When creating many corrections or initial stock entries, batch commits reduce overhead.
- Reporting: Balance computation aggregates confirmed rows; avoid unnecessary joins when only counts are needed.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and how the system handles them:

- Missing correction reason: The correction service requires a reason. If omitted, a validation error is raised before any row is appended.
- Vision candidate not affecting balance: Unconfirmed candidates are intentionally excluded from the official balance. Confirm them to include them.
- Rejected candidate still visible: Rejected rows remain in the ledger for audit purposes but do not affect balances or pending totals.
- Discrepancy alert: A discrepancy indicates that the expected count differs from the number of registered assets. Investigate missing registrations or incorrect transactions.

Operational checks:
- Verify that corrections are logged as new rows, not edits.
- Confirm that audit logs capture operator actions for confirmations and rejections.
- Review pending movement totals to identify outstanding approvals.

**Section sources**
- [equipment_service.py:279-298](file://app/services/equipment_service.py#L279-L298)
- [inventory_service.py:165-205](file://app/services/inventory_service.py#L165-L205)
- [inventory_service.py:215-273](file://app/services/inventory_service.py#L215-L273)
- [audit_service.py:13-37](file://app/services/audit_service.py#L13-L37)

## Conclusion
The append-only inventory journal ensures that every change is recorded as a new, immutable row. Corrections are explicit, traceable, and auditable. Vision candidates are safely isolated from official balances until human confirmation. Rejections preserve context without altering history. Together, these mechanisms provide strong data integrity, a clear audit trail, and compliance-friendly behavior aligned with financial auditing standards.

[No sources needed since this section summarizes without analyzing specific files]