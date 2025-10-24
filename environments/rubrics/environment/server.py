"""
FastAPI server for Rubrics environment with SEC EDGAR integration.
Manages SEC filing data access and state.
"""

import asyncio
import logging
import os
import socket
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar
from urllib.parse import urlparse

import httpx
import uvicorn
from edgar import Company, set_identity
from edgar.filing import Filing
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from rubric import Rubric


T = TypeVar("T")


# Set up logging
logger = logging.getLogger(__name__)


async def call_with_exponential_backoff(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    max_retries: int = 5,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    **kwargs: Any,
) -> T:
    """
    Call an async function with exponential backoff on rate limit errors.

    Args:
        func: The async function to call
        *args: Positional arguments for the function
        max_retries: Maximum number of retry attempts (default: 5)
        initial_delay: Initial delay in seconds (default: 1.0)
        max_delay: Maximum delay in seconds (default: 60.0)
        exponential_base: Base for exponential backoff (default: 2.0)
        **kwargs: Keyword arguments for the function

    Returns:
        The result of the function call

    Raises:
        The last exception if all retries fail
    """
    last_exception: Optional[Exception] = None
    delay = initial_delay

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                last_exception = e
                if attempt < max_retries:
                    # Log the retry attempt
                    logger.warning(
                        "Rate limit hit (429), retrying in %s seconds... (attempt %s/%s)",
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(delay)
                    # Calculate next delay with exponential backoff
                    delay = min(delay * exponential_base, max_delay)
                else:
                    # All retries exhausted
                    raise
            else:
                # Not a rate limit error, raise immediately
                raise
        except Exception:
            # Not an HTTP error, raise immediately
            raise

    # This should never be reached, but just in case
    if last_exception:
        raise last_exception
    raise RuntimeError("Unexpected error in exponential backoff")


class _EnvState:
    """In-memory environment state for tracking usage and agent answer."""

    def __init__(self) -> None:
        self.search_count: int = 0
        self.fetch_count: int = 0
        self.submitted_answer: Optional[str] = None

    def reset(self) -> None:
        self.search_count = 0
        self.fetch_count = 0
        self.submitted_answer = None


state = _EnvState()


class SearchCompanyRequest(BaseModel):
    query: str


class GetFilingsRequest(BaseModel):
    ticker: str
    form_type: Optional[str] = None
    limit: int = 10


class GetFilingContentRequest(BaseModel):
    filing_url: str


class AnswerRequest(BaseModel):
    final_answer: str


class EvaluateRequest(BaseModel):
    rubric: list[dict[str, str | float]]


app = FastAPI(title="SEC EDGAR Environment API", version="0.1.0")


# Set SEC EDGAR identity (required by SEC regulations)
SEC_EDGAR_USER_AGENT = os.getenv("SEC_EDGAR_USER_AGENT", "hud-rubrics@example.com")
set_identity(SEC_EDGAR_USER_AGENT)
logger.info(f"SEC EDGAR identity set to: {SEC_EDGAR_USER_AGENT}")


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"status": "healthy"}


async def _is_port_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.15)
    try:
        result = sock.connect_ex(("localhost", port))
        sock.close()
        return result == 0
    except Exception:
        return False


@app.post("/setup")
async def setup() -> Dict[str, Any]:
    state.reset()
    return {"ok": True}


@app.post("/search_company")
async def search_company(req: SearchCompanyRequest) -> List[Dict[str, str]]:
    """Search for a company by ticker or name."""
    try:
        # Use edgartools to search for company
        company = Company(req.query)

        results = [
            {
                "ticker": company.ticker,
                "name": company.name,
                "cik": company.cik,
                "message": f"Found company: {company.name} ({company.ticker})",
            }
        ]

        state.search_count += 1
        return results

    except Exception as e:
        logger.error(f"Company search failed: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Company search failed: {type(e).__name__}: {e}"
        )


@app.post("/get_filings")
async def get_filings(req: GetFilingsRequest) -> List[Dict[str, Any]]:
    """Get recent filings for a company."""
    try:
        company = Company(req.ticker)

        # Get filings
        if req.form_type:
            filings = company.get_filings(form=req.form_type, limit=req.limit)
        else:
            filings = company.get_filings(limit=req.limit)

        results = []
        for filing in filings:
            results.append(
                {
                    "filing_date": filing.filing_date.strftime("%Y-%m-%d")
                    if filing.filing_date
                    else "",
                    "form_type": filing.form,
                    "description": filing.description,
                    "filing_url": filing.url,
                    "accession_number": filing.accession_number,
                }
            )

        state.search_count += 1
        return results

    except Exception as e:
        logger.error(f"Get filings failed: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Get filings failed: {type(e).__name__}: {e}")


@app.post("/get_filing_content")
async def get_filing_content(req: GetFilingContentRequest) -> Dict[str, str]:
    """Get the content of a specific filing."""
    try:
        # Parse the filing URL to extract accession number
        parsed = urlparse(req.filing_url)
        path_parts = parsed.path.split("/")

        # Find the accession number in the URL
        accession = None
        for part in path_parts:
            if len(part) > 10 and "-" in part:
                accession = part.replace("-", "")
                break

        if not accession:
            raise HTTPException(
                status_code=400, detail="Could not extract accession number from URL"
            )

        # Get filing from edgartools
        filing = Filing(accession)

        # Get the HTML content
        content = filing.text

        # Truncate if too long
        max_length = 50000
        if len(content) > max_length:
            content = content[:max_length] + "\n\n...[truncated]"

        state.fetch_count += 1
        return {"content": content}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get filing content failed: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Get filing content failed: {type(e).__name__}: {e}"
        )


@app.post("/answer")
async def answer(req: AnswerRequest) -> Dict[str, Any]:
    state.submitted_answer = req.final_answer
    return {"ok": True, "message": "Answer submitted"}


@app.post("/evaluate")
async def evaluate(req: EvaluateRequest) -> Dict[str, Any]:
    submitted = state.submitted_answer
    if submitted is None:
        return {
            "reward": 0.0,
            "content": f"No answer submitted. Searches: {state.search_count}, Fetches: {state.fetch_count}",
            "done": False,
        }

    rubric = Rubric.from_dict(req.rubric)

    evaluation = await rubric.grade(submitted)

    reward = evaluation.score
    info = {"report": [r.model_dump() for r in evaluation.report] if evaluation.report else []}

    return {"reward": reward, "info": info, "done": True}


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    uvicorn.run(app, host="0.0.0.0", port=8000)
