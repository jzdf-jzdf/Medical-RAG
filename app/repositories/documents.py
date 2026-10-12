import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Document, DocumentFile
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
        self, document: Document, files: list[dict[str, object]]
    ) -> Document:
        for file_data in files:
            document.files.append(DocumentFile(**file_data))
        await self.session.flush()
        return document

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

    async def delete_file(self, file: DocumentFile) -> None:
        await self.session.delete(file)
        await self.session.commit()

    async def move_file(self, file: DocumentFile, target: Document) -> Document:
        file.document = target
        await self.session.commit()
        return await self._get_required(target.id)

    async def fill_missing_metadata(
        self, document: Document, extracted: dict[str, object]
    ) -> Document:
        for field in ("title", "doi", "authors", "abstract", "publication_year"):
            value = extracted.get(field)
            current = getattr(document, field)
            if value and (current is None or current == [] or (field == "title" and not current.strip())):
                setattr(document, field, value)
        metadata = dict(document.metadata_json or {})
        metadata.update(extracted.get("metadata", {}))
        document.metadata_json = metadata
        await self.session.commit()
        return await self._get_required(document.id)

    async def get(self, document_id: uuid.UUID) -> Document | None:
        statement = (
            select(Document)
            .options(selectinload(Document.files))
            .where(Document.id == document_id)
        )
        return await self.session.scalar(statement)

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
