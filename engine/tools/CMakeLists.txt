set(MF_TOOL_WARNINGS $<$<CXX_COMPILER_ID:GNU,Clang,AppleClang>:-Wall -Wextra -Wpedantic>)

add_executable(mf_driver mf_driver.cpp)
target_link_libraries(mf_driver PRIVATE mf::config)  # brings mf::engine
target_compile_options(mf_driver PRIVATE ${MF_TOOL_WARNINGS})

# Text commands -> binary journal, through the real sequencer.
add_executable(mf-journal mf_journal.cpp)
target_link_libraries(mf-journal PRIVATE mf::config)  # brings mf::engine
target_compile_options(mf-journal PRIVATE ${MF_TOOL_WARNINGS})

# Journal (optionally from a snapshot) -> SHA-256 of the canonical event stream.
add_executable(mf-replay mf_replay.cpp)
target_link_libraries(mf-replay PRIVATE mf::engine)
target_compile_options(mf-replay PRIVATE ${MF_TOOL_WARNINGS})
