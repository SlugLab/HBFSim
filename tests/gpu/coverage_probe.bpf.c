#define SEC(name) __attribute__((section(name), used))

SEC("kprobe/unsupported_hbf_kernel")
int hbfsim_coverage_probe(void* context)
{
    (void)context;
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
