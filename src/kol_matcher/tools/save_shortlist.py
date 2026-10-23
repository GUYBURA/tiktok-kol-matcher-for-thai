from typing import Annotated

from pydantic import Field

from kol_matcher.core import shortlist
from kol_matcher.schemas.brand import BrandProfile
from kol_matcher.schemas.scoring import Objective
from kol_matcher.schemas.tools import ShortlistResult
from kol_matcher.tools.context import tool_call


def save_shortlist(
    profile: Annotated[BrandProfile, Field(description="The BrandProfile used for the evaluation")],
    handles: Annotated[list[str], Field(description="Approved TikTok handles (already evaluated)")],
    objective: Annotated[Objective, Field(description="Campaign goal used for the evaluation")] = Objective.engagement,
    notes: Annotated[str | None, Field(description="Optional note saved with the shortlist")] = None,
) -> ShortlistResult:
    """Step 4. Save the creators the user approved. Only call this after the user confirms the list.

    Recomputes every number from the server's cache (it does not accept figures from the client),
    writes a CSV (UTF-8, opens with Thai text in Excel), and records the decision in the shortlist
    log. Returns the file path, the rows, and the CSV text.
    """
    with tool_call() as (con, settings):
        result = shortlist.save_shortlist(con, settings, profile, handles, objective, notes)
    return ShortlistResult(**result.model_dump(), csv=shortlist.to_csv(result))
