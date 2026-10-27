# Test plan

Add or fix tests: coverage, flaky tests, missing scenarios, test infrastructure.

1. **Target**: the behaviour or code under test and why it needs tests now (bug history, risk, low coverage, flakiness).
2. **Current coverage**: existing tests around it, the test framework and helpers the project uses, and gaps.
3. **Cases**: the scenarios to cover, one per line: happy path, edge cases, error paths, permissions.
4. **Approach**: unit, feature or end-to-end per case, with the factories, fixtures and fakes to reuse. For a flaky test, the cause (timing, order, shared state, network) and how to remove it.
5. **Production code**: changes needed only to make it testable, kept minimal. Bugs found while testing go into Recommendations unless the card covers them.
6. **Verify**: the new tests fail when the behaviour breaks (try it), pass on repeated runs, and the full suite stays green.
