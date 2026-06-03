from datetime import datetime, timezone

from sqlmodel import Session

from db import engine
from models import User
from schemas import UserLoginRequest, UserResponse


def record_user_login(request: UserLoginRequest) -> UserResponse:
    """Upsert a user on login.

    Keyed on the email (User.id): inserts a new row on first login, otherwise
    refreshes the existing row's name and last_login_date.
    """
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        user = session.get(User, request.id)
        if user is None:
            user = User(id=request.id, name=request.name, last_login_date=now)
        else:
            user.name = request.name
            user.last_login_date = now
        session.add(user)
        session.commit()
        session.refresh(user)

        return UserResponse(
            id=user.id,
            name=user.name,
            last_login_date=user.last_login_date,
        )
