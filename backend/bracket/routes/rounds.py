from fastapi import APIRouter, Depends, HTTPException
from starlette import status

from bracket.database import database
from bracket.logic.ranking.calculation import (
    recalculate_ranking_for_stage_item,
)
from bracket.logic.subscriptions import check_requirement
from bracket.models.db.round import (
    Round,
    RoundCreateBody,
    RoundInsertable,
    RoundUpdateBody,
)
from bracket.models.db.tournament import Tournament
from bracket.models.db.user import UserPublic
from bracket.models.db.util import RoundWithMatches
from bracket.routes.auth import user_authenticated_for_tournament
from bracket.routes.models import SuccessResponse
from bracket.routes.util import (
    disallow_archived_tournament,
    round_dependency,
    round_with_matches_dependency,
)
from bracket.sql.matches import sql_delete_match
from bracket.sql.rounds import (
    get_next_round_name,
    set_round_active_or_draft,
    sql_create_round,
    sql_delete_round,
)
from bracket.sql.stage_items import get_stage_item
from bracket.sql.stages import get_full_tournament_details
from bracket.sql.validation import check_foreign_keys_belong_to_tournament
from bracket.utils.id_types import RoundId, TournamentId
from tests.integration_tests.mocks import MOCK_NOW

router = APIRouter()


@router.delete("/tournaments/{tournament_id}/rounds/{round_id}", response_model=SuccessResponse)
async def delete_round(
    tournament_id: TournamentId,
    round_id: RoundId,
    _: UserPublic = Depends(user_authenticated_for_tournament),
    __: Tournament = Depends(disallow_archived_tournament),
    round_with_matches: RoundWithMatches = Depends(round_with_matches_dependency),
) -> SuccessResponse:
    for match in round_with_matches.matches:
        await sql_delete_match(match.id)

    await sql_delete_round(round_id)

    stage_item = await get_stage_item(tournament_id, round_with_matches.stage_item_id)
    await recalculate_ranking_for_stage_item(tournament_id, stage_item)
    return SuccessResponse()


@router.post("/tournaments/{tournament_id}/rounds", response_model=SuccessResponse)
async def create_round(
    tournament_id: TournamentId,
    round_body: RoundCreateBody,
    user: UserPublic = Depends(user_authenticated_for_tournament),
    _: Tournament = Depends(disallow_archived_tournament),
) -> SuccessResponse:
    await check_foreign_keys_belong_to_tournament(round_body, tournament_id)

    stages = await get_full_tournament_details(tournament_id)
    existing_rounds = [
        round_
        for stage in stages
        for stage_item in stage.stage_items
        for round_ in stage_item.rounds
    ]
    check_requirement(existing_rounds, user, "max_rounds")

    stage_item = await get_stage_item(tournament_id, stage_item_id=round_body.stage_item_id)

    if not stage_item.type.supports_dynamic_number_of_rounds:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stage type {stage_item.type} doesn't support manual creation of rounds",
        )

    round_id = await sql_create_round(
        RoundInsertable(
            created=MOCK_NOW,
            is_draft=False,
            stage_item_id=round_body.stage_item_id,
            name=await get_next_round_name(tournament_id, round_body.stage_item_id),
        ),
    )

    await set_round_active_or_draft(round_id, tournament_id, is_draft=True)
    return SuccessResponse()


@router.put("/tournaments/{tournament_id}/rounds/{round_id}", response_model=SuccessResponse)
async def update_round_by_id(
    tournament_id: TournamentId,
    round_id: RoundId,
    round_body: RoundUpdateBody,
    _: UserPublic = Depends(user_authenticated_for_tournament),
    __: Round = Depends(round_dependency),
    ___: Tournament = Depends(disallow_archived_tournament),
) -> SuccessResponse:
    query = """
        UPDATE rounds
        SET name = :name, is_draft = :is_draft
        WHERE rounds.id IN (
            SELECT rounds.id
            FROM rounds
            JOIN stage_items ON rounds.stage_item_id = stage_items.id
            JOIN stages s on s.id = stage_items.stage_id
            WHERE s.tournament_id = :tournament_id
        )
        AND rounds.id = :round_id
    """
    await database.execute(
        query=query,
        values={
            "tournament_id": tournament_id,
            "round_id": round_id,
            "name": round_body.name,
            "is_draft": round_body.is_draft,
        },
    )
    return SuccessResponse()
