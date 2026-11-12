"""Local administration: requires filesystem/database access, never exposed over HTTP."""
import argparse

from sqlalchemy import select

from app.config import Settings
from app.database import Base, make_database
from app.models import User


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list-users")
    promote = commands.add_parser("promote")
    selector = promote.add_mutually_exclusive_group(required=True)
    selector.add_argument("--id", type=int)
    selector.add_argument("--sub")
    args = parser.parse_args()
    settings = Settings()
    engine, factory = make_database(settings.database_url)
    Base.metadata.create_all(engine)
    with factory() as db:
        if args.command == "list-users":
            print("ID\tROLE\tEMAIL\tNAME\tGOOGLE SUB")
            for user in db.scalars(select(User).order_by(User.id)):
                print(f"{user.id}\t{user.role}\t{user.email}\t{user.name}\t{user.google_sub}")
        else:
            condition = User.id == args.id if args.id is not None else User.google_sub == args.sub
            user = db.scalar(select(User).where(condition))
            if user is None:
                parser.exit(1, "User not found. Sign in with Google first.\n")
            user.role = "admin"
            db.commit()
            print(f"Promoted user {user.id} ({user.email}) to admin.")
    engine.dispose()


if __name__ == "__main__":
    main()
