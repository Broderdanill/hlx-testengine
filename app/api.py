from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import JSONResponse
from io import BytesIO
from typing import Optional
import asyncio
from test_runner import run_test
from bmc_client import get_token, post_result
import os
import logging
import matplotlib.pyplot as plt
import pandas as pd
import base64

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


@api_router.post("/generate-graph")
async def generate_graph(request: Request):
    try:
        data = await request.json()
        entries = [entry["values"] for entry in data["entries"]]
        df = pd.DataFrame(entries)

        if df.empty or "SuiteTitle" not in df.columns or "Status" not in df.columns:
            return JSONResponse(status_code=400, content={"error": "Ogiltig data. Kräver SuiteTitle och Status."})

        # Gruppvis summering
        summary = df.groupby(["SuiteTitle", "Status"]).size().unstack(fill_value=0)
        colors = {"passed": "green", "failed": "red"}

        # --- Graf 1: Stapeldiagram med procent ---
        fig, ax = plt.subplots(figsize=(10, 6))
        summary.plot(kind="bar", stacked=True, ax=ax, color=[colors.get(x, "#333333") for x in summary.columns])

        totals = summary.sum(axis=1)
        for i, (index, row) in enumerate(summary.iterrows()):
            passed = row.get("passed", 0)
            failed = row.get("failed", 0)
            total = passed + failed
            if total > 0:
                pass_pct = passed / total * 100
                fail_pct = failed / total * 100
                ax.text(i, passed / 2, f"{pass_pct:.0f}%", ha='center', va='center', color='white', fontsize=10)
                if failed > 0:
                    ax.text(i, passed + failed / 2, f"{fail_pct:.0f}%", ha='center', va='center', color='white', fontsize=10)

        plt.title("Testresultat per SuiteTitle")
        plt.ylabel("Antal testfall")
        plt.xlabel("SuiteTitle")
        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()

        buf1 = BytesIO()
        plt.savefig(buf1, format="png")
        buf1.seek(0)
        base64_image_1 = base64.b64encode(buf1.read()).decode("utf-8")
        buf1.close()
        plt.close(fig)

        # --- Graf 2: Summerad status per Suite ---
        suite_results = df.groupby("SuiteTitle")["Status"].apply(lambda x: "failed" if "failed" in x.values else "passed")
        suite_summary = suite_results.value_counts()

        fig2, ax2 = plt.subplots(figsize=(6, 6))
        suite_summary.plot.pie(
            labels=[f"{label} ({count})" for label, count in suite_summary.items()],
            colors=[colors.get(x, "#333333") for x in suite_summary.index],
            autopct="%1.1f%%",
            startangle=90,
            ax=ax2
        )
        ax2.set_title("Översikt per SuiteTitle")
        ax2.set_ylabel("")

        buf2 = BytesIO()
        plt.savefig(buf2, format="png")
        buf2.seek(0)
        base64_image_2 = base64.b64encode(buf2.read()).decode("utf-8")
        buf2.close()
        plt.close(fig2)

        return {
            "graph1_base64": base64_image_1,
            "graph2_base64": base64_image_2
        }

    except Exception as e:
        logger.exception("Fel vid generering av grafer")
        return JSONResponse(status_code=500, content={"error": str(e)})


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
