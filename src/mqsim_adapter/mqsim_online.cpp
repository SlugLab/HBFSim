#include <hbfsim/mqsim_online.hpp>

#include <Engine.h>
#include <Flash_Parameter_Set.h>
#include <Host_Interface_HBF.h>
#include <IO_Flow_Parameter_Set.h>
#include <Media_Activity_Observer.h>
#include <ONFI_Channel_NVDDR2.h>
#include <SSD_Device.h>

#include <algorithm>
#include <cstdint>
#include <deque>
#include <functional>
#include <fstream>
#include <limits>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace hbfsim
{
    namespace
    {

        constexpr std::uint64_t kSectorBytes = 512;

        Flash_Technology_Type to_mqsim_technology(NandTechnology technology)
        {
            switch (technology)
            {
            case NandTechnology::Slc:
                return Flash_Technology_Type::SLC;
            case NandTechnology::Mlc:
                return Flash_Technology_Type::MLC;
            case NandTechnology::Tlc:
                return Flash_Technology_Type::TLC;
            case NandTechnology::Qlc:
                return Flash_Technology_Type::QLC;
            }
            throw std::invalid_argument("unknown NandTechnology");
        }

        SSD_Components::Flash_Plane_Allocation_Scheme_Type to_mqsim_scheme(
            PlaneAllocationScheme scheme)
        {
            using Scheme = SSD_Components::Flash_Plane_Allocation_Scheme_Type;
            switch (scheme)
            {
            case PlaneAllocationScheme::Cwdp:
                return Scheme::CWDP;
            case PlaneAllocationScheme::Cwpd:
                return Scheme::CWPD;
            case PlaneAllocationScheme::Cdwp:
                return Scheme::CDWP;
            case PlaneAllocationScheme::Cdpw:
                return Scheme::CDPW;
            case PlaneAllocationScheme::Cpwd:
                return Scheme::CPWD;
            case PlaneAllocationScheme::Cpdw:
                return Scheme::CPDW;
            case PlaneAllocationScheme::Wcdp:
                return Scheme::WCDP;
            case PlaneAllocationScheme::Wcpd:
                return Scheme::WCPD;
            case PlaneAllocationScheme::Wdcp:
                return Scheme::WDCP;
            case PlaneAllocationScheme::Wdpc:
                return Scheme::WDPC;
            case PlaneAllocationScheme::Wpcd:
                return Scheme::WPCD;
            case PlaneAllocationScheme::Wpdc:
                return Scheme::WPDC;
            case PlaneAllocationScheme::Dcwp:
                return Scheme::DCWP;
            case PlaneAllocationScheme::Dcpw:
                return Scheme::DCPW;
            case PlaneAllocationScheme::Dwcp:
                return Scheme::DWCP;
            case PlaneAllocationScheme::Dwpc:
                return Scheme::DWPC;
            case PlaneAllocationScheme::Dpcw:
                return Scheme::DPCW;
            case PlaneAllocationScheme::Dpwc:
                return Scheme::DPWC;
            case PlaneAllocationScheme::Pcwd:
                return Scheme::PCWD;
            case PlaneAllocationScheme::Pcdw:
                return Scheme::PCDW;
            case PlaneAllocationScheme::Pwcd:
                return Scheme::PWCD;
            case PlaneAllocationScheme::Pwdc:
                return Scheme::PWDC;
            case PlaneAllocationScheme::Pdcw:
                return Scheme::PDCW;
            case PlaneAllocationScheme::Pdwc:
                return Scheme::PDWC;
            }
            throw std::invalid_argument("unknown PlaneAllocationScheme");
        }

        MediaActivityKind from_mqsim_kind(
            NVM::FlashMemory::Media_Activity_Kind kind) noexcept
        {
            using Kind = NVM::FlashMemory::Media_Activity_Kind;
            switch (kind)
            {
            case Kind::READ:
                return MediaActivityKind::Read;
            case Kind::PROGRAM:
                return MediaActivityKind::Program;
            case Kind::ERASE:
                return MediaActivityKind::Erase;
            }
            return MediaActivityKind::Read;
        }

        MediaTransactionSource from_mqsim_source(
            NVM::FlashMemory::Media_Transaction_Source source) noexcept
        {
            using Source = NVM::FlashMemory::Media_Transaction_Source;
            switch (source)
            {
            case Source::USER_IO:
                return MediaTransactionSource::UserIo;
            case Source::CACHE:
                return MediaTransactionSource::Cache;
            case Source::GC_WL:
                return MediaTransactionSource::GcWl;
            case Source::MAPPING:
                return MediaTransactionSource::Mapping;
            }
            return MediaTransactionSource::UserIo;
        }

        std::uint64_t checked_end(std::uint64_t start, std::uint64_t bytes)
        {
            if (bytes > std::numeric_limits<std::uint64_t>::max() - start)
            {
                throw std::invalid_argument("HBF request address range overflows");
            }
            return start + bytes;
        }

        void configure_mqsim(const Profile &profile)
        {
            Device_Parameter_Set::Seed = 123;
            Device_Parameter_Set::Enabled_Preconditioning = false;
            Device_Parameter_Set::Memory_Type = NVM::NVM_Type::FLASH;
            Device_Parameter_Set::HostInterface_Type = HostInterface_Types::HBF;
            Device_Parameter_Set::IO_Queue_Depth = static_cast<std::uint16_t>(
                std::min<std::uint32_t>(profile.queue_depth,
                                        std::numeric_limits<std::uint16_t>::max()));
            Device_Parameter_Set::Caching_Mechanism =
                SSD_Components::Caching_Mechanism::SIMPLE;
            Device_Parameter_Set::Address_Mapping =
                SSD_Components::Flash_Address_Mapping_Type::PAGE_LEVEL;
            Device_Parameter_Set::Plane_Allocation_Scheme =
                to_mqsim_scheme(profile.plane_allocation_scheme);
            Device_Parameter_Set::Ideal_Mapping_Table = true;
            Device_Parameter_Set::Overprovisioning_Ratio = 0.0;
            Device_Parameter_Set::Flash_Channel_Count = profile.channels;
            Device_Parameter_Set::Flash_Channel_Width = profile.channel_width_bits / 8;
            Device_Parameter_Set::Channel_Transfer_Rate =
                profile.channel_transfer_rate_mtps;
            Device_Parameter_Set::Chip_No_Per_Channel = 1;
            Device_Parameter_Set::Flash_Comm_Protocol =
                SSD_Components::ONFI_Protocol::NVDDR2;

            Flash_Parameter_Set::Flash_Technology = to_mqsim_technology(profile.nand_technology);
            Flash_Parameter_Set::CMD_Suspension_Support =
                NVM::FlashMemory::Command_Suspension_Mode::NONE;
            Flash_Parameter_Set::Page_Read_Latency_LSB = profile.page_read_latency_lsb_ns;
            Flash_Parameter_Set::Page_Read_Latency_CSB = profile.page_read_latency_csb_ns;
            Flash_Parameter_Set::Page_Read_Latency_MSB = profile.page_read_latency_msb_ns;
            Flash_Parameter_Set::Page_Read_Latency_TSB = profile.page_read_latency_tsb_ns;
            Flash_Parameter_Set::Page_Program_Latency_LSB = profile.page_program_latency_lsb_ns;
            Flash_Parameter_Set::Page_Program_Latency_CSB = profile.page_program_latency_csb_ns;
            Flash_Parameter_Set::Page_Program_Latency_MSB = profile.page_program_latency_msb_ns;
            Flash_Parameter_Set::Page_Program_Latency_TSB = profile.page_program_latency_tsb_ns;
            Flash_Parameter_Set::Block_Erase_Latency = profile.program_latency_ns * 10;
            Flash_Parameter_Set::Suspend_Erase_Time = 0;
            Flash_Parameter_Set::Suspend_Program_Time = 0;
            Flash_Parameter_Set::Die_No_Per_Chip = profile.dies_per_channel;
            Flash_Parameter_Set::Plane_No_Per_Die = profile.planes_per_die;
            Flash_Parameter_Set::Block_No_Per_Plane =
                static_cast<unsigned int>(blocks_per_plane(profile));
            Flash_Parameter_Set::Page_No_Per_Block = profile.pages_per_block;
            Flash_Parameter_Set::Page_Capacity = profile.page_bytes;
            Flash_Parameter_Set::Page_Metadat_Capacity = 0;
        }

    } // namespace

    class ArrivalInjector final : public MQSimEngine::Sim_Object
    {
    public:
        using Handler = std::function<void(MQSimEngine::Sim_Event *)>;

        explicit ArrivalInjector(Handler handler)
            : Sim_Object("HBFSim.ArrivalInjector"), handler_(std::move(handler))
        {
        }

        void Start_simulation() override {}
        void Validate_simulation_config() override {}
        void Execute_simulator_event(MQSimEngine::Sim_Event *event) override;

    private:
        Handler handler_;
    };

    class MqsimOnlineEngine::Impl
    {
    public:
        struct Submission
        {
            HbfRequest descriptor;
            SSD_Components::User_Request *mqsim_request;
        };

        explicit Impl(const Profile &requested_profile,
                      MediaActivitySink requested_media_activity_sink = {})
            : profile(requested_profile),
              media_activity_sink(std::move(requested_media_activity_sink))
        {
            validate_profile(profile);
            if (profile.channel_width_bits % 8 != 0)
            {
                throw std::invalid_argument(
                    "channel_width_bits must be divisible by eight for MQSim");
            }
            if (blocks_per_plane(profile) < 4)
            {
                throw std::invalid_argument(
                    "MQSim requires at least four blocks per plane");
            }

            Simulator->Reset();
            configure_mqsim(profile);

            flow.Device_Level_Data_Caching_Mode =
                SSD_Components::Caching_Mode::TURNED_OFF;
            flow.Priority_Class = IO_Flow_Priority_Class::URGENT;
            flow.Initial_Occupancy_Percentage = 0;
            flows.push_back(&flow);
            device = std::make_unique<SSD_Device>(&parameters, &flows);
            host = dynamic_cast<SSD_Components::Host_Interface_HBF *>(
                device->Host_interface);
            if (host == nullptr)
            {
                throw std::runtime_error("MQSim did not construct the HBF interface");
            }

            if (media_activity_sink)
            {
                for (auto *channel_base : device->Channels)
                {
                    auto *channel =
                        static_cast<SSD_Components::ONFI_Channel_NVDDR2 *>(
                            channel_base);
                    for (unsigned int chip_index = 0;
                         chip_index < device->Chip_no_per_channel; ++chip_index)
                    {
                        channel->Chips[chip_index]
                            ->Connect_to_media_activity_observer(
                                observe_media_activity, this);
                    }
                }
            }

            injector = std::make_unique<ArrivalInjector>(
                [this](MQSimEngine::Sim_Event *event)
                {
                    submit_to_device(static_cast<Submission *>(event->Parameters));
                });
            Simulator->AddObject(injector.get());
        }

        ~Impl()
        {
            flush_staged();
            while (pending_requests != 0 && Simulator->Run_next_event())
            {
            }
            Simulator->Reset();
            injector.reset();
            device.reset();
        }

        void flush_staged()
        {
            std::sort(staged.begin(), staged.end(),
                      [](const Submission *left, const Submission *right)
                      {
                          if (left->descriptor.arrival_ns !=
                              right->descriptor.arrival_ns)
                          {
                              return left->descriptor.arrival_ns <
                                     right->descriptor.arrival_ns;
                          }
                          return left->descriptor.sequence <
                                 right->descriptor.sequence;
                      });
            for (auto *submission : staged)
            {
                Simulator->Register_sim_event(submission->descriptor.arrival_ns,
                                              injector.get(), submission);
            }
            staged.clear();
        }

        void submit_to_device(Submission *submission)
        {
            host->Submit_hbf_request(
                submission->mqsim_request,
                [this, descriptor = submission->descriptor](
                    SSD_Components::User_Request *, sim_time_type completion_ns)
                {
                    auto bounded_completion = static_cast<std::uint64_t>(completion_ns);
                    const auto transfer_ns = static_cast<std::uint64_t>(
                        (static_cast<unsigned __int128>(descriptor.bytes) *
                             1000000000ULL +
                         profile.aggregate_bandwidth_bytes_per_s - 1) /
                        profile.aggregate_bandwidth_bytes_per_s);
                    bandwidth_cursor_ns =
                        std::max(bandwidth_cursor_ns, descriptor.arrival_ns) +
                        transfer_ns;
                    bounded_completion =
                        std::max(bounded_completion, bandwidth_cursor_ns);
                    completions.push_back(HbfCompletion{
                        .request_id = descriptor.request_id,
                        .modeled_completion_ns = bounded_completion,
                        .modeled_ns = bounded_completion - descriptor.arrival_ns,
                        .service_ns = 0,
                        .cache_frame_address = 0,
                        .page_generation = descriptor.page_generation,
                        .status =
                            static_cast<std::uint32_t>(RequestStatus::Ready),
                        .checksum = 0,
                        .reserved = 0,
                    });
                });
            delete submission;
        }

        static void observe_media_activity(
            const NVM::FlashMemory::Media_Activity &activity,
            void *opaque) noexcept
        {
            auto *self = static_cast<Impl *>(opaque);
            try
            {
                self->media_activity_sink(MediaActivity{
                    .kind = from_mqsim_kind(activity.Kind),
                    .start_time_ns = activity.Start_time_ns,
                    .end_time_ns = activity.End_time_ns,
                    .channel = activity.Channel,
                    .chip = activity.Chip,
                    .die = activity.Die,
                    .plane = activity.Plane,
                    .block = activity.Block,
                    .page = activity.Page,
                    .bytes = activity.Bytes,
                    .source = from_mqsim_source(activity.Source),
                });
            }
            catch (const std::exception &error)
            {
                self->media_activity_observer_failed = true;
                try
                {
                    self->media_activity_observer_error = error.what();
                }
                catch (...)
                {
                }
            }
            catch (...)
            {
                self->media_activity_observer_failed = true;
            }
        }

        Profile profile;
        Device_Parameter_Set parameters;
        IO_Flow_Parameter_Set flow;
        std::vector<IO_Flow_Parameter_Set *> flows;
        std::unique_ptr<SSD_Device> device;
        SSD_Components::Host_Interface_HBF *host{nullptr};
        std::unique_ptr<ArrivalInjector> injector;
        std::vector<Submission *> staged;
        std::deque<HbfCompletion> completions;
        std::size_t pending_requests{0};
        std::uint64_t bandwidth_cursor_ns{0};
        MediaActivitySink media_activity_sink;
        bool media_activity_observer_failed{false};
        std::string media_activity_observer_error;
    };

    void ArrivalInjector::Execute_simulator_event(MQSimEngine::Sim_Event *event)
    {
        handler_(event);
    }

    MqsimOnlineEngine::MqsimOnlineEngine(const Profile &profile)
        : impl_(std::make_unique<Impl>(profile))
    {
    }

    MqsimOnlineEngine::MqsimOnlineEngine(
        const Profile &profile, MediaActivitySink media_activity_sink)
        : impl_(std::make_unique<Impl>(profile, std::move(media_activity_sink)))
    {
    }

    MqsimOnlineEngine::~MqsimOnlineEngine() = default;
    MqsimOnlineEngine::MqsimOnlineEngine(MqsimOnlineEngine &&) noexcept = default;
    MqsimOnlineEngine &MqsimOnlineEngine::operator=(MqsimOnlineEngine &&) noexcept =
        default;

    void MqsimOnlineEngine::submit(const HbfRequest &request)
    {
        if (request.bytes == 0 || request.bytes % kSectorBytes != 0 ||
            request.logical_address % kSectorBytes != 0)
        {
            throw std::invalid_argument(
                "MQSim requests must be non-empty and 512-byte aligned");
        }
        if (checked_end(request.logical_address, request.bytes) >
            impl_->profile.capacity_bytes)
        {
            throw std::out_of_range("MQSim request exceeds profile capacity");
        }
        if (Simulator->Has_started() && request.arrival_ns < Simulator->Time())
        {
            throw std::invalid_argument(
                "MQSim request arrival precedes current simulation time");
        }

        auto mqsim_request = std::make_unique<SSD_Components::User_Request>();
        mqsim_request->Start_LBA = request.logical_address / kSectorBytes;
        mqsim_request->Size_in_byte = request.bytes;
        mqsim_request->SizeInSectors = request.bytes / kSectorBytes;
        mqsim_request->Type = request.operation ==
                                      static_cast<std::uint32_t>(
                                          RequestOperation::Write)
                                  ? SSD_Components::UserRequestType::WRITE
                                  : SSD_Components::UserRequestType::READ;
        mqsim_request->Stream_id = 0;
        mqsim_request->Priority_class = IO_Flow_Priority_Class::URGENT;
        mqsim_request->IO_command_info = nullptr;
        mqsim_request->Data = nullptr;

        auto submission = new Impl::Submission{
            .descriptor = request,
            .mqsim_request = mqsim_request.release(),
        };
        impl_->staged.push_back(submission);
        ++impl_->pending_requests;
    }

    std::optional<HbfCompletion> MqsimOnlineEngine::run_next_completion()
    {
        impl_->flush_staged();
        while (impl_->completions.empty())
        {
            const bool ran_event = Simulator->Run_next_event();
            if (impl_->media_activity_observer_failed)
            {
                throw std::runtime_error(
                    impl_->media_activity_observer_error.empty()
                        ? "MQSim media activity observer failed"
                        : "MQSim media activity observer failed: " +
                              impl_->media_activity_observer_error);
            }
            if (!ran_event)
            {
                break;
            }
        }
        if (impl_->completions.empty())
        {
            return std::nullopt;
        }

        auto completion = impl_->completions.front();
        impl_->completions.pop_front();
        --impl_->pending_requests;
        return completion;
    }

    std::size_t MqsimOnlineEngine::pending() const noexcept
    {
        return impl_->pending_requests;
    }

    std::uint64_t MqsimOnlineEngine::current_time_ns() const noexcept
    {
        return Simulator->Has_started()
                   ? static_cast<std::uint64_t>(Simulator->Time())
                   : 0;
    }

    std::vector<HbfCompletion> run_mqsim_trace(
        const Profile &profile, const std::filesystem::path &trace_path)
    {
        std::ifstream trace(trace_path);
        if (!trace)
        {
            throw std::runtime_error("failed to open MQSim trace: " +
                                     trace_path.string());
        }

        MqsimOnlineEngine engine(profile);
        std::string line;
        std::uint64_t request_id = 1;
        std::uint64_t previous_arrival = 0;
        std::size_t line_number = 0;
        while (std::getline(trace, line))
        {
            ++line_number;
            if (line.empty())
            {
                continue;
            }

            std::uint64_t arrival_ns = 0;
            std::uint64_t device = 0;
            std::uint64_t start_sector = 0;
            std::uint64_t sector_count = 0;
            std::uint32_t operation = 0;
            std::string trailing;
            std::istringstream fields(line);
            if (!(fields >> arrival_ns >> device >> start_sector >> sector_count >>
                  operation) ||
                (fields >> trailing))
            {
                throw std::invalid_argument("invalid MQSim trace line " +
                                            std::to_string(line_number));
            }
            if (device != 0)
            {
                throw std::invalid_argument(
                    "MQSim HBF trace device must be zero at line " +
                    std::to_string(line_number));
            }
            if (request_id > 1 && arrival_ns < previous_arrival)
            {
                throw std::invalid_argument(
                    "MQSim trace arrivals must be monotonic");
            }
            if (start_sector >
                    std::numeric_limits<std::uint64_t>::max() / kSectorBytes ||
                sector_count >
                    std::numeric_limits<std::uint32_t>::max() / kSectorBytes)
            {
                throw std::overflow_error(
                    "MQSim trace address or size overflows");
            }
            if (operation > 1)
            {
                throw std::invalid_argument(
                    "MQSim trace operation must be 0 or 1 at line " +
                    std::to_string(line_number));
            }

            engine.submit(HbfRequest{
                .request_id = request_id,
                .sequence = request_id,
                .arrival_ns = arrival_ns,
                .logical_address = start_sector * kSectorBytes,
                .deadline_ns = 0,
                .bytes = static_cast<std::uint32_t>(sector_count * kSectorBytes),
                .range_id = 1,
                .stream_id = 0,
                .operation = operation == 0
                                 ? static_cast<std::uint32_t>(
                                       RequestOperation::Write)
                                 : static_cast<std::uint32_t>(
                                       RequestOperation::Read),
                .page_generation = 1,
                .flags = 0,
            });
            previous_arrival = arrival_ns;
            ++request_id;
        }

        std::vector<HbfCompletion> completions;
        completions.reserve(request_id - 1);
        while (engine.pending() != 0)
        {
            auto completion = engine.run_next_completion();
            if (!completion.has_value())
            {
                throw std::runtime_error(
                    "MQSim trace ended with pending requests");
            }
            completions.push_back(*completion);
        }
        return completions;
    }

} // namespace hbfsim
