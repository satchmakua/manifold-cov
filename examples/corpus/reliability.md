# Agent reliability

An agent is reliable when it behaves acceptably across the whole distribution of conditions
it will meet, not merely on a benchmark's happy path.

Flakiness is the gap between a single passing run and a dependable one. Running the same
seed K times and reporting how many passed gives a pass^k signal: a task that passes nine
times in ten is not a task that passes.

The classic tool-using failure is the retry loop: a tool returns an error, the agent retries
it, the error persists, and the agent retries forever until some budget stops it. A robust
agent bounds its retries and then degrades gracefully, saying plainly that it could not
retrieve the information.

Budgets bound the damage. A step budget caps tool calls, a cost budget caps spend, and a
wall budget caps elapsed time. Budget enforcement must not be swallowable by the agent's own
error handling, or the loop it is meant to stop will simply continue.
