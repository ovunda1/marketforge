// The binding's Engine, CommandBatch and EventBatch.

#include <pybind11/stl.h>

#include <algorithm>
#include <exception>
#include <stdexcept>

#include "bindings.hpp"
#include "convert.hpp"
#include "errors.hpp"
#include "mf/snapshot.hpp"

namespace mfpy {

const mf::Engine& Engine::get() const {
  if (busy_) throw std::runtime_error("Engine is in use by another thread (a batch is running)");
  if (failure_) {
    raise(errors().failed.ptr(), "the engine failed earlier and cannot be used: " + *failure_);
  }
  return *engine_;
}

void Engine::apply(const mf::Command& command, mf::Timestamp timestamp, std::vector<mf::Event>& out) {
  engine_->process(command, timestamp, out);
  if (inject_fault_) {
    inject_fault_ = false;
    throw mf::InvariantViolation("injected fault after the command was applied (test hook)");
  }
}

py::list Engine::process(const mf::Command& command, mf::Timestamp timestamp) {
  get();
  buffer_.clear();
  try {
    apply(command, timestamp, buffer_);
  } catch (const mf::SequenceError&) {
    throw;  // nothing changed
  } catch (const std::exception& e) {
    failure_ = e.what();
    throw;
  } catch (...) {
    failure_ = "unknown error";
    throw;
  }
  return py::reinterpret_steal<py::list>(py::cast(buffer_).release());  // copies each event
}

EventBatch Engine::process_batch(CommandBatch& batch) {
  get();
  EventBatch result;
  const auto& items = batch.items;
  result.offsets.reserve(items.size() + 1);
  std::exception_ptr error;
  std::size_t at = 0;
  ++batch.in_use;
  busy_ = true;
  {
    py::gil_scoped_release release;  // pure C++ from here: no Python object is touched
    for (; at < items.size(); ++at) {
      try {
        apply(items[at].first, items[at].second, result.events);
      } catch (...) {
        error = std::current_exception();
        break;
      }
      result.offsets.push_back(result.events.size());
    }
  }
  busy_ = false;
  --batch.in_use;
  if (!error) return result;

  // Keep the events of the commands before the failing one; drop the failing one's.
  result.events.erase(result.events.begin() + static_cast<std::ptrdiff_t>(result.offsets.back()),
                      result.events.end());
  py::handle type;
  std::string message;
  try {
    std::rethrow_exception(error);
  } catch (const mf::SequenceError& e) {
    type = errors().sequence;
    message = e.what();
  } catch (const mf::InvariantViolation& e) {
    failure_ = e.what();
    type = errors().invariant;
    message = e.what();
  } catch (const std::exception& e) {
    failure_ = e.what();
    throw;
  } catch (...) {
    failure_ = "unknown error";
    throw;
  }
  py::dict attrs;
  attrs["command_index"] = at;
  attrs["events"] = py::cast(std::move(result));
  raise_with(type, message, attrs);
}

namespace {

void check_not_in_use(const CommandBatch& batch) {
  if (batch.in_use != 0) throw std::runtime_error("CommandBatch is being processed by an engine");
}

std::size_t batch_index(const EventBatch& b, py::handle index) {
  auto i = to_int<std::int64_t>(index, "index");
  const auto n = static_cast<std::int64_t>(b.events.size());
  if (i < 0) i += n;
  if (i < 0 || i >= n) throw py::index_error("EventBatch index out of range");
  return static_cast<std::size_t>(i);
}

}  // namespace

void bind_engine(py::module_& m) {
  py::class_<CommandBatch>(m, "CommandBatch",
                           "Commands with their timestamps, held in C++ for process_batch.")
      .def(py::init<>())
      .def(
          "append",
          [](CommandBatch& b, const mf::Command& command, py::handle timestamp) {
            check_not_in_use(b);
            b.items.emplace_back(command, to_int<mf::Timestamp>(timestamp, "timestamp"));
          },
          py::arg("command"), py::arg("timestamp") = 0)
      .def(
          "extend",
          [](CommandBatch& b, const py::iterable& items) {
            check_not_in_use(b);
            for (const py::handle item : items) {
              const auto pair = py::reinterpret_borrow<py::sequence>(item);
              if (pair.size() != 2) throw py::value_error("expected (command, timestamp) pairs");
              b.items.emplace_back(to_command(pair[0], "command"),
                                   to_int<mf::Timestamp>(pair[1], "timestamp"));
            }
          },
          py::arg("items"))
      .def("clear",
           [](CommandBatch& b) {
             check_not_in_use(b);
             b.items.clear();
           })
      .def("__len__", [](const CommandBatch& b) { return b.items.size(); });

  py::class_<EventBatch>(m, "EventBatch",
                         "The events of a process_batch call, owned in C++; an event becomes a "
                         "Python object when it is read.")
      .def("__len__", [](const EventBatch& b) { return b.events.size(); })
      .def("__getitem__",
           [](const EventBatch& b, py::handle index) { return py::cast(b.events[batch_index(b, index)]); })
      // Iterating reads every event, so they are converted in one pass (as process()
      // does): a per-item iterator pays a Python-to-C++ call per event, which cost more
      // than the batch saved (docs/learning/m7.md). Indexing and events_of stay lazy.
      .def("__iter__", [](const EventBatch& b) { return py::iter(py::cast(b.events)); })
      .def(
          "events_of",
          [](const EventBatch& b, py::handle command) {
            const auto k = to_int<std::size_t>(command, "command");
            if (k + 1 >= b.offsets.size()) throw py::index_error("command index out of range");
            py::list out;
            for (std::size_t i = b.offsets[k]; i < b.offsets[k + 1]; ++i) out.append(py::cast(b.events[i]));
            return out;
          },
          py::arg("command"), "The events of the batch's k-th command.")
      .def_property_readonly("offsets", [](const EventBatch& b) { return py::cast(b.offsets); })
      .def_property_readonly("command_count", [](const EventBatch& b) { return b.offsets.size() - 1; })
      .def("__repr__", [](const EventBatch& b) {
        return "EventBatch(commands=" + std::to_string(b.offsets.size() - 1) +
               ", events=" + std::to_string(b.events.size()) + ")";
      });

  py::class_<Engine>(m, "Engine",
                     "One symbol's engine: the risk gate in front of the order book. A fresh "
                     "engine is CONTINUOUS with no limits.")
      .def(py::init<>())
      .def(
          "process",
          [](Engine& e, const mf::Command& command, py::handle timestamp) {
            return e.process(command, to_int<mf::Timestamp>(timestamp, "timestamp"));
          },
          py::arg("command"), py::arg("timestamp") = 0,
          "Applies one command and returns its events.")
      .def("process_batch", &Engine::process_batch, py::arg("batch"),
           "Applies every command of the batch with the GIL released.")
      .def("best_bid", [](const Engine& e) { return py::cast(e.get().book().best_bid()); })
      .def("best_ask", [](const Engine& e) { return py::cast(e.get().book().best_ask()); })
      .def(
          "depth",
          [](const Engine& e, mf::Side side, py::handle levels) {
            const auto& book = e.get().book();
            const auto prices = book.level_prices(side);
            const std::size_t n = levels.is_none()
                                      ? prices.size()
                                      : std::min(prices.size(), to_int<std::size_t>(levels, "levels"));
            py::list out;
            for (std::size_t i = 0; i < n; ++i) {
              out.append(py::make_tuple(prices[i], book.level_qty(side, prices[i]),
                                        book.level_order_count(side, prices[i])));
            }
            return out;
          },
          py::arg("side"), py::arg("levels") = py::none(),
          "(price, qty, orders) per level, best first.")
      .def(
          "level_orders",
          [](const Engine& e, mf::Side side, py::handle price) {
            return py::cast(e.get().book().level_orders(side, to_int<mf::Price>(price, "price")));
          },
          py::arg("side"), py::arg("price"), "Order ids at a level, in time priority.")
      .def(
          "order",
          [](const Engine& e, py::handle order_id) {
            return py::cast(e.get().book().find(to_int<mf::OrderId>(order_id, "order_id")));
          },
          py::arg("order_id"))
      .def_property_readonly("order_count", [](const Engine& e) { return e.get().book().order_count(); })
      .def_property_readonly("last_seq_no",
                             [](const Engine& e) { return py::cast(e.get().book().last_seq_no()); })
      .def_property_readonly("last_timestamp",
                             [](const Engine& e) { return py::cast(e.get().last_timestamp()); })
      .def_property_readonly("session", [](const Engine& e) { return e.get().risk().session(); })
      .def(
          "position",
          [](const Engine& e, py::handle account_id) {
            return e.get().risk().position(to_int<mf::AccountId>(account_id, "account_id"));
          },
          py::arg("account_id"))
      .def("positions",
           [](const Engine& e) {
             py::dict out;  // ascending account id, as RiskState keeps them
             for (const auto& p : e.get().risk().state().positions) out[py::int_(p.account_id)] = p.qty;
             return out;
           })
      .def(
          "exposure",
          [](const Engine& e, py::handle account_id) {
            return e.get().risk().exposure(to_int<mf::AccountId>(account_id, "account_id"));
          },
          py::arg("account_id"))
      .def("check_invariants", [](const Engine& e) { e.get().check_invariants(); })
      .def_property_readonly("failed", &Engine::failed)
      .def(
          "snapshot",
          [](const Engine& e, py::handle last_timestamp, py::handle journal_offset) {
            const mf::Engine& engine = e.get();
            const mf::EngineState state = engine.state();
            return mf::Snapshot{
                .book = state.book,
                .risk = state.risk,
                .last_timestamp = last_timestamp.is_none()
                                      ? engine.last_timestamp().value_or(0)
                                      : to_int<mf::Timestamp>(last_timestamp, "last_timestamp"),
                .journal_offset = to_int<std::uint64_t>(journal_offset, "journal_offset"),
            };
          },
          py::kw_only(), py::arg("last_timestamp") = py::none(), py::arg("journal_offset") = 0)
      .def_static(
          "from_snapshot",
          [](const mf::Snapshot& s) {
            return std::make_unique<Engine>(
                mf::Engine::from_state({.book = s.book, .risk = s.risk}, s.last_timestamp));
          },
          py::arg("snapshot"));

  m.def(
      "_inject_fault_after_next_command",
      [](Engine& e) { e.inject_fault_after_next_command(); }, py::arg("engine"),
      "Test hook: the engine raises InvariantViolation after applying its next command.");
}

}  // namespace mfpy
