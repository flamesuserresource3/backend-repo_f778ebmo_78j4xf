import os
from typing import List, Optional
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import db, create_document, get_documents
from schemas import Job

app = FastAPI(title="Job Nexus API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "Hello from FastAPI Backend!"}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}


@app.get("/test")
def test_database():
    """Test endpoint to check if database is available and accessible"""
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, "name") else "✅ Connected"
            response["connection_status"] = "Connected"

            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:  # noqa: BLE001
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except Exception as e:  # noqa: BLE001
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


# -----------------------------
# Job endpoints (MongoDB-backed)
# -----------------------------
COLLECTION = "job"

SAMPLE_JOBS: List[Job] = [
    Job(
        title="Senior Frontend Engineer",
        company="LinkedIn",
        location="Remote • US",
        tags=["React", "TypeScript", "Accessibility"],
        match=92,
    ),
    Job(
        title="Data Scientist, NLP",
        company="Indeed",
        location="Austin, TX",
        tags=["Python", "NLP", "HuggingFace"],
        match=88,
    ),
    Job(
        title="DevOps Engineer",
        company="Seek (AU)",
        location="Sydney, AU",
        tags=["AWS", "Kubernetes", "Terraform"],
        match=84,
    ),
    Job(
        title="Full‑stack Developer",
        company="Naukri (IN)",
        location="Bengaluru, IN",
        tags=["Node.js", "MongoDB", "Next.js"],
        match=90,
    ),
    Job(
        title="AI Product Manager",
        company="Glassdoor",
        location="San Francisco, CA",
        tags=["Product", "AI", "Strategy"],
        match=86,
    ),
]


def _ensure_seed_data():
    if db is None:
        return
    if db[COLLECTION].count_documents({}) == 0:
        for j in SAMPLE_JOBS:
            create_document(COLLECTION, j)


class JobsResponse(BaseModel):
    items: List[dict]
    count: int


@app.get("/api/jobs", response_model=JobsResponse)
def list_jobs(q: Optional[str] = Query(None), tags: Optional[str] = Query(None)):
    """List jobs with basic filtering.

    - q: free text across title/company/location (case-insensitive)
    - tags: comma-separated list; matches jobs containing all tags
    """
    _ensure_seed_data()

    query: dict = {}

    if q:
        regex = {"$regex": q, "$options": "i"}
        query["$or"] = [
            {"title": regex},
            {"company": regex},
            {"location": regex},
        ]

    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if tag_list:
            query["tags"] = {"$all": tag_list}

    docs = get_documents(COLLECTION, query)

    # Normalize ObjectId for JSON
    for d in docs:
        if "_id" in d:
            d["id"] = str(d.pop("_id"))

    # Sort by match desc by default
    docs.sort(key=lambda x: x.get("match", 0), reverse=True)

    return JobsResponse(items=docs, count=len(docs))


@app.post("/api/jobs")
def create_job(job: Job):
    inserted_id = create_document(COLLECTION, job)
    return {"id": inserted_id}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
