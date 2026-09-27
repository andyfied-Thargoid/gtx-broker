"""Tests for package data - verifies SQL files are included in wheels."""

import subprocess
import pytest
import tempfile
import sys
from pathlib import Path

# Derive repository root from this test file's location
REPO_ROOT = Path(__file__).resolve().parents[1]


class TestPackageData:
    """Tests that package data (SQL migrations) is included in wheels."""

    def test_sql_file_in_wheel(self):
        """Verify migrations/*.sql files are included in the built wheel.
        
        This test builds the wheel and checks that the SQL migration files
        are actually included in the package data, not just present in the
        source tree.
        """
        # Build the wheel
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Build wheel from repository root
            result = subprocess.run(
                ["python3", "-m", "build", "--wheel", "--outdir", str(tmpdir)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True
            )
            
            assert result.returncode == 0, f"Build failed: {result.stderr}"
            
            # Find the wheel file
            wheels = list(tmpdir.glob("*.whl"))
            assert len(wheels) == 1, f"Expected 1 wheel, found {len(wheels)}"
            
            # Check wheel contents
            result = subprocess.run(
                ["unzip", "-l", str(wheels[0])],
                capture_output=True,
                text=True
            )
            
            assert result.returncode == 0
            wheel_contents = result.stdout
            
            # Verify migrations SQL file is in the wheel
            assert "gtx_broker/scheduler/migrations/001_add_tagging.sql" in wheel_contents, \
                "SQL migration file not found in wheel package data"

    def test_installed_wheel_has_sql(self):
        """Test that an installed wheel can load SQL migrations.
        
        This simulates what happens when a user installs the package via pip
        - the SQL files should be available as package resources.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Build wheel
            subprocess.run(
                ["python3", "-m", "build", "--wheel", "--outdir", str(tmpdir)],
                cwd=REPO_ROOT,
                capture_output=True,
                check=True
            )
            
            # Find wheel
            wheels = list(tmpdir.glob("*.whl"))
            assert len(wheels) == 1
            
            # Create a test venv and install the wheel
            test_dir = tmpdir / "test_env"
            subprocess.run(
                ["python3", "-m", "venv", str(test_dir)],
                capture_output=True,
                check=True
            )
            
            # Determine pip path based on platform
            if sys.platform == "win32":
                pip_path = test_dir / "Scripts" / "pip.exe"
            else:
                pip_path = test_dir / "bin" / "pip"
            
            # Install the wheel (no deps since we're testing package contents)
            result = subprocess.run(
                [str(pip_path), "install", "--quiet", "--no-deps", str(wheels[0])],
                capture_output=True,
                text=True
            )
            
            assert result.returncode == 0, f"Install failed: {result.stderr}"
            
            # Test that the SQL file is accessible
            python_path = test_dir / "bin" / "python" if sys.platform != "win32" else test_dir / "Scripts" / "python.exe"
            
            test_script = """
import importlib.resources
import gtx_broker.scheduler

# Get the migrations package
migrations_pkg = importlib.resources.files("gtx_broker.scheduler").joinpath("migrations")

# Try to access the SQL file
sql_file = migrations_pkg / "001_add_tagging.sql"
try:
    content = sql_file.read_text()
    assert "batch_epochs" in content, "SQL file content not found"
    print("SUCCESS: SQL file accessible")
except Exception as e:
    print(f"FAIL: {e}")
    exit(1)
"""
            
            result = subprocess.run(
                [str(python_path), "-c", test_script],
                capture_output=True,
                text=True
            )
            
            assert result.returncode == 0, f"SQL file not accessible in installed wheel: {result.stderr}\n{result.stdout}"
            assert "SUCCESS" in result.stdout
