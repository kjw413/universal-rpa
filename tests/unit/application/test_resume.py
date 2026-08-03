from __future__ import annotations

from pathlib import Path

from tests.helpers.validation_fakes import ValidationSpyAdapter, registry_with, runtime_environment
from tests.unit.application.test_validation import workflow
from universal_rpa.application.resume import ResumeFingerprintBuilder
from universal_rpa.application.variable_preparation import PreparedVariables
from universal_rpa.domain.types import FrozenMapping
from universal_rpa.ports.credentials import SecretValue


class _SecretSpy:
    def __init__(self) -> None:
        self.read_calls = 0

    def exists(self, reference: str) -> bool:
        return reference == "vault/password"

    def read(self, reference: str) -> SecretValue:
        del reference
        self.read_calls += 1
        return SecretValue.from_text("NEVER_FINGERPRINT")


def test_resume_fingerprint_is_ordered_and_never_reads_secret(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    secrets = _SecretSpy()
    prepared = PreparedVariables(
        values=FrozenMapping((("query", "A"),)),
        credential_refs=FrozenMapping((("password", "vault/password"),)),
    )
    registry = registry_with(ValidationSpyAdapter())

    fingerprint = ResumeFingerprintBuilder().build(
        workflow=workflow(),
        output_root=output,
        prepared=prepared,
        snapshots=FrozenMapping.empty(),
        registry=registry,
        runtime=runtime_environment(),
        secret_store=secrets,
    )

    encoded = fingerprint.model_dump_json()
    assert secrets.read_calls == 0
    assert "NEVER_FINGERPRINT" not in encoded
    assert "vault/password" not in encoded
    assert fingerprint.adapters[0].adapter_id == "fake"
