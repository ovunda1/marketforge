#pragma once

#include <pybind11/pybind11.h>

#include <atomic>
#include <cstddef>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "mf/commands.hpp"
#include "mf/engine.hpp"
#include "mf/events.hpp"

namespace mfpy {

namespace py = pybind11;

void bind_errors(py::module_& m);
void bind_values(py::module_& m);
void bind_engine(py::module_& m);
void bind_io(py::module_& m);

// Commands with their timestamps, held in C++ so process_batch converts nothing on the
// way in. Not modifiable while an engine is processing it.
struct CommandBatch {
  std::vector<std::pair<mf::Command, mf::Timestamp>> items;
  std::atomic<int> in_use{0};
};

// The events of a batch, owned here: the engine appends straight into `events`, and
// Python objects are made only when an item is read. offsets[k] is where command k's
// events start; offsets.back() == events.size().
struct EventBatch {
  std::vector<mf::Event> events;
  std::vector<std::size_t> offsets{0};
};

// The binding's Engine. Thin: every call forwards to mf::Engine. It adds only what a
// Python caller needs to stay safe:
//  - a failed state: an exception from process() other than SequenceError may come
//    after the engine began changing state, so the engine is not used again and every
//    later call raises EngineFailed;
//  - a busy flag: process_batch releases the GIL, and the engine has a single writer,
//    so another thread's call while a batch runs raises RuntimeError instead of racing.
class Engine {
 public:
  Engine() : engine_(std::make_unique<mf::Engine>()) {}
  explicit Engine(std::unique_ptr<mf::Engine> engine) : engine_(std::move(engine)) {}

  // The engine, if it can be used now; raises EngineFailed or RuntimeError otherwise.
  const mf::Engine& get() const;

  py::list process(const mf::Command& command, mf::Timestamp timestamp);
  EventBatch process_batch(CommandBatch& batch);

  bool failed() const { return failure_.has_value(); }
  // Test hook: the next command is applied, then InvariantViolation is raised, as the
  // post-command invariant walk would.
  void inject_fault_after_next_command() { inject_fault_ = true; }

 private:
  // Applies one command; a fault-hook failure is thrown after it, like a real one.
  void apply(const mf::Command& command, mf::Timestamp timestamp, std::vector<mf::Event>& out);

  std::unique_ptr<mf::Engine> engine_;
  std::vector<mf::Event> buffer_;  // reused by process(); its events are copied out
  std::optional<std::string> failure_;
  std::atomic<bool> busy_{false};
  bool inject_fault_{false};
};

}  // namespace mfpy
