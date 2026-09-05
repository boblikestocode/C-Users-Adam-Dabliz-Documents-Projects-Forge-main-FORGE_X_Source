from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from database.services.audit import AuditContext, verify_audit_chain
from database.services.connection import connect
from database.services.integrity import run_health_gate
from database.services.registry_cache import RegistryCacheEntity, install_registry_cache
from database.validation.generate_synthetic import PROFILES, populate


ROOT = Path(__file__).resolve().parents[2]


def audit(timestamp: str) -> AuditContext:
    return AuditContext(
        event_type="Registry Cache Imported", actor_user_id="system",
        effective_authority="System", occurred_at_utc=timestamp,
        display_timezone="America/New_York", workstation_session="registry-cache-test",
        application_version="test", action_method="Automated Test",
        reason_code="REGISTRY_REFRESH",
    )


class RegistryCacheServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "database" / "tests")
        path = Path(self.temp.name) / "registry-cache.db"
        populate(path, PROFILES["smoke"])
        self.connection = connect(path)

    def tearDown(self) -> None:
        self.connection.close()
        self.temp.cleanup()

    def test_verified_generation_atomically_supersedes_prior_cache(self) -> None:
        generation_id = install_registry_cache(
            self.connection, registry_publication_id="registry-publication-2",
            registry_version="v2",
            entities=(RegistryCacheEntity(
                "Commodity", "commodity-2",
                {"commodity_code": "C200", "locked_name": "Stampings"},
            ),),
            digital_signature="valid-signature",
            signature_verifier=lambda payload, signature: signature == "valid-signature",
            imported_at_utc="2026-09-04T15:00:00Z",
            expires_at_utc="2026-10-04T15:00:00Z",
            audit=audit("2026-09-04T15:00:00Z"),
        )
        active = self.connection.execute(
            "SELECT cache_generation_id FROM v_current_active_registry_cache"
        ).fetchall()
        self.assertEqual([row[0] for row in active], [generation_id])
        statuses = self.connection.execute(
            """SELECT activation_status, COUNT(*) FROM v_current_registry_cache_status
               GROUP BY activation_status"""
        ).fetchall()
        self.assertEqual({row[0]: row[1] for row in statuses}, {"Active": 1, "Superseded": 1})
        self.assertEqual(verify_audit_chain(self.connection), [])

    def test_invalid_signature_is_rejected_without_displacing_active_cache(self) -> None:
        prior = self.connection.execute(
            "SELECT cache_generation_id FROM v_current_active_registry_cache"
        ).fetchone()[0]
        rejected = install_registry_cache(
            self.connection, registry_publication_id="registry-publication-invalid",
            registry_version="invalid",
            entities=(RegistryCacheEntity("Commodity", "bad", {"name": "Bad"}),),
            digital_signature="invalid",
            signature_verifier=lambda payload, signature: False,
            imported_at_utc="2026-09-04T16:00:00Z", expires_at_utc=None,
            audit=audit("2026-09-04T16:00:00Z"),
        )
        self.assertEqual(self.connection.execute(
            "SELECT activation_status FROM v_current_registry_cache_status WHERE cache_generation_id = ?",
            (rejected,),
        ).fetchone()[0], "Rejected")
        self.assertEqual(self.connection.execute(
            "SELECT cache_generation_id FROM v_current_active_registry_cache"
        ).fetchone()[0], prior)

    def test_declared_expiry_blocks_health_without_inventing_refresh_interval(self) -> None:
        codes = {finding.code for finding in run_health_gate(
            self.connection, as_of_utc="2100-01-01T00:00:00Z"
        )}
        self.assertIn("REGISTRY_CACHE_EXPIRED", codes)


if __name__ == "__main__":
    unittest.main()
