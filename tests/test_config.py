from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gus_reporter.config import AppConfig, load_runtime_env


class RuntimeEnvTests(unittest.TestCase):
    def test_load_runtime_env_reads_missing_values_from_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dotenv_path = Path(temp_dir) / ".env"
            dotenv_path.write_text(
                "OPENAI_API_KEY=test-openai\n"
                "TINYFISH_API_KEY=test-tinyfish\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                load_runtime_env(dotenv_path)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "test-openai")
                self.assertEqual(os.environ["TINYFISH_API_KEY"], "test-tinyfish")

    def test_load_runtime_env_does_not_override_existing_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dotenv_path = Path(temp_dir) / ".env"
            dotenv_path.write_text(
                "OPENAI_API_KEY=file-openai\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"OPENAI_API_KEY": "shell-openai"}, clear=True):
                load_runtime_env(dotenv_path)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "shell-openai")

    def test_app_config_defaults_to_pro_friendly_tinyfish_limits(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "test-openai",
                "TINYFISH_API_KEY": "test-tinyfish",
            },
            clear=True,
        ):
            with patch("gus_reporter.config.load_runtime_env"):
                config = AppConfig.from_env()
        self.assertEqual(50, config.tinyfish_batch_size)
        self.assertEqual(50, config.tinyfish_max_total_jobs)
        self.assertEqual(6, config.tinyfish_max_acquisition_waves)


if __name__ == "__main__":
    unittest.main()
