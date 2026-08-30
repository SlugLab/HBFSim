#define SEC(name) __attribute__((section(name), used))

SEC("kprobe/hbf_access_kernel")
int cuda__hbf_microbench(void* context)
{
    (void)context;
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
