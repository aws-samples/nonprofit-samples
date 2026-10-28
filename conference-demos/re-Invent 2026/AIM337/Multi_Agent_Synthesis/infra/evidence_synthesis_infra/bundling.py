# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import aws_cdk as cdk
import jsii


@jsii.implements(cdk.ILocalBundling)
class Arm64PythonBundler:
    """Build a Linux ARM64 CodeZip without requiring a running Docker daemon."""

    def __init__(self, source_path: Path) -> None:
        self._source_path = source_path.resolve(strict=True)

    def try_bundle(
        self,
        output_dir: str,
        options: cdk.BundlingOptions,
    ) -> bool:
        del options
        output_path = Path(output_dir).resolve(strict=True)
        if not self._source_path.is_dir() or not output_path.is_dir():
            raise ValueError("Bundling requires source and output directories")
        # Verify dependency wheels before installing the application itself.
        self._pip([
            "install",
            "--target",
            str(output_path),
            "--platform",
            "manylinux2014_aarch64",
            "--implementation",
            "cp",
            "--python-version",
            "3.12",
            "--only-binary=:all:",
            "--require-hashes",
            "--requirement",
            str(self._source_path / "requirements-runtime.txt"),
        ])
        self._pip([
            "install", "--target", str(output_path),
            "--no-deps", "--no-build-isolation", f"{self._source_path}[aws]",
        ])
        for cache_dir in output_path.rglob("__pycache__"):
            shutil.rmtree(cache_dir)
        for tests_dir in output_path.rglob("tests"):
            if tests_dir.is_dir():
                shutil.rmtree(tests_dir)
        for bytecode in output_path.rglob("*.pyc"):
            bytecode.unlink()
        launcher = output_path / "main.py"
        shutil.copy2(self._source_path / "deploy" / "agentcore_main.py", launcher)
        launcher.chmod(0o755)
        return True

    @staticmethod
    def _pip(arguments: list[str]) -> None:
        subprocess.run(
            ["python", "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", *arguments],
            executable=sys.executable, shell=False, check=True, timeout=600,
        )
