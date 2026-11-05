"""Create demo data so the API can be tried by hand: python -m app.seed

Safe to run more than once. Prints the ids to use with the API.
Note: running the test suite empties the tables, so seed AFTER running pytest.
"""

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Court, Member, PeakWindow, Venue


def main() -> None:
    with SessionLocal() as db:
        venue = db.scalar(select(Venue).where(Venue.name == "Demo Venue"))
        if venue is None:
            venue = Venue(
                name="Demo Venue",
                timezone="Asia/Kolkata",
                open_hour=6,
                close_hour=22,
                offpeak_rate=20000,  # Rs 200.00 per hour, stored in paise
                peak_rate=40000,
            )
            db.add(venue)
            db.flush()
            # Peak pricing Monday to Friday, 18:00-22:00.
            db.add_all(
                PeakWindow(venue_id=venue.id, weekday=d, start_hour=18, end_hour=22)
                for d in range(5)
            )
            db.add_all(Court(venue_id=venue.id, name=f"Court {n}") for n in (1, 2))
            db.add_all(Member(name=name) for name in ("Asha", "Ben"))
            db.commit()

        courts = db.scalars(
            select(Court).where(Court.venue_id == venue.id).order_by(Court.name)
        ).all()
        members = db.scalars(
            select(Member).where(Member.name.in_(["Asha", "Ben"])).order_by(Member.name)
        ).all()
        for court in courts:
            print(f"court  {court.name}: {court.id}")
        for member in members:
            print(f"member {member.name}: {member.id}")


if __name__ == "__main__":
    main()
