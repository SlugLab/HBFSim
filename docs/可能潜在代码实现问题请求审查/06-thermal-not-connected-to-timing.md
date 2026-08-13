# The thermal work is not connected to the timing path

All code quoted here was read from the remote branch `origin/hybrid`. This point
covers the same subject as item 5 of `docs/QUESTIONS-FOR-COLLABORATOR.md`;
answering either place answers both.

## What we read in the code

The thermal side exists and runs. `scripts/thermal/` holds four scripts:
`collect.py` samples real telemetry, `fit_logp.py` fits a first-order thermal
response, `gpu_heat.py` is a sustained BF16 matrix-multiply heater, and
`simulate_overheat.py` extrapolates the calibration to the named scenarios and
computes when a threshold is crossed. `configs/thermal/` holds two configuration
files, and `CMakeLists.txt` lines 273 to 278 define a test named `thermal_logp`.

Searching `src/` and `include/` on `origin/hybrid` for `thermal`, `temperature`,
`junction`, `retention`, `arrhenius` and `activation_energy` returns zero hits for
all six. No line of code turns a temperature reading into an effect on access
latency.

## Why it looks questionable to us

The paper's central claim is that the temperature states the specification defines
are used to compute the rate that can actually be sustained over a long run, and
that the sustainable rate is applied inside a real execution. The measurement side
of that claim is built; the part that would make temperature change a number is
not there yet.

Where the connection would go is already clear from the code. The delay is spent
in the spin-wait in `src/cuda_runtime/device/hbf_device.cu`, and the rate follows
from the base latency plus the transferred bytes divided by the aggregate
bandwidth. Making those two quantities depend on the temperature state is enough
for the fast path.

**One constraint that limits the options.** The detailed MQSim path cannot follow
temperature as the code stands. `src/mqsim_adapter/mqsim_online.cpp` sets the media
parameters once, in `configure_mqsim` at lines 35 to 77; the parameters take effect
when `SSD_Device` is constructed at line 126, and there is no route to change any
of them afterwards. Three ways around that, with their costs, are written out as
item 3 of `docs/QUESTIONS-FOR-COLLABORATOR.md`.

## Which direction the effect goes

Not a direction on a number, but a claim without an implementation behind it. As
the code stands, every access is charged at one fixed temperature, so a run that
would have been slowed by heat produces the same numbers as a run that stays cool,
and the sustained-rate result the paper wants to report cannot be produced at all.

## Where we may have read it wrong

The connection may be deliberately staged to come after the timing model settles,
so that the temperature-dependent parameters are added once rather than reworked.
It is also possible that the connection is implemented somewhere we did not search
— we searched `src/` and `include/` only, on the six words listed above, so a
version that uses different words, or that lives in a script, would not have shown
up.

## What we would like you to confirm

1. Who writes the connection between temperature and the delay, and by when?
2. Under the pre-registration discipline we also need the fallback agreed in
   advance: if the connection is not running by that date, what weaker statement
   does the temperature claim become? Deciding that after the date has passed is
   what the discipline exists to prevent.

