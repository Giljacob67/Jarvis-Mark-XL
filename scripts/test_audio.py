"""
MARK XL — Audio Test utility.

Quick test for microphone and TTS.
Run from terminal: .venv/bin/python scripts/test_audio.py
Or access from web dashboard: http://localhost:5050/audio_test
"""
from __future__ import annotations

import json
import time

import numpy as np
import sounddevice as sd

from core.paths import API_CONFIG_PATH
from core.logger import get_logger

log = get_logger("audio_test")

SAMPLE_RATE = 16_000


def test_microphone(duration: float = 3.0) -> dict:
    """Record audio and return stats."""
    print(f"Gravando {duration}s de áudio...")
    audio = sd.rec(
        int(duration * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
    )
    sd.wait()

    rms = float(np.sqrt(np.mean(audio ** 2)))
    peak = float(np.max(np.abs(audio)))
    duration_actual = len(audio) / SAMPLE_RATE

    result = {
        "duration": round(duration_actual, 2),
        "rms": round(rms, 6),
        "peak": round(peak, 6),
        "status": "OK" if rms > 0.001 else "SILENT",
    }

    print(f"Duração: {result['duration']}s")
    print(f"RMS: {result['rms']} (energia)")
    print(f"Peak: {result['peak']} (amplitude máxima)")
    print(f"Status: {result['status']}")

    if result["status"] == "SILENT":
        print("\n⚠️  Microfone não está captando áudio!")
        print("   Verifique: Preferências → Som → Entrada")
    else:
        print("\n✅ Microfone funcionando!")

    return result


def test_whisper(audio: np.ndarray | None = None) -> str:
    """Test Whisper STT with recorded audio."""
    if audio is None:
        print("Gravando 3s para teste de Whisper...")
        audio = sd.rec(
            int(3 * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
        )
        sd.wait()

    cfg = json.loads(API_CONFIG_PATH.read_text())
    model_name = cfg.get("stt_model", "medium")
    lang = cfg.get("stt_language", "pt")

    print(f"Testando Whisper ({model_name}, {lang})...")

    from core.stt import WhisperSTT
    stt = WhisperSTT(model_name, language=lang)

    t0 = time.time()
    text = stt.transcribe(audio.flatten())
    elapsed = time.time() - t0

    print(f"Resultado: \"{text}\"")
    print(f"Tempo: {elapsed:.1f}s")

    return text


def test_tts(text: str = "Olá! Sou o Jarvis, seu assistente pessoal.") -> bool:
    """Test TTS engine."""
    cfg = json.loads(API_CONFIG_PATH.read_text())
    engine = cfg.get("tts_engine", "edgetts")
    voice = cfg.get("tts_voice", "pt-BR-AntonioNeural")

    print(f"Testando TTS ({engine}, {voice})...")
    print(f"Texto: \"{text}\"")

    try:
        from core.tts import create_tts_player
        player = create_tts_player(cfg)
        t0 = time.time()
        player.speak(text)
        elapsed = time.time() - t0
        print(f"TTS OK! ({elapsed:.1f}s)")
        return True
    except Exception as e:
        print(f"TTS Error: {e}")
        return False


def test_llm(prompt: str = "Diga hello") -> str:
    """Test LLM response."""
    from core.llm_client import call_llm_stream, ensure_ollama_running

    ensure_ollama_running()

    cfg = json.loads(API_CONFIG_PATH.read_text())
    model = cfg.get("llm_model", "qwen3.5:2b")

    print(f"Testando LLM ({model})...")

    messages = [{"role": "user", "content": prompt}]
    t0 = time.time()

    content = ""
    for event in call_llm_stream(messages):
        if event["type"] == "done":
            content = event["content"]
            break

    elapsed = time.time() - t0
    print(f"Resposta: \"{content[:100]}\"")
    print(f"Tempo: {elapsed:.1f}s")

    return content


def run_full_test():
    """Run all audio tests."""
    print("=" * 50)
    print("MARK XL — Teste de Áudio Completo")
    print("=" * 50)

    print("\n1. Teste de Microfone")
    print("-" * 30)
    mic_result = test_microphone()

    print("\n2. Teste de Whisper STT")
    print("-" * 30)
    if mic_result["status"] != "SILENT":
        audio = sd.rec(int(3 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
        sd.wait()
        whisper_text = test_whisper(audio)
    else:
        print("Pulando Whisper (microfone silencioso)")

    print("\n3. Teste de TTS")
    print("-" * 30)
    test_tts()

    print("\n4. Teste de LLM")
    print("-" * 30)
    test_llm()

    print("\n" + "=" * 50)
    print("Teste completo!")
    print("=" * 50)


if __name__ == "__main__":
    run_full_test()
