from fastapi import APIRouter, Depends

from app.dependencies import get_current_user
from app.models import User
from app.schemas import UserOut

router = APIRouter()


@router.get("/api/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.get("/api/dashboard")
def dashboard(user: User = Depends(get_current_user)):
    return {"message": f"Welcome, {user.name}. FastAPI validated your local session.", "role": user.role}
