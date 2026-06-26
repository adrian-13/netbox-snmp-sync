# netbox-snmp-sync

> A **NetBox plugin** that reads interfaces, IP addresses, and VLANs from network devices
> over **SNMP** and synchronises them directly into NetBox — entirely from within the
> NetBox UI, with no external scripts, cron jobs, or second tools required.

[![NetBox](https://img.shields.io/badge/NetBox-4.6%2B-blue)](https://github.com/netbox-community/netbox)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## Table of contents

- [Overview](#overview)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [REST API](#rest-api)
- [Security](#security)
- [Development & tests](#development--tests)
- [Changelog](#changelog)

---

## Overview

Network devices speak SNMP: they expose their interfaces, IP addresses, VLANs, and system
information through a standard protocol that has been around for decades. `netbox-snmp-sync`
bridges that world with NetBox — it polls a device over SNMP, computes a diff against what
NetBox already knows, and either shows you the diff or writes the missing data directly through
the NetBox ORM.

Everything runs natively inside NetBox:

```
Device (SNMP)
      │
      ▼
SNMP collector (asyncio + pysnmp)
      │
      ▼
Diff engine  ──►  Preview page (pick what to write)
      │       ──►  Compare job  (read-only diff → job log)
      │       ──►  Sync job     (add-only write to NetBox ORM)
      │       ──►  Scheduled    (automatic periodic sync)
      │
      ▼
NetBox ORM ──► Changelog (who / when / before → after)
           ──► SyncRun   (history, statistics, revert)
```

The plugin is a successor to a standalone `netbox-snmp-sync` CLI tool. The SNMP collection
and mapping logic is reused; the data is now written through Django ORM and the entire
workflow lives in NetBox's UI and background-job framework.

---

## Features

### Per-device SNMP settings

Each device gets its own SNMP configuration, accessible from the device detail page
(right-side panel) or from **SNMP Sync → Device SNMP Configs**:

| Field | Description |
|-------|-------------|
| SNMP version | v1 / v2c / v3 |
| Community string | SNMPv1 and v2c authentication |
| SNMPv3 credentials | Username, auth protocol (MD5/SHA/SHA-224/256/384/512), auth key, priv protocol (DES/AES-128/192/256), priv key |
| Port | Default 161 |
| Timeout / retries | Per-device transport tuning |
| Target override | Poll a different host/IP than the device's primary IP |

### Collected data

| Data | Details |
|------|---------|
| **Interfaces** | Name, type (derived from speed), MTU, speed, duplex, admin/oper status, description, MAC address, parent interface for sub-interfaces |
| **IPv4 addresses** | With prefix length, assigned to the correct interface |
| **VLAN membership** | Tagged and untagged VLAN assignments per interface (optional) |
| **System info** | sysName, vendor, used in the test result and job logs |

### Actions

| Action | What it does |
|--------|-------------|
| **Test SNMP** | Quick connectivity probe (one SNMP GET for sysName). Renders a full result page showing OK / Failed and sysName. Saves the outcome to the *Last test* column — no NetBox data is changed. |
| **Bulk test** | Select multiple configs in the list → **Test selected** → probes all of them concurrently (worker pool of 8) and renders a combined result page. |
| **Preview & write** | Full SNMP poll → diff page with checkboxes → writes only the items you select. |
| **Compare** | SNMP poll → diff written to the background job log (read-only, nothing is changed). |
| **Sync all** | SNMP poll → add-only write of all new interfaces and IPs to NetBox. |
| **Scheduled sync** | System job that runs hourly and queues a per-device sync for every enabled device that has not been synced within the configured interval. |

Per-device SNMP settings also include **Rename device to sysName**. When enabled, apply
syncs rename the NetBox device to the collected SNMP `sysName`; read-only tests and
compare runs do not rename devices. Preview shows the collected `sysName` before writing,
and successful renames are recorded in the sync run message and change log.

### History and audit

- **SyncRun model** — every run (manual or scheduled) is stored in the database with:
  - timestamp, trigger type (manual / scheduled), mode (compare / apply / dry-run)
  - status (OK / failed)
  - counters: interfaces created / updated / existing / ignored; IPs created / existing
  - free-text message / error
- **NetBox changelog integration** — all writes (including those from background jobs) are
  wrapped in `event_tracking` so they appear in NetBox's built-in change log with the
  correct user, timestamp, and before/after snapshots.
- **Revert run** — each run records every object it created (`SyncRunObject`). Clicking
  **Revert run** deletes exactly those objects. Deletions also land in the change log.

### Global settings in the UI

Plugin-level settings are stored in a database-backed singleton (`SNMPSyncConfig`) and
editable at **SNMP Sync → Settings** without restarting NetBox:

- Sync interval (hours), update existing objects, set MAC address
- VLAN write / auto-create, history retention (days + count)

### Bulk device setup

**SNMP Sync → Bulk setup** lets you create SNMP configurations for many devices at once,
optionally reading the community string from a custom field on each device.

---

## Requirements

| Dependency | Version |
|-----------|---------|
| NetBox | 4.6 or newer |
| Python | 3.12 or newer |
| pysnmp | ≥ 7.1, < 8 |
| Redis + RQ worker | Standard NetBox prerequisite (`netbox-rq` service) |

> **Important:** The RQ worker (`netbox-rq`) must be running. Compare, Sync, and Scheduled
> jobs are dispatched to the worker queue — without it they never execute.

---

## Installation

```bash
# Activate the NetBox virtual environment
source /opt/netbox/venv/bin/activate

# Install from GitHub
pip install git+https://github.com/adrian-13/netbox-snmp-sync.git

# Or from PyPI once published
pip install netbox-snmp-sync
```

Add the plugin to `configuration.py` (or `configuration/plugins.py` in netbox-docker):

```python
PLUGINS = [
    "netbox_snmp_sync",
]
```

Run migrations and collect static files, then restart:

```bash
cd /opt/netbox/netbox
python manage.py migrate
python manage.py collectstatic --no-input
sudo systemctl restart netbox netbox-rq
```

### Verifying the installation

```bash
python manage.py showmigrations netbox_snmp_sync
# All six migrations must show [X]
```

The **SNMP Sync** menu should now appear in the NetBox navigation bar, and every device
detail page should show an **SNMP Sync** panel on the right side.

### Development with netbox-docker

The repository ships a `Dockerfile` that builds a NetBox image with the plugin installed
in editable mode and a bind-mount of the source directory so live code changes take effect
without a rebuild.

---

## Configuration

All values below can also be changed at runtime through **SNMP Sync → Settings** in the
NetBox UI — no restart needed.

```python
PLUGINS_CONFIG = {
    "netbox_snmp_sync": {
        # ── SNMP transport defaults (used when a device has no per-device override) ──
        "snmp_version":   "2c",      # "1" | "2c" | "3"
        "snmp_community": "public",  # SNMPv1/v2c community string
        "snmp_port":      161,
        "snmp_timeout":   2.0,       # seconds per request
        "snmp_retries":   1,

        # ── Data mapping ────────────────────────────────────────────────────────────
        "default_ethernet_type": "1000base-t",  # NetBox interface type when SNMP
                                                 # cannot determine one
        "set_mac_address":  True,   # populate the MAC address field on interfaces
        "update_existing":  False,  # True = also overwrite changed fields on
                                    # existing interfaces (default: add-only)
        "skip_loopback_ips": True,  # skip 127.x.x.x addresses

        # ── VLAN sync ───────────────────────────────────────────────────────────────
        "write_vlans":  False,  # assign VLAN membership on interfaces
        "create_vlans": False,  # auto-create missing VLANs in the device's site

        # ── Scheduler ───────────────────────────────────────────────────────────────
        "sync_interval_hours": 24,  # 0 = interval scheduler disabled
        "sync_at_hours": "",        # e.g. "3" or "3,15" → run only at those hours of the
                                    # day (interval is then ignored). Blank = use interval.

        # ── History retention (SyncRun pruning) ─────────────────────────────────────
        "sync_job_timeout_seconds": 300,  # max SNMP collection runtime per background job;
                                         # 0 disables this guard
        "sync_stale_job_marker_minutes": 120,  # clear stale queued/running markers after this
                                               # many minutes; 0 disables automatic cleanup

        "history_keep_days":  90,
        "history_keep_count": 1000,
    },
}
```

---

## Usage

### 1 — Add an SNMP configuration to a device

Open **Devices → \<device\> → SNMP Sync panel → Add**, or go to
**SNMP Sync → Device SNMP Configs → Add** and select the device.

Fill in the SNMP version and credentials. The poll target defaults to the device's primary
IP; set **Target override** if you need to poll a management address instead.

### 2 — Test connectivity

Click the **Test SNMP** button (the cyan icon next to the pencil in the list, or the button
in the device panel). A result page is rendered immediately:

- ✅ **OK** — shows the sysName returned by the device
- ❌ **Failed** — shows the exact error (unreachable, wrong community, timeout, …)

The result is saved to the **Last test** column in the list and to the device panel, so you
can see at a glance which devices are reachable.

To test multiple devices at once: check them in the list → click **Test selected** at the
bottom. Results are shown in a single table.

### 3 — Compare or sync

| Button | Effect |
|--------|--------|
| **Preview & write** | Poll → diff page with checkboxes → write selected items |
| **Compare** | Poll → diff written to the background job log only |
| **Sync all** | Poll → add-only write of everything new to NetBox |

All three dispatch a background job visible at **Jobs** in the NetBox admin area.

Use **Sync & schedule** when you want a manual apply sync to also reset the device's
automatic schedule from that successful run. Regular manual **Sync all** updates the
device but leaves **Next sync** unchanged.

### 4 — Review history

**SNMP Sync → Sync Runs** lists every run with its timestamp, trigger, mode, status, and
counters. Click a run to open the detail page. If the run created objects and has not been
reverted, the **Revert run** button is available.

### 5 — Automatic sync

There are two scheduling modes, both configured at **SNMP Sync → Settings**:

- **Interval mode** — set `sync_interval_hours` to a positive integer. The scheduler check
  runs every few minutes and queues a sync for every enabled device whose **Next sync** time
  is due. **Scheduled SNMP Sync** is only a scheduler check; actual per-device **SNMP Sync**
  jobs are queued only when a device is due. Changing the interval recalculates each device's
  **Next sync** from the time the setting is saved, and due devices are picked up on the next
  scheduler check. When multiple devices are re-anchored at once, their next runs are spread
  over a short window so they do not all start at the same second.
- **Fixed-hour mode** — set **Sync at hours** to one or more hours of the day (0–23,
  comma-separated, e.g. `3` or `3,15`). Syncs then run only during those hours (e.g. daily at
  03:00). When set, the interval is ignored.

Newly added device configurations are picked up automatically on the next scheduler run — no
restart or manual step needed. Each device gets its own isolated RQ job, so a slow or
unreachable device does not block the others. If a device already has a pending or running
SNMP sync job, the scheduler reuses it instead of queuing a duplicate. Failed scheduled syncs
use a simple exponential retry delay (1 h, 2 h, 4 h, up to 24 h) before trying again. The
device list and detail panel show **Retry** / **Retry due** with the failure count and last
error message.

Background sync jobs also have a configurable SNMP collection timeout. Set
**Sync job timeout seconds** in Settings to cap the collection phase for one device; use `0`
only if you explicitly want to disable this guard.

Each **Device SNMP Configuration** can override the global scheduler:

- Leave **Sync interval hours** and **Sync at hours** blank to inherit the global schedule.
- Set **Sync interval hours** on one device to give it its own rolling interval.
- Set **Sync at hours** on one device to run that device only at specific local hours.
- Set **Sync interval hours** to `0` with no per-device hours to disable automatic sync for
  that device while keeping manual sync available.

Changing a per-device schedule immediately re-anchors that device's **Next sync**. Changing
the global schedule re-anchors only devices that inherit the global scheduler; devices with
explicit per-device schedules keep their own cadence.

On a device SNMP configuration detail page, operators with change permission can also
**Recalculate** the next sync from the current effective schedule. If a queued/running marker
is visible, **Reconcile marker** safely clears it when it is stale. The list page also provides
**Reconcile markers** for selected configs, useful after a worker/container restart. Stale
marker cleanup is automatic in the scheduler too; `sync_stale_job_marker_minutes` controls
the age threshold and the config's last sync message records when a stale marker was cleared.

---

## REST API

The plugin exposes two endpoints, fully integrated with NetBox's DRF infrastructure
(authentication, filtering, pagination, OpenAPI schema):

```
GET  /api/plugins/snmp-sync/device-snmp-configs/
POST /api/plugins/snmp-sync/device-snmp-configs/
GET  /api/plugins/snmp-sync/device-snmp-configs/{id}/
PUT  /api/plugins/snmp-sync/device-snmp-configs/{id}/
PATCH /api/plugins/snmp-sync/device-snmp-configs/{id}/
DELETE /api/plugins/snmp-sync/device-snmp-configs/{id}/

GET  /api/plugins/snmp-sync/sync-runs/
GET  /api/plugins/snmp-sync/sync-runs/{id}/
```

Interactive documentation is available at `/api/schema/swagger-ui/` under the `plugins` section.

---

## Security

| Concern | Mitigation |
|---------|-----------|
| SNMP secrets in the API | `community`, `auth_key`, and `priv_key` are declared `write_only` in the serializer — `GET` requests never return them |
| SNMP secrets in the database | Stored in plain text (same as a `config.yaml`). Restrict DB access and rotate credentials regularly. |
| Access control | All views and API endpoints respect standard NetBox permissions (`view_devicesnmpconfig`, `add_devicesnmpconfig`, `change_devicesnmpconfig`, `delete_devicesnmpconfig`) |
| Production polling | The plugin only issues read-only SNMP GET/GETBULK requests — it never writes to devices |

---

## Development & tests

```bash
# Clone and install in editable mode
git clone https://github.com/adrian-13/netbox-snmp-sync.git
pip install -e netbox-snmp-sync/

# Run the test suite
export NETBOX_CONFIGURATION=netbox.configuration_testing
cd /opt/netbox/netbox
python manage.py test netbox_snmp_sync

# Run a specific module
python manage.py test netbox_snmp_sync.tests.test_api
python manage.py test netbox_snmp_sync.tests.test_security
```

### Testing without a real device

Use `snmpsim-lextudio` with the provided `*.snmprec` walk files:

```bash
pip install snmpsim-lextudio
snmpsim-command-responder --data-dir=./snmprec --agent-udpv4-endpoint=127.0.0.1:1161
```

Then set **Target override** to `127.0.0.1` and **Port** to `1161` on a test SNMP config.

### Project structure

```
netbox_snmp_sync/
├── models.py          — DeviceSNMPConfig, SyncRun, SyncRunObject, SNMPSyncConfig
├── views.py           — CRUD, Test, Bulk test, Preview, Sync, Settings, Revert
├── jobs.py            — SNMPSyncJob, ScheduledSNMPSyncJob, PruneSyncRunsJob
├── engine.py          — diff / apply logic (compare_device, apply_sync)
├── snmp_collector.py  — async SNMP collection (pysnmp 7.x, asyncio)
├── spec.py            — DeviceConfig dataclass
├── dto.py             — serialise / deserialise collected data (preview snapshot)
├── filtersets.py      — FilterSets for list views and API filtering
├── forms.py           — ModelForms, BulkEditForm, BulkSNMPConfigForm
├── tables.py          — django-tables2 table definitions
├── serializers.py     — DRF serializers (secrets write_only)
├── api/               — DRF viewsets and router
├── graphql/           — Strawberry GraphQL types
├── migrations/        — 0001 … 0006
└── tests/             — test_api.py, test_filtersets.py, test_security.py
```

The plugin uses only **public NetBox plugin APIs**: `NetBoxModel`, `NetBoxModelForm`,
`NetBoxTable`, `JobRunner`, `system_job`, `event_tracking`, `register_model_view`.
No internal NetBox code is imported directly.

---

## Changelog

### v0.3.5
- **Cisco VLAN discovery** - collect VLAN names from CISCO-VTP-MIB and port
  membership from Cisco access/trunk VLAN MIBs when Q-BRIDGE membership tables
  are not exposed in the default SNMP context.

### v0.3.4
- **Sync run change details** - sync run detail pages now store and show field-level
  created/updated changes, including VLAN creation and interface VLAN membership updates.

### v0.3.3
- **VLAN creation by device site** - when a discovered VID exists only in another site,
  sync now creates the VLAN in the current device's site instead of reusing the unrelated
  VLAN object.

### v0.3.2
- **Sync run VLAN counters** - sync run detail pages now show how many VLANs were
  created and how many interfaces had VLAN membership written.
- **Preview VLAN writes** - interactive preview/write now includes VLAN membership rows
  and uses the runtime SNMP Sync settings for VLAN writes.

### v0.3.1
- **Packaged plugin templates** - include NetBox HTML templates in the wheel so the
  Device SNMP Configurations list renders after installing the published package.
- **Visible schedule state** - device configs now track last sync, next sync, retry state,
  queued/running job markers, and stale job cleanup.
- **Deterministic scheduler** - due checks run every 5 minutes, queue isolated per-device
  jobs, avoid duplicate queued/running jobs, retry failures with backoff, and spread bulk
  schedule changes over a short window.
- **Per-device schedule overrides** - each device can inherit the global schedule, use its
  own interval, use its own fixed hours, or disable automatic sync while retaining manual
  sync.
- **Job runtime guard** - background sync jobs can cap the SNMP collection phase with
  `sync_job_timeout_seconds`.
- **Operator recovery actions** - device config detail pages include POST-only controls to
  recalculate next sync and safely reconcile stale sync markers.
- Migrations: 0008 (schedule state), 0009 (job state), 0010 (per-device schedule overrides),
  0011 (sync job timeout)

### v0.3.0
- **Fixed-hour scheduling** — new **Sync at hours** setting (0–23, comma-separated). Scheduled
  syncs can now run at specific hours of the day (e.g. daily at 03:00) instead of only on a
  rolling interval. Blank keeps the existing interval behaviour; when set, the interval is
  ignored. Input is validated and normalised in the Settings form.
- Migration: 0007 (`sync_at_hours` field)

### v0.2.0
- **Test SNMP result page** — full OK/Failed page instead of a toast message that was
  silently swallowed by the browser; last test time, status badge, and message are
  persisted and shown in the list column and device panel
- **Bulk SNMP test** — select multiple configs → *Test selected* → concurrent probes
  (thread pool of 8), combined result page
- **Global settings in UI** — `SNMPSyncConfig` singleton editable at SNMP Sync → Settings
  without restarting NetBox
- **Per-device scheduler** — `ScheduledSNMPSyncJob` enqueues one isolated `SNMPSyncJob`
  per due device; a slow or unreachable device no longer blocks the queue
- **VLAN membership sync** — writes tagged/untagged VLAN assignments; optionally
  auto-creates missing VLANs in the device's site
- **Changelog integration** — all ORM writes from background jobs are wrapped in
  `event_tracking(NetBoxFakeRequest(...))` so they appear in NetBox's audit log
- **Revert run** — `SyncRunObject` tracks created objects; *Revert run* deletes exactly
  those objects; deletions are also recorded in the change log
- **Bulk setup** — create SNMP configs for many devices at once, optionally reading the
  community string from a custom field
- **REST API secrets protection** — `community`, `auth_key`, `priv_key` are `write_only`
  in the serializer
- **Security tests** — 17 tests covering secret exposure, permission checks, job isolation
- Migrations: 0004 (`SNMPSyncConfig`), 0005 (field cleanup), 0006 (`last_test_*` fields)

### v0.1.0
- Initial release: SNMP collection (pysnmp 7.x, asyncio), per-device configuration,
  Compare / Sync background jobs, `SyncRun` history, REST API

---

## Contributing

Pull requests are welcome. Please open an issue first to discuss the change, include tests
for new functionality, and follow the existing code style (single quotes, 120-char line
length, `ruff` for linting).

## License

[MIT](LICENSE)
