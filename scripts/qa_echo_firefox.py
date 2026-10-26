"""Firefox Echo audio QA; requires Playwright Firefox and a working PulseAudio service."""

from playwright.sync_api import sync_playwright
from qa_communication import check_echo_calls


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.firefox.launch(
            firefox_user_prefs={
                "media.navigator.streams.fake": True,
                "media.navigator.permission.disabled": True,
                "media.autoplay.default": 1,
            }
        )
        errors: list[str] = []
        try:
            check_echo_calls(browser, errors, capture=False)
            assert not errors, errors
        finally:
            browser.close()
    print("Firefox Echo PCM capture, playback, timeout and hangup checks passed.")


if __name__ == "__main__":
    main()
