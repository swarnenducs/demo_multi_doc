"""Azure Blob storage — template, JSON, and generated .docx for document MCP."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse

from document_processing_mcp.core.settings import settings

BLOB_SCHEME = "blob://"
BlobKind = Literal["upload", "download"]


def is_blob_ref(path: str | Path | None) -> bool:
    raw = str(path or "")
    return raw.startswith(BLOB_SCHEME) or raw.startswith("https://")


def blob_root_prefix() -> str:
    return (settings().azure_blob_prefix or "jobs").strip().strip("/")


def blob_job_root(job_id: str) -> str:
    """Same container, one job folder: ``jobs/{job_id}``."""
    return f"{blob_root_prefix()}/{job_id}"


def blob_name_for_job(
    job_id: str,
    filename: str,
    *,
    kind: BlobKind = "upload",
) -> str:
    """Segregate inputs vs outputs in the same container.

    ``jobs/{job_id}/upload/template.docx``
    ``jobs/{job_id}/download/filled.docx``
    """
    root = blob_job_root(job_id)
    name = Path(filename).name if filename else ""
    if not name:
        return f"{root}/{kind}"
    return f"{root}/{kind}/{name}"


def _normalize_sas_token(raw: str) -> str:
    token = raw.strip()
    if token.startswith("?"):
        token = token[1:]
    return token


def _account_blob_url(account: str) -> str:
    return f"https://{account}.blob.core.windows.net"


def _account_name_from_connection_string(cs: str | None) -> str | None:
    if not cs:
        return None
    for part in cs.split(";"):
        if part.lower().startswith("accountname="):
            value = part.split("=", 1)[1].strip()
            return value or None
    return None


class BlobStore:
    """Upload/download blobs in ``AZURE_BLOB_CONTAINER`` (default: docuploadsolution).

    You can set account key (or connection string) **and** SAS in the same ``.env``.
    Runtime uses SAS when ``AZURE_STORAGE_SAS_URL`` or ``AZURE_STORAGE_SAS_TOKEN`` is set;
    otherwise connection string, then account key. Azure accepts one credential per client.
    """

    def __init__(self) -> None:
        self.cfg = settings()
        self.container_name = self.cfg.azure_blob_container
        self._client: Any | None = None

    def _account_name(self) -> str:
        named = (self.cfg.azure_storage_account_name or "").strip()
        if named:
            return named
        return _account_name_from_connection_string(self.cfg.azure_storage_connection_string) or ""

    def auth_mode(self) -> str:
        if self.cfg.azure_storage_sas_url:
            return "sas_url"
        if self.cfg.azure_storage_sas_token and self._account_name():
            return "sas_token"
        if self.cfg.azure_storage_connection_string:
            return "connection_string"
        if self.cfg.azure_storage_account_name and self.cfg.azure_storage_account_key:
            return "account_key"
        return "none"

    @property
    def enabled(self) -> bool:
        return self.cfg.file_storage_backend == "azure_blob" and self.auth_mode() != "none"

    def _container_client(self):
        if self._client is not None:
            return self._client
        if not self.enabled:
            raise RuntimeError(
                "Azure Blob is not configured. Set FILE_STORAGE_BACKEND=azure_blob and one of: "
                "AZURE_STORAGE_CONNECTION_STRING, AZURE_STORAGE_ACCOUNT_NAME+AZURE_STORAGE_ACCOUNT_KEY, "
                "AZURE_STORAGE_ACCOUNT_NAME+AZURE_STORAGE_SAS_TOKEN, or AZURE_STORAGE_SAS_URL."
            )
        from azure.storage.blob import BlobServiceClient, ContainerClient

        mode = self.auth_mode()
        can_create = mode in {"connection_string", "account_key"}
        if mode == "sas_url":
            container = ContainerClient.from_container_url(self.cfg.azure_storage_sas_url)
        elif mode == "connection_string":
            service = BlobServiceClient.from_connection_string(
                self.cfg.azure_storage_connection_string
            )
            container = service.get_container_client(self.container_name)
        elif mode == "account_key":
            url = _account_blob_url(self.cfg.azure_storage_account_name or "")
            service = BlobServiceClient(
                account_url=url, credential=self.cfg.azure_storage_account_key
            )
            container = service.get_container_client(self.container_name)
        else:
            token = _normalize_sas_token(self.cfg.azure_storage_sas_token or "")
            url = _account_blob_url(self._account_name())
            service = BlobServiceClient(account_url=url, credential=token)
            container = service.get_container_client(self.container_name)
        if can_create:
            try:
                container.create_container()
            except Exception:  # noqa: BLE001 — already exists is expected
                pass
        self._client = container
        return container

    def ping(self) -> bool:
        if not self.enabled:
            return False
        try:
            self._container_client().get_container_properties()
            return True
        except Exception:  # noqa: BLE001
            return False

    def to_ref(self, blob_name: str) -> str:
        return f"{BLOB_SCHEME}{self.container_name}/{blob_name.lstrip('/')}"

    def parse_ref(self, ref: str) -> str:
        raw = (ref or "").strip()
        if raw.startswith(BLOB_SCHEME):
            rest = raw[len(BLOB_SCHEME) :]
            parts = rest.split("/", 1)
            if len(parts) == 2 and parts[0] == self.container_name:
                return parts[1]
            return rest.split("/", 1)[-1]
        if raw.startswith("https://"):
            parsed = urlparse(raw)
            path = unquote(parsed.path.lstrip("/"))
            prefix = f"{self.container_name}/"
            if path.startswith(prefix):
                return path[len(prefix) :]
            return path
        return raw

    def upload_file(self, local_path: Path, blob_name: str) -> str:
        data = Path(local_path).read_bytes()
        self._container_client().upload_blob(name=blob_name, data=data, overwrite=True)
        return self.to_ref(blob_name)

    def download_file(self, ref_or_name: str, dest: Path) -> Path:
        blob_name = self.parse_ref(ref_or_name)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        downloader = self._container_client().download_blob(blob_name)
        dest.write_bytes(downloader.readall())
        return dest

    def delete_prefix(self, prefix: str) -> None:
        if not self.enabled:
            return
        container = self._container_client()
        for blob in container.list_blobs(name_starts_with=prefix):
            container.delete_blob(blob.name)

    def persist_job_inputs(
        self,
        job_id: str,
        template_path: Path,
        data_path: Path,
        output_path: Path,
    ) -> tuple[str, str, str]:
        """Upload template + JSON; return blob refs (output ref is the intended destination)."""
        if not self.enabled:
            return str(template_path), str(data_path), str(output_path)
        tpl_ref = self.upload_file(
            template_path, blob_name_for_job(job_id, "template.docx", kind="upload")
        )
        data_ref = self.upload_file(
            data_path, blob_name_for_job(job_id, "data.json", kind="upload")
        )
        out_ref = self.to_ref(
            blob_name_for_job(job_id, Path(output_path).name, kind="download")
        )
        return tpl_ref, data_ref, out_ref

    def persist_job_output(
        self,
        job_id: str,
        output_path: Path,
        *,
        dest_ref: str | None = None,
    ) -> str:
        """Upload the generated .docx to Azure Blob."""
        if not self.enabled:
            return str(output_path)
        if dest_ref and is_blob_ref(dest_ref):
            blob_name = self.parse_ref(dest_ref)
        else:
            blob_name = blob_name_for_job(job_id, Path(output_path).name, kind="download")
        return self.upload_file(output_path, blob_name)


_blob: BlobStore | None = None


def get_blob_store() -> BlobStore:
    global _blob
    if _blob is None:
        _blob = BlobStore()
    return _blob


def reset_blob_store() -> None:
    global _blob
    _blob = None
