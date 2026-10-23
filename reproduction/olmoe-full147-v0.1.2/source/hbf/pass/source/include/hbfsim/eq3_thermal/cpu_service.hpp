#pragma once
#include <memory>
#include <string>
#include <cstdint>

namespace hbfsim::eq3_thermal {
// Explicit ENGINEERING_FIXTURE service backend consuming existing ThermalModel
// and ActivityObserver. It is not a replacement MQSim NAND implementation and
// makes no product timing/energy/reliability claim. No host sleeps or busy waits.
class CpuService {
 public:
  explicit CpuService(const std::string& fixture_json);
  ~CpuService();
  CpuService(CpuService&&) noexcept;
  CpuService& operator=(CpuService&&) noexcept;
  CpuService(const CpuService&)=delete;
  CpuService& operator=(const CpuService&)=delete;
  bool submit(const std::string& request_json);
  void advance_to(std::uint64_t target_ns);
  std::uint64_t time_ns() const noexcept;
  std::string report() const;
  std::string checkpoint() const;
  void restore(const std::string& checkpoint_json);
 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
} // namespace hbfsim::eq3_thermal
