# Optimization log

<!-- Generated from OPTIMIZATIONS.md.in by `make bench-docs` out of bench/results/ab/*.json.
Do not edit OPTIMIZATIONS.md by hand: every number here comes from the JSON. -->

Each optimization is one commit between two git tags, `m6-opt-<n>-before` and
`m6-opt-<n>-after`. Before it is measured it must pass `make gate`: the full test suite
with the invariant walk on, ASan and UBSan, the long differential test on both generator
profiles, the suite again on the Release benchmark build, and the benchmark workloads
replayed with the invariant walk on. Correctness gates every speedup.

`make bench-ab OPT=<id>` builds both tags in git worktrees and runs them in interleaved
rounds (A B, B A, ...), so temperature and clock drift hit both sides. Each round runs
both workloads end to end and the microbenchmarks. Ratios are paired by round. A change
counts as faster or slower only if every round agrees; otherwise it is "no measurable
effect". Before and after must produce the same event digest.

Throughput rows are end-to-end commands per second (higher is better); latency rows are
the per-command percentiles (lower is better). Every ratio is after / before.

## Optimization opt-1: object pool for resting orders

**Change.** Resting orders lived inside `std::unordered_map` nodes: every order that
rested cost a heap allocation, and every fill or cancel that removed it a free. They now
come from `ObjectPool<Order>` (`engine/include/mf/object_pool.hpp`): chunks of slots that
never move, a free list, freed slots poisoned under ASan. The id index still maps
id to `Order*` through a `std::unordered_map`.

**Measured.** 7 interleaved rounds per build, 2,000,000 commands per workload per round; before `m6-opt-1-before` (`c8a4be0ea25b`), after `m6-opt-1-after` (`dc7894cf8add`).

| Workload | Metric | Before | After | After / before, median (min – max) | Verdict |
|---|---|---|---|---|---|
| narrow | commands/s | 8.31 M/s | 7.97 M/s | 0.97× (0.86× – 0.98×) | slower |
| narrow | latency p50 | 84 ns | 84 ns | 1.00× (1.00× – 1.00×) | no measurable effect |
| narrow | latency p99 | 375 ns | 375 ns | 1.00× (1.00× – 1.11×) | no measurable effect |
| narrow | latency p99.9 | 959 ns | 959 ns | 1.00× (1.00× – 1.04×) | no measurable effect |
| narrow | heap allocations per command | 1.63 | 1.63 |  |  |
| wide | commands/s | 6.35 M/s | 6.02 M/s | 0.95× (0.92× – 0.98×) | slower |
| wide | latency p50 | 125 ns | 125 ns | 1.00× (1.00× – 1.00×) | no measurable effect |
| wide | latency p99 | 500 ns | 500 ns | 1.00× (1.00× – 1.08×) | no measurable effect |
| wide | latency p99.9 | 1.46 µs | 1.50 µs | 1.03× (1.00× – 1.03×) | no measurable effect |
| wide | heap allocations per command | 1.85 | 1.85 |  |  |

<details><summary>Microbenchmarks</summary>

| Benchmark (time per op) | Before | After | After / before | Verdict |
|---|---|---|---|---|
| `AddExistingLevel` | 30 ns | 33 ns | 1.12× (0.97× – 1.17×) | no measurable effect |
| `AddNewLevel` | 44 ns | 45 ns | 1.02× (0.98× – 1.04×) | no measurable effect |
| `CancelKeepsLevel` | 23 ns | 25 ns | 1.08× (1.01× – 1.09×) | slower |
| `CancelRemovesLevel` | 46 ns | 48 ns | 1.04× (0.98× – 1.12×) | no measurable effect |
| `ReplaceQtyDown` | 7 ns | 7 ns | 1.04× (0.99× – 1.05×) | no measurable effect |
| `ReplaceReprice` | 32 ns | 34 ns | 1.05× (0.96× – 1.07×) | no measurable effect |
| `MatchOneLevel/1` | 36 ns | 40 ns | 1.14× (1.06× – 1.26×) | slower |
| `MatchOneLevel/8` | 144 ns | 169 ns | 1.17× (1.02× – 1.26×) | slower |
| `MatchMultiLevel/2` | 70 ns | 74 ns | 1.04× (1.01× – 1.09×) | slower |
| `MatchMultiLevel/8` | 226 ns | 243 ns | 1.08× (1.06× – 1.09×) | slower |
| `MatchMultiLevel/32` | 870 ns | 929 ns | 1.06× (1.05× – 1.10×) | slower |
| `RiskCheck` | 11 ns | 11 ns | 1.01× (0.99× – 1.02×) | no measurable effect |
| `RiskCheckNoLimits` | 5 ns | 4 ns | 1.00× (0.91× – 1.06×) | no measurable effect |
| `EngineAdd` | 28 ns | 30 ns | 1.09× (0.97× – 1.10×) | no measurable effect |

</details>

**Result: slower on its own, kept as the base for opt-2.** End to end:
slower (narrow workload) and
slower (wide workload), and heap allocations per command did not
move (1.63 (narrow workload)): the index was still
node-based, so each resting order still cost one node, and finding an order now took one
more pointer hop. A pool only pays once nothing else allocates per order. It is kept
because the flat index of opt-2 needs order addresses that never move, which only the
pool gives.

## Optimization opt-2: open-addressing hash map for the order index

**Change.** The id index (`std::unordered_map<OrderId, Order*>`) still allocated a node
per resting order, and every lookup chased a bucket pointer to a node. `FlatHashMap`
(`engine/include/mf/flat_hash_map.hpp`) is one array of key and value slots: linear
probing, at most half full, backward-shift deletion (no tombstones), a murmur3 finaliser
because order ids are sequential.

**Measured.** 7 interleaved rounds per build, 2,000,000 commands per workload per round; before `m6-opt-2-before` (`dc7894cf8add`), after `m6-opt-2-after` (`8c65a1b01cc3`).

| Workload | Metric | Before | After | After / before, median (min – max) | Verdict |
|---|---|---|---|---|---|
| narrow | commands/s | 8.02 M/s | 8.49 M/s | 1.06× (1.04× – 1.06×) | faster |
| narrow | latency p50 | 84 ns | 84 ns | 1.00× (1.00× – 1.00×) | no measurable effect |
| narrow | latency p99 | 375 ns | 375 ns | 1.00× (0.90× – 1.11×) | no measurable effect |
| narrow | latency p99.9 | 958 ns | 959 ns | 1.00× (1.00× – 1.13×) | no measurable effect |
| narrow | heap allocations per command | 1.63 | 1.19 |  |  |
| wide | commands/s | 6.06 M/s | 6.63 M/s | 1.09× (1.07× – 1.11×) | faster |
| wide | latency p50 | 125 ns | 125 ns | 1.00× (1.00× – 1.00×) | no measurable effect |
| wide | latency p99 | 500 ns | 459 ns | 0.92× (0.92× – 1.00×) | no measurable effect |
| wide | latency p99.9 | 1.50 µs | 1.42 µs | 0.94× (0.89× – 0.97×) | faster |
| wide | heap allocations per command | 1.85 | 1.41 |  |  |

<details><summary>Microbenchmarks</summary>

| Benchmark (time per op) | Before | After | After / before | Verdict |
|---|---|---|---|---|
| `AddExistingLevel` | 30 ns | 26 ns | 0.87× (0.75× – 0.91×) | faster |
| `AddNewLevel` | 45 ns | 40 ns | 0.88× (0.84× – 0.97×) | faster |
| `CancelKeepsLevel` | 23 ns | 19 ns | 0.82× (0.75× – 0.85×) | faster |
| `CancelRemovesLevel` | 45 ns | 44 ns | 0.98× (0.92× – 1.02×) | no measurable effect |
| `ReplaceQtyDown` | 7 ns | 9 ns | 1.40× (1.34× – 1.41×) | slower |
| `ReplaceReprice` | 32 ns | 31 ns | 0.98× (0.95× – 1.00×) | no measurable effect |
| `MatchOneLevel/1` | 36 ns | 33 ns | 0.91× (0.83× – 0.97×) | faster |
| `MatchOneLevel/8` | 154 ns | 127 ns | 0.81× (0.77× – 0.97×) | faster |
| `MatchMultiLevel/2` | 73 ns | 76 ns | 1.05× (1.02× – 1.06×) | slower |
| `MatchMultiLevel/8` | 243 ns | 208 ns | 0.86× (0.84× – 0.89×) | faster |
| `MatchMultiLevel/32` | 932 ns | 757 ns | 0.80× (0.79× – 0.85×) | faster |
| `RiskCheck` | 11 ns | 11 ns | 1.01× (0.95× – 1.03×) | no measurable effect |
| `RiskCheckNoLimits` | 5 ns | 4 ns | 0.97× (0.94× – 1.03×) | no measurable effect |
| `EngineAdd` | 28 ns | 29 ns | 1.05× (0.91× – 1.08×) | no measurable effect |

</details>

**Result: kept.** End to end faster (narrow workload)
(+6.0% (narrow workload)) and
faster (wide workload)
(+9.3% (wide workload)). One microbenchmark got slower:
`ReplaceQtyDown`, the cheapest operation measured, does two index lookups and little else,
and its ids are consecutive; probably the node-based table's identity hash put them in
adjacent buckets and the scattering hash does not (not profiled).

## Optimization opt-3: flat tables for used ids and the risk gate's open orders

**Change.** Two more per-order allocations: the session's set of used order ids
(`std::unordered_set`) and the risk gate's open-order map (`std::unordered_map`). Both
move to the flat tables of opt-2. Snapshots still sort the ids, so no hash order reaches
any output.

**Measured.** 7 interleaved rounds per build, 2,000,000 commands per workload per round; before `m6-opt-3-before` (`8c65a1b01cc3`), after `m6-opt-3-after` (`32e112f1a711`).

| Workload | Metric | Before | After | After / before, median (min – max) | Verdict |
|---|---|---|---|---|---|
| narrow | commands/s | 8.49 M/s | 7.18 M/s | 0.84× (0.83× – 0.86×) | slower |
| narrow | latency p50 | 84 ns | 84 ns | 1.00× (1.00× – 1.00×) | no measurable effect |
| narrow | latency p99 | 375 ns | 458 ns | 1.22× (1.22× – 1.22×) | slower |
| narrow | latency p99.9 | 958 ns | 1.04 µs | 1.09× (1.04× – 1.13×) | slower |
| narrow | heap allocations per command | 1.19 | 0.04 |  |  |
| wide | commands/s | 6.65 M/s | 5.81 M/s | 0.87× (0.84× – 0.89×) | slower |
| wide | latency p50 | 125 ns | 125 ns | 1.00× (1.00× – 1.00×) | no measurable effect |
| wide | latency p99 | 459 ns | 542 ns | 1.18× (0.76× – 1.18×) | no measurable effect |
| wide | latency p99.9 | 1.42 µs | 1.46 µs | 1.03× (1.00× – 1.06×) | no measurable effect |
| wide | heap allocations per command | 1.41 | 0.12 |  |  |

<details><summary>Microbenchmarks</summary>

| Benchmark (time per op) | Before | After | After / before | Verdict |
|---|---|---|---|---|
| `AddExistingLevel` | 26 ns | 57 ns | 2.22× (2.04× – 2.25×) | slower |
| `AddNewLevel` | 40 ns | 73 ns | 1.83× (1.80× – 1.93×) | slower |
| `CancelKeepsLevel` | 19 ns | 19 ns | 1.00× (0.99× – 1.02×) | no measurable effect |
| `CancelRemovesLevel` | 45 ns | 46 ns | 1.03× (1.01× – 1.04×) | slower |
| `ReplaceQtyDown` | 9 ns | 10 ns | 1.01× (1.00× – 1.03×) | no measurable effect |
| `ReplaceReprice` | 31 ns | 32 ns | 1.03× (1.02× – 1.05×) | slower |
| `MatchOneLevel/1` | 33 ns | 59 ns | 1.77× (1.53× – 1.90×) | slower |
| `MatchOneLevel/8` | 126 ns | 174 ns | 1.38× (1.24× – 1.44×) | slower |
| `MatchMultiLevel/2` | 76 ns | 135 ns | 1.79× (1.55× – 1.80×) | slower |
| `MatchMultiLevel/8` | 208 ns | 260 ns | 1.25× (1.09× – 1.26×) | slower |
| `MatchMultiLevel/32` | 756 ns | 819 ns | 1.08× (1.07× – 1.09×) | slower |
| `RiskCheck` | 11 ns | 17 ns | 1.52× (1.47× – 1.57×) | slower |
| `RiskCheckNoLimits` | 5 ns | 10 ns | 2.30× (2.16× – 2.48×) | slower |
| `EngineAdd` | 29 ns | 29 ns | 0.99× (0.98× – 4.06×) | no measurable effect |

</details>

**Result: slower, superseded by opt-5.** Allocations per command fell from
1.19 (narrow workload) to
0.04 (narrow workload), yet end to end it was
slower (narrow workload)
(-16.5% (narrow workload)) and
slower (wide workload)
(-12.5% (wide workload)), with a worse p99. Profiling
the risk check put nearly all of its time in the used-id table's probe loop. That set holds every id acked
in the session, millions in a long run; libc++ hashes an integer to itself, so the
`std::unordered_set` it replaced kept consecutive ids in neighbouring buckets, while the
scattering hash sends each new id to a random cache line of a table tens of megabytes
large. Fewer allocations, many more cache misses. The open-order map (small, live orders
only) was not the problem. opt-5 fixes the used-id set; the open-order map stays flat.

## Optimization opt-4: flat tick array for price levels

**Change.** Each side's levels were a `std::map`: a tree walk to find a level, a heap
node per new level. `PriceLevels` (`engine/include/mf/price_levels.hpp`) keeps a window
of tick slots around the best price (constant-time find, an occupancy bitmap to find the
next best, levels from a pool) and an ordered overflow map for levels outside it.

**Rule, agreed before measuring.** Kept only if the geometric mean of its end-to-end
throughput ratios on the two workloads reaches +5.0%;
otherwise logged here and backed out.

**Measured.** 7 interleaved rounds per build, 2,000,000 commands per workload per round; before `m6-opt-4-before` (`32e112f1a711`), after `m6-opt-4-after` (`75563f104040`). Geometric mean of the two throughput
ratios: 1.07×.

| Workload | Metric | Before | After | After / before, median (min – max) | Verdict |
|---|---|---|---|---|---|
| narrow | commands/s | 7.19 M/s | 7.47 M/s | 1.04× (0.96× – 1.05×) | no measurable effect |
| narrow | latency p50 | 84 ns | 84 ns | 1.00× (0.67× – 1.00×) | no measurable effect |
| narrow | latency p99 | 458 ns | 458 ns | 1.00× (0.85× – 1.00×) | no measurable effect |
| narrow | latency p99.9 | 1.08 µs | 1.04 µs | 0.96× (0.68× – 1.00×) | no measurable effect |
| narrow | heap allocations per command | 0.04 | 0.00 |  |  |
| wide | commands/s | 5.80 M/s | 6.42 M/s | 1.11× (1.06× – 1.21×) | faster |
| wide | latency p50 | 125 ns | 125 ns | 1.00× (1.00× – 1.00×) | no measurable effect |
| wide | latency p99 | 542 ns | 541 ns | 1.00× (1.00× – 1.00×) | faster |
| wide | latency p99.9 | 1.46 µs | 1.42 µs | 0.97× (0.94× – 0.97×) | faster |
| wide | heap allocations per command | 0.12 | 0.00 |  |  |

<details><summary>Microbenchmarks</summary>

| Benchmark (time per op) | Before | After | After / before | Verdict |
|---|---|---|---|---|
| `AddExistingLevel` | 58 ns | 56 ns | 0.96× (0.94× – 0.98×) | faster |
| `AddNewLevel` | 73 ns | 58 ns | 0.79× (0.77× – 0.80×) | faster |
| `CancelKeepsLevel` | 19 ns | 19 ns | 1.00× (0.99× – 1.02×) | no measurable effect |
| `CancelRemovesLevel` | 46 ns | 18 ns | 0.41× (0.40× – 0.42×) | faster |
| `ReplaceQtyDown` | 10 ns | 10 ns | 1.00× (0.99× – 1.03×) | no measurable effect |
| `ReplaceReprice` | 32 ns | 29 ns | 0.89× (0.88× – 0.91×) | faster |
| `MatchOneLevel/1` | 59 ns | 59 ns | 0.99× (0.98× – 1.03×) | no measurable effect |
| `MatchOneLevel/8` | 173 ns | 179 ns | 1.03× (1.01× – 1.29×) | slower |
| `MatchMultiLevel/2` | 136 ns | 119 ns | 0.88× (0.86× – 0.88×) | faster |
| `MatchMultiLevel/8` | 260 ns | 213 ns | 0.82× (0.81× – 0.82×) | faster |
| `MatchMultiLevel/32` | 822 ns | 1.32 µs | 1.61× (1.58× – 1.65×) | slower |
| `RiskCheck` | 17 ns | 17 ns | 1.00× (0.97× – 1.06×) | no measurable effect |
| `RiskCheckNoLimits` | 10 ns | 11 ns | 1.03× (1.02× – 1.08×) | slower |
| `EngineAdd` | 29 ns | 27 ns | 0.91× (0.47× – 1.00×) | faster |

</details>

**Result: kept under the rule** (geometric mean above). It is
faster (wide workload) on the wide workload, many shallow
levels where a tree walk is deepest, and no measurable effect (narrow workload)
on the narrow one, few deep levels where the map was already shallow. It also removed the
last regular allocation (a map node per new level). The microbenchmark
`MatchMultiLevel/32` got slower: its setup spreads more levels than the window holds, so
most of them live in the overflow map. That is a property of the benchmark's shape, not
of either workload, and the reason the window is sized to the spread of real prices.

## Optimization opt-5: paged bitmap for the session's used order ids

**Change.** `IdSet` (`engine/include/mf/id_set.hpp`) replaces the used-id table of opt-3:
one cache line of bits per run of consecutive ids, behind a small page table (a
scattering `FlatHashMap` that stays in cache). The live-order tables keep the scattering
hash. Pages come from a pool.

**Why, and what was tried first.** The fix for opt-3 had to keep consecutive ids close in memory without hurting the
small tables. A locality-keeping hash (low bits of the id kept) made the used-id set fast
but turned the live-order tables pathological: long-lived early ids form a dense run, and
newer ids that wrap around the small table land in it and scan it to the end
(`EngineAdd` became an order of magnitude slower). Hashing blocks of ids together kept
the clustering and was slower than either. A bitmap fits the set's job exactly: one bit
per id, consecutive ids in the same cache line, and the page table is small enough to
stay in cache. These trials ran on scratch builds and are not logged as results.

**Measured.** 7 interleaved rounds per build, 2,000,000 commands per workload per round; before `m6-opt-5-before` (`c589c829c610`), after `m6-opt-5-after` (`7479ed13bf04`).

| Workload | Metric | Before | After | After / before, median (min – max) | Verdict |
|---|---|---|---|---|---|
| narrow | commands/s | 7.58 M/s | 9.04 M/s | 1.19× (1.18× – 1.21×) | faster |
| narrow | latency p50 | 84 ns | 83 ns | 0.99× (0.99× – 0.99×) | faster |
| narrow | latency p99 | 458 ns | 417 ns | 0.91× (0.91× – 0.91×) | faster |
| narrow | latency p99.9 | 1.04 µs | 1.00 µs | 0.96× (0.96× – 1.04×) | no measurable effect |
| narrow | heap allocations per command | 0.00 | 0.00 |  |  |
| wide | commands/s | 6.51 M/s | 7.75 M/s | 1.20× (0.88× – 1.23×) | no measurable effect |
| wide | latency p50 | 125 ns | 84 ns | 0.67× (0.67× – 0.67×) | faster |
| wide | latency p99 | 541 ns | 500 ns | 1.00× (0.86× – 1.00×) | no measurable effect |
| wide | latency p99.9 | 1.42 µs | 1.38 µs | 0.97× (0.89× – 1.06×) | no measurable effect |
| wide | heap allocations per command | 0.00 | 0.00 |  |  |

<details><summary>Microbenchmarks</summary>

| Benchmark (time per op) | Before | After | After / before | Verdict |
|---|---|---|---|---|
| `AddExistingLevel` | 54 ns | 20 ns | 0.36× (0.34× – 0.38×) | faster |
| `AddNewLevel` | 57 ns | 21 ns | 0.36× (0.30× – 0.37×) | faster |
| `CancelKeepsLevel` | 19 ns | 19 ns | 1.00× (0.96× – 1.03×) | no measurable effect |
| `CancelRemovesLevel` | 18 ns | 18 ns | 0.99× (0.96× – 1.01×) | no measurable effect |
| `ReplaceQtyDown` | 9 ns | 9 ns | 1.00× (0.97× – 1.03×) | no measurable effect |
| `ReplaceReprice` | 29 ns | 29 ns | 0.99× (0.97× – 1.03×) | no measurable effect |
| `MatchOneLevel/1` | 57 ns | 24 ns | 0.42× (0.38× – 0.43×) | faster |
| `MatchOneLevel/8` | 180 ns | 117 ns | 0.65× (0.59× – 0.67×) | faster |
| `MatchMultiLevel/2` | 116 ns | 54 ns | 0.47× (0.45× – 0.47×) | faster |
| `MatchMultiLevel/8` | 209 ns | 152 ns | 0.73× (0.60× – 0.73×) | faster |
| `MatchMultiLevel/32` | 1.32 µs | 1.26 µs | 0.94× (0.85× – 0.97×) | faster |
| `RiskCheck` | 17 ns | 11 ns | 0.66× (0.64× – 0.68×) | faster |
| `RiskCheckNoLimits` | 10 ns | 4 ns | 0.40× (0.38× – 0.47×) | faster |
| `EngineAdd` | 26 ns | 18 ns | 0.69× (0.68× – 0.71×) | faster |

</details>

**Result: kept.** End to end faster (narrow workload)
(+19.1% (narrow workload)). On the wide workload the
median ratio is +19.8% (wide workload), but one round of
seven came out below one, so by the rule it counts as
no measurable effect (wide workload); the add and match microbenchmarks,
which create orders, are all faster.

## Optimization opt-6: host-CPU code generation and LTO

**Change.** Benchmark builds now compile with `-mcpu=native` (`-march=native` on x86)
and link with LTO (`MF_NATIVE`, `MF_LTO`, on by default only when `MF_BUILD_BENCH` is on:
a native binary may not run on another machine). The flags are recorded in every results
file.

**Measured.** 7 interleaved rounds per build, 2,000,000 commands per workload per round; before `m6-opt-6-before` (`7479ed13bf04`), after `m6-opt-6-after` (`c11ef56d515b`).

| Workload | Metric | Before | After | After / before, median (min – max) | Verdict |
|---|---|---|---|---|---|
| narrow | commands/s | 9.09 M/s | 9.25 M/s | 1.02× (1.01× – 1.02×) | faster |
| narrow | latency p50 | 83 ns | 83 ns | 1.00× (1.00× – 1.00×) | no measurable effect |
| narrow | latency p99 | 417 ns | 417 ns | 1.00× (1.00× – 1.00×) | no measurable effect |
| narrow | latency p99.9 | 1.00 µs | 1.00 µs | 1.00× (1.00× – 1.08×) | no measurable effect |
| narrow | heap allocations per command | 0.00 | 0.00 |  |  |
| wide | commands/s | 7.79 M/s | 7.89 M/s | 1.01× (0.99× – 1.02×) | no measurable effect |
| wide | latency p50 | 84 ns | 83 ns | 0.99× (0.99× – 1.00×) | no measurable effect |
| wide | latency p99 | 500 ns | 500 ns | 1.00× (1.00× – 1.08×) | no measurable effect |
| wide | latency p99.9 | 1.38 µs | 1.38 µs | 1.00× (1.00× – 1.00×) | no measurable effect |
| wide | heap allocations per command | 0.00 | 0.00 |  |  |

<details><summary>Microbenchmarks</summary>

| Benchmark (time per op) | Before | After | After / before | Verdict |
|---|---|---|---|---|
| `AddExistingLevel` | 20 ns | 17 ns | 0.87× (0.85× – 0.87×) | faster |
| `AddNewLevel` | 21 ns | 18 ns | 0.87× (0.82× – 0.90×) | faster |
| `CancelKeepsLevel` | 19 ns | 18 ns | 0.97× (0.90× – 0.98×) | faster |
| `CancelRemovesLevel` | 18 ns | 17 ns | 0.95× (0.90× – 0.97×) | faster |
| `ReplaceQtyDown` | 9 ns | 8 ns | 0.87× (0.85× – 0.88×) | faster |
| `ReplaceReprice` | 28 ns | 26 ns | 0.92× (0.90× – 0.94×) | faster |
| `MatchOneLevel/1` | 24 ns | 23 ns | 0.95× (0.93× – 0.98×) | faster |
| `MatchOneLevel/8` | 117 ns | 116 ns | 0.99× (0.99× – 1.00×) | faster |
| `MatchMultiLevel/2` | 54 ns | 53 ns | 0.98× (0.96× – 1.00×) | no measurable effect |
| `MatchMultiLevel/8` | 152 ns | 154 ns | 1.02× (1.00× – 1.02×) | no measurable effect |
| `MatchMultiLevel/32` | 1.25 µs | 1.20 µs | 0.96× (0.94× – 0.99×) | faster |
| `RiskCheck` | 11 ns | 9 ns | 0.82× (0.80× – 0.83×) | faster |
| `RiskCheckNoLimits` | 4 ns | 4 ns | 0.83× (0.68× – 0.88×) | faster |
| `EngineAdd` | 18 ns | 16 ns | 0.89× (0.86× – 0.97×) | faster |

</details>

**Result: kept, a small effect.** End to end
faster (narrow workload)
(+1.6% (narrow workload)) and
no measurable effect (wide workload)
(+1.4% (wide workload)). The microbenchmarks gain more
than the replay, most likely because LTO inlines across the book and risk files, which
matters most when one operation is a few function calls, while the replay is dominated by
memory access (not profiled).

## Optimization opt-7: flat account table in the risk gate

**Change.** After opt-5 the profile's largest engine cost was the risk gate: it looked
the account up in a `std::map` several times per order and per fill. Accounts now live in
a `FlatHashMap`; the paths that produce state walk a sorted id list, so snapshots keep
ascending account order.

**Measured.** 7 interleaved rounds per build, 2,000,000 commands per workload per round; before `m6-opt-7-before` (`c11ef56d515b`), after `m6-opt-7-after` (`5d93fe2939e1`).

| Workload | Metric | Before | After | After / before, median (min – max) | Verdict |
|---|---|---|---|---|---|
| narrow | commands/s | 9.24 M/s | 14.11 M/s | 1.52× (1.49× – 1.54×) | faster |
| narrow | latency p50 | 83 ns | 42 ns | 0.51× (0.51× – 0.51×) | faster |
| narrow | latency p99 | 417 ns | 291 ns | 0.70× (0.70× – 0.70×) | faster |
| narrow | latency p99.9 | 1.00 µs | 709 ns | 0.71× (0.68× – 0.76×) | faster |
| narrow | heap allocations per command | 0.00 | 0.00 |  |  |
| wide | commands/s | 7.91 M/s | 12.24 M/s | 1.55× (1.53× – 1.56×) | faster |
| wide | latency p50 | 83 ns | 42 ns | 0.51× (0.50× – 0.51×) | faster |
| wide | latency p99 | 500 ns | 334 ns | 0.67× (0.67× – 0.67×) | faster |
| wide | latency p99.9 | 1.38 µs | 1.00 µs | 0.73× (0.73× – 0.73×) | faster |
| wide | heap allocations per command | 0.00 | 0.00 |  |  |

<details><summary>Microbenchmarks</summary>

| Benchmark (time per op) | Before | After | After / before | Verdict |
|---|---|---|---|---|
| `AddExistingLevel` | 17 ns | 17 ns | 0.99× (0.99× – 1.00×) | no measurable effect |
| `AddNewLevel` | 18 ns | 18 ns | 1.00× (0.98× – 1.01×) | no measurable effect |
| `CancelKeepsLevel` | 18 ns | 18 ns | 1.00× (0.99× – 1.00×) | no measurable effect |
| `CancelRemovesLevel` | 17 ns | 17 ns | 0.99× (0.99× – 1.02×) | no measurable effect |
| `ReplaceQtyDown` | 8 ns | 8 ns | 1.00× (0.99× – 1.02×) | no measurable effect |
| `ReplaceReprice` | 26 ns | 26 ns | 1.00× (0.97× – 1.01×) | no measurable effect |
| `MatchOneLevel/1` | 23 ns | 23 ns | 1.00× (0.99× – 1.01×) | no measurable effect |
| `MatchOneLevel/8` | 116 ns | 117 ns | 1.00× (0.99× – 1.04×) | no measurable effect |
| `MatchMultiLevel/2` | 53 ns | 52 ns | 1.00× (0.97× – 1.01×) | no measurable effect |
| `MatchMultiLevel/8` | 153 ns | 152 ns | 1.00× (0.98× – 1.01×) | no measurable effect |
| `MatchMultiLevel/32` | 1.21 µs | 1.22 µs | 1.02× (0.99× – 1.03×) | no measurable effect |
| `RiskCheck` | 9 ns | 12 ns | 1.29× (0.97× – 1.33×) | no measurable effect |
| `RiskCheckNoLimits` | 4 ns | 3 ns | 0.94× (0.91× – 1.13×) | no measurable effect |
| `EngineAdd` | 16 ns | 16 ns | 1.00× (0.98× – 1.01×) | no measurable effect |

</details>

**Result: kept, the largest single gain.** End to end
faster (narrow workload)
(+52.4% (narrow workload)) and
faster (wide workload)
(+54.7% (wide workload)), with p50 and p99 down too. The
workloads spread orders over many accounts, so every order and every fill walked a
red-black tree several times, each walk a chain of dependent loads and branches the
predictor cannot learn. The microbenchmarks did not move: they use one account, a
one-node tree. A benchmark of the gate with one account would have hidden the gate's
biggest cost.

## Baseline to final

The same interleaved comparison between the engine before any optimization and the final
one: 7 interleaved rounds per build, 2,000,000 commands per workload per round; before `m6-opt-1-before` (`c8a4be0ea25b`), after `m6-opt-7-after` (`5d93fe2939e1`).

| Workload | Metric | Before | After | After / before, median (min – max) | Verdict |
|---|---|---|---|---|---|
| narrow | commands/s | 8.41 M/s | 14.19 M/s | 1.68× (1.59× – 1.70×) | faster |
| narrow | latency p50 | 84 ns | 42 ns | 0.50× (0.50× – 0.50×) | faster |
| narrow | latency p99 | 375 ns | 292 ns | 0.78× (0.78× – 0.78×) | faster |
| narrow | latency p99.9 | 958 ns | 750 ns | 0.82× (0.74× – 0.91×) | faster |
| narrow | heap allocations per command | 1.63 | 0.00 |  |  |
| wide | commands/s | 6.45 M/s | 12.18 M/s | 1.89× (1.85× – 1.92×) | faster |
| wide | latency p50 | 125 ns | 42 ns | 0.34× (0.34× – 0.66×) | faster |
| wide | latency p99 | 500 ns | 334 ns | 0.67× (0.47× – 0.73×) | faster |
| wide | latency p99.9 | 1.46 µs | 1.00 µs | 0.69× (0.69× – 0.71×) | faster |
| wide | heap allocations per command | 1.85 | 0.00 |  |  |

<details><summary>Microbenchmarks</summary>

| Benchmark (time per op) | Before | After | After / before | Verdict |
|---|---|---|---|---|
| `AddExistingLevel` | 29 ns | 17 ns | 0.58× (0.52× – 0.60×) | faster |
| `AddNewLevel` | 44 ns | 18 ns | 0.41× (0.41× – 0.43×) | faster |
| `CancelKeepsLevel` | 23 ns | 18 ns | 0.81× (0.80× – 0.81×) | faster |
| `CancelRemovesLevel` | 45 ns | 17 ns | 0.38× (0.38× – 0.39×) | faster |
| `ReplaceQtyDown` | 7 ns | 8 ns | 1.23× (1.12× – 1.24×) | slower |
| `ReplaceReprice` | 33 ns | 26 ns | 0.81× (0.74× – 0.83×) | faster |
| `MatchOneLevel/1` | 34 ns | 23 ns | 0.68× (0.57× – 0.69×) | faster |
| `MatchOneLevel/8` | 156 ns | 117 ns | 0.75× (0.70× – 0.76×) | faster |
| `MatchMultiLevel/2` | 71 ns | 53 ns | 0.75× (0.72× – 0.76×) | faster |
| `MatchMultiLevel/8` | 228 ns | 153 ns | 0.67× (0.66× – 0.68×) | faster |
| `MatchMultiLevel/32` | 872 ns | 1.23 µs | 1.41× (1.37× – 1.46×) | slower |
| `RiskCheck` | 11 ns | 12 ns | 1.08× (1.05× – 1.17×) | slower |
| `RiskCheckNoLimits` | 5 ns | 4 ns | 0.78× (0.73× – 0.82×) | faster |
| `EngineAdd` | 28 ns | 16 ns | 0.59× (0.53× – 0.66×) | faster |

</details>

End to end the final engine is 1.68× (narrow workload)
the baseline's throughput on the narrow workload and
1.89× (wide workload) on the wide one; heap
allocations per command went from 1.85 (wide workload)
to 0.00 (wide workload). Two lessons stand out:
fewer allocations is not the same as faster (opt-1, opt-3), and the largest win came from
the part of the engine the microbenchmarks made look cheap (opt-7).
