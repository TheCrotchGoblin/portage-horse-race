"""Customer management routes (spec §6.2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import Customer
from app.routes.deps import base_context, operator_name
from app.services import customers as customer_service
from app.services.customers import CustomerError
from app.templating import flash, render

router = APIRouter(prefix="/customers")


@router.get("")
def index(request: Request, session: Session = Depends(get_session), q: str = ""):
    ctx = base_context(request, session, "customers")
    ctx["q"] = q
    ctx["customers"] = customer_service.search(session, q, limit=50)
    return render(request, "customers/index.html", ctx)


@router.get("/new")
def new_form(request: Request, session: Session = Depends(get_session)):
    ctx = base_context(request, session, "customers")
    return render(request, "customers/form.html", ctx)


@router.post("/new")
def create(
    request: Request,
    session: Session = Depends(get_session),
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    notes: str = Form(""),
    confirm_duplicate: str = Form(""),
    return_to: str = Form(""),
):
    if not confirm_duplicate:
        dupes = customer_service.find_duplicates(session, name=name, phone=phone, email=email)
        if dupes:
            ctx = base_context(request, session, "customers")
            ctx.update({"dupes": dupes, "name": name, "phone": phone, "email": email,
                        "notes": notes, "return_to": return_to})
            return render(request, "customers/duplicate.html", ctx)
    try:
        customer = customer_service.create_customer(
            session, name=name, phone=phone, email=email, notes=notes, operator=operator_name(request)
        )
    except CustomerError as exc:
        ctx = base_context(request, session, "customers")
        ctx.update({"error": str(exc), "invalid_fields": ["name"], "return_to": return_to,
                    "values": {"name": name, "phone": phone, "email": email, "notes": notes}})
        return render(request, "customers/form.html", ctx, status_code=400)
    flash(request, f"Customer '{customer.name}' saved.")
    if return_to == "cashier":
        return RedirectResponse(f"/cashier?customer_id={customer.id}", status_code=303)
    return RedirectResponse(f"/customers/{customer.id}", status_code=303)


@router.get("/{customer_id}")
def detail(request: Request, customer_id: int, session: Session = Depends(get_session)):
    customer = session.get(Customer, customer_id)
    if customer is None:
        flash(request, "Customer not found.", "danger")
        return RedirectResponse("/customers", status_code=303)
    ctx = base_context(request, session, "customers")
    ctx["customer"] = customer
    ctx["history"] = customer_service.history(session, customer_id)
    return render(request, "customers/detail.html", ctx)


@router.get("/{customer_id}/edit")
def edit_form(request: Request, customer_id: int, session: Session = Depends(get_session)):
    customer = session.get(Customer, customer_id)
    if customer is None:
        flash(request, "Customer not found.", "danger")
        return RedirectResponse("/customers", status_code=303)
    ctx = base_context(request, session, "customers")
    ctx["customer"] = customer
    return render(request, "customers/form.html", ctx)


@router.post("/{customer_id}/edit")
def edit(
    request: Request,
    customer_id: int,
    session: Session = Depends(get_session),
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    notes: str = Form(""),
):
    customer = session.get(Customer, customer_id)
    if customer is None:
        flash(request, "Customer not found.", "danger")
        return RedirectResponse("/customers", status_code=303)
    try:
        customer_service.update_customer(
            session, customer, name=name, phone=phone, email=email, notes=notes,
            operator=operator_name(request),
        )
    except CustomerError as exc:
        ctx = base_context(request, session, "customers")
        ctx.update({"customer": customer, "error": str(exc), "invalid_fields": ["name"],
                    "values": {"name": name, "phone": phone, "email": email, "notes": notes}})
        return render(request, "customers/form.html", ctx, status_code=400)
    flash(request, "Customer updated.")
    return RedirectResponse(f"/customers/{customer_id}", status_code=303)
