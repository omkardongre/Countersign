# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Durable, tenant-scoped storage for the external Sponsor QA receiver."""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import threading
from dataclasses import dataclass
from typing import Protocol

from app.domain.post_qp_models import SponsorNotificationRequest

COLLECTION_ENV = "COUNTERSIGN_SPONSOR_QA_COLLECTION"
DATABASE_ENV = "COUNTERSIGN_SPONSOR_QA_DATABASE"
INMEMORY_ENV = "COUNTERSIGN_SPONSOR_QA_INMEMORY"
DEFAULT_COLLECTION = "sponsor_qa_notifications"


class NotificationConflictError(Exception):
    """The notification id already exists with different immutable content."""


@dataclass(frozen=True)
class StoredNotification:
    notification_id: str
    tenant_id: str
    case_id: str
    batch_id: str
    qp_outcome: str
    qp_recorded_at: datetime.datetime
    received_at: datetime.datetime
    idempotency_key: str
    decision_authority: str = "none"

    def as_dict(self) -> dict:
        return {
            "notification_id": self.notification_id,
            "tenant_id": self.tenant_id,
            "case_id": self.case_id,
            "batch_id": self.batch_id,
            "qp_outcome": self.qp_outcome,
            "qp_recorded_at": self.qp_recorded_at,
            "received_at": self.received_at,
            "idempotency_key": self.idempotency_key,
            "decision_authority": self.decision_authority,
        }


class NotificationStore(Protocol):
    def create_or_get(
        self, request: SponsorNotificationRequest
    ) -> tuple[StoredNotification, bool]: ...

    def list_for_tenant(self, tenant_id: str, *, limit: int = 100) -> list[StoredNotification]: ...


def _notification_id(tenant_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}\0{idempotency_key}".encode()).hexdigest()
    return f"sn-{digest[:32]}"


def _payload_fingerprint(request: SponsorNotificationRequest) -> str:
    canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _record(request: SponsorNotificationRequest) -> StoredNotification:
    return StoredNotification(
        notification_id=_notification_id(request.tenant_id, request.idempotency_key),
        tenant_id=request.tenant_id,
        case_id=request.case_id,
        batch_id=request.batch_id,
        qp_outcome=request.qp_outcome,
        qp_recorded_at=request.qp_recorded_at,
        received_at=datetime.datetime.now(datetime.UTC),
        idempotency_key=request.idempotency_key,
    )


def _from_dict(data: dict) -> StoredNotification:
    return StoredNotification(**{key: data[key] for key in StoredNotification.__dataclass_fields__})


class InMemoryNotificationStore:
    def __init__(self) -> None:
        self._records: dict[str, tuple[str, StoredNotification]] = {}
        self._lock = threading.Lock()

    def create_or_get(self, request: SponsorNotificationRequest) -> tuple[StoredNotification, bool]:
        notification_id = _notification_id(request.tenant_id, request.idempotency_key)
        fingerprint = _payload_fingerprint(request)
        with self._lock:
            existing = self._records.get(notification_id)
            if existing:
                if existing[0] != fingerprint:
                    raise NotificationConflictError("Notification id was already used for different content.")
                return existing[1], True
            record = _record(request)
            self._records[notification_id] = (fingerprint, record)
            return record, False

    def list_for_tenant(self, tenant_id: str, *, limit: int = 100) -> list[StoredNotification]:
        records = [record for _, record in self._records.values() if record.tenant_id == tenant_id]
        return sorted(records, key=lambda item: item.received_at, reverse=True)[:limit]

    def reset(self) -> None:
        with self._lock:
            self._records.clear()


class FirestoreNotificationStore:
    """Firestore transaction provides atomic create-or-reuse idempotency."""

    def __init__(self, client=None) -> None:
        if client is None:
            from google.cloud import firestore

            client = firestore.Client(
                project=os.environ.get("GOOGLE_CLOUD_PROJECT") or None,
                database=os.environ.get(DATABASE_ENV, "(default)"),
            )
        self._client = client
        self._collection = client.collection(os.environ.get(COLLECTION_ENV, DEFAULT_COLLECTION))

    def create_or_get(self, request: SponsorNotificationRequest) -> tuple[StoredNotification, bool]:
        from google.cloud import firestore

        fingerprint = _payload_fingerprint(request)
        reference = self._collection.document(_notification_id(request.tenant_id, request.idempotency_key))
        transaction = self._client.transaction()

        @firestore.transactional
        def persist(txn):
            snapshot = reference.get(transaction=txn)
            if snapshot.exists:
                data = snapshot.to_dict()
                if data.get("payload_fingerprint") != fingerprint:
                    raise NotificationConflictError("Notification id was already used for different content.")
                return _from_dict(data), True
            record = _record(request)
            txn.create(reference, record.as_dict() | {"payload_fingerprint": fingerprint})
            return record, False

        return persist(transaction)

    def list_for_tenant(self, tenant_id: str, *, limit: int = 100) -> list[StoredNotification]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        query = (
            self._collection.where(filter=FieldFilter("tenant_id", "==", tenant_id))
            .order_by("received_at", direction="DESCENDING")
            .limit(limit)
        )
        return [_from_dict(snapshot.to_dict()) for snapshot in query.stream()]


_store: NotificationStore | None = None


def get_notification_store() -> NotificationStore:
    global _store
    if _store is None:
        _store = (
            InMemoryNotificationStore()
            if os.environ.get(INMEMORY_ENV) == "1"
            else FirestoreNotificationStore()
        )
    return _store


def reset_notification_store_cache() -> None:
    global _store
    _store = None
