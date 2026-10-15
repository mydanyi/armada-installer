# Android → Linux TF-card handoff descriptor

The Android app (`io.github.mydanyi.armadatf`) writes one JSON descriptor after
it has created the native "mixed" adopted storage on the TF card. The Linux
side (Armada Decky installer) reads that descriptor, validates it against the
real card and GPT, and formats **only** the Linux reserve partition.

This document is the authoritative schema for that descriptor and for the pure
domain checks in `armada_installer.tf_card`.

## Where the descriptor lives

| Role | Location |
| --- | --- |
| Android writes | `Android/data/io.github.mydanyi.armadatf/files/armada-tf-handoff.json` on the **public** (Linux reserve) partition |
| Linux reads | the same file, mounted read-only through the public partition |
| Linux final marker | `.armada-tf-handoff.json` at the **root of the newly formatted Linux ext4 filesystem** |

`Android/data/io.github.mydanyi.armadatf/files/...` is the app-specific
external files directory on the public volume. The descriptor is a plain
UTF-8 JSON object.

## Schema (schema_version 1)

```json
{
  "magic": "armada-tf-handoff",
  "schema_version": 1,
  "card_cid": "1b2c3d4e5f60718293a4b5c6d7e8f901",
  "disk_bytes": 536870912,
  "linux_ratio": 50,
  "public_fs_uuid": "1A2B-3C4D",
  "public_partition_guid": "11111111-2222-3333-4444-555555555555",
  "private_partition_guid": "99999999-8888-7777-6666-555555555555",
  "private_fs_uuid": "abcdef01-2345-6789-abcd-ef0123456789"
}
```

| Key | Type | Rules |
| --- | --- | --- |
| `magic` | string | exactly `armada-tf-handoff` |
| `schema_version` | int | exactly `1` (a boolean is **not** accepted) |
| `card_cid` | string | exactly 32 hex characters; normalised to lowercase |
| `disk_bytes` | int | the whole card capacity in bytes; strictly `> 0`; a boolean is not accepted |
| `linux_ratio` | int | the Linux share as a percentage; `10..90` inclusive; a boolean is not accepted |
| `public_fs_uuid` | string | a FAT UUID `XXXX-XXXX` (8 hex, with a dash) **or** an ext-style UUID; returned unchanged |
| `public_partition_guid` | string \| null | the reserve GPT partition GUID, or `null` when it could not be read; normalised to a canonical (dashed, lowercase) UUID |
| `private_partition_guid` | string | the Android private GPT partition GUID, 32 or 36 characters; normalised to a canonical UUID |
| `private_fs_uuid` | string | the Android private filesystem UUID; normalised to a canonical UUID |

`validate_handoff(handoff)` raises `ValueError` for a wrong magic, a wrong
version, any missing required key, an oversized/ malformed CID, a boolean or
float where an integer is required, an out-of-range ratio, or an invalid UUID.
It returns a **new, normalised** dictionary. Unknown, non-authoritative keys are
preserved as-is and never influence any decision.

## Pending and connected states

The reserve partition is identified by its GPT **type GUID**, never by label or
node name:

| State | Reserve type GUID | Meaning |
| --- | --- | --- |
| pending | `EBD0A0A2-B9E5-4433-87C0-68B6B72699C7` | the Android side has not yet bound the reserve to Linux (a neutral placeholder) |
| connected | `0FC63DAF-8483-4772-8E79-3D69D8477DE4` | the reserve is already a Linux filesystem |

The two other partitions have fixed type GUIDs and must not move:

| Partition | GPT type GUID |
| --- | --- |
| Android metadata | `19A710A2-B3CA-11E4-B026-10604B889DCF` |
| Android private | `193D1EA4-B3CA-11E4-B075-10604B889DCF` |

`validate_layout(table, handoff, card_cid, disk_bytes)` accepts **either**
reserve type, requires a GPT (`label == "gpt"`) table with a 512- or
4096-byte sector size, exactly three partitions, and checks that:

* every start/size is positive and inside `[firstlba, lastlba]`;
* partitions are ordered and non-overlapping;
* the Android metadata partition is exactly 16 MiB;
* the Android private partition GUID matches `handoff.private_partition_guid`;
* the reserve GUID matches `handoff.public_partition_guid` when it is not `null`;
* `handoff.card_cid` equals `card_cid` and `handoff.disk_bytes` equals
  `disk_bytes` **exactly** (the identity read from the physical card);
* the reserve size is within 8 MiB of `disk_bytes * linux_ratio / 100`, leaving
  room for native MiB/alignment rounding.

It returns `{"reserve": <partition>, "android_parts": [<meta>, <private>]}`.
GPT identifiers and GUIDs are compared and returned case-insensitively. The
domain does **not** require a filesystem UUID inside the sfdisk JSON — that is
checked by the service layer with `blkid`/`lsblk`. The domain also never
inspects partition `node` paths for safety; guarding the real `/dev` device
(SD ancestry, system/boot disk, private/meta targets) is the runtime service's
responsibility, not the pure layout check's.

## Boundaries that must be preserved

* The Android **metadata** (16 MiB) and **private** partitions keep their exact
  start, size and GPT type GUIDs. The Linux conversion must not resize, move or
  retype them, and must not touch their UUIDs.
* The Linux side reformats **only** the reserve partition, to ext4 with the
  Linux filesystem type GUID, and preserves every Android GPT entry and GUID.
* `public_partition_guid` is the reserve's own GPT GUID. When the Android side
  could not read it, the field is `null`; the service then reads it from the
  card and still verifies geometry, sizes and identity.

## Android keys are never exported

The descriptor contains **no** Android cryptographic material. The adopted
storage's encryption/`key` and any keys, tokens or salt stay on the device and
are never written to the descriptor, the reserve partition, or the Linux
marker. Only the identifiers listed above are exported.

## Linux final marker

After conversion, Linux writes `.armada-tf-handoff.json` at the root of the new
ext4 reserve filesystem. It holds the **validated original descriptor** (the
normalised handoff) plus one extra field recorded at conversion time:

```json
{
  "magic": "armada-tf-handoff",
  "schema_version": 1,
  "card_cid": "1b2c3d4e5f60718293a4b5c6d7e8f901",
  "disk_bytes": 536870912,
  "linux_ratio": 50,
  "public_fs_uuid": "1A2B-3C4D",
  "public_partition_guid": "11111111-2222-3333-4444-555555555555",
  "private_partition_guid": "99999999-8888-7777-6666-555555555555",
  "private_fs_uuid": "abcdef01-2345-6789-abcd-ef0123456789",
  "linux_fs_uuid": "00112233-4455-6677-8899-aabbccddeeff"
}
```

`linux_fs_uuid` is the UUID of the formatted Linux ext4 filesystem, recorded so
the handoff can be inspected later. The marker is not read as Android input and
never authorises formatting on its own.
