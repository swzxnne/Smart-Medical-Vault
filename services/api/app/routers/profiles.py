import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.clerk import get_current_user
from app.db.base import get_db
from app.db.models import Profile, User
from app.schemas import ProfileCreate, ProfileOut, ProfileUpdate
from app.storage.s3 import Storage, get_storage

log = logging.getLogger(__name__)
router = APIRouter(prefix="/profiles", tags=["profiles"])


def get_owned_profile(db: Session, user: User, profile_id: uuid.UUID) -> Profile:
    profile = db.scalar(select(Profile).where(Profile.id == profile_id, Profile.user_id == user.id))
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
    return profile


@router.get("", response_model=list[ProfileOut])
def list_profiles(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.scalars(
        select(Profile).where(Profile.user_id == user.id).order_by(Profile.created_at, Profile.name)
    ).all()


@router.post("", response_model=ProfileOut, status_code=status.HTTP_201_CREATED)
def create_profile(
    body: ProfileCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    profile = Profile(user_id=user.id, **body.model_dump())
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/{profile_id}", response_model=ProfileOut)
def get_profile(
    profile_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return get_owned_profile(db, user, profile_id)


@router.patch("/{profile_id}", response_model=ProfileOut)
def update_profile(
    profile_id: uuid.UUID,
    body: ProfileUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    profile = get_owned_profile(db, user, profile_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return profile


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(
    profile_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    storage: Storage = Depends(get_storage),
):
    profile = get_owned_profile(db, user, profile_id)
    db.delete(profile)
    db.commit()
    # Files go after the rows: a failure here leaves orphaned objects, never rows pointing at missing files.
    try:
        storage.delete_prefix(f"{user.id}/{profile_id}/")
    except Exception:
        log.exception("Failed to delete files for profile %s", profile_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
