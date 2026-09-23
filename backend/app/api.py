"""HTTP interface over the two-stage pipeline.

Thin on purpose. All the thinking lives in extract.py and assess.py; this only
translates between HTTP and those, and reports what happened on the way.

The attempt lists are part of the response rather than hidden, because watching
the checker reject something and the model fix it is the most interesting thing
the tool does, and burying it would be a waste.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.assess import assess_process
from app.export_n8n import to_n8n
from app.extract import extract_process
from app.llm.base import LLMError, StructuredLLM
from app.llm.gemini import GeminiClient
from app.limits import address_of, budget_salt, open_budget, visitor_id
from app.llm.record import RecordingLLM, ReplayLLM, available_cases
from app.schemas.assessment import AutomationPlan
from app.schemas.effort import EffortInput, EffortSummary, summarise_effort
from app.shape import NUDGE, looks_like_a_request
from app.share import ShareTooLarge, open_store
from app.schemas.process import Answer, ProcessGraph

EXAMPLES = [
    {
        "id": "invoice-with-approval",
        "label": "Invoice intake, with a manager check",
        "description": (
            "Every morning I go through my emails looking for invoices. When I find "
            "one I download the PDF attachment, read the total off it, and type that "
            "into our Google Sheet. Then I message accounting on Slack to say it's "
            "in. If it's a big one, over five thousand pounds, I check with my "
            "manager first before I put it through."
        ),
    },
    {
        "id": "payment-no-approval",
        "label": "Paying suppliers, with nobody checking",
        "description": (
            "Every Friday I go through the supplier invoices sitting in our shared "
            "inbox. I read the amount off each one, pay it straight from our business "
            "account through the banking portal, mark it as paid in the spreadsheet, "
            "and then delete the email to keep the inbox tidy."
        ),
    },
]

# SQLite locally, blob storage when deployed. See app/share.py.
store = open_store()

# The public demo runs on one shared key, so model calls come out of a daily
# budget. See app/limits.py.
budget = open_budget()
SALT = budget_salt()


def _deployed() -> bool:
    """Running on a serverless host rather than somebody's machine.

    Set by the platform. Used only to skip things that need a writable disk,
    which is the one thing that genuinely differs and the one that produced a
    500 on the first real request after the key went on.
    """

    return bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def _model_key_configured() -> bool:
    """Whether this deployment can talk to a model at all.

    The public demo runs without a key on purpose, so the recorded examples
    cost nothing and cannot be used to burn somebody's quota.
    """

    return bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


app = FastAPI(
    title="Automation Architect",
    description="Turns a plain-English process description into a validated automation plan.",
)

# The front end is served separately in development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyseRequest(BaseModel):
    description: str = Field(min_length=20, max_length=4000)
    case: str | None = Field(
        default=None,
        description="Replay a recorded case instead of calling the API. No key needed.",
    )
    max_attempts: int = Field(default=3, ge=1, le=5)
    answers: list[Answer] = Field(
        default_factory=list,
        max_length=12,
        description="Replies to the questions a previous run asked. Treated as fact.",
    )


class AttemptInfo(BaseModel):
    number: int
    ok: bool
    errors: list[str]


class AnalyseResponse(BaseModel):
    ok: bool
    model: str
    graph: ProcessGraph | None = None
    plan: AutomationPlan | None = None
    extraction_attempts: list[AttemptInfo] = []
    assessment_attempts: list[AttemptInfo] = []
    error: str | None = None


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "cases": available_cases()}


@app.get("/api/examples")
def examples() -> dict:
    """Descriptions to prefill the box with, and whether each can be replayed."""

    replayable = set(available_cases())
    return {
        "examples": [
            {**e, "replayable": e["id"] in replayable} for e in EXAMPLES
        ]
    }


@app.post("/api/analyse", response_model=AnalyseResponse)
def analyse(request: AnalyseRequest, http: Request) -> AnalyseResponse:
    try:
        if request.case:
            llm: StructuredLLM = ReplayLLM(request.case)
        elif looks_like_a_request(request.description):
            # Caught before the model, not after. An answer to the wrong
            # question costs two calls and tells the person nothing.
            return AnalyseResponse(ok=False, model="none", error=NUDGE)
        elif not _model_key_configured():
            # Deployed without a key, which is the normal state of the public
            # demo. Telling a visitor to set an environment variable on their
            # own machine is advice for a problem they do not have.
            return AnalyseResponse(
                ok=False,
                model="none",
                error=(
                    "This demo has no model key attached, so it can only run the "
                    "recorded examples. Pick one above to see the whole thing "
                    "work, or run the project yourself with your own free key."
                ),
            )
        else:
            # A real call against a shared key, so it comes out of the day's
            # budget. Checked before the client is built, so a refusal costs
            # nothing.
            allowance = budget.spend(visitor_id(address_of(http), SALT))
            if not allowance.allowed:
                return AnalyseResponse(ok=False, model="none", error=allowance.reason)

            # Recording is a development convenience: it saves each response so
            # the next run can replay it for nothing. Deployed there is no disk
            # to save to and nothing would survive the request anyway, so the
            # wrapper is left off rather than made to fail quietly.
            llm = GeminiClient() if _deployed() else RecordingLLM(GeminiClient())
    except LLMError as exc:
        return AnalyseResponse(ok=False, model="none", error=str(exc))

    try:
        extraction = extract_process(
            request.description,
            llm,
            max_attempts=request.max_attempts,
            answers=request.answers,
        )
    except LLMError as exc:
        return AnalyseResponse(ok=False, model=llm.name, error=str(exc))

    extraction_attempts = _describe(extraction.attempts)

    if extraction.graph is None:
        return AnalyseResponse(
            ok=False,
            model=llm.name,
            extraction_attempts=extraction_attempts,
            error="Could not turn that description into a valid process.",
        )

    try:
        assessment = assess_process(
            extraction.graph, llm, max_attempts=request.max_attempts
        )
    except LLMError as exc:
        # A failure here still leaves a useful process map, so return it.
        return AnalyseResponse(
            ok=False,
            model=llm.name,
            graph=extraction.graph,
            extraction_attempts=extraction_attempts,
            error=str(exc),
        )

    return AnalyseResponse(
        ok=assessment.plan is not None,
        model=llm.name,
        graph=extraction.graph,
        plan=assessment.plan,
        extraction_attempts=extraction_attempts,
        assessment_attempts=_describe(assessment.attempts),
        error=None if assessment.plan else "Could not produce a valid assessment.",
    )


def _describe(attempts) -> list[AttemptInfo]:
    return [
        AttemptInfo(number=a.number, ok=a.ok, errors=a.errors) for a in attempts
    ]


class ExportRequest(BaseModel):
    graph: ProcessGraph
    plan: AutomationPlan | None = None


@app.post("/api/export/n8n")
def export_n8n(request: ExportRequest) -> dict:
    """An importable n8n workflow scaffold for a process already analysed.

    Takes the graph and plan back from the client rather than re-running the
    pipeline, so exporting costs nothing and always matches what is on screen.
    """

    return to_n8n(request.graph, request.plan)


class EffortRequest(BaseModel):
    plan: AutomationPlan
    effort: EffortInput


@app.post("/api/effort", response_model=EffortSummary)
def effort(request: EffortRequest) -> EffortSummary:
    """Work out where the time goes, from figures the person supplied.

    Deliberately a separate call from the analysis. The verdicts come from a
    model; these numbers do not, and keeping them apart makes that obvious in
    the code as well as in the interface.
    """

    return summarise_effort(request.effort, request.plan)


class ShareRequest(BaseModel):
    graph: ProcessGraph
    plan: AutomationPlan | None = None
    effort: EffortInput | None = None


class ShareCreated(BaseModel):
    id: str
    expires_at: datetime


class SharedAnalysis(BaseModel):
    id: str
    title: str
    created_at: datetime
    expires_at: datetime
    graph: ProcessGraph
    plan: AutomationPlan | None = None
    effort: EffortInput | None = None


@app.post("/api/share", response_model=ShareCreated)
def create_share(request: ShareRequest) -> ShareCreated:
    """Store an analysis so it can be sent to somebody else.

    The link is unlisted rather than private: anyone holding it can read the
    analysis. That is the right trade for "send this to my manager", but it is
    why ids are unguessable and why shares expire.
    """

    try:
        record = store.create(
            payload=request.model_dump(mode="json"),
            title=request.graph.title,
        )
    except ShareTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    return ShareCreated(id=record.id, expires_at=record.expires_at)


@app.get("/api/share/{share_id}", response_model=SharedAnalysis)
def read_share(share_id: str) -> SharedAnalysis:
    record = store.get(share_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="That link has expired or never existed. Shares last 30 days.",
        )

    return SharedAnalysis(
        id=record.id,
        title=record.title,
        created_at=record.created_at,
        expires_at=record.expires_at,
        **record.payload,
    )


# --- The built front end ------------------------------------------------------

# Where `npm run build` puts the front end. Present when deployed, absent in
# development, where Vite serves it on its own port and proxies /api back here.
BUILT_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if BUILT_FRONTEND.is_dir():
    # fallback="index.html" is what makes a shared link work. /s/abc123 is a
    # route the browser understands and the server has never heard of, so
    # without this, opening one directly returns a 404 rather than the page
    # that knows how to load it. Registered last, and API routes win regardless
    # of order, so nothing here can shadow /api.
    app.frontend("/", directory=str(BUILT_FRONTEND), fallback="index.html")
