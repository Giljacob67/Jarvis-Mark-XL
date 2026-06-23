"""
MARK XL — LLM Latency Benchmark with qwen3.5:9b.

Tests actual end-to-end latency including Ollama inference.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

MODEL = "qwen3.5:9b"
URL = "http://localhost:11434"


def banner(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_ollama_ping() -> float:
    """Measure Ollama API latency (no LLM)."""
    times = []
    for _ in range(10):
        t0 = time.perf_counter()
        r = requests.get(f"{URL}/api/tags", timeout=5)
        elapsed = (time.perf_counter() - t0) * 1000
        times.append(elapsed)
    avg = statistics.mean(times)
    print(f"  Ollama API ping: avg={avg:.1f}ms")
    return avg


def test_model_load() -> float:
    """Measure model load time (first request after unload)."""
    print(f"  Loading {MODEL}...")
    t0 = time.perf_counter()
    r = requests.post(f"{URL}/api/chat", json={
        "model": MODEL,
        "messages": [{"role": "user", "content": "hi"}],
        "stream": False,
        "keep_alive": -1,
        "options": {"num_predict": 1, "num_ctx": 4096, "num_gpu": 99},
    }, timeout=300)
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"  Model load + first token: {elapsed:.0f}ms")
    return elapsed


def test_first_token_latency() -> dict:
    """Measure time to first token (TTFT) with streaming."""
    prompts = [
        "What is 2+2?",
        "Say hello in one sentence.",
        "What time is it?",
        "Tell me a joke.",
        "What is Python?",
    ]

    ttft_times = []
    total_times = []

    for prompt in prompts:
        # TTFT
        t0 = time.perf_counter()
        first_token_time = None
        full_response = ""

        r = requests.post(f"{URL}/api/chat", json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "keep_alive": -1,
            "options": {"num_predict": 100, "num_ctx": 4096, "num_gpu": 99},
        }, timeout=60, stream=True)

        for line in r.iter_lines():
            if not line:
                continue
            try:
                chunk = json.loads(line)
                content = chunk.get("message", {}).get("content", "")
                if content and first_token_time is None:
                    first_token_time = time.perf_counter()
                    ttft_times.append((first_token_time - t0) * 1000)
                full_response += content
                if chunk.get("done"):
                    total_times.append((time.perf_counter() - t0) * 1000)
                    break
            except json.JSONDecodeError:
                continue

    result = {
        "ttft_avg_ms": statistics.mean(ttft_times),
        "ttft_min_ms": min(ttft_times),
        "ttft_max_ms": max(ttft_times),
        "ttft_p95_ms": sorted(ttft_times)[int(len(ttft_times) * 0.95)],
        "total_avg_ms": statistics.mean(total_times),
        "total_min_ms": min(total_times),
        "total_max_ms": max(total_times),
    }

    print(f"  TTFT (time to first token):")
    print(f"    avg={result['ttft_avg_ms']:.0f}ms, min={result['ttft_min_ms']:.0f}ms, "
          f"max={result['ttft_max_ms']:.0f}ms, p95={result['ttft_p95_ms']:.0f}ms")
    print(f"  Total response time:")
    print(f"    avg={result['total_avg_ms']:.0f}ms, min={result['total_min_ms']:.0f}ms, "
          f"max={result['total_max_ms']:.0f}ms")

    return result


def test_tool_calling_latency() -> dict:
    """Measure latency for tool-calling responses."""
    tools = [{
        "type": "function",
        "function": {
            "name": "weather_report",
            "description": "Get weather for a city",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name"}
                },
                "required": ["city"]
            }
        }
    }]

    prompts = [
        "What's the weather in London?",
        "How's the weather in Tokyo?",
        "Check the weather in New York.",
    ]

    ttft_times = []
    total_times = []

    for prompt in prompts:
        t0 = time.perf_counter()
        first_token_time = None

        r = requests.post(f"{URL}/api/chat", json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "keep_alive": -1,
            "tools": tools,
            "options": {"num_predict": 200, "num_ctx": 4096, "num_gpu": 99},
        }, timeout=60, stream=True)

        for line in r.iter_lines():
            if not line:
                continue
            try:
                chunk = json.loads(line)
                content = chunk.get("message", {}).get("content", "")
                tc = chunk.get("message", {}).get("tool_calls")
                if (content or tc) and first_token_time is None:
                    first_token_time = time.perf_counter()
                    ttft_times.append((first_token_time - t0) * 1000)
                if chunk.get("done"):
                    total_times.append((time.perf_counter() - t0) * 1000)
                    break
            except json.JSONDecodeError:
                continue

    result = {
        "ttft_avg_ms": statistics.mean(ttft_times) if ttft_times else 0,
        "total_avg_ms": statistics.mean(total_times) if total_times else 0,
    }

    print(f"  Tool calling TTFT: avg={result['ttft_avg_ms']:.0f}ms")
    print(f"  Tool calling total: avg={result['total_avg_ms']:.0f}ms")

    return result


def test_conversation_latency() -> dict:
    """Measure latency with conversation history (realistic scenario)."""
    messages = [
        {"role": "system", "content": "You are JARVIS, a helpful AI assistant. Be concise."},
        {"role": "user", "content": "My name is Gilberto."},
        {"role": "assistant", "content": "Nice to meet you, Gilberto! How can I help?"},
        {"role": "user", "content": "What's the weather like?"},
        {"role": "assistant", "content": "I'd need to know your location to check the weather."},
        {"role": "user", "content": "I'm in São Paulo, Brazil."},
        {"role": "assistant", "content": "Got it! Let me check the weather in São Paulo."},
    ]

    user_prompts = [
        "What was my name again?",
        "Where am I located?",
        "Thanks!",
    ]

    ttft_times = []
    total_times = []

    for prompt in user_prompts:
        msgs = messages + [{"role": "user", "content": prompt}]

        t0 = time.perf_counter()
        first_token_time = None

        r = requests.post(f"{URL}/api/chat", json={
            "model": MODEL,
            "messages": msgs,
            "stream": True,
            "keep_alive": -1,
            "options": {"num_predict": 150, "num_ctx": 4096, "num_gpu": 99},
        }, timeout=60, stream=True)

        for line in r.iter_lines():
            if not line:
                continue
            try:
                chunk = json.loads(line)
                content = chunk.get("message", {}).get("content", "")
                if content and first_token_time is None:
                    first_token_time = time.perf_counter()
                    ttft_times.append((first_token_time - t0) * 1000)
                if chunk.get("done"):
                    total_times.append((time.perf_counter() - t0) * 1000)
                    break
            except json.JSONDecodeError:
                continue

    result = {
        "ttft_avg_ms": statistics.mean(ttft_times) if ttft_times else 0,
        "total_avg_ms": statistics.mean(total_times) if total_times else 0,
    }

    print(f"  Conversation TTFT: avg={result['ttft_avg_ms']:.0f}ms")
    print(f"  Conversation total: avg={result['total_avg_ms']:.0f}ms")

    return result


def test_tokens_per_second() -> dict:
    """Measure generation speed (tokens/sec)."""
    r = requests.post(f"{URL}/api/chat", json={
        "model": MODEL,
        "messages": [{"role": "user", "content": "Write a 100-word essay about AI."}],
        "stream": False,
        "keep_alive": -1,
        "options": {"num_predict": 200, "num_ctx": 4096, "num_gpu": 99},
    }, timeout=120)

    data = r.json()
    eval_count = data.get("eval_count", 0)
    eval_duration_ns = data.get("eval_duration", 1)

    tokens_per_sec = eval_count / (eval_duration_ns / 1e9) if eval_duration_ns > 0 else 0

    print(f"  Tokens generated: {eval_count}")
    print(f"  Generation time: {eval_duration_ns / 1e6:.0f}ms")
    print(f"  Speed: {tokens_per_sec:.1f} tokens/sec")

    return {
        "tokens": eval_count,
        "duration_ms": eval_duration_ns / 1e6,
        "tokens_per_sec": tokens_per_sec,
    }


def main():
    banner(f"LLM Latency Benchmark — {MODEL}")

    print(f"Model: {MODEL}")
    print(f"URL: {URL}")
    print(f"Hardware: Apple Silicon (Metal)")

    results = {}

    print("\n[1/6] Ollama API Ping")
    results["ping"] = test_ollama_ping()

    print(f"\n[2/6] Model Load ({MODEL})")
    results["load"] = test_model_load()

    print(f"\n[3/6] First Token Latency (simple prompts)")
    results["ttft"] = test_first_token_latency()

    print(f"\n[4/6] Tool Calling Latency")
    results["tools"] = test_tool_calling_latency()

    print(f"\n[5/6] Conversation Latency (with history)")
    results["conversation"] = test_conversation_latency()

    print(f"\n[6/6] Generation Speed")
    results["speed"] = test_tokens_per_second()

    # Summary
    banner("Summary — qwen3.5:9b on Mac")

    print("  Metric                      | Value")
    print("  ----------------------------|----------------")
    print(f"  Ollama API ping             | {results['ping']:.1f}ms")
    print(f"  Model load (first request)  | {results['load']:.0f}ms")
    print(f"  TTFT (simple prompts)       | {results['ttft']['ttft_avg_ms']:.0f}ms avg")
    print(f"  TTFT (tool calling)         | {results['tools']['ttft_avg_ms']:.0f}ms avg")
    print(f"  TTFT (conversation)         | {results['conversation']['ttft_avg_ms']:.0f}ms avg")
    print(f"  Total (simple prompts)      | {results['ttft']['total_avg_ms']:.0f}ms avg")
    print(f"  Total (tool calling)        | {results['tools']['total_avg_ms']:.0f}ms avg")
    print(f"  Total (conversation)        | {results['conversation']['total_avg_ms']:.0f}ms avg")
    print(f"  Generation speed            | {results['speed']['tokens_per_sec']:.1f} tok/s")
    print()

    # Save
    out_path = Path(__file__).resolve().parent.parent / "benchmark_llm_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  Results saved to: {out_path}")


if __name__ == "__main__":
    main()
