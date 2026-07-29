"""FastFund public reads and storage-backed token-gated writes."""

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from storage import get_store

from .api_core import Resource, require_write_token

store = get_store()
store.init_db()

RESOURCES = (
    Resource("entities", "entities", "Entities", "Funds, SPVs, trusts, and operating entities."),
    Resource("obligations", "obligations", "Obligations", "Entity filing and compliance obligations."),
    Resource("documents", "tax_documents", "Documents", "Tracked tax and regulatory source documents."),
    Resource("jurisdictions", "jurisdictions", "Jurisdictions", "Jurisdictions and source-authority coverage.", primary_key="code"),
)

api = FastAPI(
    title="FastFund API",
    version="1.0.0",
    description=(
        "Open integration access to FastFund entities, obligations, documents, "
        "and jurisdictions. Reads are public. Selected writes require a bearer "
        "token and remain disabled until FASTSME_API_TOKEN is configured."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    servers=[{"url": "https://fund.fastsme.com/api", "description": "Production"}],
    license_info={"name": "MIT"},
)
api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["Accept", "Content-Type"],
)


class EntityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=250)
    type: str = "company"
    domicile: str = ""
    jurisdictions: list[str] = Field(default_factory=list)
    fy_end: str = ""
    activities: list[str] = Field(default_factory=list)
    client_ref: str = ""
    status: str = "active"


@api.get("/", tags=["System"])
def index():
    return {
        "name": "FastFund API",
        "version": "1.0.0",
        "documentation": "https://fund.fastsme.com/developers",
        "swagger": "https://fund.fastsme.com/api/docs",
        "openapi": "https://fund.fastsme.com/api/openapi.json",
    }


@api.get("/v1/health", tags=["System"])
def health():
    return {"status": "ok", "product": "FastFund", "version": "1.0.0"}


@api.get("/v1/entities", tags=["Entities"])
def list_entities(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    rows = store.list_entities(limit=limit + offset)
    return {"data": rows[offset:offset + limit], "meta": {"total": len(rows), "limit": limit, "offset": offset}}


@api.get("/v1/entities/{item_id}", tags=["Entities"])
def get_entity(item_id: int):
    row = store.get_entity(item_id)
    if not row:
        raise HTTPException(404, detail={"code": "not_found", "message": "Entity not found.", "details": {"id": item_id}})
    return row


@api.post("/v1/entities", status_code=201, dependencies=[Depends(require_write_token)], tags=["Entities"])
def create_entity(payload: EntityCreate):
    item_id = store.upsert_entity(payload.model_dump())
    return store.get_entity(item_id)


@api.get("/v1/obligations", tags=["Obligations"])
def list_obligations(entity_id: int | None = None, status: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    rows = store.list_obligations(entity_id=entity_id, status=status, limit=limit + offset)
    return {"data": rows[offset:offset + limit], "meta": {"total": len(rows), "limit": limit, "offset": offset}}


@api.get("/v1/obligations/{item_id}", tags=["Obligations"])
def get_obligation(item_id: int):
    row = store.get_obligation(item_id)
    if not row:
        raise HTTPException(404, detail={"code": "not_found", "message": "Obligation not found.", "details": {"id": item_id}})
    return row


@api.get("/v1/jurisdictions", tags=["Jurisdictions"])
def list_jurisdictions():
    rows = store.list_jurisdictions_with_counts()
    return {"data": rows, "meta": {"total": len(rows), "limit": len(rows), "offset": 0}}


@api.get("/v1/jurisdictions/{code}", tags=["Jurisdictions"])
def get_jurisdiction(code: str):
    row = store.get_jurisdiction(code)
    if not row:
        raise HTTPException(404, detail={"code": "not_found", "message": "Jurisdiction not found.", "details": {"code": code}})
    return row


@api.get("/v1/documents", tags=["Documents"])
def list_documents(jurisdiction: str | None = None, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    jurisdictions = [jurisdiction] if jurisdiction else [row["code"] for row in store.list_jurisdictions_with_counts()]
    rows = []
    for code in jurisdictions:
        rows.extend(store.list_documents_for_jurisdiction(code))
    rows = rows[offset:offset + limit]
    return {"data": rows, "meta": {"total": len(rows), "limit": limit, "offset": offset}}


@api.get("/v1/documents/{item_id}", tags=["Documents"])
def get_document(item_id: int):
    row = store.get_document_by_id(item_id)
    if not row:
        raise HTTPException(404, detail={"code": "not_found", "message": "Document not found.", "details": {"id": item_id}})
    return row
