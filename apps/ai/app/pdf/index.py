"""Separate, owner/session/source-filtered local PDF vectors."""

import asyncio
from collections.abc import Sequence
from hashlib import sha256
from typing import cast

from chromadb import Collection
from chromadb.api import ClientAPI

from app.ai.models.ports import ModelAdapter
from app.ai.schemas import EmbeddingRequest, ModelProfile
from app.pdf.contracts import PdfPage
from app.pdf.intelligence import PdfIntelligence


class PdfIndex:
    def __init__(self, client: ClientAPI, models: ModelAdapter, profile: ModelProfile) -> None:
        self.client, self.models, self.profile = client, models, profile

    def collection(self) -> Collection:
        model = self.profile.embedding_candidates[0]
        collection = self.client.get_or_create_collection(
            "workbench-pdf-v1-" + sha256(model.encode()).hexdigest()[:16],
            embedding_function=None,
            configuration={"hnsw": {"space": "cosine"}},
            metadata={"model": model, "schema": "pdf-v1"},
        )
        if (
            collection.metadata != {"model": model, "schema": "pdf-v1"}
            or (collection.configuration.get("hnsw") or {}).get("space") != "cosine"
        ):
            raise ValueError("PDF vector collection configuration does not match")
        return collection

    async def embed(self, texts: tuple[str, ...]) -> list[Sequence[float]]:
        model = self.profile.embedding_candidates[0]
        result = await self.models.create_embeddings(EmbeddingRequest(model=model, inputs=texts))
        if result.model != model:
            raise ValueError("PDF embeddings must use the indexed model")
        return [list(vector) for vector in result.vectors]

    async def ingest(
        self, owner: str, session: str, source: str, pages: tuple[PdfPage, ...]
    ) -> None:
        chunks: list[tuple[int, str]] = []
        for page in pages:
            text = PdfIntelligence.page_text(page)
            chunks.extend(
                (page.page_number, text[start : start + 1_500])
                for start in range(0, len(text), 1_350)
            )
        collection = await asyncio.to_thread(self.collection)
        for start in range(0, len(chunks), self.profile.embedding_batch_size):
            batch = chunks[start : start + self.profile.embedding_batch_size]
            vectors = await self.embed(tuple(text for _, text in batch))
            await asyncio.to_thread(
                collection.upsert,
                ids=[
                    sha256(f"{owner}:{session}:{source}:{start + i}:{text}".encode()).hexdigest()
                    for i, (_, text) in enumerate(batch)
                ],
                embeddings=vectors,
                documents=[text for _, text in batch],
                metadatas=[
                    {"owner": owner, "session": session, "source": source, "page": page}
                    for page, _ in batch
                ],
            )

    async def search(
        self, owner: str, session: str, source: str, query: str
    ) -> list[tuple[int, str]]:
        vectors = await self.embed((query,))
        collection = await asyncio.to_thread(self.collection)
        result = await asyncio.to_thread(
            collection.query,
            query_embeddings=vectors,
            n_results=5,
            where={"$and": [{"owner": owner}, {"session": session}, {"source": source}]},
            include=["metadatas", "documents", "distances"],
        )
        documents, metadata, distances = (
            result["documents"],
            result["metadatas"],
            result["distances"],
        )
        if not documents or not metadata or not distances:
            return []
        return [
            (int(cast(int, meta["page"])), text)
            for text, meta, distance in zip(documents[0], metadata[0], distances[0], strict=True)
            if text and meta and 1 - distance >= 0.30
        ]

    async def delete(self, owner: str, session: str) -> None:
        collection = await asyncio.to_thread(self.collection)
        await asyncio.to_thread(
            collection.delete, where={"$and": [{"owner": owner}, {"session": session}]}
        )
