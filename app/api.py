from fastapi import APIRouter, Request, BackgroundTasks
from typing import Optional
import asyncio
from test_runner import run_test
from bmc_client import get_token, post_result
import os
import logging

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=getattr(logging, log_level, logging.INFO)
)
logger = logging.getLogger(__name__)

api_router = APIRouter()
queue = asyncio.Queue()
current_test: Optional[dict] = None


@api_router.post("/run-test")
async def run_test_endpoint(request: Request):
    data = await request.json()
    logger.info(f"Mottog testförfrågan: {data.get('TestName')} (RunID: {data.get('TestRunId')})")
    await queue.put(data)
    return {"message": "Testet har lagts i kön.", "position": queue.qsize()}


@api_router.get("/queue-status")
async def queue_status():
    return {
        "queueLength": queue.qsize(),
        "queueItems": list(queue._queue),
        "currentRunning": current_test or {"TestName": "", "TestRunId": ""},
        "isProcessing": current_test is not None
    }


async def worker():
    global current_test
    while True:
        data = await queue.get()
        logger.debug(f"Hämtar test från kö: {data}")
        current_test = {
            "TestName": data.get("TestName", ""),
            "TestRunId": data.get("TestRunId", "")
        }
        logger.info(f"Kör test: {current_test['TestName']}")
        try:
            result = await run_test(data.get("Recording"))
            result.update({
                "TestName": data.get("TestName"),
                "SuiteTitle": data.get("SuiteTitle", "N/A"),
                "TestRunId": data.get("TestRunId")
            })

            token = await get_token()
            await post_result(result, token)

        except Exception as e:
            logger.exception(f"Fel vid testkörning eller rapportering: {e}")
        finally:
            logger.info(f"Färdig med test: {current_test['TestName']}")
            current_test = None
            queue.task_done()


async def start_worker():
    logger.info("Startar worker-loop...")
    asyncio.create_task(worker())
