"""
MARK XL — Chat Latency Benchmark.

Tests response times for:
  1. Dashboard WebSocket message delivery
  2. Dashboard server startup time
  3. Tool execution latency
  4. LLM cache hit/miss performance
  5. End-to-end message processing time

Usage: python scripts/benchmark_latency.py
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def banner(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_dashboard_startup() -> float:
    """Measure how fast the dashboard server initializes."""
    from dashboard.server import DashboardServer

    t0 = time.perf_counter()
    server = DashboardServer()
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"  Dashboard init: {elapsed:.1f}ms")
    return elapsed


def test_dashboard_broadcast_latency() -> dict:
    """Measure broadcast latency to connected WebSocket clients."""
    from dashboard.server import DashboardServer

    server = DashboardServer()

    async def _measure():
        times = []
        for i in range(10):
            t0 = time.perf_counter()
            await server.broadcast({"type": "test", "seq": i})
            elapsed = (time.perf_counter() - t0) * 1000
            times.append(elapsed)

        return {
            "avg_ms": statistics.mean(times),
            "min_ms": min(times),
            "max_ms": max(times),
            "p95_ms": sorted(times)[int(len(times) * 0.95)],
        }

    result = asyncio.run(_measure())
    print(f"  Broadcast (no clients): avg={result['avg_ms']:.2f}ms, "
          f"min={result['min_ms']:.2f}ms, max={result['max_ms']:.2f}ms")
    return result


def test_aes_encryption_latency() -> dict:
    """Measure AES-256-CBC encryption/decryption time."""
    from dashboard.server import _derive_key, _decrypt_cbc
    import base64

    key = _derive_key("TESTKEY")

    # Simulate encryption (client-side)
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_pad
    import os

    def encrypt(plaintext: str) -> str:
        iv = os.urandom(16)
        padder = sym_pad.PKCS7(128).padder()
        padded = padder.update(plaintext.encode()) + padder.finalize()
        enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        ct = enc.update(padded) + enc.finalize()
        return base64.b64encode(iv + ct).decode()

    plaintext = "Hello JARVIS, what's the weather today?"
    times_enc = []
    times_dec = []

    for _ in range(100):
        t0 = time.perf_counter()
        encrypted = encrypt(plaintext)
        times_enc.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        _decrypt_cbc(key, encrypted)
        times_dec.append((time.perf_counter() - t0) * 1000)

    result = {
        "encrypt_avg_ms": statistics.mean(times_enc),
        "decrypt_avg_ms": statistics.mean(times_dec),
        "encrypt_p95_ms": sorted(times_enc)[95],
        "decrypt_p95_ms": sorted(times_dec)[95],
    }
    print(f"  AES-256 encrypt: avg={result['encrypt_avg_ms']:.3f}ms, "
          f"p95={result['encrypt_p95_ms']:.3f}ms")
    print(f"  AES-256 decrypt: avg={result['decrypt_avg_ms']:.3f}ms, "
          f"p95={result['decrypt_p95_ms']:.3f}ms")
    return result


def test_performance_cache() -> dict:
    """Measure cache hit/miss performance."""
    from core.performance import ResponseCache

    cache = ResponseCache(max_size=100, ttl_seconds=60)

    # Fill cache
    for i in range(50):
        cache.put(f"query_{i}", f"response_{i}")

    # Miss latency
    miss_times = []
    for i in range(50, 100):
        t0 = time.perf_counter()
        cache.get(f"query_{i}")
        miss_times.append((time.perf_counter() - t0) * 1000)

    # Hit latency
    hit_times = []
    for i in range(50):
        t0 = time.perf_counter()
        cache.get(f"query_{i}")
        hit_times.append((time.perf_counter() - t0) * 1000)

    result = {
        "hit_avg_ms": statistics.mean(hit_times),
        "miss_avg_ms": statistics.mean(miss_times),
        "hit_p95_ms": sorted(hit_times)[47],
        "miss_p95_ms": sorted(miss_times)[47],
    }
    print(f"  Cache hit:  avg={result['hit_avg_ms']:.4f}ms, "
          f"p95={result['hit_p95_ms']:.4f}ms")
    print(f"  Cache miss: avg={result['miss_avg_ms']:.4f}ms, "
          f"p95={result['miss_p95_ms']:.4f}ms")
    return result


def test_conversation_trimming() -> dict:
    """Measure conversation trimming performance."""
    from core.performance import trim_conversation

    # Build a large conversation
    conversation = []
    for i in range(100):
        conversation.append({"role": "user", "content": f"Message {i}"})
        conversation.append({"role": "assistant", "content": f"Response {i}"})

    times = []
    for _ in range(50):
        t0 = time.perf_counter()
        trim_conversation(conversation, max_turns=12)
        times.append((time.perf_counter() - t0) * 1000)

    result = {
        "avg_ms": statistics.mean(times),
        "p95_ms": sorted(times)[47],
    }
    print(f"  Trim 200 msgs → 12 turns: avg={result['avg_ms']:.3f}ms, "
          f"p95={result['p95_ms']:.3f}ms")
    return result


def test_idle_detection() -> dict:
    """Measure idle detection check latency."""
    from core.performance import IdleDetector

    detector = IdleDetector(idle_threshold_sec=1.0)

    times = []
    for _ in range(1000):
        t0 = time.perf_counter()
        detector.check_idle()
        times.append((time.perf_counter() - t0) * 1000)

    result = {
        "avg_us": statistics.mean(times) * 1000,  # microseconds
        "p95_us": sorted(times)[950] * 1000,
    }
    print(f"  Idle check: avg={result['avg_us']:.1f}µs, "
          f"p95={result['p95_us']:.1f}µs")
    return result


def test_vad_latency() -> dict:
    """Measure VAD processing latency per audio chunk."""
    import numpy as np
    from main import _VADBuffer

    vad = _VADBuffer()
    chunk = np.random.randn(1024).astype(np.float32) * 0.01

    times = []
    for _ in range(500):
        t0 = time.perf_counter()
        vad.process(chunk)
        times.append((time.perf_counter() - t0) * 1000)

    result = {
        "avg_us": statistics.mean(times) * 1000,
        "p95_us": sorted(times)[475] * 1000,
        "max_us": max(times) * 1000,
    }
    print(f"  VAD (1024 samples): avg={result['avg_us']:.1f}µs, "
          f"p95={result['p95_us']:.1f}µs, max={result['max_us']:.1f}µs")
    return result


def test_memory_load() -> dict:
    """Measure memory loading performance."""
    from memory.memory_manager import load_memory

    times = []
    for _ in range(20):
        t0 = time.perf_counter()
        load_memory()
        times.append((time.perf_counter() - t0) * 1000)

    result = {
        "avg_ms": statistics.mean(times),
        "p95_ms": sorted(times)[19],
    }
    print(f"  Load memory: avg={result['avg_ms']:.2f}ms, "
          f"p95={result['p95_ms']:.2f}ms")
    return result


def test_system_prompt_build() -> dict:
    """Measure system prompt construction time."""
    from main import JarvisLocal, _load_config, _load_system_prompt
    from memory.memory_manager import load_memory, format_memory_for_prompt
    from datetime import datetime

    times = []
    for _ in range(20):
        t0 = time.perf_counter()
        sys_p = _load_system_prompt()
        memory = load_memory()
        mem_str = format_memory_for_prompt(memory)
        now = datetime.now()
        time_ctx = f"Right now it is: {now.strftime('%A, %B %d, %Y — %I:%M %p')}"
        parts = [sys_p]
        if mem_str:
            parts.append(mem_str)
        try:
            from actions.location import get_context_string
            loc_ctx = get_context_string()
            if loc_ctx:
                parts.append(loc_ctx)
        except Exception:
            pass
        try:
            from memory.preferences import format_preferences_for_prompt
            prefs_ctx = format_preferences_for_prompt()
            if prefs_ctx:
                parts.append(prefs_ctx)
        except Exception:
            pass
        parts.append(time_ctx)
        prompt = "\n\n".join(parts)
        elapsed = (time.perf_counter() - t0) * 1000
        times.append(elapsed)

    result = {
        "avg_ms": statistics.mean(times),
        "p95_ms": sorted(times)[19],
        "prompt_len": len(prompt),
    }
    print(f"  Build system prompt: avg={result['avg_ms']:.2f}ms, "
          f"p95={result['p95_ms']:.2f}ms, len={result['prompt_len']} chars")
    return result


def main():
    banner("MARK XL — Chat Latency Benchmark")

    print("Testing component latencies...\n")

    results = {}

    print("[1/8] Dashboard Server Startup")
    results["dashboard_startup"] = test_dashboard_startup()

    print("\n[2/8] Dashboard Broadcast Latency")
    results["broadcast"] = test_dashboard_broadcast_latency()

    print("\n[3/8] AES-256 Encryption Latency")
    results["aes"] = test_aes_encryption_latency()

    print("\n[4/8] Performance Cache")
    results["cache"] = test_performance_cache()

    print("\n[5/8] Conversation Trimming")
    results["trimming"] = test_conversation_trimming()

    print("\n[6/8] Idle Detection")
    results["idle"] = test_idle_detection()

    print("\n[7/8] VAD Processing")
    results["vad"] = test_vad_latency()

    print("\n[8/8] Memory + System Prompt")
    results["memory"] = test_memory_load()
    results["system_prompt"] = test_system_prompt_build()

    # Summary
    banner("Summary — Expected End-to-End Latency")

    print("  Component                    | Latency")
    print("  -----------------------------|----------------")
    print(f"  Dashboard broadcast          | {results['broadcast']['avg_ms']:.2f}ms")
    print(f"  AES-256 encrypt              | {results['aes']['encrypt_avg_ms']:.3f}ms")
    print(f"  AES-256 decrypt              | {results['aes']['decrypt_avg_ms']:.3f}ms")
    print(f"  Cache lookup                 | {results['cache']['hit_avg_ms']:.4f}ms")
    print(f"  VAD (per 64ms chunk)         | {results['vad']['avg_us']:.1f}µs")
    print(f"  System prompt build          | {results['system_prompt']['avg_ms']:.2f}ms")
    print(f"  Memory load                  | {results['memory']['avg_ms']:.2f}ms")
    print()
    print("  Estimated End-to-End (no LLM):")
    no_llm = (results['broadcast']['avg_ms'] +
              results['aes']['decrypt_avg_ms'] +
              results['system_prompt']['avg_ms'])
    print(f"    Dashboard → Decrypt → Prompt: ~{no_llm:.1f}ms")
    print()
    print("  Note: LLM inference time (Ollama) is the dominant factor.")
    print("  Typical Ollama latency: 200ms-2s depending on model and hardware.")
    print("  With streaming, first token arrives in ~200-500ms.")

    # Save results
    out_path = Path(__file__).resolve().parent.parent / "benchmark_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Results saved to: {out_path}")


if __name__ == "__main__":
    main()
