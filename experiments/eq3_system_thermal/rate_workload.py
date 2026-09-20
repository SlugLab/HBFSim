"""Exact offered traffic, independent of controller and service outcomes."""
from __future__ import annotations


class RateWorkload:
    def __init__(self, channels, config):
        self.channels = channels
        self.config = config
        self.active_ns = config["active_ns"]
        self.rate = config["per_stack_Bps"]
        self.pattern = config.get("pattern", "continuous")
        if self.pattern not in {"continuous", "burst_equal_mean"}:
            raise ValueError("unknown rate pattern")
        if type(self.rate) is not int or self.rate < 0:
            raise ValueError("rate must be nonnegative integer B/s")
        self.remainder = {s: 0 for s in channels}
        self.cursor = {s: 0 for s in channels}
        self.now = 0
        self.total = 0

    def advance(self, start_ns, end_ns):
        if start_ns != self.now or end_ns <= start_ns:
            raise ValueError("noncontiguous workload horizon")
        duration = max(0, min(end_ns, self.active_ns) - start_ns)
        if self.pattern == "burst_equal_mean":
            period = self.config.get("burst_period_ns", 200_000_000)
            on = period // 2
            if period <= 0 or period % (end_ns - start_ns) or on % (end_ns - start_ns):
                raise ValueError("burst edges must align with thermal observation windows")
            duration *= 2 if start_ns % period < on else 0
        result = {}
        hot = self.config.get("hot_stack")
        distribution = self.config.get("channel_distribution", "uniform")
        for stack, channel_mapping in sorted(self.channels.items()):
            ids = sorted(channel_mapping, key=int)
            if distribution == "first_quarter":
                active = ids[:max(1, len(ids) // 4)]
            elif distribution == "first_half":
                active = ids[:max(1, len(ids) // 2)]
            elif distribution == "uniform":
                active = ids
            else:
                raise ValueError("unknown channel distribution")
            # Hot-stack experiment keeps aggregate offered bytes fixed: half
            # to one stack, half uniformly to the other stacks.
            if hot is not None:
                if hot not in self.channels or len(self.channels) < 2:
                    raise ValueError("invalid hot-stack identity")
                numerator = len(self.channels)
                denominator = 2 if stack == hot else 2 * (len(self.channels) - 1)
            else:
                numerator, denominator = 1, 1
            divisor = 1_000_000_000 * denominator
            offered, self.remainder[stack] = divmod(
                self.rate * duration * numerator + self.remainder[stack], divisor)
            base, tail = divmod(offered, len(active))
            row = {c: (base if c in active else 0) for c in ids}
            for offset in range(tail):
                row[active[(self.cursor[stack] + offset) % len(active)]] += 1
            self.cursor[stack] = (self.cursor[stack] + tail) % len(active)
            result[stack] = row
            self.total += offered
        self.now = end_ns
        return result
