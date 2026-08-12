# Cloud Deployment Guide

Quantum Trader Pro deploys as a **finite, simulation-only systemd job**. It does not need a public port, inbound firewall rule, broker credential, API key, database server, or persistent background loop.

## Deployment Choices

| Approach | Benefit | Trade-off | Status |
|---|---|---|---|
| Manual virtual-environment run | Simplest debugging and local iteration | Relies on the invoking user and shell environment | Supported |
| Hardened systemd oneshot service | Repeatable, least privilege, network isolated, journaled | Requires root once for installation | **Recommended for the cloud computer** |
| Scheduled systemd timer | Repeats a declared simulation automatically | Can create many artifacts and stale-data confusion | Not enabled by default |
| Paper or live service | External execution | Requires credentials and a separate security design | Not implemented |

## Filesystem Layout

| Path | Purpose | Permissions |
|---|---|---|
| `/opt/quantum-trader-pro` | Immutable checked-out source and virtual environment | Root-owned; service-readable |
| `/srv/quantum-trader-data` | Operator-provided local OHLCV input | Root-owned; service-readable |
| `/var/lib/quantum-trader` | Timestamped run artifacts | `quantumtrader`-owned; service-writable |
| `/etc/quantum-trader/simulation.env` | Non-secret run parameters | Root-owned, mode `0640` |
| `/etc/systemd/system/quantum-trader-sim.service` | Hardened service unit | Root-owned |

## Installation

Run these steps from an audited repository checkout:

```bash
sudo useradd --system --home /var/lib/quantum-trader --shell /usr/sbin/nologin quantumtrader
sudo install -d -o root -g root -m 0755 /opt/quantum-trader-pro
sudo install -d -o root -g quantumtrader -m 0750 /srv/quantum-trader-data
sudo install -d -o quantumtrader -g quantumtrader -m 0750 /var/lib/quantum-trader
sudo install -d -o root -g quantumtrader -m 0750 /etc/quantum-trader
```

Copy the repository into `/opt/quantum-trader-pro`, then build an isolated environment:

```bash
cd /opt/quantum-trader-pro
sudo python3 -m venv venv
sudo ./venv/bin/python -m pip install --upgrade pip
sudo ./venv/bin/python -m pip install .
sudo chown -R root:root /opt/quantum-trader-pro
sudo chmod 0755 scripts/run-cloud-simulation.sh
```

Install the non-secret environment and service templates:

```bash
sudo install -o root -g quantumtrader -m 0640 \
  deployment/simulation.env.example /etc/quantum-trader/simulation.env
sudo install -o root -g root -m 0644 \
  deployment/quantum-trader-sim.service \
  /etc/systemd/system/quantum-trader-sim.service
sudo systemctl daemon-reload
```

Copy a reviewed CSV into `/srv/quantum-trader-data`, then update `QTP_DATA_FILE`, `QTP_SYMBOL`, strategy windows, costs, and risk limits in `/etc/quantum-trader/simulation.env`. The service does not read an `.env` file from the repository.

## Verification Before Start

```bash
sudo -u quantumtrader /opt/quantum-trader-pro/venv/bin/quantum-trader preflight
sudo systemd-analyze verify /etc/systemd/system/quantum-trader-sim.service
sudo systemctl cat quantum-trader-sim.service
```

The preflight must report one allowed mode, no network requirement, no broker credential requirement, and both paper and live trading unimplemented. Review `systemctl cat` to ensure no local drop-in weakens the hardening settings.

## Run and Observe

Start one finite job:

```bash
sudo systemctl start quantum-trader-sim.service
sudo systemctl status quantum-trader-sim.service --no-pager
sudo journalctl -u quantum-trader-sim.service -n 100 --no-pager
```

A successful oneshot unit becomes `inactive (dead)` with a successful exit status after writing a timestamped directory under `/var/lib/quantum-trader/runs/`. That state is expected; it does not mean the job was canceled.

Inspect the newest result:

```bash
latest="$(find /var/lib/quantum-trader/runs -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
sudo -u quantumtrader cat "$latest/simulation_report.md"
sudo -u quantumtrader sha256sum "$latest"/*
```

The service’s `PrivateNetwork=yes` and `RestrictAddressFamilies=AF_UNIX` settings prevent Internet access from the simulation process. The input must already exist locally.

## Scheduling

No timer is installed or enabled by default. If repeated execution is later required, define a separate `.timer` with an explicit cadence and data-refresh process. A timer must not imply that stale historical data represents current trading, and retention limits must be defined before recurring runs begin.

## Upgrade and Rollback

Stop starting new jobs, preserve `/var/lib/quantum-trader`, and install the new release into a versioned directory or from a verified Git commit. Run the quality gate and preflight before repointing the service. Do not upgrade while a oneshot job is active.

Rollback by restoring the prior root-owned source directory and virtual environment, then running `daemon-reload`, preflight, and one smoke simulation. Existing run directories remain independent evidence and should not be modified.

## Uninstall

```bash
sudo systemctl disable --now quantum-trader-sim.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/quantum-trader-sim.service
sudo systemctl daemon-reload
sudo userdel quantumtrader 2>/dev/null || true
```

Remove `/opt/quantum-trader-pro`, `/etc/quantum-trader`, `/srv/quantum-trader-data`, or `/var/lib/quantum-trader` only after separately backing up any source, input, or run evidence you intend to retain.

## Incident Response

If a run behaves unexpectedly, do not delete its directory. Copy the report, ledger, input checksum, installed commit hash, environment file with no secrets, service unit, and journal to a restricted incident folder. Because no network broker exists, stopping the unit cannot strand an external order; local output integrity remains the primary concern.
