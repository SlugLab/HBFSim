#define SEC(name) __attribute__((section(name), used))

SEC("kprobe/hbf_vmem_sequential")
int cuda__hbf_vmem_tuning(void* context)
{
    (void)context;
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
