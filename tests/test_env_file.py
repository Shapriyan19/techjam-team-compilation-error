from __future__ import annotations

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from starter.env_file import load_env_file, parse_env_file, resolve_env_path


class ParseEnvFileTest(unittest.TestCase):
    def test_plain_assignments(self) -> None:
        parsed = parse_env_file("NVIDIA_API_KEY=nvapi-abc\nTECHJAM_LLM_PROVIDER=nvidia\n")

        self.assertEqual(parsed, {"NVIDIA_API_KEY": "nvapi-abc", "TECHJAM_LLM_PROVIDER": "nvidia"})

    def test_comments_blank_lines_and_export_prefix_are_handled(self) -> None:
        parsed = parse_env_file("# comment\n\nexport NVIDIA_API_KEY=nvapi-abc\nnot an assignment\n")

        self.assertEqual(parsed, {"NVIDIA_API_KEY": "nvapi-abc"})

    def test_quotes_and_trailing_comments_are_stripped(self) -> None:
        parsed = parse_env_file(
            'A="quoted value"\n'
            "B='single'\n"
            "C=bare # trailing note\n"
            "D=has#hash\n"
        )

        self.assertEqual(parsed, {"A": "quoted value", "B": "single", "C": "bare", "D": "has#hash"})


class LoadEnvFileTest(unittest.TestCase):
    def write(self, directory: str, text: str) -> Path:
        path = Path(directory) / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_values_are_applied_to_the_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, "TECHJAM_TEST_ENV_VALUE=from-file\n")
            with unittest.mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("TECHJAM_TEST_ENV_VALUE", None)
                applied = load_env_file(path)

                self.assertEqual(applied, {"TECHJAM_TEST_ENV_VALUE": "from-file"})
                self.assertEqual(os.environ["TECHJAM_TEST_ENV_VALUE"], "from-file")

    def test_the_real_environment_wins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, "TECHJAM_TEST_ENV_VALUE=from-file\n")
            with unittest.mock.patch.dict(os.environ, {"TECHJAM_TEST_ENV_VALUE": "exported"}):
                applied = load_env_file(path)

                self.assertEqual(applied, {})
                self.assertEqual(os.environ["TECHJAM_TEST_ENV_VALUE"], "exported")

    def test_override_flag_replaces_the_exported_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, "TECHJAM_TEST_ENV_VALUE=from-file\n")
            with unittest.mock.patch.dict(os.environ, {"TECHJAM_TEST_ENV_VALUE": "exported"}):
                load_env_file(path, override=True)

                self.assertEqual(os.environ["TECHJAM_TEST_ENV_VALUE"], "from-file")

    def test_missing_file_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load_env_file(Path(directory) / "absent.env"), {})

    def test_env_file_variable_selects_the_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, "TECHJAM_TEST_ENV_VALUE=from-file\n")
            with unittest.mock.patch.dict(os.environ, {"TECHJAM_ENV_FILE": str(path)}):
                self.assertEqual(resolve_env_path(), path)


class EnvFileFeedsPhaseSixConfigTest(unittest.TestCase):
    def test_credentials_and_provider_come_from_the_file(self) -> None:
        from starter.llm.config import PhaseSixConfig

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "TECHJAM_LLM_PROVIDER=nvidia\nNVIDIA_API_KEY=nvapi-from-file\n",
                encoding="utf-8",
            )
            with unittest.mock.patch.dict(os.environ, {}, clear=False):
                for name in ("TECHJAM_LLM_PROVIDER", "NVIDIA_API_KEY", "TECHJAM_LLM_MODEL"):
                    os.environ.pop(name, None)
                load_env_file(path)
                config = PhaseSixConfig.from_environment()

                self.assertEqual(config.provider, "nvidia")
                self.assertEqual(config.api_key_variable, "NVIDIA_API_KEY")
                self.assertEqual(os.environ["NVIDIA_API_KEY"], "nvapi-from-file")


if __name__ == "__main__":
    unittest.main()
