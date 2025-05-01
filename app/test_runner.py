from playwright.async_api import async_playwright
import base64, time
from datetime import datetime
from logging import getLogger

logger = getLogger(__name__)

async def run_test(recording: dict):
    logger.info(f"Startar test med titel: {recording.get('title', 'N/A')}")
    result = {
        "Status": "passed",
        "ErrorMessage": None,
        "ScreenshotBase64": None,
        "ScreenshotMissing": True,
        "DurationMs": 0,
        "RunTime": datetime.utcnow().isoformat() + "Z"
    }

    start = time.time()
    page = None

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel="msedge", headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            current_frame = page.main_frame

            for i, step in enumerate(recording.get("steps", [])):
                step_type = step.get("type")
                logger.debug(f"Steg {i+1}/{len(recording['steps'])}: {step_type}")

                if "frame" in step:
                    try:
                        frame_index = step["frame"][0]
                        frames = page.frames
                        if frame_index < len(frames):
                            current_frame = frames[frame_index]
                            logger.debug(f"Använder frame index {frame_index}")
                        else:
                            logger.warning(f"Ogiltigt frame-index: {frame_index}")
                            continue
                    except Exception as e:
                        logger.warning(f"Kunde inte byta till frame: {e}")
                        continue

                try:
                    if step_type == "navigate":
                        await page.goto(step["url"], wait_until="load", timeout=20000)

                    elif step_type == "click":
                        await _try_selectors(
                            step,
                            current_frame,
                            action=lambda loc: loc.click(
                                position={"x": step.get("offsetX", 0), "y": step.get("offsetY", 0)},
                                timeout=8000,
                                force=True
                            )
                        )
                        await page.wait_for_timeout(300)  # Vänta efter klick

                    elif step_type == "change":
                        await _try_selectors(
                            step,
                            current_frame,
                            action=lambda loc: loc.fill(step.get("value", ""), timeout=5000)
                        )

                    elif step_type == "hover":
                        await _try_selectors(
                            step,
                            current_frame,
                            action=lambda loc: loc.hover(timeout=5000)
                        )

                    elif step_type == "waitForSelector":
                        await _try_selectors(
                            step,
                            current_frame,
                            action=lambda loc: loc.wait_for(timeout=5000)
                        )

                    elif step_type == "keyDown":
                        await page.keyboard.down(step.get("key", ""))

                    elif step_type == "keyUp":
                        await page.keyboard.up(step.get("key", ""))

                    elif step_type == "setViewport":
                        await page.set_viewport_size({
                            "width": step.get("width", 1280),
                            "height": step.get("height", 720)
                        })

                    elif step_type == "scroll":
                        await current_frame.evaluate("window.scrollBy(0, 100)")

                    elif step_type == "waitForTimeout":
                        await current_frame.wait_for_timeout(step.get("timeout", 1000))

                    elif step_type == "screenshot":
                        await page.screenshot(path=f"screenshot_{i}.png")

                    elif step_type == "close":
                        await page.close()

                    elif step_type == "assert":
                        events = step.get("assertedEvents", [])
                        for event in events:
                            if event["type"] == "navigation":
                                expected_url = event.get("url")
                                expected_title = event.get("title")
                                actual_url = page.url
                                actual_title = await page.title()
                                if expected_url and expected_url not in actual_url:
                                    raise AssertionError(f"URL stämmer ej. Förväntat: {expected_url}, Fick: {actual_url}")
                                if expected_title and expected_title.strip() and expected_title.strip() not in actual_title:
                                    raise AssertionError(f"Titel stämmer ej. Förväntat: {expected_title}, Fick: {actual_title}")
                    else:
                        logger.warning(f"Ohanterat stegtyp: {step_type}")

                except Exception as step_error:
                    msg = f"Steg {i+1} ({step_type}) misslyckades: {step_error}"
                    logger.error(msg)
                    result["Status"] = "failed"
                    result["ErrorMessage"] = msg
                    try:
                        if page and not page.is_closed():
                            screenshot = await page.screenshot()
                            result["ScreenshotBase64"] = base64.b64encode(screenshot).decode("utf-8")
                            result["ScreenshotMissing"] = False
                            logger.debug("Skärmdump tagen vid fel.")
                        else:
                            logger.warning("Sidan är stängd – ingen skärmdump kunde tas.")
                            result["ScreenshotMissing"] = True
                    except Exception as e:
                        logger.warning(f"Kunde inte ta skärmdump: {e}")
                        result["ScreenshotMissing"] = True
                    raise step_error

            await browser.close()

    except Exception as e:
        logger.error(f"Testet misslyckades: {e}")
        result["Status"] = "failed"
        result["ErrorMessage"] = str(e)
        try:
            if page and not page.is_closed():
                screenshot = await page.screenshot()
                result["ScreenshotBase64"] = base64.b64encode(screenshot).decode("utf-8")
                result["ScreenshotMissing"] = False
            else:
                logger.warning("Sidan är stängd; kan inte ta skärmdump.")
                result["ScreenshotMissing"] = True
        except Exception as ss_err:
            logger.warning(f"Kunde inte ta skärmdump: {ss_err}")
            result["ScreenshotMissing"] = True

    finally:
        result["DurationMs"] = int((time.time() - start) * 1000)
        logger.info(f"Test klart. Status: {result['Status']}, Tid: {result['DurationMs']}ms")
        return result


def _normalize_selector(raw_selector: str) -> str | None:
    if raw_selector.startswith("aria/"):
        return None
    elif raw_selector.startswith("xpath/"):
        return "xpath=" + raw_selector[6:]
    elif raw_selector.startswith("pierce/"):
        return None
    elif raw_selector.startswith("text/"):
        return "text=" + raw_selector[5:]
    elif raw_selector.startswith("css/"):
        return raw_selector[4:]
    elif raw_selector.startswith("testid/"):
        return f"[data-testid='{raw_selector[7:]}']"
    else:
        return raw_selector


async def _try_selectors(step, frame, action):
    selector_groups = step.get("selectors", [])
    
    for group in selector_groups:
        for raw_selector in group:
            selector = _normalize_selector(raw_selector)
            if not selector:
                logger.debug(f"Hoppar över osupportad selector: {raw_selector}")
                continue

            try:
                base_locator = frame.locator(selector)
                count = await base_locator.count()

                if count == 0:
                    logger.debug(f"Selector {selector} hittade inga element.")
                    continue

                logger.debug(f"Selector {selector} matchade {count} element – testar varje enskilt.")

                for i in range(count):
                    try:
                        candidate = base_locator.nth(i)
                        await candidate.wait_for(state="attached", timeout=3000)

                        if await candidate.is_visible():
                            await candidate.scroll_into_view_if_needed()
                            await action(candidate)
                            logger.debug(f"Agerade på selector: {selector} [element {i}]")
                            return
                        else:
                            logger.debug(f"Selector {selector} [element {i}] är inte synlig.")
                    except Exception as inner_e:
                        logger.debug(f"Misslyckades på selector: {selector} [element {i}], fel: {inner_e}")

            except Exception as e:
                logger.debug(f"Misslyckades på selector: {selector}, fel: {e}")

    raise Exception("Inget selektoralternativ fungerade")
