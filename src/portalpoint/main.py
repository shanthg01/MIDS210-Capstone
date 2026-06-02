# Step 4: Wire up FastAPI app, register routers, add CORS middleware, /health endpoint
from fastapi import FastAPI

app = FastAPI(
    title="PortalPoint API",
    version="0.1.0",
    description="Transfer portal decision platform for college basketball",
)
