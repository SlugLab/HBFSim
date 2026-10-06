// Included only in the adapter's private namespace; no worker wire change.
std::string stripe_descriptor_digest(json descriptor)
{
    descriptor.erase("descriptor_sha256");
    // Default json objects have sorted keys. ensure_ascii matches the generator's
    // canonical JSON bytes, including aliases; no field is excluded except the digest.
    const auto bytes = descriptor.dump(-1, ' ', true);
    unsigned char digest[SHA256_DIGEST_LENGTH];
    if (!SHA256(reinterpret_cast<const unsigned char*>(bytes.data()), bytes.size(), digest))
        throw std::runtime_error("stripe descriptor SHA256 failed");
    std::ostringstream result;
    result << std::hex << std::setfill('0');
    for (const auto byte : digest)
        result << std::setw(2) << static_cast<unsigned>(byte);
    return result.str();
}

std::map<std::uint32_t, Placement> load_page_striped_placements(
    const json& root, const ucie::MultistackProfile& profile)
{
    exact_keys(root, {"schema", "placements"});
    if (root.at("schema") != "hbfsim.ucie.host_placement.page_striped.v3" ||
        !root.at("placements").is_array() || root.at("placements").empty() ||
        root.at("placements").size() > 147 ||
        root.at("placements").size() > profile.software.max_backing_ranges ||
        profile.stack_count != 4 || profile.modules_per_stack != 16 ||
        profile.stack_capacity_bytes != (512ULL << 30))
        throw std::invalid_argument("invalid compact stripe placement geometry");
    const auto module_bytes = profile.stack_capacity_bytes / profile.modules_per_stack;
    const auto capacity = profile.stack_capacity_bytes * profile.stack_count;
    std::map<std::uint32_t, Placement> result;
    std::set<std::uint64_t> ordinals;
    std::set<std::string> aliases;
    std::vector<std::pair<std::uint64_t, std::uint64_t>> logical, physical;
    std::map<std::uint32_t, std::size_t> per_channel_segments;
    for (const auto& value : root.at("placements")) {
        exact_keys(value, {"range_id", "file_offset", "length", "page_bytes",
            "registered_address", "endpoint_id", "generation", "stream_id",
            "aliases", "storage_sha256", "canonical_ordinal", "phase", "segments",
            "descriptor_sha256"});
        const auto id = unsigned_field(value, "range_id");
        const auto stream = unsigned_field(value, "stream_id");
        const auto ordinal = unsigned_field(value, "canonical_ordinal");
        const auto phase = unsigned_field(value, "phase");
        if (!id || id > UINT32_MAX || stream > UINT32_MAX || ordinal >= 147 ||
            !ordinals.insert(ordinal).second || phase != ordinal % 64)
            throw std::invalid_argument("invalid stripe parent/stream/phase");
        Placement item{.range_id = static_cast<std::uint32_t>(id),
            .file_offset = unsigned_field(value, "file_offset"),
            .registered_address = unsigned_field(value, "registered_address"),
            .length = unsigned_field(value, "length"),
            .page_bytes = unsigned_field(value, "page_bytes"),
            .endpoint_id = unsigned_field(value, "endpoint_id"),
            .generation = unsigned_field(value, "generation")};
        item.striped = true;
        item.stream_id = static_cast<std::uint32_t>(stream);
        item.extent = extent(item.length, item.page_bytes);
        if (item.page_bytes != 16384 || !item.registered_address ||
            !item.endpoint_id || !item.generation || item.file_offset % item.page_bytes)
            throw std::invalid_argument("invalid stripe logical parent identity");
        checked_add(item.registered_address, item.length);
        logical.emplace_back(item.file_offset, checked_add(item.file_offset, item.extent));
        if (!value.at("aliases").is_array() || value.at("aliases").empty())
            throw std::invalid_argument("stripe aliases absent");
        for (const auto& alias : value.at("aliases")) {
            if (!alias.is_string() || alias.get<std::string>().empty() ||
                !aliases.insert(alias.get<std::string>()).second)
                throw std::invalid_argument("invalid or duplicated stripe alias");
            item.aliases.push_back(alias.get<std::string>());
        }
        const auto hex_digest = [](const json& field) {
            if (!field.is_string()) throw std::invalid_argument("stripe hash type");
            const auto text = field.get<std::string>();
            if (text.size() != 64 || !std::all_of(text.begin(), text.end(),
                [](char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); }))
                throw std::invalid_argument("stripe hash format");
            return text;
        };
        item.storage_sha256 = hex_digest(value.at("storage_sha256"));
        item.stripe.parent = {.region_id = id, .address = item.file_offset,
            .bytes = item.extent, .canonical_id = id, .generation = item.generation,
            .endpoint_id = item.endpoint_id, .readable = true};
        item.stripe.actual_storage_bytes = item.length;
        item.stripe.page_bytes = item.page_bytes;
        item.stripe.phase = static_cast<std::uint32_t>(phase);
        item.stripe.descriptor_sha256 = hex_digest(value.at("descriptor_sha256"));
        if (stripe_descriptor_digest(value) != item.stripe.descriptor_sha256)
            throw std::invalid_argument("stripe complete descriptor SHA256 differs");
        const auto pages = item.extent / item.page_bytes;
        const auto& segments = value.at("segments");
        if (!segments.is_array() || segments.empty() || segments.size() > 64)
            throw std::invalid_argument("invalid packed segment list");
        std::set<std::uint64_t> channels;
        std::uint64_t total_pages = 0;
        for (const auto& segment : segments) {
            exact_keys(segment, {"channel", "page_count", "canonical_physical_address"});
            const auto channel = unsigned_field(segment, "channel");
            const auto count = unsigned_field(segment, "page_count");
            const auto start = unsigned_field(segment, "canonical_physical_address");
            if (channel >= 64 || !channels.insert(channel).second || count > UINT64_MAX / 16384)
                throw std::invalid_argument("invalid packed channel/count");
            const auto p0 = (channel + 64 - phase) % 64;
            const auto expected = p0 < pages ? 1 + (pages - 1 - p0) / 64 : 0;
            if (!expected || count != expected || start % 16384)
                throw std::invalid_argument("packed page union differs");
            const auto end = checked_add(start, count * 16384);
            if (end > capacity || start / module_bytes != (end - 1) / module_bytes ||
                start / profile.stack_capacity_bytes != channel % 4 ||
                start % profile.stack_capacity_bytes / module_bytes != channel / 4 ||
                ++per_channel_segments[static_cast<std::uint32_t>(channel)] >
                    profile.software.max_backing_ranges)
                throw std::invalid_argument("packed segment physical module/bound differs");
            physical.emplace_back(start, end);
            total_pages = checked_add(total_pages, count);
            item.stripe.segments.push_back({static_cast<std::uint32_t>(channel), start, count});
        }
        if (total_pages != pages || !result.emplace(item.range_id, std::move(item)).second)
            throw std::invalid_argument("stripe exact union/parent uniqueness differs");
    }
    for (auto* spans : {&logical, &physical}) {
        std::sort(spans->begin(), spans->end());
        for (std::size_t i = 1; i < spans->size(); ++i)
            if ((*spans)[i].first < (*spans)[i - 1].second)
                throw std::invalid_argument("overlapping stripe logical/physical parents");
    }
    return result;
}
