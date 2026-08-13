# Cloud Deployment Evidence

> This document records the accepted cloud state for the current A+ research-software platform. It does **not** certify authenticated paper operation or live trading.

| Field | Accepted value |
|---|---|
| Deployment date | 2026-08-13 UTC |
| Source commit | `b60e311c53ff0111a2b2ade22d8d96c51e61042f` |
| Wheel SHA-256 | `dea83cda1fb32654a0eb840c27ed60b6590c1de823af6716416a1cb651b34060` |
| Runtime | CPython 3.11.15 in `/opt/quantum-trader-runtime-b60e311c53ff`, linked at `/opt/quantum-trader-pro/venv` |
| Service type | Finite, manually requested `systemd` one-shot simulation |
| Service state after acceptance | **Disabled and inactive** |
| Live execution | **Unavailable** |
| Public paper command | **Unavailable** |

## Installation and Rollback

The accepted source archive and wheel were checksummed before transfer and independently verified on the cloud computer. The package was installed from the wheel with `--no-index --no-deps`, so deployment did not resolve or download runtime dependencies. The source and environment are root-owned; execution uses the dedicated non-login `quantumtrader` account.

The first atomic installer uncovered a movable-virtual-environment defect: a generated console-script shebang retained the temporary staging path after the directory was renamed. The service was disabled and inactive, so no workload started. The v0.2.0 synchronization resolved the defect by building the environment directly at the stable versioned path `/opt/quantum-trader-runtime-b60e311c53ff` and linking it into the source tree only after validation. The original baseline and the prior accepted checkpoint remain preserved at `/opt/quantum-trader-pro.rollback.20260813T001403Z` and `/opt/quantum-trader-pro.rollback.20260813T002534Z`.

## Effective Runtime Boundary

The installed command exposes only `demo`, `simulate`, `evaluate`, `open-holdout`, `preflight`, and `version`. No operator command can submit a paper or live order. Preflight truthfully reports the completed literal-process and simulated-storage acceptance evidence while reporting service-manager paper recovery, authenticated paper acceptance, position flattening, public paper commands, and live execution as unavailable.

The accepted service unit matches the source copy byte-for-byte and enforces the following controls:

| Control | Effective value |
|---|---:|
| Dedicated account | `quantumtrader` |
| Private network namespace | `yes` |
| Address families | `AF_UNIX` only |
| New privileges | denied |
| System filesystem | strictly protected |
| Home directories | protected |
| Linux capabilities | empty |
| `systemd-analyze security` | **2.1 OK** |

## Deterministic Cloud Runs

Two manually requested, network-isolated one-shot simulations completed successfully. Each processed 1,255 events and produced six fills, one completed round trip, no risk halt, and a disclosed final marked open position. The service returned to inactive state after each run and remained disabled.

All six core artifacts were byte-for-byte identical across the independent runs:

| Artifact | SHA-256 |
|---|---|
| `events.sqlite3` | `d65a7e6cd06f7b937db334fca797437e3d9b69b60ef5b172bbd620bbbf2039ca` |
| `simulation_report.json` | `893368600d30ecb1ab70cbc4d5a96f4365279a0a15d27fdf40f382c2b91a00b6` |
| `simulation_report.md` | `218a052dac5d81f4542d8e59a17e775ddcf046c36464356cf886f8a4bcc3fc3c` |
| `equity_curve.csv` | `8bce0917aeab5034cbe949759ddf33ca190d296786822eb05688a7568002003c` |
| `fills.csv` | `c3a7b90aa4c3cbd8df7bc95700513758e3ce7af79c52481f2c7867fe69bfaf8d` |
| `round_trip_trades.csv` | `0810dc56d9dc00cbf2253a58913f54271dc40306eed746d1a2b4b15d5900928e` |

## Filesystem and Cleanup

| Path | Accepted mode and owner |
|---|---|
| `/opt/quantum-trader-pro` | `0755 root:root` |
| `/opt/quantum-trader-runtime-b60e311c53ff` | `0755 root:root` |
| `/opt/quantum-trader-pro/venv` | root-owned link to the versioned runtime |
| `/etc/quantum-trader` | `0750 root:quantumtrader` |
| `/var/lib/quantum-trader` | `0750 quantumtrader:quantumtrader` |
| `/srv/quantum-trader-data/spy_daily.csv` | `0640 root:quantumtrader` |

No Quantum Trader or unrelated user Python process remained after acceptance. Temporary source archives, wheels, checksums, and installer files were removed from the cloud home directory.

## Remaining Activation Boundary

The package contains thoroughly tested paper-safety internals, but the deployed operator surface cannot invoke them. Authenticated multi-session paper evidence, real broker outage/rate-limit behavior, a validated flatten action, and paper service-manager recovery remain mandatory before a paper command can be exposed. A live adapter and real-money activation remain structurally unavailable.
