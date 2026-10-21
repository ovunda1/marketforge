#include "platform.hpp"

#if defined(__APPLE__)
#include <pthread.h>
#include <sys/qos.h>
#endif

namespace mf::bench {

std::string prefer_performance_cores() {
#if defined(__APPLE__)
  if (pthread_set_qos_class_self_np(QOS_CLASS_USER_INTERACTIVE, 0) == 0) {
    return "macOS QoS USER_INTERACTIVE (prefers performance cores; not a pin)";
  }
  return "macOS QoS request failed";
#else
  return "default scheduling (no affinity set)";
#endif
}

}  // namespace mf::bench
