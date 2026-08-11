#define SEC(name) __attribute__((section(name), used))

SEC("kprobe/fused_moe_kernel")
int cuda__hbf_moe(void* context)
{
    (void)context;
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
