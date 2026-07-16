# Constrained-random generation

Constrained-random generation samples stimulus from a declared space under constraints,
rather than enumerating hand-written cases. A seed resolves to a concrete scenario by a
pure function, so any run reproduces exactly from its seed.

Coverage-directed generation biases the sampler toward bins that are still unhit, using
live coverage feedback. It is the difference between verification and fuzzing-by-luck:
random sampling has a long coupon-collector tail, while a directed sampler targets what it
has not yet tested.

Fault injection makes the environment adversarial on purpose. Common fault kinds are a
returned error, a timeout, corrupted output, and added latency. Faults are models of
failure modes; they are not an emulation of the external system.

Delta debugging reduces a failing case to a minimal one that still fails. The classic
algorithm, ddmin, repeatedly removes subsets and keeps the removal whenever the failure
survives, yielding a one-minimal reproducer.
