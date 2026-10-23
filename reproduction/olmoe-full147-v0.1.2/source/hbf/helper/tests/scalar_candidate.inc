void scalar_candidate(std::uint64_t* field, std::uint64_t value)
{
    if (value == 0) return;
    auto observed = system_acquire(field);
    for (;;) {
        const bool overflow = value > UINT64_MAX - observed;
        const auto desired = overflow ? UINT64_MAX : observed + value;
        auto expected = observed;
        if (system_compare_exchange(field, expected, desired)) {
            if (overflow) {
                (void)system_fetch_add(
                    &__hbfsim_access_accounting_counters.counter_overflow, 1);
            }
            return;
        }
        observed = expected;
    }
}
