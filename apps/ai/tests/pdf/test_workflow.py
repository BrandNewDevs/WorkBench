"""Real local storage/index/rendering with deterministic model responses."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest
from pydantic import JsonValue

from app.ai.fakes import FakeAIEngine, FakeModelAdapter
from app.ai.knowledge.chroma_ingestion import create_persistent_chroma_client
from app.ai.models.profiles import load_model_profile
from app.ai.schemas import (
    ApprovedKnowledgeRoot,
    InferenceMetrics,
    TextGenerationRequest,
    TextGenerationResult,
)
from app.pdf.contracts import PdfApprovalDecision, PdfTurnRequest
from app.pdf.engine import PdfDocumentError
from app.pdf.index import PdfIndex
from app.pdf.service import PdfWorkflowService
from app.pdf.store import PdfStateStore
from app.storage import (
    LocalSessionWorkspaceStore,
    LocalSQLiteDatabase,
    SQLiteSessionFileStore,
    SQLiteWorkflowStore,
)
from app.workflow.contracts import WorkflowSession, WorkflowStage, WorkflowStatus, WorkflowType


class PdfModels(FakeModelAdapter):
    tool = "answer_pdf"
    invalid_page = False

    async def generate_text(self, request: TextGenerationRequest) -> TextGenerationResult:
        title = request.output_schema["title"]
        content: dict[str, JsonValue]
        if title == "PdfTurnIntent":
            content = {"tool": self.tool}
        elif title == "PdfDocumentDraft":
            content = {
                "title": "Inspection report",
                "purpose": "Summarize findings",
                "sections": [{"heading": "Findings", "paragraphs": ["Valve 17 needs inspection."]}],
            }
        else:
            content = {
                "claims": [
                    {
                        "text": "Valve 17 needs inspection.",
                        "pages": [999 if self.invalid_page else 1],
                    }
                ]
            }
        return TextGenerationResult(
            model=request.model,
            text="recorded",
            structured_output=content,
            metrics=InferenceMetrics(client_elapsed_ms=1),
        )


async def chunks(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def setup(tmp_path: Path) -> tuple[PdfWorkflowService, WorkflowSession, PdfModels, bytes]:
    database = LocalSQLiteDatabase(tmp_path / "state.db")
    await database.initialize()
    state = PdfStateStore(database)
    await state.initialize()
    now = datetime.now(UTC)
    session = WorkflowSession(
        session_id=uuid4(),
        owner_user_id=uuid4(),
        workflow_type=WorkflowType.PDF_DOCUMENT,
        title="PDF",
        stage=WorkflowStage.COLLECTING_INPUTS,
        status=WorkflowStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    await SQLiteWorkflowStore(database).create_session(session)
    workspace = LocalSessionWorkspaceStore(tmp_path / "sessions")
    files = SQLiteSessionFileStore(database, workspace)
    models = PdfModels()
    profile = load_model_profile()
    root = tmp_path / "chroma"
    root.mkdir()
    client = create_persistent_chroma_client(ApprovedKnowledgeRoot(path=root))
    service = PdfWorkflowService(
        store=state,
        files=files,
        workspaces=workspace,
        models=models,
        profile=profile,
        ai=FakeAIEngine(),
        index=PdfIndex(client, models, profile),
    )
    with pymupdf.open() as pdf:  # type: ignore[no-untyped-call]
        pdf.new_page().insert_text((72, 72), "Valve 17 needs inspection.")
        data = pdf.tobytes()
    return service, session, models, data


@pytest.mark.asyncio
async def test_uploaded_pdf_question_persists_and_restores_grounded_followup(
    tmp_path: Path,
) -> None:
    service, session, _, data = await setup(tmp_path)
    upload = await service.files.save_upload(
        session=session,
        upload_id=uuid4(),
        source_id=uuid4(),
        file_name="inspection.pdf",
        mime_type="application/pdf",
        content=chunks(data),
    )
    request = PdfTurnRequest(
        message="Which valve needs inspection?",
        client_request_id=uuid4(),
        upload_id=upload.upload_id,
    )
    result = await service.turn(session.session_id, session.owner_user_id, request)
    assert result.answer == "Valve 17 needs inspection."
    assert result.pages == (1,)
    assert await service.turn(session.session_id, session.owner_user_id, request) == result
    state = await service.store.get(session.session_id, session.owner_user_id)
    assert len(state.turns) == 1
    assert state.source is not None and state.source.upload_id == upload.upload_id
    followup = await service.turn(
        session.session_id,
        session.owner_user_id,
        PdfTurnRequest(message="Repeat that valve number", client_request_id=uuid4()),
    )
    assert followup.pages == (1,)
    with pytest.raises(PermissionError):
        await service.store.get(session.session_id, uuid4())


@pytest.mark.asyncio
async def test_creation_requires_exact_approval_and_replay_creates_one_artifact(
    tmp_path: Path,
) -> None:
    service, session, models, _ = await setup(tmp_path)
    models.tool = "create_pdf"
    turn = await service.turn(
        session.session_id,
        session.owner_user_id,
        PdfTurnRequest(message="Create a PDF inspection note", client_request_id=uuid4()),
    )
    assert not list(tmp_path.rglob("*.pdf"))
    approval = turn.approval
    assert approval is not None
    with pytest.raises(PdfDocumentError, match="matches"):
        await service.resolve(
            session.session_id,
            session.owner_user_id,
            approval.approval_id,
            PdfApprovalDecision(approve=True, arguments_hash="0" * 64),
        )
    decision = PdfApprovalDecision(approve=True, arguments_hash=approval.arguments_hash)
    completed = await service.resolve(
        session.session_id, session.owner_user_id, approval.approval_id, decision
    )
    assert completed.artifact is not None
    assert len(list(tmp_path.rglob("*.pdf"))) == 1
    assert (
        await service.resolve(
            session.session_id, session.owner_user_id, approval.approval_id, decision
        )
        == completed
    )
    metadata, data = await service.artifact(
        session.session_id, session.owner_user_id, completed.artifact.artifact_id
    )
    assert data.startswith(b"%PDF-") and metadata.page_count == 1
    with pytest.raises(PermissionError):
        await service.artifact(session.session_id, uuid4(), completed.artifact.artifact_id)


@pytest.mark.asyncio
async def test_rejection_creates_no_artifact(tmp_path: Path) -> None:
    service, session, models, _ = await setup(tmp_path)
    models.tool = "create_pdf"
    turn = await service.turn(
        session.session_id,
        session.owner_user_id,
        PdfTurnRequest(message="Create a report", client_request_id=uuid4()),
    )
    assert turn.approval
    result = await service.resolve(
        session.session_id,
        session.owner_user_id,
        turn.approval.approval_id,
        PdfApprovalDecision(approve=False, arguments_hash=turn.approval.arguments_hash),
    )
    assert result.approval and result.approval.status == "rejected"
    assert not list(tmp_path.rglob("*.pdf"))


@pytest.mark.asyncio
async def test_fastapi_upload_to_pdf_answer_and_session_restore(tmp_path: Path) -> None:
    import httpx

    from app.auth.contracts import AuthenticatedUser, UserRole
    from app.auth.service import get_authenticated_user
    from app.health import ApplicationDependencies
    from app.main import create_app
    from app.storage import SQLiteActivityEventStore

    service, session, _, data = await setup(tmp_path)
    store = SQLiteWorkflowStore(service.store.database)
    app = create_app(
        dependencies=ApplicationDependencies(
            pdf_service=service,
            chat_store=store,
            workflow_store=store,
            session_file_store=service.files,
            activity_event_store=SQLiteActivityEventStore(service.store.database),
        )
    )
    app.dependency_overrides[get_authenticated_user] = lambda: AuthenticatedUser(
        user_id=session.owner_user_id,
        username="engineer",
        display_name="Engineer",
        role=UserRole.EMPLOYEE,
        auth_session_id=uuid4(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://localhost",
        headers={"Origin": "http://127.0.0.1:5173"},
    ) as client:
        uploaded = await client.post(
            f"/sessions/{session.session_id}/uploads",
            files={"file": ("inspection.pdf", data, "application/pdf")},
        )
        assert uploaded.status_code == 201, uploaded.text
        response = await client.post(
            f"/pdf/sessions/{session.session_id}/turns",
            json={
                "message": "What needs inspection?",
                "clientRequestId": str(uuid4()),
                "uploadId": uploaded.json()["uploadId"],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["pages"] == [1]
        restored = await client.get(f"/pdf/sessions/{session.session_id}")
        assert restored.json()["turns"][0]["answer"] == "Valve 17 needs inspection."
        assert "Calling answer_pdf" in restored.json()["activity"]
        assert "path" not in restored.text.lower()


@pytest.mark.asyncio
async def test_unowned_source_and_second_source_are_rejected(tmp_path: Path) -> None:
    service, session, _, data = await setup(tmp_path)
    with pytest.raises(PermissionError):
        await service.turn(
            session.session_id,
            uuid4(),
            PdfTurnRequest(message="Read it", client_request_id=uuid4(), upload_id=uuid4()),
        )
    await service.files.save_upload(
        session=session,
        upload_id=uuid4(),
        source_id=uuid4(),
        file_name="first.pdf",
        mime_type="application/pdf",
        content=chunks(data),
    )
    from app.storage import UploadSessionStateConflictError

    with pytest.raises(UploadSessionStateConflictError):
        await service.files.save_upload(
            session=session,
            upload_id=uuid4(),
            source_id=uuid4(),
            file_name="second.pdf",
            mime_type="application/pdf",
            content=chunks(data),
        )


@pytest.mark.asyncio
async def test_invented_page_is_not_persisted(tmp_path: Path) -> None:
    from app.ai.errors import InvalidStructuredOutput

    service, session, models, data = await setup(tmp_path)
    models.invalid_page = True
    uploaded = await service.files.save_upload(
        session=session,
        upload_id=uuid4(),
        source_id=uuid4(),
        file_name="first.pdf",
        mime_type="application/pdf",
        content=chunks(data),
    )
    with pytest.raises(InvalidStructuredOutput):
        await service.turn(
            session.session_id,
            session.owner_user_id,
            PdfTurnRequest(
                message="Read it", client_request_id=uuid4(), upload_id=uploaded.upload_id
            ),
        )
    assert not (await service.store.get(session.session_id, session.owner_user_id)).turns
