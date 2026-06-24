"""Tests for core.tts — TTS player factory and engine selection."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_create_tts_player_edgetts():
    """EdgeTTS should be created without external dependencies."""
    with patch("core.tts.EdgeTTSEngine") as mock_engine:
        from core.tts import create_tts_player
        config = {"tts_engine": "edgetts", "tts_voice": "en-US-GuyNeural"}
        player = create_tts_player(config)
        mock_engine.assert_called_once_with(voice="en-US-GuyNeural")
        assert player._engine is mock_engine.return_value


def test_create_tts_player_kokoro():
    """Kokoro should use voice and speed from config."""
    with patch("core.tts.KokoroTTSEngine") as mock_engine:
        from core.tts import create_tts_player
        config = {"tts_engine": "kokoro", "tts_voice": "af_heart", "tts_speed": "1.2"}
        player = create_tts_player(config)
        mock_engine.assert_called_once_with(voice="af_heart", speed=1.2)


def test_create_tts_player_elevenlabs():
    """ElevenLabs should pass API key and voice settings."""
    with patch("core.tts.ElevenLabsTTSEngine") as mock_engine:
        from core.tts import create_tts_player
        config = {
            "tts_engine": "elevenlabs",
            "elevenlabs_api_key": "test-key",
            "tts_voice": "voice-123",
            "tts_stability": "0.6",
            "tts_similarity_boost": "0.8",
            "tts_style": "0.3",
            "tts_use_speaker_boost": False,
            "tts_speed": "1.1",
        }
        player = create_tts_player(config)
        mock_engine.assert_called_once_with(
            api_key="test-key",
            voice_id="voice-123",
            stability=0.6,
            similarity_boost=0.8,
            style=0.3,
            use_speaker_boost=False,
            speed=1.1,
        )


def test_tts_player_is_playing():
    """TTSPlayer should track playing state."""
    from core.tts import TTSPlayer
    mock_engine = MagicMock()
    player = TTSPlayer(mock_engine)
    assert player.is_playing is False


def test_tts_player_stop():
    """TTSPlayer stop should reset playing state."""
    from core.tts import TTSPlayer
    mock_engine = MagicMock()
    player = TTSPlayer(mock_engine)
    player.stop()
    assert player.is_playing is False
