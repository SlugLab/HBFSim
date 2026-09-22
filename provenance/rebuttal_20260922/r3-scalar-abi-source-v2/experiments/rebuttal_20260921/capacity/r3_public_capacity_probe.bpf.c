#define SEC(name) __attribute__((section(name), used))

SEC("kprobe/r3_page_read_kernel")
int cuda__r3_public_capacity(void* context)
{
    (void)context;
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
