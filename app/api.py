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
from matplotlib import patheffects as path_effects
import pandas as pd
import base64

# Logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=getattr(logging, log_level, logging.INFO)
)
logger = logging.getLogger(__name__)

# FastAPI setup
api_router = APIRouter()
queue = asyncio.Queue()
current_test: Optional[dict] = None

# Routes

@api_router.post("/run-test")
async def run_test_endpoint(request: Request):
    data = await request.json()
    parallel = int(data.get("parallel", 1))
    logger.info(f"Mottog testförfrågan: {data.get('TestName')} (RunID: {data.get('TestRunId')}) parallellt: {parallel}")

    for i in range(parallel):
        asyncio.create_task(run_test(data.copy()))

    return {
        "message": f"Startade {parallel} tester parallellt."
    }


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
            "passed": "#198754",  # Bootstrap green
            "failed": "#dc3545"   # Bootstrap red
        }

        plt.style.use('default')
        plt.rcParams.update({
            'font.size': 12,
            'axes.titlesize': 17,
            'axes.labelsize': 13
        })

        def save_fig_to_base64(fig):
            buf = BytesIO()
            plt.savefig(buf, format="png", dpi=250, bbox_inches="tight")
            buf.seek(0)
            img_str = base64.b64encode(buf.read()).decode("utf-8")
            buf.close()
            plt.close(fig)
            return img_str

        def plot_bar_grouped(df_grouped, title, xlabel):
            df_grouped = df_grouped.reindex(columns=["failed", "passed"], fill_value=0)
            fig, ax = plt.subplots(figsize=(12, 7))
            bar_colors = [colors.get(x, "#999999") for x in df_grouped.columns]

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
                    if failed > 0:
                        percent_failed = (failed / total) * 100
                        ax.text(i, failed * 0.5, f"{percent_failed:.0f}%", ha='center', va='center',
                                color='white', fontsize=12, fontweight='bold',
                                path_effects=[path_effects.withStroke(linewidth=2, foreground='black')])

                    if passed > 0:
                        percent_passed = (passed / total) * 100
                        ax.text(i, failed + passed * 0.5, f"{percent_passed:.0f}%", ha='center', va='center',
                                color='white', fontsize=12, fontweight='bold',
                                path_effects=[path_effects.withStroke(linewidth=2, foreground='black')])

            ax.set_title(title)
            ax.set_ylabel("Antal testfall")
            ax.set_xlabel(xlabel)
            ax.tick_params(axis='x', labelsize=10)
            ax.tick_params(axis='y', labelsize=10)
            plt.xticks(rotation=45, ha="right")
            plt.tight_layout()

            return save_fig_to_base64(fig)

        def plot_pie(summary_series, title):
            fig, ax = plt.subplots(figsize=(8, 8))

            labels = summary_series.index.tolist()
            values = summary_series.values.tolist()
            pie_colors = [colors.get(label, "#999999") for label in labels]

            wedges, texts, autotexts = ax.pie(
                values,
                labels=[f"{label.title()} ({value})" for label, value in zip(labels, values)],
                colors=pie_colors,
                autopct="%1.1f%%",
                startangle=90,
                textprops={'fontsize': 12, 'color': 'white', 'weight': 'bold'}
            )

            for text in autotexts:
                text.set_path_effects([path_effects.withStroke(linewidth=2, foreground='black')])

            ax.set_title(title)
            ax.set_ylabel("")
            ax.axis('equal')

            return save_fig_to_base64(fig)

        # Grafer
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


# Worker
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