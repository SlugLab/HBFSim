# All traffic goes through one counter, while the profiles declare many channels

All code quoted here was read from the remote branch `origin/hybrid`.

## What we read in the code

The fast path — the timing model that runs on the GPU itself — tracks how far the
service has progressed in a single 64-bit value.
`src/host_service/control_layout.hpp` line 112:

```
alignas(8) std::uint64_t fast_channel_tail_ns;
```

`src/cuda_runtime/device/hbf_device.cu` advances that value with compare-and-swap:
a thread reads the current value, computes when its own request would finish, and
writes the new value back only if no other thread changed the value in between.

The parameter profiles under `configs/profiles/` describe a device with many
channels. `conservative.json` declares 16 channels, `nominal.json` 32, and
`aggressive.json` 64; each declares 8 dies per channel.

## Why it looks questionable to us

One value can represent one service channel. With one value, two requests that a
16-channel device would have served at the same time on different channels are
placed one after the other, and the second request waits for the first. The
profiles say 16, 32 or 64 channels; the timing behaves like one.

## Which direction the effect goes

Upward, in the queueing component of the latency: requests wait longer than they
would on a device with the declared number of channels. The gap is largest when
the device is close to saturation, since that is when the queue is what decides the
latency, and smallest when requests arrive far apart. We have not measured the gap
and give no factor here.

## Where we may have read it wrong

This may be a deliberate first version: one value is simple to advance atomically
from the GPU, whereas per-channel counters need an address computation for the
channel and raise the question of how a request is mapped onto a channel. It is
also possible that the workloads run so far never reach the arrival rate at which
the difference between one channel and 16 shows up, in which case the simplification
costs nothing yet.

## What we would like you to confirm

1. Is the single value a deliberate first version, or was the multi-channel case
   intended to be there already?
2. If per-channel counters are planned, how do you intend to map a request to a
   channel — by address bits, round-robin, or something else? We ask because the
   choice affects what the profiles' 8-dies-per-channel figure should do as well.
