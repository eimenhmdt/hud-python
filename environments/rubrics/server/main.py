"""MCP server for SEC EDGAR research environment."""

from typing import List, Dict, Any, Optional
import httpx
import os
import sys
import logging

from hud.tools.types import EvaluationResult
from hud.server import MCPServer

# Configure logging
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[%(levelname)s] %(asctime)s | %(name)s | %(message)s",
    force=True,  # Force all loggers to use stderr
)

# MCP server
mcp = MCPServer(name="sec-rubrics")

# Environment server URL (backend)
ENV_SERVER_URL = os.getenv("ENV_SERVER_URL", "http://localhost:8000")

# Shared HTTP client to talk to the environment
http_client = httpx.AsyncClient(
    base_url=ENV_SERVER_URL,
    timeout=60.0,  # Increased timeout for SEC EDGAR operations
    headers={"User-Agent": "HUD-SEC-Rubrics-Controller/1.0"},
)


@mcp.initialize
async def init():
    # Ensure environment server is reachable
    await http_client.get("/health")


@mcp.shutdown
async def cleanup():
    await http_client.aclose()


@mcp.tool()
async def setup() -> str:
    """Initialize the SEC EDGAR research environment."""
    await http_client.post("/setup")
    return "Environment setup complete"


@mcp.tool()
async def search_company(query: str) -> List[Dict[str, str]]:
    """
    Search for a company by ticker symbol or company name.

    Args:
        query: Company ticker (e.g., "TSLA") or company name (e.g., "Tesla")

    Returns:
        List of company information including ticker, name, and CIK
    """
    resp = await http_client.post("/search_company", json={"query": query})
    return resp.json()


@mcp.tool()
async def get_filings(
    ticker: str, form_type: Optional[str] = None, limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Get recent SEC filings for a company.

    Args:
        ticker: Company ticker symbol (e.g., "TSLA")
        form_type: Optional form type filter (e.g., "10-K", "10-Q", "8-K")
        limit: Maximum number of filings to return (default: 10)

    Returns:
        List of filings with filing date, form type, description, and URL
    """
    resp = await http_client.post(
        "/get_filings", json={"ticker": ticker, "form_type": form_type, "limit": limit}
    )
    return resp.json()


@mcp.tool()
async def get_filing_content(filing_url: str) -> str:
    """
    Fetch the full content of a specific SEC filing.

    Args:
        filing_url: URL of the SEC filing (can be partial URL or full EDGAR URL)

    Returns:
        The text content of the filing
    """
    resp = await http_client.post("/get_filing_content", json={"filing_url": filing_url})
    data = resp.json()
    return data.get("content", "")


@mcp.tool()
async def get_recent_filings(
    identifier: Optional[str] = None, form_type: Optional[str] = None, limit: int = 50
) -> Any:
    """Get recent filings (company-specific when identifier provided, else global)."""
    resp = await http_client.post(
        "/get_recent_filings",
        json={
            "identifier": identifier,
            "form_type": form_type,
            "limit": limit,
        },
    )
    return resp.json()


@mcp.tool()
async def get_filing_content_by_accession(identifier: str, accession_number: str) -> Any:
    """Get filing content using identifier (ticker/CIK) and accession number."""
    resp = await http_client.post(
        "/get_filing_content_by_accession",
        json={
            "identifier": identifier,
            "accession_number": accession_number,
        },
    )
    return resp.json()


@mcp.tool()
async def analyze_8k(identifier: str, accession_number: str) -> Any:
    """Analyze an 8-K filing for specific events and items."""
    resp = await http_client.post(
        "/analyze_8k",
        json={
            "identifier": identifier,
            "accession_number": accession_number,
        },
    )
    return resp.json()


@mcp.tool()
async def get_filing_sections(identifier: str, accession_number: str) -> Any:
    """Get specific sections from a 10-K or 10-Q filing (business, MD&A, financials, etc)."""
    resp = await http_client.post(
        "/get_filing_sections",
        json={
            "identifier": identifier,
            "accession_number": accession_number,
        },
    )
    return resp.json()


@mcp.tool()
async def get_financials(identifier: str, accession_number: str) -> Any:
    """Extract financial statements (balance sheet, income statement, cash flow) from a 10-K or 10-Q filing."""
    resp = await http_client.post(
        "/get_financials",
        json={
            "identifier": identifier,
            "accession_number": accession_number,
        },
    )
    return resp.json()


@mcp.tool()
async def get_segment_data(identifier: str, accession_number: str) -> Any:
    """Extract segment-level financial data from a 10-K or 10-Q filing."""
    resp = await http_client.post(
        "/get_segment_data",
        json={
            "identifier": identifier,
            "accession_number": accession_number,
        },
    )
    return resp.json()


@mcp.tool()
async def answer(final_answer: str) -> str:
    """
    Submit the final research answer.

    Args:
        final_answer: The complete answer to the research question

    Returns:
        Confirmation message
    """
    await http_client.post("/answer", json={"final_answer": final_answer})
    return f"Answer submitted: {final_answer}"


@mcp.tool()
async def evaluate(rubric: list[dict[str, str | float]]) -> EvaluationResult:
    """
    Evaluate the submitted answer using a structured rubric.

    Args:
        rubric: List of rubric requirements with 'requirement' and 'weight' fields

    Returns:
        Evaluation result with reward score and detailed report
    """
    try:
        resp = await http_client.post("/evaluate", json={"rubric": rubric})
        resp.raise_for_status()
        return EvaluationResult(**resp.json())
    except Exception as e:
        logging.error(f"Evaluation tool error: {e}")
        return EvaluationResult(
            reward=0.0, done=True, content=f"Evaluation error: {e}", isError=True
        )


if __name__ == "__main__":
    mcp.run()
