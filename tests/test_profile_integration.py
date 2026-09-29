"""Integration test for profile switching with systemd/launcher."""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from gtx_broker.scheduler.model_profiles import P40ModelProfileController


class TestProfileLauncherIntegration:
    """Test that launcher reads from active symlink."""
    
    def test_launcher_reads_active_symlink(self, tmp_path):
        """Launcher resolves /etc/llama-cpp/p40-active.conf to get profile."""
        active_config = tmp_path / "p40-active.conf"
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        
        # Create coding profile
        (profiles_dir / "qwen35-coding.conf").write_text("""
MODEL_PATH=/path/to/coding.gguf
CHAT_TEMPLATE=qwen3.5
QUANTIZATION=Q3_K_XL
GPU_LAYERS=99
CONTEXT_SIZE=262144
SERVER_SLOTS=1
MODEL_ID=qwen3.5-35b-ud-q3_k_xl
""")
        
        # Create vision profile with projector
        (profiles_dir / "qwen35-vision.conf").write_text("""
MODEL_PATH=/path/to/vision.gguf
CHAT_TEMPLATE=qwen3.5-vision
QUANTIZATION=Q2_K
GPU_LAYERS=99
CONTEXT_SIZE=262144
SERVER_SLOTS=1
MODEL_ID=qwen3.5-35b-vision-q2_k
MMPROJ_PATH=/path/to/mmproj.mmproj
MMPROJ_TYPE=bf16
""")
        
        # Set active config to coding
        active_config.symlink_to(profiles_dir / "qwen35-coding.conf")
        
        with patch("gtx_broker.scheduler.model_profiles.P40ModelProfileController.PROFILES_DIR", profiles_dir):
            controller = P40ModelProfileController()
            controller._load_profiles_from_conf()
            
            # Manually test the symlink resolution logic from launcher
            resolved = os.readlink(active_config)
            expected_path = str(profiles_dir / "qwen35-coding.conf")
            assert resolved == expected_path
            
            # Switch to vision
            active_config.unlink()
            active_config.symlink_to(profiles_dir / "qwen35-vision.conf")
            
            resolved = os.readlink(active_config)
            expected_path = str(profiles_dir / "qwen35-vision.conf")
            assert resolved == expected_path
    
    def test_launcher_passes_configured_model_alias(self):
        """The launcher must expose MODEL_ID through the OpenAI API alias."""
        launcher = Path(__file__).parents[1] / "scripts" / "p40-profile-launcher"
        assert '"--alias" "${MODEL_ID}"' in launcher.read_text()

    def test_launcher_executes_with_vision_args(self, tmp_path):
        """Integration test: launcher resolves symlink and generates correct arguments."""
        active_config = tmp_path / "p40-active.conf"
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        
        # Create vision profile with projector
        (profiles_dir / "qwen35-vision.conf").write_text("""
MODEL_PATH=/path/to/vision.gguf
CHAT_TEMPLATE=qwen3.5-vision
QUANTIZATION=Q2_K
GPU_LAYERS=99
CONTEXT_SIZE=262144
SERVER_SLOTS=1
MODEL_ID=qwen3.5-35b-vision-q2_k
MMPROJ_PATH=/path/to/mmproj.mmproj
MMPROJ_TYPE=bf16
""")
        
        active_config.symlink_to(profiles_dir / "qwen35-vision.conf")
        
        # Create mock launcher
        mock_launcher = tmp_path / "mock-launcher"
        mock_launcher.write_text(f"""#!/bin/bash
ACTIVE_CONFIG="{active_config}"
CONF_FILE=$(readlink -f "$ACTIVE_CONFIG")
source "$CONF_FILE"
echo "Args: --model $MODEL_PATH --mmproj $MMPROJ_PATH" > /tmp/launcher_args.txt
""")
        mock_launcher.chmod(0o755)
        
        # Execute launcher
        result = subprocess.run([str(mock_launcher)], capture_output=True, text=True)
        
        # Read generated args
        with open("/tmp/launcher_args.txt") as f:
            args = f.read()
        
        # Verify vision args include mmproj and correct profile path
        assert "qwen35-vision.conf" in str(active_config.resolve())
        assert "--mmproj" in args
        assert "/path/to/mmproj.mmproj" in args


class TestProfileControllerWithActiveConfig:
    """Test controller using active symlink."""
    
    def test_switch_updates_active_symlink(self, tmp_path):
        """ensure_profile() triggers wrapper which updates p40-active.conf symlink."""
        profiles_dir = tmp_path / "profiles"
        profiles_dir.mkdir()
        
        (profiles_dir / "qwen35-coding.conf").write_text("""
MODEL_PATH=/path/to/coding.gguf
CHAT_TEMPLATE=qwen3.5
QUANTIZATION=Q3_K_XL
GPU_LAYERS=99
CONTEXT_SIZE=262144
SERVER_SLOTS=1
MODEL_ID=qwen3.5-35b-ud-q3_k_xl
""")
        
        (profiles_dir / "qwen35-vision.conf").write_text("""
MODEL_PATH=/path/to/vision.gguf
CHAT_TEMPLATE=qwen3.5-vision
GPU_LAYERS=99
CONTEXT_SIZE=262144
SERVER_SLOTS=1
MODEL_ID=qwen3.5-35b-vision-q2_k
MMPROJ_PATH=/path/to/mmproj.mmproj
MMPROJ_TYPE=bf16
""")
        
        active_config = tmp_path / "p40-active.conf"
        
        controller = P40ModelProfileController()
        with patch.object(controller, 'PROFILES_DIR', profiles_dir):
            controller._load_profiles_from_conf()
            controller._profiles["qwen35-coding"].expected_model_id = "qwen3.5-35b-ud-q3_k_xl"
            controller._profiles["qwen35-vision"].expected_model_id = "qwen3.5-35b-vision-q2_k"
        
        # Set initial active config to coding
        active_config.symlink_to(profiles_dir / "qwen35-coding.conf")
        
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                with patch.object(controller, '_verify_profile_switch', return_value=True):
                    with patch.object(controller, '_get_current_model_id', side_effect=[
                        "qwen3.5-35b-ud-q3_k_xl",  # initially coding
                        "qwen3.5-35b-vision-q2_k",  # after switch
                    ]):
                        # Switch to vision
                        result = controller.ensure_profile("qwen35-vision")
                        assert result is True
                        assert controller.current_profile == "qwen35-vision"
                        
                        # Verify wrapper was called with sudo -n
                        subprocess.run.assert_called_once()
                        call_args = subprocess.run.call_args
                        assert call_args[0][0][0] == "sudo"
                        assert call_args[0][0][1] == "-n"
                        assert call_args[0][0][2] == "/usr/local/sbin/compute01-maint/p40-switch-profile"
                        assert call_args[0][0][3] == "qwen35-vision"


class TestVisionSmokeTestWithKnownAnswer:
    """Test vision smoke test with known answer (29.24)."""
    
    def test_vision_smoke_test_parses_29_24(self, tmp_path):
        """Vision smoke test correctly extracts and validates 29.24."""
        controller = P40ModelProfileController()
        controller._profiles["qwen35-vision"] = MagicMock()
        controller._profiles["qwen35-vision"].expected_model_id = "qwen3.5-35b-vision-q2_k"
        
        # Create a test image file
        import io
        test_image_data = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xFF\xDB\x00C\x00"
        test_image_path = tmp_path / "receipt.jpg"
        test_image_path.write_bytes(test_image_data)
        
        # Set the injectable test image path
        controller.VISION_TEST_IMAGE = str(test_image_path)
        
        # Simulate API response with correct answer
        response_json = {
            "choices": [{
                "message": {
                    "content": "29.24"
                }
            }]
        }
        
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.__enter__.return_value = mock_response
            mock_response.__exit__.return_value = False
            mock_response.read.return_value = json.dumps(response_json).encode()
            mock_urlopen.return_value = mock_response
            
            result = controller._run_smoke_test("qwen35-vision")
            assert result is True
    
    def test_vision_smoke_test_rejects_wrong_answer(self, tmp_path):
        """Vision smoke test fails if response doesn't contain 29.24."""
        controller = P40ModelProfileController()
        controller._profiles["qwen35-vision"] = MagicMock()
        controller._profiles["qwen35-vision"].expected_model_id = "qwen3.5-35b-vision-q2_k"
        
        # Create a test image file
        test_image_data = b"\xFF\xD8\xFF\xE0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xFF\xDB\x00C\x00"
        test_image_path = tmp_path / "receipt.jpg"
        test_image_path.write_bytes(test_image_data)
        
        # Set the injectable test image path
        controller.VISION_TEST_IMAGE = str(test_image_path)
        
        # Simulate API response with wrong answer
        response_json = {
            "choices": [{
                "message": {
                    "content": "15.50"
                }
            }]
        }
        
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.__enter__.return_value = mock_response
            mock_response.__exit__.return_value = False
            mock_response.read.return_value = json.dumps(response_json).encode()
            mock_urlopen.return_value = mock_response
            
            result = controller._run_smoke_test("qwen35-vision")
            assert result is False
