from __future__ import annotations

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from database.services.audit import AuditContext, verify_audit_chain
from database.services.connection import connect
from database.services.publication import (
    begin_publication_attempt,
    complete_publication_attempt,
    create_verified_checkpoint,
    prepare_publication,
    queue_publication,
    recover_expired_publication_leases,
    run_automated_restore_test,
    verify_restore_candidate,
)
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


def audit(event_type: str, timestamp: str) -> AuditContext:
    return AuditContext(
        event_type=event_type,
        actor_user_id="system",
        effective_authority="System",
        occurred_at_utc=timestamp,
        display_timezone="America/New_York",
        workstation_session="publication-test",
        application_version="test",
        action_method="Automated Test",
        reason_code="TEST",
    )


def signer(payload: bytes) -> str:
    return "TEST-SIGNATURE:" + hashlib.sha256(b"test-key" + payload).hexdigest()


class PublicationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        self.base = Path(self.temp.name)
        self.database_path = self.base / "commodity.db"
        populate(self.database_path, PROFILES["smoke"])
        self.connection = connect(self.database_path)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def _checkpoint(self):
        checkpoint = create_verified_checkpoint(
            self.connection,
            checkpoint_path=self.base / "checkpoint.db",
            created_at_utc="2026-09-03T18:00:00Z",
            audit=audit("Checkpoint Verified", "2026-09-03T18:00:00Z"),
        )
        restored = run_automated_restore_test(
            self.connection, checkpoint=checkpoint,
            restored_path=self.base / "automated-restore.db",
            started_at_utc="2026-09-03T18:00:10Z",
            completed_at_utc="2026-09-03T18:00:20Z",
            audit=audit("Automated Restore Verified", "2026-09-03T18:00:20Z"),
        )
        self.assertEqual(restored.restore_status, "Verified")
        return checkpoint

    def test_publication_requires_recorded_automated_restore(self) -> None:
        checkpoint = create_verified_checkpoint(
            self.connection, checkpoint_path=self.base / "unrestored.db",
            created_at_utc="2026-09-03T18:00:00Z",
            audit=audit("Checkpoint Verified", "2026-09-03T18:00:00Z"),
        )
        with self.assertRaisesRegex(ValueError, "automated restore"):
            prepare_publication(
                self.connection, checkpoint=checkpoint,
                destination_locator="sharepoint://C100/Database Snapshots",
                signer=signer, initiated_at_utc="2026-09-03T18:01:00Z",
                audit=audit("Publication Prepared", "2026-09-03T18:01:00Z"),
            )

    def test_checkpoint_publication_and_queue_are_verified_and_idempotent(self) -> None:
        checkpoint = self._checkpoint()
        self.assertEqual(
            verify_restore_candidate(
                checkpoint.checkpoint_path, checkpoint.verification_manifest
            ),
            [],
        )
        self.assertEqual(
            checkpoint.verification_manifest["record_counts"]["pbd_observation"],
            self.connection.execute("SELECT COUNT(*) FROM pbd_observation").fetchone()[0],
        )
        self.assertEqual(len(checkpoint.representative_reproduction_hash), 64)
        publication = prepare_publication(
            self.connection,
            checkpoint=checkpoint,
            destination_locator="sharepoint://C100/Database Snapshots",
            signer=signer,
            initiated_at_utc="2026-09-03T18:01:00Z",
            audit=audit("Publication Prepared", "2026-09-03T18:01:00Z"),
        )
        first_queue_id = queue_publication(
            self.connection,
            publication_id=publication.publication_id,
            recorded_at_utc="2026-09-03T18:02:00Z",
            audit=audit("Publication Queued", "2026-09-03T18:02:00Z"),
        )
        second_queue_id = queue_publication(
            self.connection,
            publication_id=publication.publication_id,
            recorded_at_utc="2026-09-03T18:03:00Z",
            audit=audit("Publication Queued", "2026-09-03T18:03:00Z"),
        )
        self.assertEqual(first_queue_id, second_queue_id)
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM sync_queue_item").fetchone()[0], 1
        )
        current = self.connection.execute(
            "SELECT publication_status FROM v_current_publication_status WHERE publication_id = ?",
            (publication.publication_id,),
        ).fetchone()[0]
        self.assertEqual(current, "Queued")
        self.assertEqual(verify_audit_chain(self.connection), [])
        version_payload = self.connection.execute(
            """SELECT version_payload FROM publication_manifest_entry
               WHERE publication_id = ?""", (publication.publication_id,),
        ).fetchone()[0]
        self.assertIn(checkpoint.verification_manifest_hash, version_payload)

    def test_changed_checkpoint_is_rejected_before_publication(self) -> None:
        checkpoint = self._checkpoint()
        with checkpoint.checkpoint_path.open("ab") as stream:
            stream.write(b"tamper")
        with self.assertRaisesRegex(RuntimeError, "hash no longer matches"):
            prepare_publication(
                self.connection,
                checkpoint=checkpoint,
                destination_locator="sharepoint://C100/Database Snapshots",
                signer=signer,
                initiated_at_utc="2026-09-03T18:01:00Z",
                audit=audit("Publication Prepared", "2026-09-03T18:01:00Z"),
            )
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM publication").fetchone()[0], 0)

    def test_forged_checkpoint_manifest_is_rejected_before_publication(self) -> None:
        checkpoint = self._checkpoint()
        forged = replace(checkpoint, verification_manifest_hash="0" * 64)
        with self.assertRaisesRegex(RuntimeError, "verification manifest"):
            prepare_publication(
                self.connection, checkpoint=forged,
                destination_locator="sharepoint://C100/Database Snapshots",
                signer=signer, initiated_at_utc="2026-09-03T18:01:00Z",
                audit=audit("Publication Prepared", "2026-09-03T18:01:00Z"),
            )
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM publication").fetchone()[0], 0)

    def test_publication_and_status_history_are_immutable(self) -> None:
        checkpoint = self._checkpoint()
        publication = prepare_publication(
            self.connection,
            checkpoint=checkpoint,
            destination_locator="sharepoint://C100/Database Snapshots",
            signer=signer,
            initiated_at_utc="2026-09-03T18:01:00Z",
            audit=audit("Publication Prepared", "2026-09-03T18:01:00Z"),
        )
        with self.assertRaisesRegex(Exception, "publication is immutable"):
            self.connection.execute(
                "UPDATE publication SET destination_locator = 'changed' WHERE publication_id = ?",
                (publication.publication_id,),
            )
        with self.assertRaisesRegex(Exception, "publication_status_event is immutable"):
            self.connection.execute(
                "UPDATE publication_status_event SET publication_status = 'Failed' WHERE publication_id = ?",
                (publication.publication_id,),
            )

    def test_offline_failure_retries_same_immutable_package_then_publishes(self) -> None:
        checkpoint = self._checkpoint()
        publication = prepare_publication(
            self.connection, checkpoint=checkpoint,
            destination_locator="sharepoint://C100/Database Snapshots", signer=signer,
            initiated_at_utc="2026-09-03T18:01:00Z",
            audit=audit("Publication Prepared", "2026-09-03T18:01:00Z"),
        )
        queue_id = queue_publication(
            self.connection, publication_id=publication.publication_id,
            recorded_at_utc="2026-09-03T18:02:00Z",
            audit=audit("Publication Queued", "2026-09-03T18:02:00Z"),
        )
        first = begin_publication_attempt(
            self.connection, sync_queue_item_id=queue_id, lease_owner="worker-1",
            lease_expires_at_utc="2026-09-03T18:10:00Z",
            authority_verification_reference="authority-proof-1",
            authority_verified_online_at_utc="2026-09-03T18:03:00Z",
            expected_predecessor_publication_id=None,
            observed_remote_publication_id=None,
            started_at_utc="2026-09-03T18:03:00Z",
            audit=audit("Publication Upload Started", "2026-09-03T18:03:00Z"),
        )
        self.assertTrue(first.may_upload)
        self.assertEqual(complete_publication_attempt(
            self.connection, publication_attempt_id=first.publication_attempt_id,
            lease_owner="worker-1", outcome="Retry Pending",
            observed_remote_hash=None, detail="SharePoint unavailable",
            completed_at_utc="2026-09-03T18:04:00Z",
            audit=audit("Publication Retry Pending", "2026-09-03T18:04:00Z"),
        ), "Retry Pending")
        second = begin_publication_attempt(
            self.connection, sync_queue_item_id=queue_id, lease_owner="worker-2",
            lease_expires_at_utc="2026-09-03T18:20:00Z",
            authority_verification_reference="authority-proof-2",
            authority_verified_online_at_utc="2026-09-03T18:11:00Z",
            expected_predecessor_publication_id=None,
            observed_remote_publication_id=None,
            started_at_utc="2026-09-03T18:11:00Z",
            audit=audit("Publication Upload Started", "2026-09-03T18:11:00Z"),
        )
        self.assertEqual(complete_publication_attempt(
            self.connection, publication_attempt_id=second.publication_attempt_id,
            lease_owner="worker-2", outcome="Published",
            observed_remote_hash=publication.manifest_hash,
            detail="Remote upload verified",
            completed_at_utc="2026-09-03T18:12:00Z",
            audit=audit("Publication Published", "2026-09-03T18:12:00Z"),
        ), "Published")
        queue = self.connection.execute(
            "SELECT queue_status, retry_count FROM sync_queue_item WHERE sync_queue_item_id = ?",
            (queue_id,),
        ).fetchone()
        self.assertEqual(tuple(queue), ("Complete", 1))
        statuses = [row[0] for row in self.connection.execute(
            """SELECT publication_status FROM publication_status_event
               WHERE publication_id = ? ORDER BY recorded_at_utc,
               rowid""", (publication.publication_id,),
        )]
        self.assertIn("Retry Pending", statuses)
        self.assertEqual(statuses[-2:], ["Remote Verification", "Published"])
        self.assertEqual(self.connection.execute(
            """SELECT publication_status FROM v_current_publication_status
               WHERE publication_id = ?""", (publication.publication_id,),
        ).fetchone()[0], "Published")
        self.assertEqual(verify_audit_chain(self.connection), [])

    def test_newer_remote_predecessor_creates_conflict_without_upload(self) -> None:
        checkpoint = self._checkpoint()
        publication = prepare_publication(
            self.connection, checkpoint=checkpoint,
            destination_locator="sharepoint://C100/Database Snapshots", signer=signer,
            initiated_at_utc="2026-09-03T19:01:00Z",
            audit=audit("Publication Prepared", "2026-09-03T19:01:00Z"),
        )
        queue_id = queue_publication(
            self.connection, publication_id=publication.publication_id,
            recorded_at_utc="2026-09-03T19:02:00Z",
            audit=audit("Publication Queued", "2026-09-03T19:02:00Z"),
        )
        attempt = begin_publication_attempt(
            self.connection, sync_queue_item_id=queue_id, lease_owner="worker-1",
            lease_expires_at_utc="2026-09-03T19:10:00Z",
            authority_verification_reference="authority-proof",
            authority_verified_online_at_utc="2026-09-03T19:03:00Z",
            expected_predecessor_publication_id="expected-remote",
            observed_remote_publication_id="newer-remote",
            started_at_utc="2026-09-03T19:03:00Z",
            audit=audit("Publication Conflict", "2026-09-03T19:03:00Z"),
        )
        self.assertFalse(attempt.may_upload)
        self.assertEqual(attempt.attempt_status, "Conflict")
        conflict = self.connection.execute(
            """SELECT remote_publication_id, conflict_status FROM sync_conflict
               WHERE local_publication_id = ?""", (publication.publication_id,),
        ).fetchone()
        self.assertEqual(tuple(conflict), ("newer-remote", "Open"))
        self.assertEqual(self.connection.execute(
            "SELECT queue_status FROM sync_queue_item WHERE sync_queue_item_id = ?",
            (queue_id,),
        ).fetchone()[0], "Conflict")

    def test_expired_worker_lease_recovers_to_retry_without_changing_package(self) -> None:
        checkpoint = self._checkpoint()
        publication = prepare_publication(
            self.connection, checkpoint=checkpoint,
            destination_locator="sharepoint://C100/Database Snapshots", signer=signer,
            initiated_at_utc="2026-09-03T20:01:00Z",
            audit=audit("Publication Prepared", "2026-09-03T20:01:00Z"),
        )
        queue_id = queue_publication(
            self.connection, publication_id=publication.publication_id,
            recorded_at_utc="2026-09-03T20:02:00Z",
            audit=audit("Publication Queued", "2026-09-03T20:02:00Z"),
        )
        begin_publication_attempt(
            self.connection, sync_queue_item_id=queue_id, lease_owner="lost-worker",
            lease_expires_at_utc="2026-09-03T20:04:00Z",
            authority_verification_reference="authority-proof",
            authority_verified_online_at_utc="2026-09-03T20:03:00Z",
            expected_predecessor_publication_id=None,
            observed_remote_publication_id=None,
            started_at_utc="2026-09-03T20:03:00Z",
            audit=audit("Publication Upload Started", "2026-09-03T20:03:00Z"),
        )
        recovered = recover_expired_publication_leases(
            self.connection, observed_at_utc="2026-09-03T20:05:00Z",
            audit=audit("Publication Lease Recovered", "2026-09-03T20:05:00Z"),
        )
        self.assertEqual(recovered, (queue_id,))
        queue = self.connection.execute(
            """SELECT queue_status, retry_count, lease_owner, lease_expires_at_utc
               FROM sync_queue_item WHERE sync_queue_item_id = ?""", (queue_id,),
        ).fetchone()
        self.assertEqual(tuple(queue), ("Retry Pending", 1, None, None))
        self.assertEqual(self.connection.execute(
            "SELECT package_hash FROM publication WHERE publication_id = ?",
            (publication.publication_id,),
        ).fetchone()[0], publication.manifest_hash)
        self.assertEqual(verify_audit_chain(self.connection), [])


if __name__ == "__main__":
    unittest.main()
