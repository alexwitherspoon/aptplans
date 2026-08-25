"""Typed inference cache and resumable task checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from pipeline.extraction_store import (
    ExtractionManifest,
    ExtractionStore,
    extraction_dir,
)
from pipeline.queue import JobQueue, _connect, _utc_now


LANES = frozenset({"easy_text", "complex_text", "vision"})
MAX_CHECKPOINT_BYTES = 64 * 1024


def _canonical(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InferenceKey:
    content_sha256: str
    extraction_manifest_key: str
    task_type: str
    task_version: str
    lane: str
    model_digest: str
    options: dict

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "options",
            json.loads(_canonical(self.options)),
        )
        if len(self.content_sha256) != 64:
            raise ValueError("invalid inference artifact hash")
        if len(self.extraction_manifest_key) != 64:
            raise ValueError("invalid inference extraction key")
        if self.lane not in LANES:
            raise ValueError(f"invalid inference lane: {self.lane}")
        for name, value in (
            ("task_type", self.task_type),
            ("task_version", self.task_version),
        ):
            if not str(value).strip():
                raise ValueError(f"inference {name} is required")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.model_digest):
            raise ValueError("inference model_digest must be a SHA-256 digest")

    @property
    def options_sha256(self) -> str:
        return _sha256(_canonical(self.options))

    def identity(self) -> dict:
        return {
            "content_sha256": self.content_sha256,
            "extraction_manifest_key": self.extraction_manifest_key,
            "task_type": self.task_type,
            "task_version": self.task_version,
            "lane": self.lane,
            "model_digest": self.model_digest,
            "options_sha256": self.options_sha256,
        }

    @property
    def task_key(self) -> str:
        return _sha256(_canonical(self.identity()))

    def checkpoint_payload(self) -> dict:
        return {**self.identity(), "options": self.options}


@dataclass(frozen=True)
class InferenceResult:
    key: InferenceKey
    result: dict
    evidence: list[dict]
    quality: dict
    created_at: str

    @property
    def result_sha256(self) -> str:
        return _sha256(_canonical(self.result))


class InferenceStore:
    def __init__(
        self,
        ledger_root: Path,
        extraction_root: Path | None = None,
    ) -> None:
        self.path = JobQueue(Path(ledger_root)).path
        self.extractions = ExtractionStore(
            Path(ledger_root),
            extraction_root or extraction_dir(),
        )

    def _connection(self):
        return _connect(self.path)

    def _manifest(self, key: InferenceKey) -> ExtractionManifest:
        manifest = self.extractions.get(key.extraction_manifest_key)
        if (
            manifest is None
            or manifest.content_sha256 != key.content_sha256
        ):
            raise ValueError(
                "inference key does not match extraction artifact"
            )
        return manifest

    @staticmethod
    def _validate_evidence(
        evidence: list[dict],
        manifest: ExtractionManifest,
    ) -> None:
        if not evidence:
            raise ValueError("inference result requires cited evidence")
        for citation in evidence:
            if not isinstance(citation, dict):
                raise ValueError("inference evidence must be objects")
            if not {"page", "text_sha256"} <= set(citation):
                raise ValueError(
                    "inference evidence requires page and text_sha256"
                )
            if set(citation) - {"page", "text_sha256", "bbox"}:
                raise ValueError("inference evidence contains unknown fields")
            page = citation["page"]
            if (
                not isinstance(page, int)
                or isinstance(page, bool)
                or not 1 <= page <= len(manifest.pages)
            ):
                raise ValueError("inference evidence page is out of range")
            expected_sha256 = str(
                manifest.pages[page - 1]["text_sha256"]
            )
            if citation["text_sha256"] != expected_sha256:
                raise ValueError(
                    "inference evidence text hash does not match extraction"
                )
            if "bbox" in citation:
                bbox = citation["bbox"]
                if (
                    not isinstance(bbox, dict)
                    or set(bbox) != {"x", "y", "width", "height"}
                    or any(
                        not isinstance(value, (int, float))
                        or isinstance(value, bool)
                        for value in bbox.values()
                    )
                    or bbox["x"] < 0
                    or bbox["y"] < 0
                    or bbox["width"] <= 0
                    or bbox["height"] <= 0
                ):
                    raise ValueError("invalid inference evidence bbox")

    def get(self, key: InferenceKey) -> InferenceResult | None:
        manifest = self._manifest(key)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT result_json, evidence_json, quality_json,
                       envelope_sha256, created_at
                FROM inference_results WHERE task_key=?
                """,
                (key.task_key,),
            ).fetchone()
        if row is None:
            return None
        envelope = {
            "result": json.loads(str(row["result_json"])),
            "evidence": json.loads(str(row["evidence_json"])),
            "quality": json.loads(str(row["quality_json"])),
        }
        if _sha256(_canonical(envelope)) != str(row["envelope_sha256"]):
            raise ValueError("corrupt inference result envelope")
        self._validate_evidence(envelope["evidence"], manifest)
        return InferenceResult(
            key=key,
            result=envelope["result"],
            evidence=envelope["evidence"],
            quality=envelope["quality"],
            created_at=str(row["created_at"]),
        )

    def load_checkpoint(self, key: InferenceKey) -> dict | None:
        self._manifest(key)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT key_json, cursor, state_json, updated_at
                FROM inference_checkpoints WHERE task_key=?
                """,
                (key.task_key,),
            ).fetchone()
        if row is None:
            return None
        if json.loads(str(row["key_json"])) != key.checkpoint_payload():
            raise ValueError("inference checkpoint key conflict")
        return {
            "cursor": int(row["cursor"]),
            "state": json.loads(str(row["state_json"])),
            "updated_at": str(row["updated_at"]),
        }

    def checkpoint(
        self,
        key: InferenceKey,
        *,
        cursor: int,
        state: dict,
    ) -> bool:
        if cursor < 0:
            raise ValueError("inference checkpoint cursor must be nonnegative")
        if not isinstance(state, dict):
            raise ValueError("inference checkpoint state must be an object")
        encoded_state = _canonical(state)
        if len(encoded_state.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
            raise ValueError("inference checkpoint state exceeds size limit")
        self._manifest(key)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if connection.execute(
                    "SELECT 1 FROM inference_results WHERE task_key=?",
                    (key.task_key,),
                ).fetchone():
                    connection.execute("COMMIT")
                    return False
                existing = connection.execute(
                    """
                    SELECT key_json FROM inference_checkpoints
                    WHERE task_key=?
                    """,
                    (key.task_key,),
                ).fetchone()
                key_json = _canonical(key.checkpoint_payload())
                if (
                    existing is not None
                    and str(existing["key_json"]) != key_json
                ):
                    raise ValueError("inference checkpoint key conflict")
                connection.execute(
                    """
                    INSERT INTO inference_checkpoints(
                        task_key, content_sha256,
                        extraction_manifest_key, key_json, cursor,
                        state_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_key) DO UPDATE SET
                        key_json=excluded.key_json,
                        cursor=excluded.cursor,
                        state_json=excluded.state_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        key.task_key,
                        key.content_sha256,
                        key.extraction_manifest_key,
                        key_json,
                        int(cursor),
                        encoded_state,
                        _utc_now(),
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return True

    def complete(
        self,
        key: InferenceKey,
        *,
        result: dict,
        evidence: list[dict],
        quality: dict | None = None,
    ) -> InferenceResult:
        manifest = self._manifest(key)
        self._validate_evidence(evidence, manifest)
        quality = dict(quality or {})
        created_at = _utc_now()
        encoded_result = _canonical(result)
        encoded_evidence = _canonical(evidence)
        encoded_quality = _canonical(quality)
        envelope_sha256 = _sha256(
            _canonical(
                {
                    "result": result,
                    "evidence": evidence,
                    "quality": quality,
                }
            )
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                manifest = connection.execute(
                    """
                    SELECT content_sha256 FROM extraction_manifests
                    WHERE manifest_key=?
                    """,
                    (key.extraction_manifest_key,),
                ).fetchone()
                if (
                    manifest is None
                    or str(manifest["content_sha256"])
                    != key.content_sha256
                ):
                    raise ValueError(
                        "inference key does not match extraction artifact"
                    )
                existing = connection.execute(
                    """
                    SELECT result_json, evidence_json, quality_json, created_at
                    FROM inference_results WHERE task_key=?
                    """,
                    (key.task_key,),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO inference_results(
                            task_key, content_sha256,
                            extraction_manifest_key, task_type, task_version,
                            lane, model_digest, options_sha256, result_sha256,
                            envelope_sha256, result_json, evidence_json,
                            quality_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            key.task_key,
                            key.content_sha256,
                            key.extraction_manifest_key,
                            key.task_type,
                            key.task_version,
                            key.lane,
                            key.model_digest,
                            key.options_sha256,
                            _sha256(encoded_result),
                            envelope_sha256,
                            encoded_result,
                            encoded_evidence,
                            encoded_quality,
                            created_at,
                        ),
                    )
                else:
                    if (
                        str(existing["result_json"]) != encoded_result
                        or str(existing["evidence_json"])
                        != encoded_evidence
                        or str(existing["quality_json"]) != encoded_quality
                    ):
                        raise ValueError("inference result conflict")
                    created_at = str(existing["created_at"])
                connection.execute(
                    "DELETE FROM inference_checkpoints WHERE task_key=?",
                    (key.task_key,),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return InferenceResult(
            key=key,
            result=result,
            evidence=evidence,
            quality=quality,
            created_at=created_at,
        )
