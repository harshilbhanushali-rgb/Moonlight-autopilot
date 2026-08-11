import argparse

from app.avoma.client import AvomaClient
from app.core.config import settings
from app.db.client_session import get_client_session
from app.db.session import get_session
from app.services.fetcher.fetcher import fetch_new_calls


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Fetch new NB calls from Avoma into call_storage.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N new calls this run (default: all new calls).",
    )
    args = parser.parse_args(argv)

    avoma_client = AvomaClient(base_url=settings.avoma_base_url, api_key=settings.avoma_api_key)
    client_session = get_client_session()
    our_session = get_session()
    try:
        summary = fetch_new_calls(
            client_session=client_session,
            our_session=our_session,
            avoma_client=avoma_client,
            limit=args.limit,
        )
        print(
            f"Fetcher run complete: {summary.candidates} candidate(s) processed, "
            f"{summary.fetched} fetched, {summary.skipped_no_transcript} skipped "
            f"(no transcript yet — will retry next run), "
            f"{summary.skipped_malformed_transcript} skipped (transcript missing "
            f"turn timestamps — see logs)."
        )
    finally:
        client_session.close()
        our_session.close()


if __name__ == "__main__":
    main()
