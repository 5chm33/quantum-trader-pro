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
| Secret leakage in source or artifacts | Broker integration, because none is implemented |
| Denial of service from crafted input | Feature requests without a security impact |

## Credential Incident Procedure

Quantum Trader Pro requires no secret. If a credential is nevertheless committed or attached to an issue, treat it as compromised immediately: revoke and rotate it, remove it from the current tree, assess repository history and cached artifacts, invalidate dependent sessions, and document the incident without reproducing the secret value.

## Safe-Harbor Intent

Good-faith research that avoids privacy violations, service disruption, financial transactions, persistence outside the test environment, and data destruction is welcome. Stop testing and report privately if you encounter a real credential, personal data, or a path that could place an order.
