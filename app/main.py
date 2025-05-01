from fastapi import FastAPI
from api import api_router, start_worker
import os
import logging

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=getattr(logging, log_level, logging.INFO)
)
logger = logging.getLogger(__name__)

app = FastAPI()
app.mount("/api", api_router)

@app.on_event("startup")
async def startup_event():
    logger.info("Startar hlx-testengine...")
    await start_worker()
