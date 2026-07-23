# Security Policy

OpenKOS is a **local-first** project: it runs on your machine and, by design, does not require sending your knowledge to any server. Most of the trust boundary is therefore local — but that does not make security unimportant, especially since OpenKOS ingests untrusted content and can drive AI agents.

## Project status

OpenKOS is **alpha**. There is no published release yet, so there is no supported-version matrix to publish. Security fixes are applied to the `main` branch. This document will be updated with a supported-versions table once the first release ships.

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues, discussions, or pull requests.**

Instead, use one of these private channels:

1. **GitHub private vulnerability reporting** (preferred): go to the repository's **Security** tab and choose **Report a vulnerability**. This opens a private advisory visible only to maintainers.
2. **Email**: send the details to **jasonssdev@gmail.com** with a subject line starting with `[SECURITY]`.

Please include, as much as you can:

- a description of the vulnerability and its potential impact;
- the steps or a minimal proof of concept to reproduce it;
- affected component (for example: ingestion, extraction, retrieval, CLI, API, MCP server);
- your environment (OS, Python version, commit or version);
- any suggested remediation.

## What to expect

- **Acknowledgement** within a few days of your report.
- An initial assessment and, if valid, a plan and rough timeline for a fix.
- Updates as we work on it, and credit for the discovery when the fix is published (unless you prefer to remain anonymous).

We follow **coordinated disclosure**: please give us reasonable time to release a fix before disclosing publicly. We will work with you on timing.

## Scope

Security-relevant areas that are especially in scope for OpenKOS include:

- **Untrusted source ingestion.** OpenKOS reads arbitrary text, and later PDFs, web content, and other files. Reports about malicious inputs that cause code execution, path traversal, resource exhaustion, or corruption of the knowledge bundle are in scope.
- **Prompt injection.** Because ingested content and compiled knowledge are fed to language models and agents, injection that causes an agent to take unintended actions, exfiltrate data, or bypass review is in scope.
- **Permission and boundary escapes.** Any way OpenKOS reads or writes outside its intended directories, or an agent exceeds its granted permissions.
- **Provenance or freshness integrity.** Ways to forge provenance chains or defeat the freshness guarantees such that the system silently presents false knowledge as trustworthy.
- **Supply chain.** Issues in how dependencies are pinned, fetched, or executed.

## Out of scope

- Vulnerabilities in third-party dependencies that have no impact on OpenKOS (please report those upstream).
- Issues that require a compromised local machine or a malicious operator who already has full local access, since the local user is inside the trust boundary.
- Missing hardening that has no demonstrated exploit (we welcome these as regular enhancement issues instead).

## A note for users

Because OpenKOS runs locally and can act through AI agents, treat the sources you ingest the way you would treat any untrusted input, and keep a human in the loop for consequential, agent-driven changes to your knowledge base. Provenance and freshness are designed to keep the base auditable — use them.

---

Thank you for helping keep OpenKOS and its users safe.
