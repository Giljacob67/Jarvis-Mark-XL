"""
Quick STT test — run from terminal to verify voice recognition.
Usage: .venv/bin/python scripts/test_stt.py
"""
import numpy as np
import sounddevice as sd
import time

SAMPLE_RATE = 16000
BLOCK_SIZE = 1024

print("=" * 50)
print("STT Quick Test")
print("=" * 50)
print("Fale algo em português (3 segundos)...")
print("Gravando em 3... 2... 1...")
time.sleep(3)

print("Gravando... (3 segundos)")
audio = sd.rec(int(3 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype='float32')
sd.wait()
print("Gravado! Processando...")

# Test with current model
from core.stt import WhisperSTT
import json
from core.paths import API_CONFIG_PATH

cfg = json.loads(API_CONFIG_PATH.read_text())
model_name = cfg.get("stt_model", "medium")
lang = cfg.get("stt_language", "pt")

print(f"\nModelo: {model_name}")
print(f"Idioma: {lang}")
print("-" * 50)

stt = WhisperSTT(model_name, language=lang)
text = stt.transcribe(audio.flatten())

print(f"Resultado: \"{text}\"")
print(f"Tamanho: {len(text)} caracteres")
