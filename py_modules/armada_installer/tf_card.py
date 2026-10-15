"""Pure Linux TF-domain contract for the Armada installer.

``validate_handoff`` normalises and checks the Android-produced handoff
descriptor.  ``validate_layout`` checks the geometry and identity of an
``sfdisk --json`` partition-table body against that descriptor and against the
card identity the service independently read (``card_cid`` and ``disk_bytes``).

Both functions raise ``ValueError`` on any violation.  The domain never looks
at partition ``node`` paths for safety and never touches a device: guarding the
real block device is the runtime service's job, not the pure layout check's.
"""

import re

MAGIC = "armada-tf-handoff"
SCHEMA_VERSION = 1
MIB = 1024 * 1024
META_BYTES = 16 * MIB
MIN_RATIO = 10
MAX_RATIO = 90
RESERVE_TOLERANCE = 8 * MIB

CID_LENGTH = 32
GUID_LENGTHS = (32, 36)

# GPT type GUIDs (canonical lowercase).
RESERVE_PENDING = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"    # Microsoft basic data
RESERVE_CONNECTED = "0fc63daf-8483-4772-8e79-3d69d8477de4"  # Linux filesystem
ANDROID_META = "19a710a2-b3ca-11e4-b026-10604b889dcf"       # Android metadata
ANDROID_PRIVATE = "193d1ea4-b3ca-11e4-b075-10604b889dcf"    # Android private
RESERVE_TYPES = (RESERVE_PENDING, RESERVE_CONNECTED)

_HANDOFF_KEYS = ("magic", "schema_version", "card_cid", "disk_bytes", "linux_ratio",
                 "public_fs_uuid", "public_partition_guid", "private_partition_guid",
                 "private_fs_uuid")
_HEX = re.compile(r"[0-9a-fA-F]+\Z")
_FAT_UUID = re.compile(r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}\Z")


def _require(condition, detail):
    if not condition:
        raise ValueError(detail)


def _strict_int(value, field):
    _require(type(value) is int, "%s must be a whole number" % field)
    return value


def _card_cid(value):
    _require(isinstance(value, str) and len(value) == CID_LENGTH and _HEX.match(value),
             "a card CID must be exactly 32 hexadecimal characters")
    return value.lower()


def _canonical_guid(value, field):
    _require(isinstance(value, str) and len(value) in GUID_LENGTHS,
             "%s must be a 32- or 36-character GUID" % field)
    digits = value.replace("-", "").lower()
    _require(len(digits) == CID_LENGTH and _HEX.match(digits), "%s must be a hexadecimal GUID" % field)
    return "-".join((digits[0:8], digits[8:12], digits[12:16], digits[16:20], digits[20:32]))


def _fs_uuid(value):
    _require(isinstance(value, str), "public_fs_uuid must be a filesystem UUID")
    if _FAT_UUID.match(value):
        return value
    _canonical_guid(value, "public_fs_uuid")
    return value


def _same_guid(left, right):
    return _canonical_guid(left, "GUID") == _canonical_guid(right, "GUID")


def validate_handoff(handoff):
    """Return a normalised copy of an Android handoff descriptor.

    Raises ``ValueError`` for a wrong magic or version, a missing key, a
    boolean or float where an integer is required, a malformed CID, an
    out-of-range ratio, or an invalid UUID.  Unknown keys are preserved as-is.
    """
    _require(isinstance(handoff, dict), "the handoff must be an object")
    for key in _HANDOFF_KEYS:
        _require(key in handoff, "the handoff is missing %r" % key)
    _require(handoff["magic"] == MAGIC, "unsupported handoff magic")
    _require(_strict_int(handoff["schema_version"], "schema_version") == SCHEMA_VERSION,
             "unsupported handoff schema_version")
    seen = dict(handoff)
    seen["card_cid"] = _card_cid(handoff["card_cid"])
    _require(_strict_int(handoff["disk_bytes"], "disk_bytes") > 0, "disk_bytes must be positive")
    ratio = _strict_int(handoff["linux_ratio"], "linux_ratio")
    _require(MIN_RATIO <= ratio <= MAX_RATIO, "linux_ratio must be %d..%d" % (MIN_RATIO, MAX_RATIO))
    seen["public_fs_uuid"] = _fs_uuid(handoff["public_fs_uuid"])
    public = handoff["public_partition_guid"]
    seen["public_partition_guid"] = None if public is None else _canonical_guid(public, "public_partition_guid")
    seen["private_partition_guid"] = _canonical_guid(handoff["private_partition_guid"], "private_partition_guid")
    seen["private_fs_uuid"] = _canonical_guid(handoff["private_fs_uuid"], "private_fs_uuid")
    return seen


def _layout_part(part, first, last):
    _require(isinstance(part, dict), "a partition entry must be an object")
    start, size = part.get("start"), part.get("size")
    _require(type(start) is int and type(size) is int and start > 0 and size > 0,
             "every partition must have a positive start and size")
    _require(start >= first and start + size - 1 <= last,
             "a partition lies outside the GPT usable range")
    type_guid = part.get("type")
    _require(isinstance(type_guid, str) and type_guid, "a partition has no type GUID")
    return {"start": start, "size": size, "type": type_guid, "uuid": part.get("uuid"), "raw": part}


def validate_layout(table, handoff, card_cid, disk_bytes):
    """Validate an ``sfdisk --json`` table body against a normalized handoff.

    Returns ``{"reserve": <partition>, "android_parts": [<meta>, <private>]}``
    and raises ``ValueError`` on any geometry, type, GUID or identity mismatch.
    """
    _require(isinstance(table, dict), "the partition table must be an object")
    _require(isinstance(handoff, dict), "the handoff must be an object")
    _require(str(table.get("label", "")).lower() == "gpt", "only GPT partition tables are supported")
    sector = table.get("sectorsize")
    _require(type(sector) is int and sector in (512, 4096), "the sector size must be 512 or 4096")
    first, last = table.get("firstlba"), table.get("lastlba")
    _require(type(first) is int and type(last) is int and first > 0 and last >= first,
             "the GPT usable range is invalid")
    # The GPT ranges are attacker-controlled; bound them by the capacity the
    # service independently read from the physical card, not by the table itself.
    physical = _strict_int(disk_bytes, "disk_bytes")
    _require(physical > 0, "the card capacity must be positive")
    _require(last < physical // sector, "the GPT usable range exceeds the physical card")
    parts = table.get("partitions")
    _require(isinstance(parts, list) and len(parts) == 3,
             "the card must contain exactly three native partitions")
    reserve, meta, private = [_layout_part(part, first, last) for part in parts]
    for earlier, later in ((reserve, meta), (meta, private)):
        _require(later["start"] >= earlier["start"] + earlier["size"],
                 "the partitions overlap or are out of order")
    _require(reserve["type"].lower() in RESERVE_TYPES, "the reserve partition type is unsupported")
    _require(meta["type"].lower() == ANDROID_META, "the Android metadata partition type is unchanged")
    _require(private["type"].lower() == ANDROID_PRIVATE, "the Android private partition type is unchanged")
    _require(meta["size"] * sector == META_BYTES, "the Android metadata partition must be exactly 16 MiB")
    _require(_card_cid(handoff.get("card_cid")) == _card_cid(card_cid),
             "the handoff card CID does not match the card")
    _require(_strict_int(handoff.get("disk_bytes"), "disk_bytes") == _strict_int(disk_bytes, "disk_bytes"),
             "the handoff capacity does not match the card")
    _require(_same_guid(private["uuid"], handoff.get("private_partition_guid")),
             "the Android private partition GUID does not match the handoff")
    public = handoff.get("public_partition_guid")
    if public is not None:
        _require(_same_guid(reserve["uuid"], public), "the reserve partition GUID does not match the handoff")
    expected = disk_bytes * handoff["linux_ratio"] / 100
    reserve_bytes = reserve["size"] * sector
    _require(abs(reserve_bytes - expected) <= RESERVE_TOLERANCE,
             "the reserve size is not within 8 MiB of the requested Linux share")
    return {"reserve": reserve["raw"], "android_parts": [meta["raw"], private["raw"]]}
