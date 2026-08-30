#include "../../src/host_service/control_layout.hpp"
#include "../../src/host_service/request_dispatcher.hpp"

#include <hbfsim/api.h>

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <optional>
#include <limits>
#include <vector>

namespace hbfsim::host_service {

class RequestDispatcherTestAccess {
  public:
    static void set_next_engine_id(RequestDispatcher& dispatcher,
                                   std::uint64_t next)
    {
        dispatcher.next_engine_id_ = next;
        dispatcher.engine_ids_exhausted_ = false;
    }
};

}  // namespace hbfsim::host_service

namespace {

[[noreturn]] void fail(const char* expression, int line)
{
    std::fprintf(stderr, "capacity dispatch CHECK failed at line %d: %s\n",
                 line, expression);
    std::exit(1);
}

#define CHECK(expression)                                                      \
    do {                                                                       \
        if (!(expression)) {                                                   \
            fail(#expression, __LINE__);                                       \
        }                                                                      \
    } while (false)

struct ControlFixture {
    static constexpr std::uint32_t capacity = 8;

    ControlFixture()
        : bytes(hbfsim::host_service::control_region_bytes(capacity))
    {
        CHECK(::posix_memalign(&storage, 64, bytes) == 0);
        control = hbfsim::host_service::ControlView(storage, bytes);
        CHECK(control.initialize(capacity));
    }

    ~ControlFixture() { std::free(storage); }

    void* storage{nullptr};
    std::size_t bytes{0};
    hbfsim::host_service::ControlView control;
};

hbfsim::HbfRequest request(std::uint64_t id, std::uint64_t address)
{
    return hbfsim::HbfRequest{
        .request_id = id,
        .logical_address = address,
        .bytes = 4096,
        .range_id = 1,
        .operation = static_cast<std::uint32_t>(
            hbfsim::RequestOperation::Read),
        .page_generation = 9,
    };
}

hbfsim::HbfCompletion prepared_completion(
    const hbfsim::HbfRequest& value, std::uint64_t frame)
{
    return hbfsim::HbfCompletion{
        .request_id = value.request_id,
        .cache_frame_address = frame,
        .page_generation = value.page_generation,
        .status = static_cast<std::uint32_t>(hbfsim::RequestStatus::Ready),
    };
}

hbfsim::HbfCompletion engine_completion(
    const hbfsim::HbfRequest& action, std::uint64_t modeled_ns)
{
    return hbfsim::HbfCompletion{
        .request_id = action.request_id,
        .modeled_completion_ns = modeled_ns,
        .modeled_ns = modeled_ns,
        .page_generation = action.page_generation,
        .status = static_cast<std::uint32_t>(hbfsim::RequestStatus::Ready),
    };
}

}  // namespace

int main()
{
    using hbfsim::host_service::PreparedDispatch;
    using hbfsim::host_service::RequestDispatcher;

    {
        auto original = request(77, 0x8000);
        const auto dirty = hbfsim::host_service::prepare_capacity_media_dispatch(
            original,
            {.status = hbfsim::RequestStatus::Ready,
             .frame_address = 0x123000,
             .media = {
                 .flags = hbfsim::host_service::CapacityMediaProgram |
                          hbfsim::host_service::CapacityMediaRead,
                 .program_page = 3,
                 .program_range_id = 9,
             }},
            23);
        CHECK(dirty.media_action_count == 2);
        CHECK(dirty.completion.cache_frame_address == 0x123000);
        CHECK(dirty.completion.service_ns == 23);
        CHECK(dirty.media_actions[0].logical_address == 3 * original.bytes);
        CHECK(dirty.media_actions[0].range_id == 9);
        CHECK(dirty.media_actions[0].operation ==
              static_cast<std::uint32_t>(hbfsim::RequestOperation::Write));
        CHECK((dirty.media_actions[0].flags &
               hbfsim::host_service::kRequestFlagExplicitCapacityProgram) !=
              0);
        CHECK(dirty.media_actions[1].logical_address ==
              original.logical_address);
        CHECK(dirty.media_actions[1].operation ==
              static_cast<std::uint32_t>(hbfsim::RequestOperation::Read));

        const auto hit = hbfsim::host_service::prepare_capacity_media_dispatch(
            original,
            {.status = hbfsim::RequestStatus::Ready,
             .frame_address = 0x456000},
            7);
        CHECK(hit.media_action_count == 0);
        CHECK(hit.completion.modeled_ns == 0);

        original.bytes = 2;
        const auto overflow =
            hbfsim::host_service::prepare_capacity_media_dispatch(
                original,
                {.status = hbfsim::RequestStatus::Ready,
                 .frame_address = 0x789000,
                 .media = {
                     .flags = hbfsim::host_service::CapacityMediaProgram,
                     .program_page =
                         std::numeric_limits<std::uint64_t>::max(),
                     .program_range_id = 1,
                 }},
                0);
        CHECK(overflow.media_action_count == 0);
        CHECK(overflow.completion.status == static_cast<std::uint32_t>(
                                                hbfsim::RequestStatus::IoError));
        CHECK(overflow.completion.cache_frame_address == 0);
    }

    {
        ControlFixture fixture;
        fixture.control.ranges()[0] = {
            .base = 0x100000,
            .length = 8 * 4096,
            .file_offset = 0x10000,
            .range_id = 1,
            .mode = HBFSIM_RANGE_MODE_CAPACITY,
            .page_bytes = 4096,
        };
        hbfsim::host_service::atomic_store(
            fixture.control.header()->range_count, 1,
            std::memory_order_release);
        fixture.control.header()->request_timeout_ns = 1000;
        auto hit_request = request(88, 2 * 4096);
        std::uint64_t now = 100;
        std::size_t waits = 0;
        const auto hit = hbfsim::host_service::prepare_host_dispatch(
            fixture.control, hit_request,
            [&] { return now++; },
            [&] {
                ++waits;
                hbfsim::host_service::CapacityHandoff handoff{};
                CHECK(fixture.control.try_capacity_handoff(0, handoff));
                CHECK(fixture.control.complete_capacity_handoff(
                    handoff.ticket, handoff.request_id, 0xabc000,
                    hbfsim::RequestStatus::Ready));
            });
        CHECK(waits == 1);
        CHECK(hit.media_action_count == 0);
        CHECK(hit.completion.cache_frame_address == 0xabc000);
        CHECK(hit.completion.status == static_cast<std::uint32_t>(
                                           hbfsim::RequestStatus::Ready));

        auto program = hit_request;
        program.logical_address = fixture.control.ranges()[0].file_offset;
        program.flags =
            hbfsim::host_service::kRequestFlagExplicitCapacityProgram;
        program.operation = static_cast<std::uint32_t>(
            hbfsim::RequestOperation::Write);
        bool explicit_waited = false;
        const auto explicit_program =
            hbfsim::host_service::prepare_host_dispatch(
                fixture.control, program, [&] { return now++; },
                [&] { explicit_waited = true; });
        CHECK(!explicit_waited);
        CHECK(explicit_program.media_action_count == 1);
        CHECK(explicit_program.media_actions[0].flags == program.flags);

        auto invalid_program = program;
        invalid_program.flags |= 1U << 8;
        CHECK(hbfsim::host_service::prepare_host_dispatch(
                  fixture.control, invalid_program, [&] { return now++; },
                  [] {})
                  .media_action_count == 0);
        invalid_program = program;
        invalid_program.logical_address =
            fixture.control.ranges()[0].file_offset - program.bytes;
        CHECK(hbfsim::host_service::prepare_host_dispatch(
                  fixture.control, invalid_program, [&] { return now++; },
                  [] {})
                  .media_action_count == 0);
        invalid_program.logical_address =
            fixture.control.ranges()[0].file_offset +
            fixture.control.ranges()[0].length;
        CHECK(hbfsim::host_service::prepare_host_dispatch(
                  fixture.control, invalid_program, [&] { return now++; },
                  [] {})
                  .media_action_count == 0);
        invalid_program.logical_address -= program.bytes;
        CHECK(hbfsim::host_service::prepare_host_dispatch(
                  fixture.control, invalid_program, [&] { return now++; },
                  [] {})
                  .media_action_count == 1);

        bool unsupported_waited = false;
        const auto unsupported =
            hbfsim::host_service::prepare_host_dispatch(
                fixture.control, hit_request, [&] { return now++; },
                [&] { unsupported_waited = true; }, false);
        CHECK(!unsupported_waited);
        CHECK(unsupported.media_action_count == 0);
        CHECK(unsupported.completion.status == static_cast<std::uint32_t>(
                                                   hbfsim::RequestStatus::Unsupported));
        hbfsim::host_service::CapacityHandoff rejected_handoff{};
        CHECK(!fixture.control.try_capacity_handoff(0, rejected_handoff));

        auto timeout_request = request(89, 3 * 4096);
        std::uint64_t timeout_now = 0;
        const auto timeout = hbfsim::host_service::prepare_host_dispatch(
            fixture.control, timeout_request,
            [&] {
                timeout_now += 1001;
                return timeout_now;
            },
            [] {});
        CHECK(timeout.fail_all);
        CHECK(timeout.media_action_count == 0);
        CHECK(timeout.completion.status == static_cast<std::uint32_t>(
                                               hbfsim::RequestStatus::Timeout));
    }

    {
        ControlFixture fixture;
        auto hit = request(101, 0);
        std::uint64_t ticket = 0;
        CHECK(fixture.control.try_push_request(hit, ticket));
        std::size_t submissions = 0;
        std::size_t completion_polls = 0;
        RequestDispatcher dispatcher(
            fixture.control,
            RequestDispatcher::Engine{
                .prepare = [](const hbfsim::HbfRequest& value) {
                    return PreparedDispatch{
                        .completion = prepared_completion(value, 0x1000)};
                },
                .submit = [&](const hbfsim::HbfRequest&) { ++submissions; },
                .run_next_completion = [&]()
                    -> std::optional<hbfsim::HbfCompletion> {
                    ++completion_polls;
                    return std::nullopt;
                },
            });
        CHECK(dispatcher.poll_once());
        CHECK(submissions == 0);
        CHECK(completion_polls == 0);
        hbfsim::HbfCompletion completion{};
        CHECK(fixture.control.try_consume_completion(ticket, completion));
        CHECK(completion.request_id == 101);
        CHECK(completion.modeled_ns == 0);
        CHECK(completion.cache_frame_address == 0x1000);
    }

    {
        ControlFixture fixture;
        auto miss = request(201, 0x2000);
        std::uint64_t ticket = 0;
        CHECK(fixture.control.try_push_request(miss, ticket));
        std::vector<hbfsim::HbfRequest> submitted;
        RequestDispatcher dispatcher(
            fixture.control,
            RequestDispatcher::Engine{
                .prepare = [](const hbfsim::HbfRequest& value) {
                    auto action = value;
                    action.operation = static_cast<std::uint32_t>(
                        hbfsim::RequestOperation::Read);
                    return PreparedDispatch{
                        .completion = prepared_completion(value, 0x2000),
                        .media_actions = {action},
                        .media_action_count = 1,
                    };
                },
                .submit = [&](const hbfsim::HbfRequest& action) {
                    submitted.push_back(action);
                },
                .run_next_completion = [&]()
                    -> std::optional<hbfsim::HbfCompletion> {
                    return engine_completion(submitted.front(), 13);
                },
            });
        CHECK(dispatcher.poll_once());
        CHECK(submitted.size() == 1);
        CHECK(submitted[0].request_id != 0);
        CHECK(submitted[0].request_id != miss.request_id);
        CHECK(submitted[0].operation == static_cast<std::uint32_t>(
                                            hbfsim::RequestOperation::Read));
        hbfsim::HbfCompletion completion{};
        CHECK(fixture.control.try_consume_completion(ticket, completion));
        CHECK(completion.request_id == miss.request_id);
        CHECK(completion.modeled_ns == 13);
    }

    {
        ControlFixture fixture;
        auto dirty = request(301, 0x3000);
        auto unrelated = request(302, 0x9000);
        std::uint64_t dirty_ticket = 0;
        std::uint64_t unrelated_ticket = 0;
        CHECK(fixture.control.try_push_request(dirty, dirty_ticket));
        CHECK(fixture.control.try_push_request(unrelated, unrelated_ticket));

        std::vector<hbfsim::HbfRequest> submitted;
        std::size_t completion_index = 0;
        RequestDispatcher dispatcher(
            fixture.control,
            RequestDispatcher::Engine{
                .prepare = [&](const hbfsim::HbfRequest& value) {
                    auto read = value;
                    read.operation = static_cast<std::uint32_t>(
                        hbfsim::RequestOperation::Read);
                    if (value.logical_address == dirty.logical_address) {
                        auto program = value;
                        program.logical_address = 0x7000;
                        program.range_id = 7;
                        program.operation = static_cast<std::uint32_t>(
                            hbfsim::RequestOperation::Write);
                        return PreparedDispatch{
                            .completion = prepared_completion(value, 0x3000),
                            .media_actions = {program, read},
                            .media_action_count = 2,
                        };
                    }
                    return PreparedDispatch{
                        .completion = prepared_completion(value, 0x9000),
                        .media_actions = {read},
                        .media_action_count = 1,
                    };
                },
                .submit = [&](const hbfsim::HbfRequest& action) {
                    submitted.push_back(action);
                },
                .run_next_completion = [&]()
                    -> std::optional<hbfsim::HbfCompletion> {
                    ++completion_index;
                    if (completion_index == 1) {
                        const auto found = std::ranges::find_if(
                            submitted, [&](const auto& action) {
                                return action.logical_address ==
                                       unrelated.logical_address;
                            });
                        CHECK(found != submitted.end());
                        return engine_completion(*found, 5);
                    }
                    if (completion_index == 2) {
                        const auto found = std::ranges::find_if(
                            submitted, [](const auto& action) {
                                return action.logical_address == 0x7000;
                            });
                        CHECK(found != submitted.end());
                        return engine_completion(*found, 7);
                    }
                    CHECK(submitted.size() == 3);
                    return engine_completion(submitted.back(), 11);
                },
            });
        CHECK(dispatcher.poll_once());
        CHECK(submitted.size() == 3);
        CHECK(submitted[0].operation == static_cast<std::uint32_t>(
                                            hbfsim::RequestOperation::Write));
        CHECK(submitted[1].logical_address == unrelated.logical_address);
        CHECK(submitted[2].operation == static_cast<std::uint32_t>(
                                            hbfsim::RequestOperation::Read));
        CHECK(submitted[2].arrival_ns == 7);
        CHECK(submitted[0].request_id != submitted[1].request_id);
        CHECK(submitted[1].request_id != submitted[2].request_id);
        CHECK(submitted[0].request_id != submitted[2].request_id);

        hbfsim::HbfCompletion unrelated_completion{};
        CHECK(fixture.control.try_consume_completion(
            unrelated_ticket, unrelated_completion));
        CHECK(unrelated_completion.modeled_ns == 5);
        hbfsim::HbfCompletion dirty_completion{};
        CHECK(fixture.control.try_consume_completion(
            dirty_ticket, dirty_completion));
        CHECK(dirty_completion.modeled_ns == 18);
        CHECK(dirty_completion.request_id == dirty.request_id);
    }

    {
        ControlFixture fixture;
        auto first = request(401, 0x4000);
        auto second = request(402, 0x5000);
        std::uint64_t first_ticket = 0;
        std::uint64_t second_ticket = 0;
        CHECK(fixture.control.try_push_request(first, first_ticket));
        CHECK(fixture.control.try_push_request(second, second_ticket));
        std::size_t submissions = 0;
        RequestDispatcher dispatcher(
            fixture.control,
            RequestDispatcher::Engine{
                .prepare = [](const hbfsim::HbfRequest& value) {
                    return PreparedDispatch{
                        .completion = prepared_completion(value, 0x4000),
                        .media_actions = {value},
                        .media_action_count = 1,
                    };
                },
                .submit = [&](const hbfsim::HbfRequest&) { ++submissions; },
                .run_next_completion = []()
                    -> std::optional<hbfsim::HbfCompletion> {
                    return std::nullopt;
                },
            });
        hbfsim::host_service::RequestDispatcherTestAccess::set_next_engine_id(
            dispatcher, std::numeric_limits<std::uint64_t>::max());
        CHECK(dispatcher.poll_once());
        CHECK(submissions == 1);
        CHECK(hbfsim::host_service::atomic_load(
                  fixture.control.header()->fault,
                  std::memory_order_acquire) == HBFSIM_IO_ERROR);
        hbfsim::HbfCompletion completion{};
        CHECK(fixture.control.try_consume_completion(first_ticket,
                                                     completion));
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::IoError));
        CHECK(fixture.control.try_consume_completion(second_ticket,
                                                     completion));
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::IoError));
    }

    const auto check_malformed_action = [](int kind) {
        ControlFixture fixture;
        auto malformed = request(551 + kind, 0x6800);
        auto admitted = request(561 + kind, 0x7800);
        std::uint64_t malformed_ticket = 0;
        std::uint64_t admitted_ticket = 0;
        CHECK(fixture.control.try_push_request(malformed,
                                               malformed_ticket));
        CHECK(fixture.control.try_push_request(admitted,
                                               admitted_ticket));
        std::size_t submissions = 0;
        RequestDispatcher dispatcher(
            fixture.control,
            RequestDispatcher::Engine{
                .prepare = [=](const hbfsim::HbfRequest& value) {
                    auto action = value;
                    if (kind == 0) {
                        action.bytes = 0;
                    } else if (kind == 1) {
                        action.operation = 99;
                    } else {
                        ++action.page_generation;
                    }
                    return PreparedDispatch{
                        .completion = prepared_completion(value, 0x6800),
                        .media_actions = {action},
                        .media_action_count = 1,
                    };
                },
                .submit = [&](const hbfsim::HbfRequest&) { ++submissions; },
                .run_next_completion = []()
                    -> std::optional<hbfsim::HbfCompletion> {
                    return std::nullopt;
                },
            });
        CHECK(dispatcher.poll_once());
        CHECK(submissions == 0);
        CHECK(hbfsim::host_service::atomic_load(
                  fixture.control.header()->fault,
                  std::memory_order_acquire) == HBFSIM_IO_ERROR);
        hbfsim::HbfCompletion completion{};
        CHECK(fixture.control.try_consume_completion(malformed_ticket,
                                                     completion));
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::IoError));
        CHECK(fixture.control.try_consume_completion(admitted_ticket,
                                                     completion));
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::IoError));
    };
    check_malformed_action(0);
    check_malformed_action(1);
    check_malformed_action(2);

    {
        ControlFixture fixture;
        auto malformed = request(501, 0x6000);
        std::uint64_t ticket = 0;
        CHECK(fixture.control.try_push_request(malformed, ticket));
        RequestDispatcher dispatcher(
            fixture.control,
            RequestDispatcher::Engine{
                .prepare = [](const hbfsim::HbfRequest& value) {
                    auto prepared = PreparedDispatch{
                        .completion = prepared_completion(value, 0x6000)};
                    prepared.media_action_count = 3;
                    return prepared;
                },
                .submit = [](const hbfsim::HbfRequest&) {},
                .run_next_completion = []()
                    -> std::optional<hbfsim::HbfCompletion> {
                    return std::nullopt;
                },
            });
        CHECK(dispatcher.poll_once());
        hbfsim::HbfCompletion completion{};
        CHECK(fixture.control.try_consume_completion(ticket, completion));
        CHECK(completion.request_id == malformed.request_id);
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::IoError));
    }

    {
        ControlFixture fixture;
        auto timed_out = request(601, 0x7000);
        auto admitted = request(602, 0x8000);
        std::uint64_t timeout_ticket = 0;
        std::uint64_t admitted_ticket = 0;
        CHECK(fixture.control.try_push_request(timed_out, timeout_ticket));
        CHECK(fixture.control.try_push_request(admitted, admitted_ticket));
        RequestDispatcher dispatcher(
            fixture.control,
            RequestDispatcher::Engine{
                .prepare = [](const hbfsim::HbfRequest& value) {
                    if (value.request_id == 601) {
                        return PreparedDispatch{
                            .completion = {
                                .request_id = value.request_id,
                                .page_generation = value.page_generation,
                                .status = static_cast<std::uint32_t>(
                                    hbfsim::RequestStatus::Timeout),
                            },
                            .fail_all = true,
                        };
                    }
                    return PreparedDispatch{
                        .completion = prepared_completion(value, 0x8000)};
                },
                .submit = [](const hbfsim::HbfRequest&) {},
                .run_next_completion = []()
                    -> std::optional<hbfsim::HbfCompletion> {
                    return std::nullopt;
                },
            });
        CHECK(dispatcher.poll_once());
        CHECK(hbfsim::host_service::atomic_load(
                  fixture.control.header()->fault,
                  std::memory_order_acquire) == HBFSIM_IO_ERROR);
        hbfsim::HbfCompletion completion{};
        CHECK(fixture.control.try_consume_completion(timeout_ticket,
                                                     completion));
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::IoError));
        CHECK(fixture.control.try_consume_completion(admitted_ticket,
                                                     completion));
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::IoError));
    }

    const auto check_engine_failure = [](bool wrong_generation) {
        ControlFixture fixture;
        auto first = request(wrong_generation ? 701 : 711, 0x9000);
        auto second = request(wrong_generation ? 702 : 712, 0xa000);
        std::uint64_t first_ticket = 0;
        std::uint64_t second_ticket = 0;
        CHECK(fixture.control.try_push_request(first, first_ticket));
        CHECK(fixture.control.try_push_request(second, second_ticket));
        std::vector<hbfsim::HbfRequest> submitted;
        RequestDispatcher dispatcher(
            fixture.control,
            RequestDispatcher::Engine{
                .prepare = [](const hbfsim::HbfRequest& value) {
                    return PreparedDispatch{
                        .completion = prepared_completion(value, 0xb000),
                        .media_actions = {value},
                        .media_action_count = 1,
                    };
                },
                .submit = [&](const hbfsim::HbfRequest& value) {
                    submitted.push_back(value);
                },
                .run_next_completion = [&]()
                    -> std::optional<hbfsim::HbfCompletion> {
                    auto completion = engine_completion(submitted.front(), 3);
                    if (wrong_generation) {
                        ++completion.page_generation;
                    } else {
                        completion.status = static_cast<std::uint32_t>(
                            hbfsim::RequestStatus::IoError);
                    }
                    return completion;
                },
            });
        CHECK(dispatcher.poll_once());
        CHECK(hbfsim::host_service::atomic_load(
                  fixture.control.header()->fault,
                  std::memory_order_acquire) == HBFSIM_IO_ERROR);
        hbfsim::HbfCompletion completion{};
        CHECK(fixture.control.try_consume_completion(first_ticket,
                                                     completion));
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::IoError));
        CHECK(fixture.control.try_consume_completion(second_ticket,
                                                     completion));
        CHECK(completion.status == static_cast<std::uint32_t>(
                                       hbfsim::RequestStatus::IoError));
    };
    check_engine_failure(false);
    check_engine_failure(true);

    return 0;
}
