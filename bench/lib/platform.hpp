#pragma once

#include <string>

namespace mf::bench {

// Asks the scheduler to run this thread on a performance core: on macOS the
// USER_INTERACTIVE QoS class (Apple Silicon has no API to pin a thread to a core).
// Returns a description of what was done, for the results file.
std::string prefer_performance_cores();

}  // namespace mf::bench
