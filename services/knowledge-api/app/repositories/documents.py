"""Database operations for literature entries and their file/version lifecycle."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Collection, Document, DocumentFile, DocumentVersion, ProcessingTask
from app.models.associations import collection_documents
from app.repositories.processing_tasks import ACTIVE_STATUSES
from app.schemas.documents import DocumentCreate, DocumentUpdate


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, data: DocumentCreate) -> Document:
        values = data.model_dump()
        values["metadata_json"] = values.pop("metadata")
        document = Document(**values)
        self.session.add(document)
        await self.session.commit()
        return await self._get_required(document.id)

    async def create_with_file(
        self,
        data: DocumentCreate,
        *,
        original_name: str,
        storage_path: str,
        sha256: str,
        media_type: str,
        size_bytes: int,
    ) -> Document:
        values = data.model_dump()
        values["metadata_json"] = values.pop("metadata")
        document = Document(**values)
        document.files.append(
            DocumentFile(
                original_name=original_name,
                storage_path=storage_path,
                sha256=sha256,
                media_type=media_type,
                size_bytes=size_bytes,
            )
        )
        self.session.add(document)
        await self.session.commit()
        return await self._get_required(document.id)

    async def create_many_with_files(
        self,
        items: list[tuple[DocumentCreate, dict[str, object]]],
    ) -> list[Document]:
        documents: list[Document] = []
        for data, file_data in items:
            values = data.model_dump()
            values["metadata_json"] = values.pop("metadata")
            document = Document(**values)
            document.files.append(DocumentFile(**file_data))
            self.session.add(document)
            documents.append(document)
        await self.session.commit()
        return [await self._get_required(document.id) for document in documents]

    async def add_file(
        self,
        document: Document,
        *,
        original_name: str,
        storage_path: str,
        sha256: str,
        media_type: str,
        size_bytes: int,
    ) -> Document:
        self.session.add(
            DocumentFile(
                document_id=document.id,
                original_name=original_name,
                storage_path=storage_path,
                sha256=sha256,
                media_type=media_type,
                size_bytes=size_bytes,
            )
        )
        await self.session.commit()
        return await self._get_required(document.id)

    async def add_files(
        self,
        document: Document,
        files: list[dict[str, object]],
        *,
        replace_existing: bool = False,
    ) -> tuple[Document, list[str]]:
        # The unique constraint is the final guard. This preflight provides a useful
        # conflict message and records paths that can be removed after commit.
        incoming_kinds = {str(file_data["kind"]) for file_data in files}
        existing = [file for file in document.files if file.kind in incoming_kinds]
        if existing and not replace_existing:
            labels = "、".join("MD" if file.kind == "markdown" else "PDF" for file in existing)
            raise FileExistsError(f"该条目已包含 {labels} 文件")

        removed_paths: list[str] = []
        for file in existing:
            removed_paths.append(file.storage_path)
            await self.session.delete(file)
        if existing:
            await self.session.flush()
        for file_data in files:
            document.files.append(DocumentFile(**file_data))
        await self.session.commit()
        return await self._get_required(document.id), removed_paths

    async def file_exists(self, sha256: str) -> bool:
        statement = select(DocumentFile.id).where(DocumentFile.sha256 == sha256)
        return (await self.session.scalar(statement)) is not None

    async def any_file_exists(self, digests: list[str]) -> bool:
        if not digests:
            return False
        statement = select(DocumentFile.id).where(DocumentFile.sha256.in_(digests)).limit(1)
        return (await self.session.scalar(statement)) is not None

    async def get_file(self, file_id: uuid.UUID) -> DocumentFile | None:
        return await self.session.get(DocumentFile, file_id)

    async def knowledge_base_names(self, document_id: uuid.UUID) -> list[str]:
        """Return every knowledge base that currently protects this document."""

        statement = (
            select(Collection.name)
            .join(
                collection_documents,
                collection_documents.c.collection_id == Collection.id,
            )
            .where(collection_documents.c.document_id == document_id)
            .order_by(Collection.name)
        )
        return list((await self.session.scalars(statement)).all())

    async def delete_file(self, file: DocumentFile) -> None:
        if file.kind == "markdown":
            versions = list(
                (
                    await self.session.scalars(
                        select(DocumentVersion).where(
                            DocumentVersion.document_id == file.document_id,
                            DocumentVersion.markdown_path == file.storage_path,
                        )
                    )
                ).all()
            )
            # Version rows are audit records. Removing the active MD invalidates
            # their file pointer but should not erase the parsing history itself.
            for version in versions:
                version.status = "superseded"
                version.markdown_path = None
        await self.session.delete(file)
        await self.session.commit()

    async def move_file(self, file: DocumentFile, target: Document) -> Document:
        if any(item.kind == file.kind for item in target.files):
            label = "MD" if file.kind == "markdown" else "PDF"
            raise FileExistsError(f"目标条目已经包含 {label} 文件")
        if file.kind == "markdown":
            source_versions = list(
                (
                    await self.session.scalars(
                        select(DocumentVersion).where(
                            DocumentVersion.document_id == file.document_id,
                            DocumentVersion.markdown_path == file.storage_path,
                        )
                    )
                ).all()
            )
            for version in source_versions:
                version.status = "superseded"
                version.markdown_path = None
            target_versions = list(
                (
                    await self.session.scalars(
                        select(DocumentVersion)
                        .where(DocumentVersion.document_id == target.id)
                        .order_by(DocumentVersion.version)
                    )
                ).all()
            )
            # Moving an MD creates a new active version under the destination
            # entry while preserving the source entry's historical row.
            self.session.add(
                DocumentVersion(
                    document_id=target.id,
                    version=(target_versions[-1].version + 1) if target_versions else 1,
                    parser="move",
                    parser_version="1",
                    status="ready",
                    markdown_path=file.storage_path,
                )
            )
        file.document = target
        await self.session.commit()
        return await self._get_required(target.id)

    async def fill_missing_metadata(
        self, document: Document, extracted: dict[str, object]
    ) -> Document:
        for field in ("title", "doi", "authors", "abstract", "publication_year"):
            value = extracted.get(field)
            current = getattr(document, field)
            if value and (
                current is None or current == [] or (field == "title" and not current.strip())
            ):
                setattr(document, field, value)
        metadata = dict(document.metadata_json or {})
        metadata.update(extracted.get("metadata", {}))
        document.metadata_json = metadata
        await self.session.commit()
        return await self._get_required(document.id)

    async def set_markdown_version(
        self,
        document: Document,
        *,
        storage_path: str,
        parser: str,
        parser_version: str,
    ) -> Document:
        versions = list(
            (
                await self.session.scalars(
                    select(DocumentVersion)
                    .where(DocumentVersion.document_id == document.id)
                    .order_by(DocumentVersion.version)
                )
            ).all()
        )
        # Keep old version rows as provenance, but clear paths because a replaced
        # physical Markdown file may be deleted after the transaction commits.
        for version in versions:
            version.status = "superseded"
            version.markdown_path = None
        self.session.add(
            DocumentVersion(
                document_id=document.id,
                version=(versions[-1].version + 1) if versions else 1,
                parser=parser,
                parser_version=parser_version,
                status="ready",
                markdown_path=storage_path,
            )
        )
        await self.session.commit()
        return await self._get_required(document.id)

    async def get(self, document_id: uuid.UUID) -> Document | None:
        statement = (
            select(Document).options(selectinload(Document.files)).where(Document.id == document_id)
        )
        return await self.session.scalar(statement)

    async def get_for_update(self, document_id: uuid.UUID) -> Document | None:
        statement = (
            select(Document)
            .options(selectinload(Document.files))
            .where(Document.id == document_id)
            .with_for_update()
        )
        return await self.session.scalar(statement)

    async def _protected_document_ids(self) -> set[uuid.UUID]:
        return set(
            (
                await self.session.scalars(
                    select(collection_documents.c.document_id).distinct()
                )
            ).all()
        )

    async def _processing_document_ids(self) -> set[uuid.UUID]:
        return set(
            (
                await self.session.scalars(
                    select(ProcessingTask.resource_id).where(
                        ProcessingTask.resource_type == "document",
                        ProcessingTask.status.in_(ACTIVE_STATUSES),
                    ).distinct()
                )
            ).all()
        )

    async def deletion_summary(self) -> dict[str, int]:
        document_ids = set((await self.session.scalars(select(Document.id))).all())
        protected_ids = await self._protected_document_ids()
        processing_ids = await self._processing_document_ids()
        protected_ids.intersection_update(document_ids)
        processing_ids.intersection_update(document_ids)
        return {
            "total_count": len(document_ids),
            "protected_count": len(protected_ids),
            "processing_count": len(processing_ids - protected_ids),
            "deletable_count": len(document_ids - protected_ids - processing_ids),
        }

    async def delete_unprotected(self) -> tuple[list[str], dict[str, int]]:
        # Lock the document rows before checking associations. A concurrent FK
        # insert into collection_documents must wait for these locks, ensuring
        # that membership is checked again against a fresh statement snapshot.
        documents = list(
            (
                await self.session.scalars(
                    select(Document)
                    .options(selectinload(Document.files))
                    .order_by(Document.id)
                    .with_for_update()
                )
            ).all()
        )
        protected_ids = await self._protected_document_ids()
        processing_ids = await self._processing_document_ids()
        document_ids = {document.id for document in documents}
        protected_ids.intersection_update(document_ids)
        processing_ids.intersection_update(document_ids)
        deletable_ids = document_ids - protected_ids - processing_ids
        targets = [document for document in documents if document.id in deletable_ids]
        storage_paths = [file.storage_path for document in targets for file in document.files]
        for document in targets:
            await self.session.delete(document)
        await self.session.commit()
        return storage_paths, {
            "deleted_count": len(targets),
            "protected_count": len(protected_ids),
            "processing_count": len(processing_ids - protected_ids),
        }

    async def _get_required(self, document_id: uuid.UUID) -> Document:
        document = await self.get(document_id)
        if document is None:
            raise RuntimeError("Document disappeared after persistence")
        return document

    async def list(
        self, page: int, page_size: int, query: str | None
    ) -> tuple[list[Document], int]:
        statement = select(Document).options(selectinload(Document.files))
        count_statement = select(func.count()).select_from(Document)
        if query:
            pattern = f"%{query}%"
            statement = statement.where(Document.title.ilike(pattern))
            count_statement = count_statement.where(Document.title.ilike(pattern))
        total = int((await self.session.scalar(count_statement)) or 0)
        items = list(
            (
                await self.session.scalars(
                    statement.order_by(Document.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
        )
        return items, total

    async def update(self, document: Document, data: DocumentUpdate) -> Document:
        values = data.model_dump(exclude_unset=True)
        if "metadata" in values:
            values["metadata_json"] = values.pop("metadata")
        for field, value in values.items():
            setattr(document, field, value)
        await self.session.commit()
        return await self._get_required(document.id)

    async def delete(self, document: Document) -> None:
        await self.session.delete(document)
        await self.session.commit()
