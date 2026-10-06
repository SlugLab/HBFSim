#include "control_layout.hpp"
#include <cstddef>
static_assert(sizeof(hbfsim::host_service::SharedControlHeader) == 384);
static_assert(offsetof(hbfsim::host_service::SharedControlHeader, reserved_device_state) == 368);
