# 30-second / 64 GB rerun

- Workstation: `kubuntu-workstation`, dispatched through Harnesslite.
- Commit: `36f597e79f4f7e389ef47133126cb06b4cc2609d`.
- Job: `0fcf9281-a50e-4e03-8ba5-cd72829956bc`.
- Only source-proof timeouts changed, from 10,000 to 30,000 ms, in the generator, checker, and both solver entry points. Pipeline arguments and model inputs were preserved.
- Enforced process-group memory limit: 64 GB; swap disabled.

## Diagnosis

The preceding 8 GB failure was a kernel-confirmed process-group OOM kill. The isolated solver child consumed 8,251,800 KiB of anonymous resident memory; the main proof process consumed about 195 MB of total resident memory. The large allocation occurred during solving, after source compilation. A longer timeout therefore exposed memory growth that the earlier one-second cutoff had interrupted.

A live native-stack inspection was attempted during the rerun, but the operating system denied attachment. The exact internal Z3 operation responsible has not been established. No solver algorithm or model semantics were changed.

## Rerun result

The full pipeline ran for 275.521 seconds and stopped at Stage 3. Cruise control, mixing, and thermostat each returned UNKNOWN with reason `solver_timeout` and recorded timeout 30,000 ms. No MDP proof was obtained; downstream discretization certification and reduced training were not reached.

The systemd unit peaked at 10.8 GB, with zero swap. It exited normally with pipeline failure status, not an OOM kill. The larger memory limit prevented the previous memory-limit failure but did not resolve the proof timeouts.

[Run evidence](timeout30-64gb-events.json)

[Detailed artifacts](integrated-worker-artifacts/outputs/compact_lazy_timeout30_64gb_20260919/summary.json)
