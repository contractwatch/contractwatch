# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities privately via [GitHub Security Advisories](https://github.com/contractwatch/contractwatch/security/advisories/new). Do not open public issues for security-sensitive findings.

## In scope

- Code-level vulnerabilities in the engine, loader, or scanner (SQL injection, path traversal, deserialization issues, dependency CVEs, etc.)
- Structural filter bypass patterns: an entity-naming or description-pattern technique that lets a flag-eligible award be incorrectly stripped from the dashboard
- Data poisoning vectors in the bulk loader or daily scan paths

## Not in scope

- Reports about specific awards on the dashboard. The dashboard is descriptive; every flagged award links to its public USASpending record and most have routine explanations
- Reports about USASpending data accuracy. Source data quality is the responsibility of the awarding agencies and USASpending.gov
- Issues with forks or third-party deployments not affiliated with this repository
