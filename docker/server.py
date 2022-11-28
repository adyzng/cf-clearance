import asyncio

from fastapi import FastAPI
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field
from pyvirtualdisplay import Display

from cf_clearance import async_cf_retry, async_stealth

app = FastAPI()


class ProxySetting(BaseModel):
    server: str = Field(...)
    username: str = Field(
        "",
        description="Optional username to use if HTTP proxy requires authentication.",
    )
    password: str = Field(
        "",
        description="Optional password to use if HTTP proxy requires authentication.",
    )


class ChallengeRequest(BaseModel):
    proxy: ProxySetting = Field(...)
    timeout: int = Field(10)
    url: str = Field(...)

    class Config:
        schema_extra = {
            "example": {
                "proxy": {"server": "socks5://localhost:7890"},
                "timeout": 20,
                "url": "https://nowsecure.nl",
            },
        }


class ChallengeResponse(BaseModel):
    success: bool = Field(True)
    msg: str = Field(None)
    user_agent: str = Field(None)
    cookies: dict = Field(None)


async def pw_challenge(data: ChallengeRequest):
    # Create a new context for each request:
    # https://github.com/microsoft/playwright/issues/17736#issuecomment-1263667429
    # https://github.com/microsoft/playwright/issues/6319
    with Display():
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                proxy={
                    "server": data.proxy.server,
                    "username": data.proxy.username,
                    "password": data.proxy.password,
                },
                args=[
                    "--disable-gpu",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--no-first-run",
                    "--no-service-autorun",
                    "--no-default-browser-check",
                    "--password-store=basic",
                ],
            )
            page = await browser.new_page()
            await async_stealth(page, pure=True)
            await page.goto(data.url)
            success = await async_cf_retry(page)
            if not success:
                await browser.close()
                return {"success": success, "msg": "cf challenge fail"}
            user_agent = await page.evaluate("() => navigator.userAgent")
            cookies = {
                cookie["name"]: cookie["value"]
                for cookie in await page.context.cookies()
            }
            await browser.close()
    return {
        "success": success,
        "user_agent": user_agent,
        "cookies": cookies,
        "msg": "cf challenge success",
    }


@app.post("/challenge", response_model=ChallengeResponse)
async def cf_challenge(data: ChallengeRequest):
    try:
        return await asyncio.wait_for(pw_challenge(data), timeout=data.timeout)
    except asyncio.TimeoutError:
        return {"success": False, "msg": "challenge timeout"}
    except Exception as e:
        return {"success": False, "msg": str(e)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)