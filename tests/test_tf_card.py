"""Contract tests for ``armada_installer.tf_card``.

The domain is pure Python: no device access, no ``/dev`` existence checks, no
subprocess.  ``validate_handoff`` normalises and checks the Android-produced
handoff descriptor.  ``validate_layout`` checks the geometry and identity of an
``sfdisk --json`` partition-table body against that descriptor and against the
card identity the service independently read (``card_cid`` and ``disk_bytes``).

Both functions raise ``ValueError`` on any violation.  The domain never looks at
partition ``node`` paths for safety: guarding against a malicious or system
disk device is the runtime service's job, not the pure layout check's.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "py_modules"))

from armada_installer import tf_card  # noqa: E402

MIB = 1024 * 1024

# A native "mixed" 512 MiB card: 256 MiB Linux reserve, 16 MiB Android meta and
# the remainder as the Android private partition, minus GPT/alignment slack.
DISK_BYTES = 512 * MIB
SECTOR = 512
FIRST_LBA = 2048                       # 1 MiB GPT alignment at 512-byte sectors
LAST_LBA = DISK_BYTES // SECTOR - 34   # usable GPT range, minus the backup table
DISK_GUID = "01234567-89ab-cdef-0123-456789abcdef"

CID = "1b2c3d4e5f60718293a4b5c6d7e8f901"
CID_UPPER = "1B2C3D4E5F60718293A4B5C6D7E8F901"

# GPT partition type GUIDs (canonical lowercase).
RESERVE_PENDING = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"   # Microsoft basic data (pending)
RESERVE_CONNECTED = "0fc63daf-8483-4772-8e79-3d69d8477de4"  # Linux filesystem (connected)
META_TYPE = "19a710a2-b3ca-11e4-b026-10604b889dcf"          # Android metadata
PRIVATE_TYPE = "193d1ea4-b3ca-11e4-b075-10604b889dcf"       # Android private

RESERVE_PARTUUID = "11111111-2222-3333-4444-555555555555"
META_PARTUUID = "22222222-3333-4444-5555-666666666666"
PRIVATE_PARTUUID = "99999999-8888-7777-6666-555555555555"
PRIVATE_PARTUUID_32 = PRIVATE_PARTUUID.replace("-", "")
PRIVATE_FS_UUID = "abcdef01-2345-6789-abcd-ef0123456789"
OTHER_UUID = "0a0b0c0d-1e1f-2021-3233-445566778899"

TOLERANCE = 8 * MIB                    # alignment slack on the reserve share


def handoff(**overrides):
    """A minimal valid descriptor; overrides model the rejected variants."""
    base = {
        "magic": "armada-tf-handoff",
        "schema_version": 1,
        "card_cid": CID,
        "disk_bytes": DISK_BYTES,
        "linux_ratio": 50,
        "public_fs_uuid": "1A2B-3C4D",
        "public_partition_guid": RESERVE_PARTUUID,
        "private_partition_guid": PRIVATE_PARTUUID,
        "private_fs_uuid": PRIVATE_FS_UUID,
    }
    base.update(overrides)
    return base


def normalized(**overrides):
    """The descriptor after ``validate_handoff`` normalisation."""
    return tf_card.validate_handoff(handoff(**overrides))


def partition(node, start, size, type_guid, uuid, name):
    return {"node": node, "start": start, "size": size,
            "type": type_guid, "uuid": uuid, "name": name}


def native_parts(sector=SECTOR, reserve_bytes=256 * MIB, meta_bytes=16 * MIB,
                 reserve_type=RESERVE_CONNECTED, reserve_uuid=RESERVE_PARTUUID,
                 private_uuid=PRIVATE_PARTUUID):
    """Ordered reserve/meta/private partitions that exactly fill the card."""
    first = FIRST_LBA * SECTOR // sector
    last = DISK_BYTES // sector - 34
    reserve_sectors = reserve_bytes // sector
    meta_sectors = meta_bytes // sector
    meta_start = first + reserve_sectors
    private_start = meta_start + meta_sectors
    return [
        partition("/dev/mmcblk0p1", first, reserve_sectors, reserve_type, reserve_uuid, "linux-reserve"),
        partition("/dev/mmcblk0p2", meta_start, meta_sectors, META_TYPE, META_PARTUUID, "android-meta"),
        partition("/dev/mmcblk0p3", private_start, last - private_start + 1, PRIVATE_TYPE,
                  private_uuid, "android-private"),
    ]


def native_table(parts=None, sector=SECTOR, label="gpt", **kwargs):
    """An sfdisk --json partition-table body for the fixture card."""
    first = FIRST_LBA * SECTOR // sector
    last = DISK_BYTES // sector - 34
    if parts is None:
        parts = native_parts(sector=sector, **kwargs)
    return {"label": label, "sectorsize": sector, "firstlba": first, "lastlba": last,
            "id": DISK_GUID, "device": "/dev/mmcblk0", "partitions": parts}


class HandoffTests(unittest.TestCase):
    def test_valid_handoff_is_normalised(self):
        seen = tf_card.validate_handoff(handoff())
        self.assertEqual(seen["magic"], "armada-tf-handoff")
        self.assertEqual(seen["schema_version"], 1)
        self.assertEqual(seen["disk_bytes"], DISK_BYTES)
        self.assertEqual(seen["linux_ratio"], 50)
        self.assertEqual(seen["card_cid"], CID)
        self.assertEqual(seen["private_partition_guid"], PRIVATE_PARTUUID)
        self.assertEqual(seen["public_partition_guid"], RESERVE_PARTUUID)
        self.assertEqual(seen["private_fs_uuid"], PRIVATE_FS_UUID)
        self.assertEqual(seen["public_fs_uuid"], "1A2B-3C4D")

    def test_cid_is_lowercased(self):
        self.assertEqual(tf_card.validate_handoff(handoff(card_cid=CID_UPPER))["card_cid"], CID)

    def test_guid_case_is_canonicalised(self):
        seen = tf_card.validate_handoff(handoff(
            public_partition_guid=RESERVE_PARTUUID.upper(),
            private_partition_guid=PRIVATE_PARTUUID.upper(),
            private_fs_uuid=PRIVATE_FS_UUID.upper()))
        self.assertEqual(seen["public_partition_guid"], RESERVE_PARTUUID)
        self.assertEqual(seen["private_partition_guid"], PRIVATE_PARTUUID)
        self.assertEqual(seen["private_fs_uuid"], PRIVATE_FS_UUID)

    def test_32_hex_private_guid_is_canonicalised(self):
        seen = tf_card.validate_handoff(handoff(private_partition_guid=PRIVATE_PARTUUID_32))
        self.assertEqual(seen["private_partition_guid"], PRIVATE_PARTUUID)

    def test_null_public_partition_guid_is_allowed(self):
        self.assertIsNone(tf_card.validate_handoff(handoff(public_partition_guid=None))["public_partition_guid"])

    def test_extfs_uuid_is_allowed_for_public_fs_uuid(self):
        seen = tf_card.validate_handoff(handoff(public_fs_uuid=PRIVATE_FS_UUID))
        self.assertEqual(seen["public_fs_uuid"], PRIVATE_FS_UUID)

    def test_unknown_optional_keys_are_preserved(self):
        seen = tf_card.validate_handoff(handoff(extra={"note": "ignore me"}))
        self.assertEqual(seen["extra"], {"note": "ignore me"})

    def test_booleans_are_not_integers(self):
        for field in ("schema_version", "disk_bytes", "linux_ratio"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    tf_card.validate_handoff(handoff(**{field: True}))

    def test_floats_are_not_integers(self):
        for field, value in (("disk_bytes", 512.0), ("linux_ratio", 50.0), ("schema_version", 1.0)):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    tf_card.validate_handoff(handoff(**{field: value}))

    def test_wrong_magic_and_version_are_rejected(self):
        for bad in ("armada-handoff", "ARMADA-TF-HANDOFF", "", None):
            with self.subTest(magic=bad):
                with self.assertRaises(ValueError):
                    tf_card.validate_handoff(handoff(magic=bad))
        for version in (0, 2, 10):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    tf_card.validate_handoff(handoff(schema_version=version))

    def test_every_required_field_is_mandatory(self):
        for field in handoff():
            with self.subTest(field=field):
                broken = handoff()
                del broken[field]
                with self.assertRaises(ValueError):
                    tf_card.validate_handoff(broken)

    def test_oversized_and_malformed_cid_are_rejected(self):
        for bad in (CID + "0", CID[:-1], "zz" + CID[2:], CID.upper() + "0", "", "not-a-cid"):
            with self.subTest(cid=bad):
                with self.assertRaises(ValueError):
                    tf_card.validate_handoff(handoff(card_cid=bad))

    def test_out_of_range_linux_ratio_is_rejected(self):
        for ratio in (9, 91, 0, 100, -1):
            with self.subTest(ratio=ratio):
                with self.assertRaises(ValueError):
                    tf_card.validate_handoff(handoff(linux_ratio=ratio))

    def test_invalid_uuids_are_rejected(self):
        cases = ({"private_partition_guid": "not-a-guid"},
                 {"private_partition_guid": PRIVATE_PARTUUID_32 + "0"},
                 {"private_fs_uuid": "1234-5678"},
                 {"public_fs_uuid": "1234"},
                 {"public_fs_uuid": "not-a-uuid"})
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    tf_card.validate_handoff(handoff(**case))


class LayoutTests(unittest.TestCase):
    def validate(self, table=None, handoff_dict=None, card_cid=CID, disk_bytes=DISK_BYTES):
        return tf_card.validate_layout(table if table is not None else native_table(),
                                       handoff_dict if handoff_dict is not None else normalized(),
                                       card_cid, disk_bytes)

    def test_connected_layout_is_accepted(self):
        seen = self.validate()
        self.assertEqual(seen["reserve"]["node"], "/dev/mmcblk0p1")
        self.assertEqual(seen["reserve"]["type"].lower(), RESERVE_CONNECTED)
        self.assertEqual([p["type"].lower() for p in seen["android_parts"]], [META_TYPE, PRIVATE_TYPE])
        self.assertEqual([p["node"] for p in seen["android_parts"]], ["/dev/mmcblk0p2", "/dev/mmcblk0p3"])

    def test_pending_layout_is_accepted(self):
        table = native_table(reserve_type=RESERVE_PENDING)
        self.assertEqual(self.validate(table=table)["reserve"]["type"].lower(), RESERVE_PENDING)

    def test_public_guid_match_is_case_insensitive(self):
        table = native_table()
        table["partitions"][0]["uuid"] = RESERVE_PARTUUID.upper()
        self.validate(table=table)

    def test_32_hex_private_guid_matches(self):
        table = native_table(parts=native_parts(private_uuid=PRIVATE_PARTUUID_32))
        seen = self.validate(table=table, handoff_dict=normalized(private_partition_guid=PRIVATE_PARTUUID_32))
        self.assertEqual(seen["android_parts"][1]["uuid"].replace("-", "").lower(), PRIVATE_PARTUUID_32)

    def test_null_public_guid_skips_the_reserve_check(self):
        table = native_table(parts=native_parts(reserve_uuid=OTHER_UUID))
        self.validate(table=table, handoff_dict=normalized(public_partition_guid=None))

    def test_reserve_size_within_alignment_tolerance_is_accepted(self):
        table = native_table(parts=native_parts(reserve_bytes=256 * MIB + 4 * MIB))
        self.validate(table=table)

    def test_4096_sector_meta_is_accepted(self):
        table = native_table(sector=4096)
        seen = self.validate(table=table)
        self.assertEqual(seen["android_parts"][0]["size"] * 4096, 16 * MIB)

    def test_partitions_need_no_filesystem_fields(self):
        table = native_table()
        for entry in table["partitions"]:
            self.assertNotIn("filesystem", entry)
        self.validate(table=table)

    def test_malicious_node_is_checked_by_the_service_not_the_domain(self):
        # The pure layout check must not reject a traversal-looking node; only
        # the runtime service guards the real device path.
        table = native_table()
        table["partitions"][0]["node"] = "/dev/mmcblk0p1/../../../dev/mmcblk0"
        self.validate(table=table)

    def test_mismatched_card_cid_is_rejected(self):
        with self.assertRaises(ValueError):
            self.validate(card_cid="0" * 32)

    def test_mismatched_disk_bytes_are_rejected(self):
        with self.assertRaises(ValueError):
            self.validate(disk_bytes=DISK_BYTES + 1)

    def test_mismatched_private_guid_is_rejected(self):
        table = native_table(parts=native_parts(private_uuid=OTHER_UUID))
        with self.assertRaises(ValueError):
            self.validate(table=table)

    def test_mismatched_public_guid_is_rejected(self):
        with self.assertRaises(ValueError):
            self.validate(handoff_dict=normalized(public_partition_guid=OTHER_UUID))

    def test_swapped_android_types_are_rejected(self):
        parts = native_parts()
        parts[1]["type"], parts[2]["type"] = PRIVATE_TYPE, META_TYPE
        with self.assertRaises(ValueError):
            self.validate(table=native_table(parts=parts))

    def test_missing_or_extra_partitions_are_rejected(self):
        parts = native_parts()
        extra = partition("/dev/mmcblk0p4", 0, 1, META_TYPE, OTHER_UUID, "extra")
        for candidate in (parts[:2], parts + [extra]):
            with self.subTest(count=len(candidate)):
                with self.assertRaises(ValueError):
                    self.validate(table=native_table(parts=list(candidate)))

    def test_overlapping_partitions_are_rejected(self):
        parts = native_parts()
        parts[1]["start"] = parts[0]["start"] + parts[0]["size"] - 1  # one sector of overlap
        with self.assertRaises(ValueError):
            self.validate(table=native_table(parts=parts))

    def test_out_of_bounds_partitions_are_rejected(self):
        parts = native_parts()
        parts[2]["size"] += 1  # private partition runs past the last usable LBA
        with self.assertRaises(ValueError):
            self.validate(table=native_table(parts=parts))

    def test_extent_beyond_the_physical_card_is_rejected(self):
        # A forged GPT can claim an arbitrarily large usable range; the service's
        # independently-read disk_bytes is authoritative, so a private partition
        # (and a lastlba) that runs past the real card must be refused even though
        # the table's own lastlba still contains them.
        physical_sectors = DISK_BYTES // SECTOR
        parts = native_parts()
        parts[2]["size"] = physical_sectors - parts[2]["start"] + 64  # 64 sectors past the card
        table = native_table(parts=parts)
        table["lastlba"] = physical_sectors + 4096                    # untrusted, self-consistent
        with self.assertRaises(ValueError):
            self.validate(table=table)

    def test_non_gpt_label_is_rejected(self):
        with self.assertRaises(ValueError):
            self.validate(table=native_table(label="dos"))

    def test_boolean_sector_size_is_rejected(self):
        with self.assertRaises(ValueError):
            self.validate(table=native_table(sector=True))

    def test_reserve_percentage_mismatch_is_rejected(self):
        # 272 MiB reserve against a 50% share of 512 MiB: 16 MiB off, well past
        # the 8 MiB alignment tolerance, even though the geometry stays valid.
        table = native_table(parts=native_parts(reserve_bytes=256 * MIB + 16 * MIB))
        with self.assertRaises(ValueError):
            self.validate(table=table)


if __name__ == "__main__":
    unittest.main()
