from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from app.database import get_db
from app.dependencies import require_admin
from app.models import User
from app.schemas import UserOut

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


@router.get("/dashboard")
def dashboard(db: DBSession = Depends(get_db)):
    return {"message": "Administrator access granted by FastAPI.",
            "registered_users": db.scalar(select(func.count(User.id)))}


@router.get("/users", response_model=list[UserOut])
def users(db: DBSession = Depends(get_db)):
    return db.scalars(select(User).order_by(User.id)).all()
