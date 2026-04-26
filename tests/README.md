# Regression Test Strategy

The kernel should be tested against pristine-XFOIL baselines before and after
each extraction step.

Test layers:

1. baseline parser tests,
2. worker protocol tests,
3. numerical regression tests against pristine-XFOIL outputs,
4. client integration tests using a fake worker,
5. optional local integration tests using the real worker executable,
6. modernization safety tests that characterize refresh reproducibility,
   one-shot/session equivalence, option-change invalidation, warm-start
   direction, boundary-layer reset behavior, and stress-airfoil paneling
   behavior before Fortran state
   refactors.

Numerical comparisons should use tolerances appropriate to XFOIL's iterative
behavior. Exact text-output matching should be avoided except for protocol and
parser tests.

Transition regression cases should include:

- free transition with `xtr_top = xtr_bottom = 1.0`,
- forced early transition on both sides,
- asymmetric forced transition,
- a case where actual transition occurs upstream of the forced trip.

Baselines must store both the forced-trip inputs and the actual transition
outputs returned by the solved operating point.
