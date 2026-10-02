# Security and privacy boundaries

The repository contains original prototype code, synthetic fixtures and aggregate
engineering measurements. It contains no actual API token, broker/account record,
market database, user conversation export or customer contact material.

Practice connectors constrain request destinations/routes and reject credential-
forwarding redirects. Tokens are prompted locally, never put into command-line
arguments, and passed to workers through private stdin pipes. They remain in
process memory; this is not encryption or proof against local-process compromise.

The chat-export utility excludes internal instructions/private reasoning and
redacts known credential patterns. This is a heuristic, not a complete data-loss
prevention system. Review exports before sharing. Never upload a real transcript
or generated data directory as a repository fixture.

Live collectors are optional, read-only and not invoked by tests or CI. No order
placement, bank transaction or invoice mutation is implemented. Data rights and
user authorization remain separate from technical API access.

For an accidental exposure, revoke affected credentials at the provider and
remove public artifacts/history as appropriate; changing a local file does not
revoke access. Report software issues without including secrets or private data.
