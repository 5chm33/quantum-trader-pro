# Security Policy

## Supported Versions

| Version | Supported |
|---|---:|
| Latest release on `main` | Yes |
| Older tags | Security fixes are assessed case by case |
| Historical legacy archive | No; preserved for audit only and not distributed here |

## Reporting a Vulnerability

Please use GitHub’s private security-advisory feature for this repository. Include the affected commit, component, reproduction steps, impact, and any safe mitigation you have identified. Do not include real credentials, brokerage account data, or private market records in the report.

A good report explains whether the issue can violate the simulation-only boundary, bypass risk review, corrupt accounting, alter event ordering, overwrite arbitrary files, exhaust resources, expose secrets, forge evidence, or start duplicate processes.

## Response Expectations

The maintainer will acknowledge a complete report when reasonably possible, reproduce the issue in an isolated environment, assign severity based on exploitability and impact, and coordinate a fix before public disclosure. Exact response timelines are not guaranteed for a solo-maintained portfolio project.

## Security Scope

| In scope | Out of scope |
|---|---|
| Execution-policy bypass | Claims that simulation cannot predict future returns |
| Unsafe filesystem writes | Attacks requiring prior root control of the host |
| Ledger/report integrity flaws | Vulnerabilities solely in unsupported historical files |
| Dependency or CI compromise | Social engineering unrelated to this repository |
| Secret leakage in source or artifacts | Authenticated paper-broker behavior not yet enabled by a public command |
| Fixed-origin paper adapter, durable journal, recovery, and operator-control bypass | Real-money execution, because no live adapter or command exists |
| Denial of service from crafted input | Feature requests without a security impact |

## Credential Incident Procedure

The public simulation path requires no secret. Optional internal paper components accept only strict out-of-band credential files and have no public command. If any credential is committed or attached to an issue, treat it as compromised immediately: revoke and rotate it, remove it from the current tree, assess repository history and cached artifacts, invalidate dependent sessions, and document the incident without reproducing the secret value.

## Supply-Chain Controls

The cross-platform development graph is committed in `uv.lock`, and both local acceptance and protected Python jobs reject lock drift. External GitHub Actions are pinned to immutable 40-character commit SHAs. The protected Python 3.12 job exports the locked graph, audits it for known vulnerabilities, generates a CycloneDX SBOM, and retains both files as workflow artifacts. These controls reduce drift and mutable-tag risk but do not eliminate registry, runner, dependency, or zero-day compromise.

## Safe-Harbor Intent

Good-faith research that avoids privacy violations, service disruption, financial transactions, persistence outside the test environment, and data destruction is welcome. Stop testing and report privately if you encounter a real credential, personal data, or a path that could place an order.
