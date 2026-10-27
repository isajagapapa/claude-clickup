# Bug plan

Something worked, or should work, and does not. Fix the cause with the smallest safe change and prove it with a test.

1. **Reproduce**: steps to trigger it, expected vs actual behaviour, environment (browser, device, user role, data). Mark any step the card leaves unclear.
2. **Root cause**: hypotheses, the code paths to check (files and functions), and logs, data or queries that confirm or rule each one out.
3. **Failing test first**: a regression test that fails today because of this bug. Name the test file.
4. **Fix**: the minimal change and where it goes. Note what you deliberately leave alone.
5. **Impact**: other features using the same code, data that may already be wrong and needs repair, migrations.
6. **Verify**: the new test passes, the full suite passes, the reproduction steps now behave as expected.
7. **Rollback**: how to revert safely if the fix misbehaves in production.
