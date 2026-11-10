# Security policy

## Supported versions

Only the latest release of NET-TRV receives fixes.

## Reporting a vulnerability

Please report security problems **privately** via GitHub:
**Security → Report a vulnerability** on
<https://github.com/kingpainter/net-trv/security/advisories/new>.
Do not open a public issue.

Include what you found, how to reproduce it and the affected version. You will
get an answer as soon as possible; this is a hobby project maintained in spare
time.

## What counts

- Bypassing the webhook signature check (HMAC-SHA256 in `X-Netatmo-secret`).
- Leaking OAuth tokens, the client secret, the webhook id or URL, or personal
  data (e-mail, home name, location) in logs, diagnostics or entities.
- Anything that lets someone control the heating without being signed in.

## Keep your secrets out of issues

Never post your client id, client secret, access or refresh tokens, or your
webhook / Nabu Casa cloudhook URL. NET-TRV diagnostics redact these
automatically, so attach the diagnostics file instead of copying values by
hand. If you posted a secret by mistake, delete it and regenerate the client
secret on <https://dev.netatmo.com/apps>.
