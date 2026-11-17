"""Regression tests for the schema3 mixed handoff (tf_card.py).

Covers ``validate_handoff`` and ``validate_layout`` acceptance of the schema3
``identity_mode == "volume"`` / ``allocation_mode == "mixed"`` handoff whose
``card_cid`` is explicitly null, while keeping every guard in force and
preserving the legacy schema1 behaviour.  Reuses the fixtures defined in
``test_tf_card``.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "py_modules"))

from armada_installer import tf_card  # noqa: E402

from test_tf_card import (  # noqa: E402
    CID,
    CID_UPPER,
    DISK_BYTES,
    MIB,
    META_PARTUUID,
    META_TYPE,
    OTHER_UUID,
    PRIVATE_FS_UUID,
    PRIVATE_PARTUUID,
    PRIVATE_TYPE,
    RESERVE_CONNECTED,
    RESERVE_PARTUUID,
    RESERVE_PENDING,
    SECTOR,
    handoff as v1_handoff,
    native_parts,
    native_table,
)


PRIVATE_PARTUUID_32 = PRIVATE_PARTUUID.replace("-", "")


def v3_handoff(**overrides):
    """A valid schema3 mixed handoff (card_cid is explicitly null)."""
    base = {
        "magic": "armada-tf-handoff",
        "schema_version": 3,
        "identity_mode": "volume",
        "allocation_mode": "mixed",
        "card_cid": None,
        "disk_bytes": DISK_BYTES,
        "linux_ratio": 50,
        "public_fs_uuid": "1A2B-3C4D",
        "public_partition_guid": RESERVE_PARTUUID,
        "private_partition_guid": PRIVATE_PARTUUID,
        "private_fs_uuid": PRIVATE_FS_UUID,
    }
    base.update(overrides)
    return base


class V3HandoffAcceptTest(unittest.TestCase):
    def test_accepts_canonical_v3_mixed(self):
        seen = tf_card.validate_handoff(v3_handoff())
        self.assertEqual(seen["schema_version"], 3)
        self.assertEqual(seen["identity_mode"], "volume")
        self.assertEqual(seen["allocation_mode"], "mixed")
        self.assertIsNone(seen["card_cid"])
        self.assertEqual(seen["private_partition_guid"], PRIVATE_PARTUUID)

    def test_accepts_null_public_partition_guid(self):
        seen = tf_card.validate_handoff(v3_handoff(public_partition_guid=None))
        self.assertIsNone(seen["public_partition_guid"])

    def test_accepts_private_guid_without_dashes(self):
        seen = tf_card.validate_handoff(v3_handoff(private_partition_guid=PRIVATE_PARTUUID_32))
        self.assertEqual(seen["private_partition_guid"], PRIVATE_PARTUUID)

    def test_accepts_unknown_keys(self):
        seen = tf_card.validate_handoff(v3_handoff(something_extra="x"))
        self.assertEqual(seen["something_extra"], "x")


class V3HandoffRejectTest(unittest.TestCase):
    def _reject(self, **overrides):
        with self.assertRaises(ValueError):
            tf_card.validate_handoff(v3_handoff(**overrides))

    def test_embedded_cid_not_allowed(self):
        self._reject(card_cid=CID)

    def test_missing_card_cid_key(self):
        raw = v3_handoff()
        del raw["card_cid"]
        with self.assertRaises(ValueError):
            tf_card.validate_handoff(raw)

    def test_identity_mode_missing(self):
        raw = v3_handoff()
        del raw["identity_mode"]
        with self.assertRaises(ValueError):
            tf_card.validate_handoff(raw)

    def test_identity_mode_wrong(self):
        self._reject(identity_mode="cid")

    def test_allocation_mode_wrong(self):
        self._reject(allocation_mode="linux_only")

    def test_schema2_rejected(self):
        self._reject(schema_version=2)

    def test_schema4_rejected(self):
        self._reject(schema_version=4)

    def test_schema_bool_rejected(self):
        self._reject(schema_version=True)

    def test_schema_float_rejected(self):
        self._reject(schema_version=3.0)

    def test_malformed_public_fs_uuid_rejected(self):
        self._reject(public_fs_uuid="not-a-uuid")

    def test_malformed_private_fs_uuid_rejected(self):
        self._reject(private_fs_uuid="zzzz")

    def test_malformed_private_guid_rejected(self):
        self._reject(private_partition_guid="0" * 40)

    def test_disk_bytes_bool_rejected(self):
        self._reject(disk_bytes=True)

    def test_linux_ratio_float_rejected(self):
        self._reject(linux_ratio=50.0)

    def test_ratio_out_of_range_rejected(self):
        self._reject(linux_ratio=5)

    def test_wrong_magic_rejected(self):
        self._reject(magic="other-handoff")

    def test_v1_still_requires_card_cid(self):
        raw = v1_handoff()
        raw["card_cid"] = None
        with self.assertRaises(ValueError):
            tf_card.validate_handoff(raw)

    def test_v1_still_rejects_schema2(self):
        with self.assertRaises(ValueError):
            tf_card.validate_handoff(v1_handoff(schema_version=2))


class V3LayoutAcceptTest(unittest.TestCase):
    def test_layout_accepts_v3_mixed(self):
        norm = tf_card.validate_handoff(v3_handoff())
        result = tf_card.validate_layout(native_table(), norm, CID_UPPER, DISK_BYTES)
        self.assertEqual(result["reserve"]["uuid"], RESERVE_PARTUUID)
        self.assertEqual(len(result["android_parts"]), 2)

    def test_layout_accepts_null_public_guid(self):
        norm = tf_card.validate_handoff(v3_handoff(public_partition_guid=None))
        result = tf_card.validate_layout(native_table(), norm, CID, DISK_BYTES)
        self.assertEqual(result["reserve"]["uuid"], RESERVE_PARTUUID)

    def test_layout_accepts_pending_reserve_type(self):
        norm = tf_card.validate_handoff(v3_handoff(public_partition_guid=None))
        parts = native_parts(reserve_type=RESERVE_PENDING)
        tf_card.validate_layout(native_table(parts=parts), norm, CID, DISK_BYTES)


class V3LayoutRejectTest(unittest.TestCase):
    def setUp(self):
        self.norm = tf_card.validate_handoff(v3_handoff(public_partition_guid=None))

    def test_physical_cid_not_hex_rejected(self):
        with self.assertRaises(ValueError):
            tf_card.validate_layout(native_table(), self.norm, "x" * 32, DISK_BYTES)

    def test_physical_cid_short_rejected(self):
        with self.assertRaises(ValueError):
            tf_card.validate_layout(native_table(), self.norm, CID[:16], DISK_BYTES)

    def test_capacity_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            tf_card.validate_layout(native_table(), self.norm, CID, DISK_BYTES * 2)

    def test_private_guid_mismatch_rejected(self):
        parts = native_parts(private_uuid=OTHER_UUID)
        with self.assertRaises(ValueError):
            tf_card.validate_layout(native_table(parts=parts), self.norm, CID, DISK_BYTES)

    def test_public_guid_mismatch_rejected(self):
        norm = tf_card.validate_handoff(v3_handoff())
        parts = native_parts(reserve_uuid=OTHER_UUID)
        with self.assertRaises(ValueError):
            tf_card.validate_layout(native_table(parts=parts), norm, CID, DISK_BYTES)

    def test_type_mismatch_rejected(self):
        parts = native_parts(reserve_type=PRIVATE_TYPE)
        with self.assertRaises(ValueError):
            tf_card.validate_layout(native_table(parts=parts), self.norm, CID, DISK_BYTES)

    def test_ratio_mismatch_rejected(self):
        # ratio 50 but reserve holds 128 MiB -> > 8 MiB off target.
        parts = native_parts(reserve_bytes=128 * MIB)
        with self.assertRaises(ValueError):
            tf_card.validate_layout(native_table(parts=parts), self.norm, CID, DISK_BYTES)

    def test_overlap_rejected(self):
        parts = native_parts()
        parts[1]["start"] = parts[0]["start"] + parts[0]["size"] - 1
        with self.assertRaises(ValueError):
            tf_card.validate_layout(native_table(parts=parts), self.norm, CID, DISK_BYTES)

    def test_meta_size_mismatch_rejected(self):
        parts = native_parts(meta_bytes=8 * MIB)
        with self.assertRaises(ValueError):
            tf_card.validate_layout(native_table(parts=parts), self.norm, CID, DISK_BYTES)

    def test_raw_v3_handoff_does_not_bypass_v3_contract(self):
        # Handoff not passed through validate_handoff: identity_mode missing.
        raw = v3_handoff()
        del raw["identity_mode"]
        with self.assertRaises(ValueError):
            tf_card.validate_layout(native_table(), raw, CID, DISK_BYTES)

    def test_raw_v3_handoff_with_embedded_cid_rejected(self):
        raw = v3_handoff(card_cid=CID)
        with self.assertRaises(ValueError):
            tf_card.validate_layout(native_table(), raw, CID, DISK_BYTES)

    def test_raw_v3_handoff_wrong_allocation_mode_rejected(self):
        raw = v3_handoff(allocation_mode="linux_only")
        with self.assertRaises(ValueError):
            tf_card.validate_layout(native_table(), raw, CID, DISK_BYTES)


class V3LegacyRegressionTest(unittest.TestCase):
    def test_v1_layout_still_checks_cid(self):
        norm = tf_card.validate_handoff(v1_handoff())
        tf_card.validate_layout(native_table(), norm, CID.upper(), DISK_BYTES)
        other = "0" * 32
        with self.assertRaises(ValueError):
            tf_card.validate_layout(native_table(), norm, other, DISK_BYTES)

    def test_v1_upper_cid_normalised(self):
        seen = tf_card.validate_handoff(v1_handoff(card_cid=CID_UPPER))
        self.assertEqual(seen["card_cid"], CID)


if __name__ == "__main__":
    unittest.main()
