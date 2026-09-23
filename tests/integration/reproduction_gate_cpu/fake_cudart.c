#include <stddef.h>
#include <stdint.h>

typedef struct { unsigned x, y, z; } dim3;
static int public_calls, private_calls, return_code;
uint64_t bpftime_nv_strict_bridge_capabilities_v1(void) { return 3; }

void fake_reset(int result) { public_calls = private_calls = 0; return_code = result; }
int fake_public_calls(void) { return public_calls; }
int fake_private_calls(void) { return private_calls; }
int fake_public(const void *f, dim3 g, dim3 b, void **a, size_t s, void *stream)
{ (void)f;(void)g;(void)b;(void)a;(void)s;(void)stream; ++public_calls; return return_code; }
int fake_private(void *f, dim3 g, dim3 b, void **a, size_t s, void *stream)
{ (void)f;(void)g;(void)b;(void)a;(void)s;(void)stream; ++private_calls; return return_code; }
int fake_get_func(void **out, const void *f) { (void)f; *out = 0; return 1; }
int cudaHostRegister(void *p, size_t n, unsigned flags) { (void)p;(void)n;(void)flags; return 801; }
int cudaHostUnregister(void *p) { (void)p; return 801; }
int cudaDeviceSynchronize(void) { return 801; }
int cudaHostGetDevicePointer(void **out, void *p, unsigned flags)
{ (void)p;(void)flags; *out=0; return 801; }
const char *cudaGetErrorString(int code) { (void)code; return "fake cudart"; }
int cudaGetLastError(void) { return 801; }
int cudaSetDeviceFlags(unsigned flags) { (void)flags; return 801; }
int cudaStreamIsCapturing(void *stream, int *status)
{ (void)stream; if (status) *status=0; return 801; }
int fake_cudaHostRegister13(void *p, size_t n, unsigned flags)
{ return cudaHostRegister(p,n,flags); }
int fake_cudaHostUnregister13(void *p) { return cudaHostUnregister(p); }
int fake_cudaHostGetDevicePointer13(void **out, void *p, unsigned flags)
{ return cudaHostGetDevicePointer(out,p,flags); }
int fake_cudaStreamIsCapturing13(void *stream, int *status)
{ return cudaStreamIsCapturing(stream,status); }
int fake_cudaSetDeviceFlags13(unsigned flags) { return cudaSetDeviceFlags(flags); }
int fake_cudaGetLastError13(void) { return cudaGetLastError(); }
const char *fake_cudaGetErrorString13(int code) { return cudaGetErrorString(code); }

__asm__(".symver fake_public,cudaLaunchKernel@@libcudart.so.12");
__asm__(".symver fake_private,__cudaLaunchKernel@@libcudart.so.13");
__asm__(".symver fake_get_func,cudaGetFuncBySymbol@@libcudart.so.12");
__asm__(".symver fake_cudaHostRegister13,cudaHostRegister@libcudart.so.13");
__asm__(".symver fake_cudaHostUnregister13,cudaHostUnregister@libcudart.so.13");
__asm__(".symver fake_cudaHostGetDevicePointer13,cudaHostGetDevicePointer@libcudart.so.13");
__asm__(".symver fake_cudaStreamIsCapturing13,cudaStreamIsCapturing@libcudart.so.13");
__asm__(".symver fake_cudaSetDeviceFlags13,cudaSetDeviceFlags@libcudart.so.13");
__asm__(".symver fake_cudaGetLastError13,cudaGetLastError@libcudart.so.13");
__asm__(".symver fake_cudaGetErrorString13,cudaGetErrorString@libcudart.so.13");
