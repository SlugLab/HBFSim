#define SEC(name) __attribute__((section(name), used))

SEC("kprobe/hbfsim_llama_probe_kernel")
int cuda__hbf_llama(void* context)
{
    (void)context;
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
