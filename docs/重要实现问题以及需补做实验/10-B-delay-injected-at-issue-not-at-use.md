# The modeled delay is spent where the access is issued, not where the value is used

All code quoted here was read from the remote branch `origin/hybrid`.

## What we read in the code

The rewrite sequence in `src/ptxpass_hbf/transform.cpp` emits, in order: the call
that obtains the timing, the fault branch, and finally, at line 222,
`replace_address(*operation, address)`, which swaps the address operand of the
original memory instruction for the address the call returned. `replace_address`
is defined at line 63 of the same file.

The waiting happens inside that call. In
`src/cuda_runtime/device/hbf_device.cu`, line 62 reads `%globaltimer`, lines 66
to 70 define `bounded_sleep`, which starts at 64 nanoseconds and doubles up to a
ceiling of 1048576 nanoseconds, and three places spin until the target time is
reached: line 236, around line 364, and around line 412.

## Why it looks questionable to us

Because the memory instruction now takes its address from the value the call
returned, the two are joined by a real data dependence, and ptxas — the compiler
that turns `PTX` into the machine code the card runs — cannot move the memory
instruction above the call. The wait therefore always completes before the
access is issued.

On real hardware a global read is issued asynchronously: the instruction starts
the access, the program keeps running, and the thread stops only when the
destination register is read by a later instruction. Putting the wait at the
issue point turns an access that later instructions could have covered into a
stall that nothing can cover.

## Which direction the effect goes

Upward: the modeled execution time comes out above what the same access pattern
would cost on hardware. We have not measured how far above, and give no factor
here.

## Where we may have read it wrong

The placement may be a deliberate trade. Waiting at the issue point is far simpler
to emit than waiting at the first use, which needs the pass to find that use; and
the capacity mode — the mode where the call hands back a different address, in the
page cache — has to substitute the address before the access executes, so for that
mode the wait has nowhere else to go. It is also possible that in the timing-only mode — the mode that adds a delay and
leaves the data where it is — the difference is small for the kernels this project
runs, if those kernels consume a loaded value close to where the value was loaded.

## What we would like you to confirm

1. In the timing-only mode, could the wait be moved to just before the first
   instruction that uses the destination register, leaving the call itself at the
   issue point?
2. In the capacity mode, does the wait have to stay at the issue point? Our
   reading is that it does, because the address the call returns is a different
   address in the page cache and a correction made after the access has executed
   would be too late. That reading is our inference from the code, not something
   we found stated anywhere, so please say whether it is right.

