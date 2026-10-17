# Copyright (c) 2026 Amazon Web Services
# SPDX-License-Identifier: MIT
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import aws_cdk as cdk
import pytest
from evidence_synthesis_infra import bundling


def test_bundle_passes_paths_as_literal_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This helper records arguments without installing packages or using the network.
    recorder = tmp_path / "record_arguments"
    recorder.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        "target = Path(sys.argv[sys.argv.index('--target') + 1])\n"
        "(target / 'arguments.json').write_text(json.dumps(sys.argv[1:]))\n"
    )
    recorder.chmod(0o755)
    source = tmp_path / "source; touch UNEXPECTED"
    (source / "deploy").mkdir(parents=True)
    (source / "deploy" / "agentcore_main.py").write_text("# harmless launcher\n")
    (source / "requirements-runtime.txt").write_text("")
    output = tmp_path / "output with spaces"
    output.mkdir()
    bundler = bundling.Arm64PythonBundler(source)
    monkeypatch.setattr(bundling.sys, "executable", str(recorder))
    monkeypatch.chdir(tmp_path)

    assert bundler.try_bundle(
        str(output),
        cdk.BundlingOptions(image=cdk.DockerImage.from_registry("unused-test-image")),
    )
    arguments = json.loads((output / "arguments.json").read_text())
    assert arguments[arguments.index("--target") + 1] == str(output.resolve())
    assert arguments[-1] == f"{source.resolve()}[aws]"
    assert not (tmp_path / "UNEXPECTED").exists()
    assert (output / "main.py").read_text() == "# harmless launcher\n"


def test_bundle_rejects_non_directory_output(tmp_path: Path) -> None:
    output = tmp_path / "file"
    output.write_text("preserve this file")
    bundler = bundling.Arm64PythonBundler(tmp_path)
    with pytest.raises(ValueError, match="source and output directories"):
        bundler.try_bundle(
            str(output),
            cdk.BundlingOptions(image=cdk.DockerImage.from_registry("unused-test-image")),
        )
    assert output.read_text() == "preserve this file"


def test_bundle_rejects_a_tampered_dependency_wheel(
    tmp_path: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    wheel = tmp_path / "safety_fixture-0.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("safety_fixture/__init__.py", "")
        archive.writestr(
            "safety_fixture-0.1.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: safety-fixture\nVersion: 0.1\n",
        )
        archive.writestr(
            "safety_fixture-0.1.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr("safety_fixture-0.1.dist-info/RECORD", "")
    original_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
    with wheel.open("ab") as stream:
        stream.write(b"tampered")
    (tmp_path / "requirements-runtime.txt").write_text(
        f"safety-fixture @ {wheel.as_uri()} --hash=sha256:{original_hash}\n",
    )
    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(subprocess.CalledProcessError):
        bundling.Arm64PythonBundler(tmp_path).try_bundle(
            str(output),
            cdk.BundlingOptions(image=cdk.DockerImage.from_registry("unused-test-image")),
        )
    captured = capfd.readouterr()
    assert "DO NOT MATCH THE HASHES" in captured.out + captured.err
    assert not list(output.iterdir())
