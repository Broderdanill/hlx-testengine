from playwright.async_api import async_playwright
import base64, time, traceback
from datetime import datetime
from logging import getLogger

logger = getLogger(__name__)

DEFAULT_TIMEOUTS = {
    "navigate": 20000,
    "click": 8000,
    "change": 5000,
    "hover": 5000,
    "waitForSelector": 5000,
    "default": 3000
}

async def run_test(recording: dict):
    logger.info(f"Startar test med titel: {recording.get('title', 'N/A')}")
    result = {
        "Status": "passed",
        "ErrorMessage": None,
        "ErrorStack": None,
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

            page.on("console", lambda msg: logger.debug(f"Console [{msg.type}]: {msg.text}"))
            page.on("pageerror", lambda exc: logger.error(f"Page error: {exc}"))

            current_frame = page.main_frame
            popup_pages = []
            context.on("page", lambda new_page: popup_pages.append(new_page))

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
                    timeout = step.get("timeout", DEFAULT_TIMEOUTS.get(step_type, DEFAULT_TIMEOUTS["default"]))

                    if step_type == "navigate":
                        url = step.get("url", "")
                        if url.startswith("edge://") or url.startswith("chrome://") or url.startswith("about:"):
                            raise Exception(f"Ogiltig eller osupportad URL: {url}")
                        await page.goto(url, wait_until="load", timeout=timeout)
                        await _wait_for_dom_stability(page)

                    elif step_type == "switchToPopup":
                        if popup_pages:
                            page = popup_pages[-1]
                            current_frame = page.main_frame
                            logger.info("Växlat till popup-fönster")
                        else:
                            raise Exception("Inget popup-fönster hittades")

                    elif step_type == "switchToMain":
                        page = context.pages[0]
                        current_frame = page.main_frame
                        logger.info("Växlat tillbaka till huvudsidan")

                    elif step_type == "click":
                        await _try_selectors_with_retries(
                            step,
                            current_frame,
                            action=lambda loc: loc.click(
                                position={"x": step.get("offsetX", 0), "y": step.get("offsetY", 0)},
                                timeout=timeout,
                                force=True
                            )
                        )
                        await page.wait_for_timeout(300)

                    elif step_type == "doubleClick":
                        await _try_selectors_with_retries(
                            step,
                            current_frame,
                            action=lambda loc: loc.dblclick(timeout=timeout)
                        )

                    elif step_type == "rightClick":
                        await _try_selectors_with_retries(
                            step,
                            current_frame,
                            action=lambda loc: loc.click(button="right", timeout=timeout)
                        )

                    elif step_type == "type":
                        await page.keyboard.type(step.get("text", ""), delay=step.get("delay", 100))

                    elif step_type == "press":
                        await page.keyboard.press(step.get("key", ""), timeout=timeout)

                    elif step_type == "dragAndDrop":
                        source = step.get("source")
                        target = step.get("target")
                        if source and target:
                            src_selector = _normalize_selector(source)
                            tgt_selector = _normalize_selector(target)
                            if src_selector and tgt_selector:
                                await page.locator(src_selector).drag_to(page.locator(tgt_selector))

                    elif step_type == "change":
                        await _try_selectors_with_retries(
                            step,
                            current_frame,
                            action=lambda loc: loc.fill(step.get("value", ""), timeout=timeout)
                        )

                    elif step_type == "hover":
                        await _try_selectors_with_retries(
                            step,
                            current_frame,
                            action=lambda loc: loc.hover(timeout=timeout)
                        )

                    elif step_type == "waitForSelector":
                        await _try_selectors_with_retries(
                            step,
                            current_frame,
                            action=lambda loc: loc.wait_for(timeout=timeout)
                        )

                    elif step_type == "keyDown":
                        await _wait_for_dom_stability(page)
                        await page.wait_for_timeout(500)
                        await page.keyboard.down(step.get("key", ""))
                        await page.wait_for_timeout(300)

                    elif step_type == "keyUp":
                        await _wait_for_dom_stability(page)
                        await page.wait_for_timeout(500)
                        await page.keyboard.up(step.get("key", ""))
                        await page.wait_for_timeout(300)

                    elif step_type == "setViewport":
                        if "width" in step and "height" in step:
                            await page.set_viewport_size({
                                "width": step["width"],
                                "height": step["height"]
                            })
                        else:
                            logger.warning("setViewport saknar width och height – använder standard.")
                            await page.set_viewport_size({"width": 1280, "height": 720})

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
                            await _handle_assert_event(event, current_frame, page)

                    else:
                        logger.warning(f"Ohanterat stegtyp: {step_type}")

                    logger.debug(f"Efter steg {i+1}: URL = {page.url}, Titel = {await page.title()}")

                except Exception as step_error:
                    msg = f"Steg {i+1}/{len(recording['steps'])} ({step_type}) misslyckades: {step_error}"
                    logger.error(msg)
                    result["Status"] = "failed"
                    result["ErrorMessage"] = msg
                    result["ErrorStack"] = traceback.format_exc()
                    try:
                        if page and not page.is_closed():
                            screenshot = await page.screenshot()
                            result["ScreenshotBase64"] = base64.b64encode(screenshot).decode("utf-8")
                            result["ScreenshotMissing"] = False
                        else:
                            result["ScreenshotMissing"] = True
                    except Exception as e:
                        logger.warning(f"Kunde inte ta skärmdump: {e}")
                        result["ScreenshotMissing"] = True
                    raise step_error

            await browser.close()

    except Exception as e:
        logger.error(f"Testet misslyckades: {e}")
        result["Status"] = "failed"
        if not result["ErrorMessage"]:
            result["ErrorMessage"] = str(e)
        result["ErrorStack"] = traceback.format_exc()
        try:
            if page and not page.is_closed():
                screenshot = await page.screenshot()
                result["ScreenshotBase64"] = base64.b64encode(screenshot).decode("utf-8")
                result["ScreenshotMissing"] = False
            else:
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


async def _handle_assert_event(event, frame, page):
    event_type = event.get("type")
    selector = _normalize_selector(event.get("selector", ""))
    if event_type == "navigation":
        expected_url = event.get("url")
        expected_title = event.get("title")
        actual_url = page.url
        actual_title = await page.title()
        if expected_url and expected_url not in actual_url:
            raise AssertionError(f"URL stämmer ej. Förväntat: {expected_url}, Fick: {actual_url}")
        if expected_title and expected_title.strip() and expected_title.strip() not in actual_title:
            raise AssertionError(f"Titel stämmer ej. Förväntat: {expected_title}, Fick: {actual_title}")
    elif event_type == "elementAppears" and selector:
        await frame.locator(selector).wait_for(state="attached", timeout=5000)
    elif event_type == "textContent" and selector:
        expected_text = event.get("text", "")
        locator = frame.locator(selector)
        await locator.wait_for(state="attached", timeout=5000)
        actual_text = await locator.inner_text()
        if expected_text not in actual_text:
            raise AssertionError(f"Text stämmer ej. Förväntat: '{expected_text}', Fick: '{actual_text}'")
    elif event_type == "elementVisible" and selector:
        await frame.locator(selector).wait_for(state="visible", timeout=5000)
    elif event_type == "elementHidden" and selector:
        await frame.locator(selector).wait_for(state="hidden", timeout=5000)
    elif event_type == "attributeValue" and selector:
        attr = event.get("attribute")
        expected = event.get("value")
        locator = frame.locator(selector)
        await locator.wait_for(state="attached", timeout=5000)
        actual = await locator.get_attribute(attr)
        if expected not in (actual or ""):
            raise AssertionError(f"Attributvärde stämmer ej: {attr}. Förväntat: '{expected}', Fick: '{actual}'")
    else:
        logger.warning(f"Ohanterad assert-event typ: {event_type}")


async def _try_selectors_with_retries(step, frame, action, max_retries=10, delay_ms=1000):
    for attempt in range(max_retries):
        try:
            await _try_selectors(step, frame, action)
            return
        except Exception as e:
            logger.debug(f"Försök {attempt+1}/{max_retries} misslyckades: {e}")
            await frame.wait_for_timeout(delay_ms)
    raise Exception("Inget selektoralternativ fungerade efter flera försök")


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
                    continue
                for i in range(count):
                    candidate = base_locator.nth(i)
                    await candidate.wait_for(state="attached", timeout=3000)
                    if await candidate.is_visible():
                        await candidate.scroll_into_view_if_needed()
                        await action(candidate)
                        return
            except Exception as e:
                logger.debug(f"Misslyckades på selector {selector}: {e}")
    raise Exception("Ingen fungerande selector hittades")


async def _wait_for_dom_stability(page):
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
        await page.wait_for_function("""
            () => {
                const spinner = document.querySelector('.spinner, .loading, .waitCursor');
                return !spinner || spinner.offsetParent === null;
            }
        """, timeout=5000)
        await page.evaluate("() => new Promise(r => requestAnimationFrame(() => r()))")
        await page.wait_for_timeout(500)
    except Exception as e:
        logger.debug(f"DOM stabilitet kunde inte säkerställas: {e}")
