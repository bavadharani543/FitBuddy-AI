from pathlib import Path
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .gemini_flash_generator import generate_nutrition_tip_with_flash
from .gemini_generator import generate_workout_gemini
from .models import User
from .schemas import FeedbackRequest, UserInput
from .updated_plan import update_workout_plan

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter()

@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@router.post("/generate-workout", response_class=HTMLResponse)
def generate_workout(
    request: Request,
    username: str = Form(...),
    user_id: str = Form(...),
    age: int = Form(...),
    weight: float = Form(...),
    goal: str = Form(...),
    intensity: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        data = UserInput(
            username=username,
            user_id=user_id,
            age=age,
            weight=weight,
            goal=goal,
            intensity=intensity,
        )
        existing = db.scalar(select(User).where(User.user_id == data.user_id))
        workout = generate_workout_gemini(
            data.username, data.age, data.weight, data.goal, data.intensity
        )
        tip = generate_nutrition_tip_with_flash(data.goal)

        if existing:
            existing.username = data.username
            existing.age = data.age
            existing.weight = data.weight
            existing.goal = data.goal
            existing.intensity = data.intensity
            existing.original_plan = workout
            existing.updated_plan = None
            existing.feedback = None
            existing.nutrition_tip = tip
            user = existing
        else:
            user = User(
                user_id=data.user_id,
                username=data.username,
                age=data.age,
                weight=data.weight,
                goal=data.goal,
                intensity=data.intensity,
                original_plan=workout,
                nutrition_tip=tip,
            )
            db.add(user)

        db.commit()
        db.refresh(user)
        return templates.TemplateResponse(
            "result.html",
            {
                "request": request,
                "user": user,
                "plan": user.updated_plan or user.original_plan,
                "is_updated": bool(user.updated_plan),
            },
        )
    except ValueError as exc:
        db.rollback()
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": str(exc)},
            status_code=400,
        )
    except Exception as exc:
        db.rollback()
        return templates.TemplateResponse(
            "error.html",
            {
                "request": request,
                "message": f"Could not generate the plan. {exc}",
            },
            status_code=500,
        )

@router.post("/submit-feedback", response_class=HTMLResponse)
def submit_feedback(
    request: Request,
    user_id: str = Form(...),
    feedback: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        data = FeedbackRequest(user_id=user_id, feedback=feedback)
        user = db.scalar(select(User).where(User.user_id == data.user_id))
        if not user:
            raise HTTPException(status_code=404, detail="User ID not found.")

        base_plan = user.updated_plan or user.original_plan
        revised = update_workout_plan(base_plan, data.feedback, user.goal, user.intensity)
        user.updated_plan = revised
        user.feedback = data.feedback
        user.nutrition_tip = generate_nutrition_tip_with_flash(user.goal)
        db.commit()
        db.refresh(user)

        return templates.TemplateResponse(
            "result.html",
            {
                "request": request,
                "user": user,
                "plan": user.updated_plan,
                "is_updated": True,
                "message": "Your plan has been updated using your feedback.",
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        return templates.TemplateResponse(
            "error.html",
            {"request": request, "message": f"Could not update the plan. {exc}"},
            status_code=500,
        )

@router.get("/view-all-users", response_class=HTMLResponse)
def view_all_users(request: Request, token: str = "", db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.admin_token or token != settings.admin_token:
        raise HTTPException(status_code=403, detail="Invalid admin token.")
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    return templates.TemplateResponse(
        "all_users.html",
        {"request": request, "users": users},
    )

@router.post("/admin/delete/{user_id}")
def delete_user(user_id: int, token: str = Form(...), db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.admin_token or token != settings.admin_token:
        raise HTTPException(status_code=403, detail="Invalid admin token.")
    user = db.get(User, user_id)
    if user:
        db.delete(user)
        db.commit()
    return RedirectResponse(
        url=f"/view-all-users?token={token}",
        status_code=303,
    )

@router.get("/health")
def health():
    return {"status": "ok"}
