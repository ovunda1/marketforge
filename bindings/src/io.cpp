// Journal, replay, snapshots, risk config and the canonical text formats.

#include <pybind11/stl.h>
#include <pybind11/stl/filesystem.h>

#include <filesystem>
#include <memory>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include "bindings.hpp"
#include "convert.hpp"
#include "mf/journal.hpp"
#include "mf/replay.hpp"
#include "mf/risk_config.hpp"
#include "mf/snapshot.hpp"
#include "mf/text_format.hpp"

namespace mfpy {
namespace {

using Path = std::filesystem::path;

// Writes a new journal; closed by close() or at the end of a with block.
class JournalWriter {
 public:
  explicit JournalWriter(const Path& path) : writer_(std::make_unique<mf::JournalWriter>(path.string())) {}

  void append(const mf::JournalRecord& record) {
    if (!writer_) throw py::value_error("journal is closed");
    writer_->append(record);
    last_seq_no_ = writer_->last_seq_no();
    bytes_written_ = writer_->bytes_written();
  }
  void close() { writer_.reset(); }

  mf::SeqNo last_seq_no() const { return last_seq_no_; }
  std::uint64_t bytes_written() const { return bytes_written_; }

 private:
  std::unique_ptr<mf::JournalWriter> writer_;
  mf::SeqNo last_seq_no_{};
  std::uint64_t bytes_written_{};
};

struct ReplayResult {
  mf::ReplayResult result;
  std::optional<std::string> events;
};

std::vector<mf::JournalRecord> read_all(mf::JournalReader& reader) {
  std::vector<mf::JournalRecord> out;
  py::gil_scoped_release release;
  while (auto record = reader.next()) out.push_back(std::move(*record));
  return out;
}

}  // namespace

void bind_io(py::module_& m) {
  py::class_<JournalWriter>(m, "JournalWriter",
                            "Creates a new journal (never overwrites) and appends records.")
      .def(py::init<const Path&>(), py::arg("path"))
      .def("append", &JournalWriter::append, py::arg("record"))
      .def("close", &JournalWriter::close)
      .def("__enter__", [](py::handle self) { return self; })
      .def("__exit__", [](JournalWriter& w, const py::args&) { w.close(); })
      .def_property_readonly("last_seq_no", &JournalWriter::last_seq_no)
      .def_property_readonly("bytes_written", &JournalWriter::bytes_written);

  py::class_<mf::JournalReader>(m, "JournalReader",
                                "Reads and validates a journal; iterate it for its records.")
      .def(py::init([](const Path& path) { return std::make_unique<mf::JournalReader>(path.string()); }),
           py::arg("path"))
      .def("__iter__", [](py::handle self) { return self; })
      .def("__next__",
           [](mf::JournalReader& r) {
             auto record = r.next();
             if (!record) throw py::stop_iteration();
             return std::move(*record);
           })
      .def("read_all", &read_all, "Every remaining record, read with the GIL released.")
      .def(
          "seek",
          [](mf::JournalReader& r, py::handle offset, py::handle last_seq_no, py::handle last_timestamp) {
            r.seek(to_int<std::uint64_t>(offset, "offset"), to_int<mf::SeqNo>(last_seq_no, "last_seq_no"),
                   to_int<mf::Timestamp>(last_timestamp, "last_timestamp"));
          },
          py::arg("offset"), py::arg("last_seq_no"), py::arg("last_timestamp"))
      .def_property_readonly("offset", &mf::JournalReader::offset)
      .def_property_readonly("last_seq_no", &mf::JournalReader::last_seq_no)
      .def_property_readonly("last_timestamp", &mf::JournalReader::last_timestamp);

  py::class_<ReplayResult>(m, "ReplayResult", "The outcome of replay().")
      .def_property_readonly("sha256", [](const ReplayResult& r) { return r.result.sha256; })
      .def_property_readonly("commands", [](const ReplayResult& r) { return r.result.commands; })
      .def_property_readonly("last_seq_no", [](const ReplayResult& r) { return r.result.last_seq_no; })
      .def_property_readonly("snapshots_written",
                             [](const ReplayResult& r) { return r.result.snapshots_written; })
      .def_property_readonly("events", [](const ReplayResult& r) -> py::object {
        if (!r.events) return py::none();
        return py::bytes(*r.events);
      });

  m.def(
      "replay",
      [](const Path& journal, std::optional<Path> snapshot, py::handle snapshot_every,
         std::optional<Path> snapshot_dir, py::handle capture_events) {
        mf::ReplayOptions options{.journal_path = journal.string()};
        if (snapshot) options.snapshot_path = snapshot->string();
        options.snapshot_every = to_int<std::uint64_t>(snapshot_every, "snapshot_every");
        if (snapshot_dir) options.snapshot_dir = snapshot_dir->string();
        if (options.snapshot_every != 0 && !snapshot_dir) {
          throw py::value_error("snapshot_every needs snapshot_dir");
        }
        std::ostringstream events;
        const bool capture = to_bool(capture_events, "capture_events");
        if (capture) options.events_out = &events;
        ReplayResult out;
        {
          py::gil_scoped_release release;
          out.result = mf::replay(options);
        }
        if (capture) out.events = std::move(events).str();
        return out;
      },
      py::arg("journal"), py::kw_only(), py::arg("snapshot") = py::none(),
      py::arg("snapshot_every") = 0, py::arg("snapshot_dir") = py::none(),
      py::arg("capture_events") = false,
      "Replays a journal (optionally from a snapshot) through a fresh engine and hashes "
      "the canonical event stream, exactly as mf-replay does.");

  m.def(
      "encode_snapshot",
      [](const mf::Snapshot& s) { return py::bytes(mf::encode_snapshot(s)); }, py::arg("snapshot"));
  m.def(
      "decode_snapshot",
      [](const py::bytes& data) { return mf::decode_snapshot(std::string_view(data)); },
      py::arg("data"));
  m.def(
      "write_snapshot_file",
      [](const Path& path, const mf::Snapshot& s) {
        py::gil_scoped_release release;
        mf::write_snapshot_file(path.string(), s);
      },
      py::arg("path"), py::arg("snapshot"));
  m.def(
      "read_snapshot_file",
      [](const Path& path) {
        py::gil_scoped_release release;
        return mf::read_snapshot_file(path.string());
      },
      py::arg("path"));

  m.def(
      "load_risk_config", [](const Path& path) { return mf::load_risk_config(path.string()); },
      py::arg("path"),
      "Admin commands from a YAML risk config, all with seq_no 0, in a fixed order.");
  m.def(
      "parse_risk_config", [](const std::string& text) { return mf::parse_risk_config(text); },
      py::arg("text"));

  m.def(
      "parse_line",
      [](const std::string& line) -> py::object {
        const auto parsed = mf::parse_line(line);
        if (!parsed) return py::none();
        return py::cast(*parsed);
      },
      py::arg("line"), "A canonical command line as a command or ClockSet; None if blank.");
  m.def(
      "format_top_of_book",
      [](const Engine& e, py::handle seq_no) {
        return mf::format_top_of_book(to_int<mf::SeqNo>(seq_no, "seq_no"), e.get().book());
      },
      py::arg("engine"), py::arg("seq_no"));
  m.def(
      "format_depth", [](const Engine& e) { return mf::format_depth(e.get().book()); },
      py::arg("engine"));
}

}  // namespace mfpy
