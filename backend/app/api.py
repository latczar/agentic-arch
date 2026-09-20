"""HTTP interface over the two-stage pipeline.

Thin on purpose. All the thinking lives in extract.py and assess.py; this only
translates between HTTP and those, and reports what happened on the way.

The attempt lists are part of the response rather than hidden, because watching
the checker reject something and the model fix it is the most interesting thing
the tool does, and burying it would be a waste.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.assess import assess_process
from app.export_n8n import to_n8n
from app.extract import extract_process
from app.llm.base import LLMError, StructuredLLM
from app.llm.gemini import GeminiClient
from app.llm.record import RecordingLLM, ReplayLLM, available_cases
from app.schemas.assessment import AutomationPlan
from app.schemas.process import ProcessGraph

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

app = FastAPI(
    title="AI Automation Architect",
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
def analyse(request: AnalyseRequest) -> AnalyseResponse:
    try:
        llm: StructuredLLM = (
            ReplayLLM(request.case) if request.case else RecordingLLM(GeminiClient())
        )
    except LLMError as exc:
        return AnalyseResponse(ok=False, model="none", error=str(exc))

    try:
        extraction = extract_process(
            request.description, llm, max_attempts=request.max_attempts
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
