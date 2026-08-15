from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RETRY = ROOT / "tools" / "run-with-transient-retry.sh"


def _run_retry(command: str, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.update(env)
    return subprocess.run(
        ["bash", str(RETRY), "bash", "-c", command],
        cwd=ROOT,
        env=merged,
        text=True,
        capture_output=True,
        check=False,
    )


def test_transient_timeout_retries_until_success(tmp_path: Path) -> None:
    counter = tmp_path / "attempts"
    command = (
        f'n=$(cat "{counter}" 2>/dev/null || echo 0); '
        'n=$((n + 1)); '
        f'echo "$n" > "{counter}"; '
        'if [ "$n" -lt 3 ]; then echo "httpx.ReadTimeout" >&2; exit 7; fi; '
        'echo success'
    )
    result = _run_retry(
        command,
        env={"ABRED_RETRY_MAX_ATTEMPTS": "4", "ABRED_RETRY_DELAYS": "0 0 0"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "success"
    assert counter.read_text().strip() == "3"
    assert result.stderr.count("Transient producer HTTP failure; retrying") == 2


def test_non_transient_failure_is_not_retried(tmp_path: Path) -> None:
    counter = tmp_path / "attempts"
    command = (
        f'n=$(cat "{counter}" 2>/dev/null || echo 0); '
        'n=$((n + 1)); '
        f'echo "$n" > "{counter}"; '
        'echo "ValueError: broken parser contract" >&2; '
        'exit 9'
    )
    result = _run_retry(
        command,
        env={"ABRED_RETRY_MAX_ATTEMPTS": "4", "ABRED_RETRY_DELAYS": "0 0 0"},
    )

    assert result.returncode == 9
    assert counter.read_text().strip() == "1"
    assert "retrying" not in result.stderr


def test_audiopolka_workflow_fails_closed_before_artifact_upload() -> None:
    text = (ROOT / ".github" / "workflows" / "audiopolka.yml").read_text(encoding="utf-8")
    assert "shell: bash" in text
    assert "set -o pipefail" in text
    assert "bash tools/run-with-transient-retry.sh" in text
    assert "- name: Audit feed contract" in text
    assert 'assert len(feeds) == 1' in text
    assert 'assert len(manifests) == 1' in text
    assert text.index("- name: Audit feed contract") < text.index("- name: Upload feed artifact")


def test_uknig_uses_same_transient_retry_gate() -> None:
    text = (ROOT / ".github" / "workflows" / "uknig.yml").read_text(encoding="utf-8")
    assert "set -o pipefail" in text
    assert "bash tools/run-with-transient-retry.sh" in text
    assert 'assert len(feeds) == 1' in text
    assert 'assert len(manifests) == 1' in text
