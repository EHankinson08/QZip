# Security

## Reporting a vulnerability
Please don't post details of a vulnerability in a public issue. Use the repository's **Security → Report a
vulnerability** form, or open a short issue asking for a private contact and we'll follow up.

## Scope and known limits
No keys or secrets are in this repo. Placeholder API keys found in the old code (web UI default key, two
test helpers) were removed. Never commit `.env*`, databases or model/data files (see `.gitignore`).
The servers are not hardened for public exposure (open CORS, pickle job store, zip extraction).
