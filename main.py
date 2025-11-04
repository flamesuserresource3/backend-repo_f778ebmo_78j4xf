import os
from typing import List, Optional
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import db, create_document, get_documents
from schemas import Job, LinkedInProfile

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


# -------------------------------------------------
# LinkedIn OAuth 2.0: Login + Callback + Profile DB
# -------------------------------------------------
LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
LINKEDIN_REDIRECT_URI = os.getenv("LINKEDIN_REDIRECT_URI")


@app.get("/api/auth/linkedin/login")
def linkedin_login():
    if not (LINKEDIN_CLIENT_ID and LINKEDIN_REDIRECT_URI):
        raise HTTPException(status_code=400, detail="LinkedIn OAuth is not configured. Set LINKEDIN_CLIENT_ID and LINKEDIN_REDIRECT_URI.")

    scope = "r_liteprofile r_emailaddress"
    # NOTE: In production, generate and persist a CSRF state value per session
    state = "static_state"
    auth_url = (
        "https://www.linkedin.com/oauth/v2/authorization"
        f"?response_type=code&client_id={LINKEDIN_CLIENT_ID}"
        f"&redirect_uri={requests.utils.quote(LINKEDIN_REDIRECT_URI, safe='')}"
        f"&scope={requests.utils.quote(scope, safe='')}"
        f"&state={state}"
    )
    return {"auth_url": auth_url, "state": state}


@app.get("/api/auth/linkedin/callback")
def linkedin_callback(code: str, state: Optional[str] = None):
    if not (LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET and LINKEDIN_REDIRECT_URI):
        raise HTTPException(status_code=400, detail="LinkedIn OAuth is not configured. Set LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET and LINKEDIN_REDIRECT_URI.")

    # Exchange code for access token
    token_resp = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": LINKEDIN_REDIRECT_URI,
            "client_id": LINKEDIN_CLIENT_ID,
            "client_secret": LINKEDIN_CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    if token_resp.status_code != 200:
        raise HTTPException(status_code=token_resp.status_code, detail=f"LinkedIn token exchange failed: {token_resp.text}")
    token_json = token_resp.json()
    access_token = token_json.get("access_token")
    if not access_token:
        raise HTTPException(status_code=400, detail="No access token in response")

    headers = {"Authorization": f"Bearer {access_token}"}

    # Basic profile
    me_resp = requests.get(
        "https://api.linkedin.com/v2/me?projection=(id,localizedFirstName,localizedLastName,headline,profilePicture(displayImage~:playableStreams),firstName,lastName,vanityName,primaryLocale)",
        headers=headers,
        timeout=20,
    )
    if me_resp.status_code != 200:
        raise HTTPException(status_code=me_resp.status_code, detail=f"LinkedIn /me failed: {me_resp.text}")
    me = me_resp.json()

    # Email
    email_resp = requests.get(
        "https://api.linkedin.com/v2/emailAddress?q=members&projection=(elements*(handle~))",
        headers=headers,
        timeout=20,
    )
    email = None
    if email_resp.status_code == 200:
        ej = email_resp.json()
        try:
            email = ej.get("elements", [])[0].get("handle~", {}).get("emailAddress")
        except Exception:
            email = None

    # Extract avatar url if present
    avatar_url = None
    try:
        imgs = me["profilePicture"]["displayImage~"]["elements"]
        # Choose the highest resolution last entry
        if imgs:
            ident = imgs[-1]["identifiers"][0]
            avatar_url = ident.get("identifier")
    except Exception:
        avatar_url = None

    first_name = me.get("localizedFirstName")
    last_name = me.get("localizedLastName")
    full_name = " ".join([p for p in [first_name, last_name] if p]) or None
    linkedin_id = me.get("id") or ""

    profile = LinkedInProfile(
        linkedin_id=linkedin_id,
        first_name=first_name,
        last_name=last_name,
        full_name=full_name,
        email=email,
        headline=me.get("headline"),
        avatar_url=avatar_url,
        locale=(me.get("primaryLocale", {}).get("language") or "") + ("_" + me.get("primaryLocale", {}).get("country") if me.get("primaryLocale", {}).get("country") else ""),
        raw={"me": me},
    )

    # Upsert into DB
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    now = datetime.now(timezone.utc)
    db["linkedinprofile"].update_one(
        {"linkedin_id": profile.linkedin_id},
        {
            "$set": {**profile.model_dump(), "updated_at": now},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    # Normalize output
    out = profile.model_dump()
    out["created_at"] = now.isoformat()
    out["updated_at"] = now.isoformat()

    return out


@app.get("/api/users/linkedin/{linkedin_id}")
def get_linked_in_user(linkedin_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    doc = db["linkedinprofile"].find_one({"linkedin_id": linkedin_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Profile not found")
    doc["id"] = str(doc.pop("_id"))
    return doc


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
