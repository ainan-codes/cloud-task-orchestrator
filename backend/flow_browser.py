"""
flow_browser.py — Playwright Persistent Browser Driver for Google Flow & Omni Flash.

Handles:
1. Multi-account persistent profiles in backend/data/flow_profiles/acc_{id}/
2. Setup Mode: Launches a visible, non-bot-flagged Chrome window for one-time Google Login
3. Generation Mode: Enters 9:16 prompts, selects Omni Flash, triggers render, scrapes credits, and downloads raw MP4
"""

import os
import sys
import time
import logging
import subprocess
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

logger = logging.getLogger("flow_browser")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.join(BASE_DIR, "data", "flow_profiles")
STAGING_RAW = os.path.join(BASE_DIR, "staging", "raw")
STAGING_CLEAN = os.path.join(BASE_DIR, "staging", "clean")

os.makedirs(PROFILES_DIR, exist_ok=True)
os.makedirs(STAGING_RAW, exist_ok=True)
os.makedirs(STAGING_CLEAN, exist_ok=True)

FLOW_URL = os.environ.get("GOOGLE_FLOW_URL", "https://flow.google.com")


def get_profile_path(profile_folder: str) -> str:
    path = os.path.join(PROFILES_DIR, profile_folder)
    os.makedirs(path, exist_ok=True)
    return path


def launch_interactive_login(profile_folder: str, target_url: str = FLOW_URL):
    """
    Launches a visible Chromium window with persistent user data so you can log into Google.
    Once you log in, all cookies, OAuth sessions, and credentials are saved permanently
    in your local profile directory.
    """
    profile_path = get_profile_path(profile_folder)
    logger.info(f"Opening Google Flow login window for profile: {profile_folder} at {profile_path}...")

    from core.flow_lifecycle import get_active_profile_directory
    active_prof = get_active_profile_directory(profile_path)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            headless=False,
            viewport=None,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                f"--profile-directory={active_prof}",
            ],
            ignore_default_args=["--enable-automation"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
            if "/about" in page.url:
                btn = page.query_selector("button:has-text('Create with Google Flow'), a:has-text('Create with Google Flow'), button:has-text('Sign in')")
                if btn:
                    logger.info("Found 'Create with Google Flow' button. Clicking to open Google Sign-in...")
                    btn.click()
        except Exception as e:
            logger.warning(f"Initial navigation notice: {e}")

        logger.info("Browser open. Please log into your Google Account in the window.")
        logger.info("Once logged in and Google Flow loads, you can close the browser.")

        # Keep browser alive until user explicitly closes all pages/windows
        try:
            while True:
                open_pages = [pg for pg in context.pages if not pg.is_closed()]
                if not open_pages:
                    logger.info("All browser pages closed by user.")
                    break
                open_pages[0].wait_for_timeout(1000)
        except Exception as e:
            logger.info(f"Browser window closed: {e}")

        try:
            context.close()
        except Exception:
            pass

    return True


def scrape_credits_from_page(page) -> int:
    """Attempts to scrape remaining credits from the Google Flow credit badge."""
    try:
        credit_elements = page.query_selector_all("[data-testid*='credit'], [class*='credit'], button:has-text('credits')")
        for el in credit_elements:
            txt = el.inner_text().lower()
            digits = "".join(ch for ch in txt if ch.isdigit())
            if digits:
                return int(digits)
    except Exception as e:
        logger.debug(f"Could not scrape credit balance: {e}")
    return 1000


def close_running_browser_for_profile(profile_folder: str) -> bool:
    """Closes any background or interactive Chrome window locking this profile."""
    try:
        cmd = f'powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \\"Name like \'%chrome%\'\\" | Where-Object {{ $_.CommandLine -like \'*{profile_folder}*\' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"'
        subprocess.run(cmd, shell=True, capture_output=True, timeout=10)
        time.sleep(1)
        return True
    except Exception as e:
        logger.warning(f"Error closing browser for profile {profile_folder}: {e}")
        return False


def generate_single_clip(
    profile_folder: str,
    prompt: str,
    output_filename: str,
    model_name: str = "omni-flash",
    timeout_sec: int = 180,
    headless: bool = False
) -> dict:
    """
    Automates Google Flow to generate a single 9:16 vertical video clip using Omni Flash.
    Returns: { 'success': bool, 'video_path': str, 'credits_scraped': int, 'error': str }
    """
    profile_path = get_profile_path(profile_folder)
    raw_video_path = os.path.join(STAGING_RAW, output_filename)

    # Check if Chromium is currently locking this profile
    from core.flow_lifecycle import is_profile_in_use
    in_use, pid = is_profile_in_use(profile_folder)
    if in_use:
        return {
            "success": False,
            "video_path": None,
            "credits_scraped": 1000,
            "error": f"Google Chrome window is currently open (PID: {pid}). Please close the Google Flow window on your desktop so automated rendering can start."
        }

    logger.info(f"Starting Google Flow render for '{output_filename}'...")
    logger.info(f"Prompt: {prompt[:80]}...")

    from core.flow_lifecycle import get_active_profile_directory
    active_prof = get_active_profile_directory(profile_path)

    with sync_playwright() as p:
        try:
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                f"--profile-directory={active_prof}",
            ]
            if headless:
                launch_args.append("--headless=new")

            context = p.chromium.launch_persistent_context(
                user_data_dir=profile_path,
                headless=False,
                viewport={"width": 1280, "height": 800},
                args=launch_args,
                ignore_default_args=["--enable-automation"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(FLOW_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)

            # Check if user needs to log in (redirected to Google login)
            if "accounts.google.com" in page.url:
                context.close()
                return {
                    "success": False,
                    "error": "Google Account not logged in. Please launch Setup Login first.",
                    "requires_login": True
                }

            # If on the Flow landing dashboard, click '+ New project' or 'Get started'
            try:
                new_proj = page.query_selector("button:has-text('New project'), div:has-text('New project'), [aria-label*='New project'], button:has-text('Get started')")
                if new_proj:
                    logger.info("Found '+ New project' button on Google Flow landing page. Clicking it...")
                    new_proj.click()
                    page.wait_for_timeout(3000)
            except Exception as e:
                logger.debug(f"Landing page check: {e}")

            # 1. Ensure 9:16 Vertical ratio is active
            try:
                aspect_btn = page.query_selector("button:has-text('9:16'), [aria-label*='9:16'], [data-aspect='9:16']")
                if aspect_btn:
                    aspect_btn.click()
                    page.wait_for_timeout(500)
            except Exception:
                pass

            # 2. Input Prompt into Prompt Box
            prompt_box = page.wait_for_selector("textarea, div[contenteditable='true'], input[type='text']", timeout=15000)
            if not prompt_box:
                raise RuntimeError("Could not find prompt input field on Google Flow.")

            prompt_box.click()
            prompt_box.fill(prompt)
            page.wait_for_timeout(1000)

            # 3. Click Generate Button
            gen_btn = page.wait_for_selector("button:has-text('Generate'), button:has-text('Create'), [aria-label*='Generate']", timeout=10000)
            if not gen_btn:
                raise RuntimeError("Could not locate Generate button.")

            gen_btn.click()
            logger.info("Submitted generation to Omni Flash. Waiting for render completion...")

            # 4. Wait for Render Completion & Download Link
            start_wait = time.time()
            download_btn = None
            while time.time() - start_wait < timeout_sec:
                download_btn = page.query_selector("button:has-text('Download'), a[download], [aria-label*='Download']")
                if download_btn:
                    break
                page.wait_for_timeout(4000)

            credits_scraped = scrape_credits_from_page(page)

            if download_btn:
                with page.expect_download(timeout=30000) as download_info:
                    download_btn.click()
                download = download_info.value
                download.save_as(raw_video_path)
                logger.info(f"Downloaded generated video to {raw_video_path}")
                context.close()
                return {
                    "success": True,
                    "video_path": raw_video_path,
                    "credits_scraped": credits_scraped,
                    "error": None
                }
            else:
                context.close()
                return {
                    "success": False,
                    "video_path": None,
                    "credits_scraped": credits_scraped,
                    "error": "Render timed out before download button appeared."
                }

        except Exception as e:
            logger.error(f"Playwright automation error: {e}")
            return {
                "success": False,
                "video_path": None,
                "credits_scraped": 1000,
                "error": str(e)
            }


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--login":
        folder = sys.argv[2] if len(sys.argv) > 2 else "acc_1"
        launch_interactive_login(folder)
    else:
        print("Usage: python flow_browser.py --login [profile_folder]")
