import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, field_validator
from pymongo import ASCENDING, DESCENDING, ReturnDocument

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "campusAutomation")
IST = timezone(timedelta(hours=5, minutes=30))

client = AsyncIOMotorClient(MONGO_URI)
db = client[DB_NAME]

router = APIRouter()

ALLOWED_PAYMENT_MODES = {"UPI", "Cash", "Bank Transfer", "Card", "Cheque"}


async def ensure_payment_indexes():
    """Creates unique and partial indexes for receipt numbers and UTR reference IDs."""
    try:
        await db.fee_transactions.create_index([("receipt_no", ASCENDING)], unique=True)
        await db.fee_transactions.create_index(
            [("reference_id", ASCENDING)],
            unique=True,
            partialFilterExpression={"reference_id": {"$type": "string", "$gt": ""}}
        )
    except Exception as e:
        print(f"Payment index initialization notice: {e}")


class RecordPaymentPayload(BaseModel):
    student_roll_no: str = Field(..., min_length=4, max_length=20)
    amount: int = Field(..., gt=0, le=500000)
    payment_mode: str
    reference_id: str | None = Field(default=None, max_length=64)
    recorded_by: str = Field(..., min_length=2, max_length=30)
    actor_role: str = Field(default="super_admin", pattern="^(warden|super_admin|accountant)$")
    remarks: str | None = Field(default="", max_length=200)

    @field_validator("student_roll_no")
    @classmethod
    def clean_roll_no(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("payment_mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        cleaned = v.strip()
        if cleaned not in ALLOWED_PAYMENT_MODES:
            raise ValueError(f"Payment mode must be one of: {', '.join(ALLOWED_PAYMENT_MODES)}")
        return cleaned

    @field_validator("reference_id")
    @classmethod
    def clean_ref(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().upper()
        return cleaned if cleaned else None


class FeeAdjustmentPayload(BaseModel):
    new_total_fee: int = Field(..., ge=0, le=500000)
    adjusted_by: str = Field(..., min_length=2, max_length=30)
    reason: str = Field(..., min_length=3, max_length=200)


@router.get("/api/payments/overview")
async def get_payments_overview(
    status: str = Query(default="ALL", pattern="^(ALL|paid|partial|unpaid)$"),
    hostel_id: str = Query(default="ALL"),
    q: str = Query(default="")
):
    """Returns global fee KPIs (via MongoDB aggregation) + filtered student fee roster."""
    try:
        base_match = {"is_active": True}
        if hostel_id != "ALL":
            base_match["hostel_id"] = hostel_id

        kpi_pipeline = [
            {"$match": base_match},
            {
                "$group": {
                    "_id": None,
                    "total_students": {"$sum": 1},
                    "total_expected": {"$sum": {"$ifNull": ["$fee_details.total_fee", 0]}},
                    "total_collected": {"$sum": {"$ifNull": ["$fee_details.paid_fee", 0]}},
                    "total_due": {"$sum": {"$ifNull": ["$fee_details.due_fee", 0]}},
                    "paid_count": {
                        "$sum": {"$cond": [{"$eq": ["$fee_details.status", "paid"]}, 1, 0]}
                    },
                    "partial_count": {
                        "$sum": {"$cond": [{"$eq": ["$fee_details.status", "partial"]}, 1, 0]}
                    },
                    "unpaid_count": {
                        "$sum": {"$cond": [{"$eq": ["$fee_details.status", "unpaid"]}, 1, 0]}
                    }
                }
            }
        ]

        kpi_docs = await db.students.aggregate(kpi_pipeline).to_list(length=1)
        if kpi_docs:
            k = kpi_docs[0]
            expected = k["total_expected"]
            collected = k["total_collected"]
            collection_pct = round((collected / expected) * 100, 1) if expected > 0 else 0
            kpis = {
                "total_students": k["total_students"],
                "total_expected": expected,
                "total_collected": collected,
                "total_due": k["total_due"],
                "paid_count": k["paid_count"],
                "partial_count": k["partial_count"],
                "unpaid_count": k["unpaid_count"],
                "collection_pct": collection_pct
            }
        else:
            kpis = {
                "total_students": 0,
                "total_expected": 0,
                "total_collected": 0,
                "total_due": 0,
                "paid_count": 0,
                "partial_count": 0,
                "unpaid_count": 0,
                "collection_pct": 0
            }

        roster_query = dict(base_match)
        if status != "ALL":
            roster_query["fee_details.status"] = status

        search_term = q.strip()
        if search_term:
            regex = {"$regex": re.escape(search_term), "$options": "i"}
            roster_query["$or"] = [
                {"name": regex},
                {"roll_no": regex},
                {"room_id": regex},
                {"course": regex},
                {"branch": regex}
            ]

        students = await db.students.find(
            roster_query,
            {
                "_id": 0,
                "roll_no": 1,
                "name": 1,
                "course": 1,
                "branch": 1,
                "semester": 1,
                "hostel_id": 1,
                "room_id": 1,
                "phone": 1,
                "fee_details": 1
            }
        ).sort([("fee_details.due_fee", DESCENDING), ("roll_no", ASCENDING)]).to_list(length=1000)

        hostels = await db.hostels.find(
            {}, {"_id": 0, "hostel_id": 1, "name": 1}
        ).sort("hostel_id", ASCENDING).to_list(length=50)

        staff = await db.staff_users.find(
            {}, {"_id": 0, "staff_id": 1, "name": 1, "role": 1}
        ).sort("staff_id", ASCENDING).to_list(length=50)

        return {
            "kpis": kpis,
            "hostels": hostels,
            "staff": staff,
            "students": students
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/payments/record", status_code=201)
async def record_fee_payment(payload: RecordPaymentPayload):
    """Atomically deducts student due_fee, records immutable receipt, and writes audit log."""
    try:
        if payload.payment_mode != "Cash" and not payload.reference_id:
            raise HTTPException(
                status_code=400,
                detail=f"Reference / UTR ID is mandatory for {payload.payment_mode} payments."
            )

        if payload.reference_id:
            dup_txn = await db.fee_transactions.find_one({"reference_id": payload.reference_id})
            if dup_txn:
                raise HTTPException(
                    status_code=409,
                    detail=f"Reference ID '{payload.reference_id}' is already recorded under receipt {dup_txn.get('receipt_no')}."
                )

        student = await db.students.find_one(
            {"roll_no": payload.student_roll_no, "is_active": True},
            {"_id": 0, "name": 1, "room_id": 1, "hostel_id": 1, "fee_details": 1}
        )
        if not student:
            raise HTTPException(status_code=404, detail=f"Active student {payload.student_roll_no} not found.")

        old_fee = student.get("fee_details") or {
            "total_fee": 85000,
            "paid_fee": 0,
            "due_fee": 85000,
            "status": "unpaid",
            "last_payment_date": None
        }

        if old_fee["due_fee"] <= 0:
            raise HTTPException(status_code=400, detail=f"{student['name']} has already paid full fees (Zero Due).")

        if payload.amount > old_fee["due_fee"]:
            raise HTTPException(
                status_code=400,
                detail=f"Overpayment blocked. Maximum payable due is Rs.{old_fee['due_fee']:,}."
            )

        now_ist = datetime.now(IST)
        now_iso = now_ist.isoformat()
        receipt_no = f"RCP-{now_ist.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

        updated_student = await db.students.find_one_and_update(
            {
                "roll_no": payload.student_roll_no,
                "is_active": True,
                "fee_details.due_fee": {"$gte": payload.amount}
            },
            [
                {
                    "$set": {
                        "fee_details.paid_fee": {"$add": ["$fee_details.paid_fee", payload.amount]},
                        "fee_details.due_fee": {"$subtract": ["$fee_details.due_fee", payload.amount]},
                        "fee_details.last_payment_date": now_iso,
                        "fee_details.status": {
                            "$cond": [
                                {"$lte": [{"$subtract": ["$fee_details.due_fee", payload.amount]}, 0]},
                                "paid",
                                "partial"
                            ]
                        }
                    }
                }
            ],
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0, "fee_details": 1}
        )

        if not updated_student:
            raise HTTPException(
                status_code=409,
                detail="Concurrent payment conflict detected. Please refresh and try again."
            )

        new_fee = updated_student["fee_details"]

        txn_doc = {
            "receipt_no": receipt_no,
            "student_roll_no": payload.student_roll_no,
            "student_name": student["name"],
            "hostel_id": student.get("hostel_id"),
            "room_id": student.get("room_id"),
            "amount": payload.amount,
            "payment_mode": payload.payment_mode,
            "reference_id": payload.reference_id,
            "status": "completed",
            "recorded_by": payload.recorded_by,
            "remarks": (payload.remarks or "").strip(),
            "balance_after": new_fee["due_fee"],
            "paid_at": now_iso
        }

        try:
            await db.fee_transactions.insert_one(txn_doc)
        except Exception:
            await db.students.update_one(
                {"roll_no": payload.student_roll_no},
                {"$set": {"fee_details": old_fee}}
            )
            raise

        await db.audit_logs.insert_one({
            "actor_id": payload.recorded_by,
            "actor_role": payload.actor_role,
            "action": "RECORD_PAYMENT",
            "target_roll_no": payload.student_roll_no,
            "old_value": f"Due: Rs.{old_fee['due_fee']:,} ({old_fee['status']})",
            "new_value": f"Paid Rs.{payload.amount:,} via {payload.payment_mode} | Due: Rs.{new_fee['due_fee']:,} ({new_fee['status']})",
            "timestamp": now_iso
        })

        txn_doc.pop("_id", None)
        return {
            "message": f"Payment of Rs.{payload.amount:,} recorded ({receipt_no})",
            "receipt": txn_doc,
            "fee_details": new_fee
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/payments/history/{roll_no}")
async def get_student_payment_history(roll_no: str):
    """Fetches complete fee summary and chronological transaction ledger for a student."""
    try:
        clean_roll = roll_no.strip().upper()
        student = await db.students.find_one(
            {"roll_no": clean_roll},
            {
                "_id": 0,
                "roll_no": 1,
                "name": 1,
                "course": 1,
                "branch": 1,
                "semester": 1,
                "hostel_id": 1,
                "room_id": 1,
                "phone": 1,
                "guardian_phone": 1,
                "fee_details": 1
            }
        )
        if not student:
            raise HTTPException(status_code=404, detail="Student not found.")

        transactions = await db.fee_transactions.find(
            {"student_roll_no": clean_roll},
            {"_id": 0}
        ).sort("paid_at", DESCENDING).to_list(length=100)

        return {
            "student": student,
            "transactions": transactions
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/api/payments/adjust-fee/{roll_no}")
async def adjust_student_total_fee(roll_no: str, payload: FeeAdjustmentPayload):
    """Allows Admin to modify total fee (e.g., scholarship/concession) without breaking paid balance."""
    try:
        clean_roll = roll_no.strip().upper()
        student = await db.students.find_one({"roll_no": clean_roll}, {"_id": 0, "fee_details": 1})
        if not student:
            raise HTTPException(status_code=404, detail="Student not found.")

        old_fee = student["fee_details"]
        paid_so_far = old_fee.get("paid_fee", 0)

        if payload.new_total_fee < paid_so_far:
            raise HTTPException(
                status_code=400,
                detail=f"New total fee cannot be less than already paid amount (Rs.{paid_so_far:,})."
            )

        new_due = payload.new_total_fee - paid_so_far
        new_status = "paid" if new_due == 0 else ("partial" if paid_so_far > 0 else "unpaid")
        now_iso = datetime.now(IST).isoformat()

        await db.students.update_one(
            {"roll_no": clean_roll},
            {
                "$set": {
                    "fee_details.total_fee": payload.new_total_fee,
                    "fee_details.due_fee": new_due,
                    "fee_details.status": new_status
                }
            }
        )

        await db.audit_logs.insert_one({
            "actor_id": payload.adjusted_by,
            "actor_role": "super_admin",
            "action": "ADJUST_TOTAL_FEE",
            "target_roll_no": clean_roll,
            "old_value": f"Total: Rs.{old_fee['total_fee']:,}",
            "new_value": f"Total: Rs.{payload.new_total_fee:,} ({payload.reason})",
            "timestamp": now_iso
        })

        return {
            "message": f"Total fee updated to Rs.{payload.new_total_fee:,}",
            "due_fee": new_due,
            "status": new_status
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))