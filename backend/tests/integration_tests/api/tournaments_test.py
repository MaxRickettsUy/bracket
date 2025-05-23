import aiofiles
import aiofiles.os
import aiohttp
import pytest

from bracket.database import database
from bracket.logic.tournaments import sql_delete_tournament_completely
from bracket.models.db.tournament import Tournament, TournamentStatus
from bracket.schema import tournaments
from bracket.sql.tournaments import sql_delete_tournament, sql_get_tournament_by_endpoint_name
from bracket.utils.db import fetch_one_parsed_certain
from bracket.utils.dummy_records import DUMMY_MOCK_TIME, DUMMY_TOURNAMENT
from bracket.utils.http import HTTPMethod
from bracket.utils.types import assert_some
from tests.integration_tests.api.shared import (
    SUCCESS_RESPONSE,
    send_auth_request,
    send_tournament_request,
)
from tests.integration_tests.models import AuthContext
from tests.integration_tests.sql import inserted_tournament


@pytest.mark.asyncio(loop_scope="session")
async def test_tournaments_endpoint(
    startup_and_shutdown_uvicorn_server: None, auth_context: AuthContext
) -> None:
    assert await send_auth_request(HTTPMethod.GET, "tournaments", auth_context, {}) == {
        "data": [
            {
                "id": auth_context.tournament.id,
                "club_id": auth_context.club.id,
                "created": DUMMY_MOCK_TIME.isoformat().replace("+00:00", "Z"),
                "start_time": DUMMY_MOCK_TIME.isoformat().replace("+00:00", "Z"),
                "name": "Some Cool Tournament",
                "logo_path": None,
                "dashboard_public": True,
                "dashboard_endpoint": "endpoint-test",
                "players_can_be_in_multiple_teams": True,
                "auto_assign_courts": True,
                "duration_minutes": 10,
                "margin_minutes": 5,
                "status": "OPEN",
            }
        ],
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_tournament_endpoint(
    startup_and_shutdown_uvicorn_server: None, auth_context: AuthContext
) -> None:
    assert await send_auth_request(
        HTTPMethod.GET, f"tournaments/{auth_context.tournament.id}", auth_context, {}
    ) == {
        "data": {
            "id": auth_context.tournament.id,
            "club_id": auth_context.club.id,
            "created": DUMMY_MOCK_TIME.isoformat().replace("+00:00", "Z"),
            "start_time": DUMMY_MOCK_TIME.isoformat().replace("+00:00", "Z"),
            "logo_path": None,
            "name": "Some Cool Tournament",
            "dashboard_public": True,
            "dashboard_endpoint": "endpoint-test",
            "players_can_be_in_multiple_teams": True,
            "auto_assign_courts": True,
            "duration_minutes": 10,
            "margin_minutes": 5,
            "status": "OPEN",
        },
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_create_tournament(
    startup_and_shutdown_uvicorn_server: None, auth_context: AuthContext
) -> None:
    dashboard_endpoint = "some-new-endpoint"
    body = {
        "name": "Some new name",
        "start_time": DUMMY_MOCK_TIME.isoformat().replace("+00:00", "Z"),
        "club_id": auth_context.club.id,
        "dashboard_public": True,
        "dashboard_endpoint": dashboard_endpoint,
        "players_can_be_in_multiple_teams": True,
        "auto_assign_courts": True,
        "duration_minutes": 12,
        "margin_minutes": 3,
    }
    assert (
        await send_auth_request(HTTPMethod.POST, "tournaments", auth_context, json=body)
        == SUCCESS_RESPONSE
    )

    # Cleanup
    tournament = assert_some(await sql_get_tournament_by_endpoint_name(dashboard_endpoint))
    await sql_delete_tournament_completely(tournament.id)


@pytest.mark.asyncio(loop_scope="session")
async def test_create_tournament_duplicate_dashboard_endpoint(
    startup_and_shutdown_uvicorn_server: None, auth_context: AuthContext
) -> None:
    body = {
        "name": "Some new name",
        "start_time": DUMMY_MOCK_TIME.isoformat().replace("+00:00", "Z"),
        "club_id": auth_context.club.id,
        "dashboard_public": True,
        "dashboard_endpoint": "endpoint-test",
        "players_can_be_in_multiple_teams": True,
        "auto_assign_courts": True,
        "duration_minutes": 12,
        "margin_minutes": 3,
    }
    assert await send_auth_request(HTTPMethod.POST, "tournaments", auth_context, json=body) == {
        "detail": "This dashboard link is already taken"
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_update_tournament(
    startup_and_shutdown_uvicorn_server: None, auth_context: AuthContext
) -> None:
    body = {
        "name": "Some new name",
        "start_time": DUMMY_MOCK_TIME.isoformat().replace("+00:00", "Z"),
        "dashboard_public": False,
        "players_can_be_in_multiple_teams": True,
        "auto_assign_courts": True,
        "duration_minutes": 12,
        "margin_minutes": 3,
    }
    assert (
        await send_tournament_request(HTTPMethod.PUT, "", auth_context, json=body)
        == SUCCESS_RESPONSE
    )
    updated_tournament = await fetch_one_parsed_certain(
        database,
        Tournament,
        query=tournaments.select().where(tournaments.c.id == auth_context.tournament.id),
    )
    assert updated_tournament.name == body["name"]
    assert updated_tournament.dashboard_public == body["dashboard_public"]


@pytest.mark.asyncio(loop_scope="session")
async def test_archive_and_unarchive_tournament(
    startup_and_shutdown_uvicorn_server: None, auth_context: AuthContext
) -> None:
    query = tournaments.select().where(tournaments.c.id == auth_context.tournament.id)
    body = {"status": "ARCHIVED"}
    assert (
        await send_tournament_request(HTTPMethod.POST, "change-status", auth_context, json=body)
        == SUCCESS_RESPONSE
    )
    updated_tournament = await fetch_one_parsed_certain(database, Tournament, query)
    assert updated_tournament.status is TournamentStatus.ARCHIVED
    assert updated_tournament.dashboard_public is False

    # Archiving twice is not allowed
    assert await send_tournament_request(
        HTTPMethod.POST, "change-status", auth_context, json=body
    ) == {"detail": "Tournament already has the requested status"}

    # Unarchive the tournament
    body = {"status": "OPEN"}
    assert (
        await send_tournament_request(HTTPMethod.POST, "change-status", auth_context, json=body)
        == SUCCESS_RESPONSE
    )
    updated_tournament = await fetch_one_parsed_certain(database, Tournament, query)
    assert updated_tournament.status is TournamentStatus.OPEN
    assert updated_tournament.dashboard_public is False


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_tournament(
    startup_and_shutdown_uvicorn_server: None, auth_context: AuthContext
) -> None:
    async with inserted_tournament(
        DUMMY_TOURNAMENT.model_copy(
            update={"club_id": auth_context.club.id, "dashboard_endpoint": None}
        )
    ) as tournament_inserted:
        assert (
            await send_tournament_request(
                HTTPMethod.DELETE,
                "",
                auth_context.model_copy(update={"tournament": tournament_inserted}),
            )
            == SUCCESS_RESPONSE
        )

    await sql_delete_tournament(tournament_inserted.id)


@pytest.mark.asyncio(loop_scope="session")
async def test_tournament_upload_and_remove_logo(
    startup_and_shutdown_uvicorn_server: None, auth_context: AuthContext
) -> None:
    test_file_path = "tests/integration_tests/assets/test_logo.png"
    data = aiohttp.FormData()
    data.add_field(
        "file",
        open(test_file_path, "rb"),  # pylint: disable=consider-using-with
        filename="test_logo.png",
        content_type="image/png",
    )

    response = await send_tournament_request(
        method=HTTPMethod.POST,
        endpoint="logo",
        auth_context=auth_context,
        body=data,
    )

    assert response.get("data", {}).get("logo_path"), f"Response: {response}"
    assert await aiofiles.os.path.exists(f"static/tournament-logos/{response['data']['logo_path']}")

    response = await send_tournament_request(
        method=HTTPMethod.POST, endpoint="logo", auth_context=auth_context, body=aiohttp.FormData()
    )

    assert response["data"]["logo_path"] is None, f"Response: {response}"
    assert not await aiofiles.os.path.exists(
        f"static/tournament-logos/{response['data']['logo_path']}"
    )
