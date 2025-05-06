import os
import httpx
from logging import getLogger

logger = getLogger(__name__)

async def get_token():
    logger.info("Begär token från BMC med form-urlencoded...")
    async with httpx.AsyncClient(verify=False) as client:  # 👈
        response = await client.post(
            os.getenv("BMC_AUTH_URL"),
            data={
                "username": os.getenv("USERNAME"),
                "password": os.getenv("PASSWORD")
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10
        )
        response.raise_for_status()
        token = response.text.strip()
        logger.debug(f"Mottog token: {token[:10]}...")
        return token

async def post_result(data: dict, token: str):
    logger.info("Postar resultat till BMC...")

    allowed_fields = [
        "Status",
        "ErrorMessage",
        "ScreenshotBase64",
        "ScreenshotMissing",
        "DurationMs",
        "RunTime",
        "TestName",
        "SuiteTitle",
        "TestRunId"
    ]

    filtered_data = {k: v for k, v in data.items() if k in allowed_fields}

    if logger.isEnabledFor(10):  # 10 = logging.DEBUG
        import json
        logger.debug("Begäran till BMC (JSON):\n%s", json.dumps({"values": filtered_data}, indent=2))

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(
            os.getenv("BMC_HELIX_API"),
            headers={"Authorization": f"AR-JWT {token}"},
            json={"values": filtered_data},
            timeout=10
        )
        response.raise_for_status()
        logger.info("Resultat skickat till BMC.")
