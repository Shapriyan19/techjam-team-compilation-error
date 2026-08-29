from __future__ import annotations

import argparse
import ctypes
import json
import os
import statistics
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
from starter.agent import Agent


def _rss_bytes() -> int | None:
    if os.name == "nt":
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCounters),
            ctypes.c_ulong,
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        handle = kernel32.GetCurrentProcess()
        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
        return None
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if value > 10_000_000 else value * 1024)
    except (ImportError, AttributeError):
        return None


class PeakMemorySampler:
    def __init__(self, interval_seconds: float = 0.05) -> None:
        self.interval_seconds = interval_seconds
        self.peak_bytes = _rss_bytes() or 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1.0)
        self.sample()

    def sample(self) -> int | None:
        value = _rss_bytes()
        if value is not None:
            self.peak_bytes = max(self.peak_bytes, value)
        return value

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.sample()


class TimedAgent(Agent):
    """Evaluator-compatible Agent that records end-to-end respond latency."""

    def __init__(self, *args, **kwargs) -> None:
        started = time.perf_counter()
        super().__init__(*args, **kwargs)
        self.measured_startup_seconds = time.perf_counter() - started
        self.respond_latencies_ms: list[float] = []
        self.reset_order: list[str] = []

    def reset(self, session_id: str, user_profile: dict) -> None:
        super().reset(session_id, user_profile)
        self.reset_order.append(session_id)

    def respond(self, *args, **kwargs) -> dict:
        started = time.perf_counter()
        try:
            return super().respond(*args, **kwargs)
        finally:
            self.respond_latencies_ms.append((time.perf_counter() - started) * 1000.0)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(round((len(ordered) - 1) * fraction), len(ordered) - 1)
    return ordered[index]


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _component_summary(records: list[dict]) -> dict:
    grouped: dict[str, list[float]] = defaultdict(list)
    failures: dict[str, int] = defaultdict(int)
    fallbacks: dict[str, int] = defaultdict(int)
    for record in records:
        component = str(record["component"])
        grouped[component].append(float(record["elapsed_ms"]))
        failures[component] += int(not record["success"])
        fallbacks[component] += int(record["fallback_used"])
    return {
        component: {
            "count": len(values),
            "average_ms": _rounded(statistics.fmean(values)),
            "p95_ms": _rounded(_percentile(values, 0.95)),
            "max_ms": _rounded(max(values)),
            "failures": failures[component],
            "fallbacks": fallbacks[component],
        }
        for component, values in sorted(grouped.items())
    }


def _behavior_diagnostics(agent: TimedAgent, samples: list[dict]) -> dict:
    sample_by_session = {
        session_id: sample
        for session_id, sample in zip(agent.reset_order, samples)
    }
    question_attributes: Counter[str] = Counter()
    question_api_attributes: Counter[str] = Counter()
    question_turns: Counter[int] = Counter()
    question_scenarios: Counter[str] = Counter()
    turn_outputs: list[dict] = []
    for session_id, sample in sample_by_session.items():
        state = agent.session_state(session_id)
        for turn in state.phase4_turn_history:
            attribute = turn.get("ask_attribute")
            if attribute:
                question_attributes[str(attribute)] += 1
                question_api_attributes[str(turn.get("api_attribute"))] += 1
                question_turns[int(turn["turn"])] += 1
                question_scenarios[str(sample["scenario_type"])] += 1
            turn_outputs.append({
                "sample_id": sample["sample_id"],
                "turn": turn["turn"],
                "ask_attribute": attribute,
                "api_attribute": turn.get("api_attribute"),
                "recommendations": turn.get("recommendations", []),
            })

    scenario_by_session = {
        session_id: str(sample["scenario_type"])
        for session_id, sample in sample_by_session.items()
    }
    activated = [item for item in agent.topk_history if item["activated"]]
    changed_positions: Counter[int] = Counter()
    activation_scenarios: Counter[str] = Counter()
    for item in activated:
        changed_positions.update(int(position) for position in item["changed_positions"])
        activation_scenarios[scenario_by_session.get(item["session_id"], "unknown")] += 1
    return {
        "questions": {
            "count": sum(question_attributes.values()),
            "attributes": dict(sorted(question_attributes.items())),
            "api_attributes": dict(sorted(question_api_attributes.items())),
            "turns": {str(key): value for key, value in sorted(question_turns.items())},
            "scenarios": dict(sorted(question_scenarios.items())),
        },
        "topk": {
            "turn_count": len(agent.topk_history),
            "activation_count": len(activated),
            "changed_positions": {str(key): value for key, value in sorted(changed_positions.items())},
            "activation_scenarios": dict(sorted(activation_scenarios.items())),
            "activations": activated,
        },
        "turn_outputs": turn_outputs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 5 official-evaluator latency benchmark")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--output", default="artifacts/evaluation/phase5_benchmark_result.json")
    parser.add_argument("--timing-output", default="artifacts/evaluation/phase5_benchmark_timing.json")
    parser.add_argument("--diagnostic-output", default="artifacts/evaluation/phase5_benchmark_diagnostics.json")
    args = parser.parse_args()

    memory = PeakMemorySampler()
    memory.start()
    memory_before_load = memory.sample()
    samples = load_jsonl(args.dataset)
    catalog_ids, categories, products = catalog_index(args.catalog)
    memory_after_evaluator_catalog = memory.sample()
    agent = TimedAgent(args.catalog)
    memory_after_agent_startup = memory.sample()
    started = time.perf_counter()
    try:
        result = evaluate(agent, samples, catalog_ids, categories, products)
        runtime_stats = agent.runtime_stats()
        component_timings = _component_summary(agent.trace_recorder.records())
        behavior_diagnostics = _behavior_diagnostics(agent, samples)
    finally:
        evaluator_seconds = time.perf_counter() - started
        agent.close()
        memory.stop()

    latencies = agent.respond_latencies_ms
    timing = {
        "startup_seconds": _rounded(agent.measured_startup_seconds),
        "evaluator_wall_seconds": _rounded(evaluator_seconds),
        "respond_count": len(latencies),
        "respond_average_ms": _rounded(statistics.fmean(latencies) if latencies else None),
        "respond_p50_ms": _rounded(statistics.median(latencies) if latencies else None),
        "respond_p95_ms": _rounded(_percentile(latencies, 0.95)),
        "respond_max_ms": _rounded(max(latencies) if latencies else None),
        "runtime_stats": runtime_stats,
        "component_timings": component_timings,
        "memory": {
            "before_load_bytes": memory_before_load,
            "after_evaluator_catalog_bytes": memory_after_evaluator_catalog,
            "after_agent_startup_bytes": memory_after_agent_startup,
            "peak_process_rss_bytes": memory.peak_bytes,
            "peak_process_rss_mib": round(memory.peak_bytes / (1024 * 1024), 3),
        },
    }
    output_path = Path(args.output)
    timing_path = Path(args.timing_output)
    diagnostic_path = Path(args.diagnostic_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    timing_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    timing_path.write_text(json.dumps(timing, indent=2) + "\n", encoding="utf-8")
    diagnostic_path.write_text(json.dumps(behavior_diagnostics, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"metrics": {key: value for key, value in result.items() if key != "sessions"}, "timing": timing}, indent=2))


if __name__ == "__main__":
    main()
