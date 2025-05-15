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

        if "TestName" not in df.columns:
            return JSONResponse(status_code=400, content={"error": "Ogiltig data. Kräver även TestName för vissa grafer."})

        # Bootstrap-liknande färgpalett
        colors = {
            "passed": "#198754",  # Bootstrap's green
            "failed": "#dc3545"   # Bootstrap's red
        }

        images = []
        plt.style.use('default')  # Ljus bakgrund

        def save_fig_to_base64(fig):
            buf = BytesIO()
            plt.savefig(buf, format="png", dpi=250, bbox_inches="tight")
            buf.seek(0)
            img_str = base64.b64encode(buf.read()).decode("utf-8")
            buf.close()
            plt.close(fig)
            return img_str

        def plot_bar_grouped(df_grouped, title, xlabel):
            fig, ax = plt.subplots(figsize=(12, 7))
            color_keys = df_grouped.columns.tolist()
            bar_colors = [colors.get(x, "#999999") for x in color_keys]

            df_grouped.plot(
                kind="bar",
                stacked=True,
                ax=ax,
                color=bar_colors,
                edgecolor="black",
                linewidth=0.8
            )

            ax.grid(True, which='major', axis='y', linestyle='--', alpha=0.7)

            for i, (index, row) in enumerate(df_grouped.iterrows()):
                passed = row.get("passed", 0)
                failed = row.get("failed", 0)
                total = passed + failed
                if total > 0:
                    if passed > 0:
                        ax.text(i, passed * 0.5, f"{(passed / total * 100):.0f}%", ha='center', va='center', color='white', fontsize=11)
                    if failed > 0:
                        ax.text(i, passed + failed * 0.5, f"{(failed / total * 100):.0f}%", ha='center', va='center', color='white', fontsize=11)

            ax.set_title(title, fontsize=17)
            ax.set_ylabel("Antal testfall")
            ax.set_xlabel(xlabel)
            ax.tick_params(axis='x', labelsize=10)
            ax.tick_params(axis='y', labelsize=10)
            plt.xticks(rotation=45, ha="right")
            plt.tight_layout()

            return save_fig_to_base64(fig)

        def plot_pie(summary_series, title):
            fig, ax = plt.subplots(figsize=(8, 8))
            summary_series.plot.pie(
                labels=[f"{label} ({count})" for label, count in summary_series.items()],
                colors=[colors.get(x, "#999999") for x in summary_series.index],
                autopct="%1.1f%%",
                startangle=90,
                textprops={'fontsize': 11},
                ax=ax
            )
            ax.set_title(title, fontsize=16)
            ax.set_ylabel("")
            return save_fig_to_base64(fig)

        # --- Grafer ---
        graph1 = plot_bar_grouped(
            df.groupby(["SuiteTitle", "Status"]).size().unstack(fill_value=0),
            "Testresultat per SuiteTitle",
            "SuiteTitle"
        )

        suite_results = df.groupby("SuiteTitle")["Status"].apply(lambda x: "failed" if "failed" in x.values else "passed")
        graph2 = plot_pie(suite_results.value_counts(), "Översikt per SuiteTitle")

        graph3 = plot_bar_grouped(
            df.groupby(["TestName", "Status"]).size().unstack(fill_value=0),
            "Testresultat per TestName",
            "TestName"
        )

        testname_results = df.groupby("TestName")["Status"].apply(lambda x: "failed" if "failed" in x.values else "passed")
        graph4 = plot_pie(testname_results.value_counts(), "Översikt per TestName")

        total_passed = df[df["Status"] == "passed"].shape[0]
        total_failed = df[df["Status"] == "failed"].shape[0]

        return {
            "graph1_base64": graph1,
            "graph2_base64": graph2,
            "graph3_base64": graph3,
            "graph4_base64": graph4,
            "summary": {
                "total_passed": total_passed,
                "total_failed": total_failed,
                "total_tests": len(df)
            }
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
