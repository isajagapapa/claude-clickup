# Refactor plan

Change the structure of the code without changing what it does.

1. **Goal**: what gets better (readability, duplication, coupling, speed of change) and why now.
2. **Behaviour that must not change**: the public functions, endpoints and outputs to keep identical.
3. **Safety net first**: existing tests that cover it, and characterization tests to add before moving anything.
4. **Steps**: small moves, each one leaving the suite green and reviewable on its own.
5. **Out of scope**: tempting changes you will not make in this card.
6. **Verify**: suite green after every step, no behaviour change in the running app.
