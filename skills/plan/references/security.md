# Security plan

A vulnerability or security weakness: fix it fully without exposing it further.

1. **Threat**: what an attacker can do, who can do it (anonymous, any user, a role), and what is at stake (data, money, accounts). Rate the severity.
2. **Attack surface**: the entry points involved (routes, forms, uploads, webhooks, jobs) and the code paths behind them, with files and functions.
3. **Exploit test first**: a test that reproduces the attack and fails today. Keep payloads and secrets out of logs, commits and plan text shared outside the team.
4. **Fix**: the root-cause fix at the right layer (validation, authorization, escaping, parameterized queries, secure defaults), not a filter for one payload. Check the sibling endpoints with the same pattern.
5. **Exposure**: whether it was exploited (logs, audit trails), data or credentials to rotate, users or authorities to notify.
6. **Verify**: the exploit test passes, the full suite passes, and the related security scans or audits (dependency audit, linters) are clean.
7. **Rollback**: how to revert safely without reopening the hole.
