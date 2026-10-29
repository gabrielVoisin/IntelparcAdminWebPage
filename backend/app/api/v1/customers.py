from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, require_permission
from app.models import Customer, StaffUser, Vehicle
from app.schemas import CustomerIn, CustomerOut, CustomerPatch, CustomerProfileOut, VehicleIn, VehicleOut
from app.services import queries
from app.services.audit import audit

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("/search")
def search(q: str = Query(..., min_length=2, description="First name, last name or plate"),
           user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    return queries.customer_search(db, q)


@router.get("/{customer_id}", response_model=CustomerProfileOut)
def profile(customer_id: uuid.UUID, user: StaffUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    data = queries.customer_profile(db, customer_id)
    if data is None:
        raise HTTPException(404, "customer_not_found")
    return data


@router.post("", response_model=CustomerOut, status_code=201)
def create(body: CustomerIn, user: StaffUser = Depends(require_permission("edit_customers")), db: Session = Depends(get_db)) -> Customer:
    next_num = (db.scalar(select(func.count(Customer.id))) or 0) + 1
    customer = Customer(reference=f"C-{next_num:05d}", **body.model_dump())
    db.add(customer)
    db.flush()
    audit(db, "customer.create", "customers", customer.id, actor_id=user.id)
    db.refresh(customer)
    return customer


@router.patch("/{customer_id}", response_model=CustomerOut)
def update(customer_id: uuid.UUID, body: CustomerPatch, user: StaffUser = Depends(require_permission("edit_customers")),
           db: Session = Depends(get_db)) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(404, "customer_not_found")
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(customer, field, value)
    audit(db, "customer.update", "customers", customer.id, actor_id=user.id, data={"fields": sorted(changes)})
    db.flush()
    db.refresh(customer)
    return customer


@router.post("/{customer_id}/vehicles", response_model=VehicleOut, status_code=201)
def add_vehicle(customer_id: uuid.UUID, body: VehicleIn, user: StaffUser = Depends(require_permission("edit_customers")),
                db: Session = Depends(get_db)) -> Vehicle:
    if db.get(Customer, customer_id) is None:
        raise HTTPException(404, "customer_not_found")
    vehicle = Vehicle(customer_id=customer_id, **body.model_dump())
    db.add(vehicle)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "plate_already_registered") from exc
    db.refresh(vehicle)
    audit(db, "vehicle.create", "vehicles", vehicle.id, actor_id=user.id)
    return vehicle


@router.delete("/{customer_id}/vehicles/{vehicle_id}", status_code=204)
def remove_vehicle(customer_id: uuid.UUID, vehicle_id: uuid.UUID, user: StaffUser = Depends(require_permission("edit_customers")),
                   db: Session = Depends(get_db)) -> None:
    vehicle = db.get(Vehicle, vehicle_id)
    if vehicle is None or vehicle.customer_id != customer_id:
        raise HTTPException(404, "vehicle_not_found")
    vehicle.customer_id = None
    audit(db, "vehicle.detach", "vehicles", vehicle.id, actor_id=user.id)
