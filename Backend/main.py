import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from payments import router as payment_router, ensure_payment_indexes
load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME", "campusAutomation")
IST = timezone(timedelta(hours=5, minutes=30))

client = AsyncIOMotorClient(MONGO_URI)
db = client[DB_NAME]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure high-speed indexes exist for 30-day range queries and duplicate prevention
    try:
        await db.attendance.create_index([("date", DESCENDING)])
        await db.attendance.create_index([("updated_at", DESCENDING)])
        await db.audit_logs.create_index([("timestamp", DESCENDING)])
        await db.fee_transactions.create_index([("paid_at", DESCENDING)])
        await db.attendance.create_index(
            [("student_roll_no", ASCENDING), ("date", ASCENDING)],
            unique=True
        )
        await ensure_payment_indexes()
    except Exception as e:
        print(f"Index initialization notice: {e}")
    yield


app = FastAPI(title="Campus Automation Real-Time Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(payment_router)

class WardenAttendancePayload(BaseModel):
    student_roll_no: str
    status: str = Field(..., pattern="^(present|absent)$")
    marked_by: str = Field(..., example="WRD-01")
    actor_role: str = Field(default="warden", pattern="^(warden|super_admin)$")
    date: str | None = Field(default=None, pattern="^\\d{4}-\\d{2}-\\d{2}$")


# ==================== 1. LIVE 7D / 30D STATS ENGINE ====================
@app.get("/api/stats/live")
async def get_live_stats(days: int = Query(default=7, ge=1, le=30)):
    try:
        now_ist = datetime.now(IST)
        today_date = now_ist.date()
        yesterday_date = today_date - timedelta(days=1)
        start_date = today_date - timedelta(days=days - 1)

        # Ensure yesterday is always included in the DB query even if days=1
        query_start = min(start_date, yesterday_date).isoformat()
        query_end = today_date.isoformat()

        # MongoDB Native Aggregation: zero memory bloat even with 50,000+ records
        pipeline = [
            {"$match": {"date": {"$gte": query_start, "$lte": query_end}}},
            {
                "$group": {
                    "_id": "$date",
                    "total_marked": {"$sum": 1},
                    "present": {
                        "$sum": {
                            "$cond": [{"$eq": [{"$toLower": "$status"}, "present"]}, 1, 0]
                        }
                    },
                    "absent": {
                        "$sum": {
                            "$cond": [{"$eq": [{"$toLower": "$status"}, "absent"]}, 1, 0]
                        }
                    }
                }
            }
        ]

        agg_results = await db.attendance.aggregate(pipeline).to_list(length=35)
        agg_map = {row["_id"]: row for row in agg_results}

        # Build exact rolling calendar timeline from start_date -> today_date
        timeline = []
        active_rates = []

        for offset in range(days):
            current_d = start_date + timedelta(days=offset)
            d_str = current_d.isoformat()
            rec = agg_map.get(d_str)

            if rec and rec["total_marked"] > 0:
                rate = round((rec["present"] / rec["total_marked"]) * 100)
                active_rates.append(rate)
                timeline.append({
                    "date": d_str,
                    "day": current_d.strftime("%a"),
                    "short_date": current_d.strftime("%d %b"),
                    "present": rec["present"],
                    "absent": rec["absent"],
                    "total_marked": rec["total_marked"],
                    "rate": rate,
                    "has_data": True
                })
            else:
                timeline.append({
                    "date": d_str,
                    "day": current_d.strftime("%a"),
                    "short_date": current_d.strftime("%d %b"),
                    "present": 0,
                    "absent": 0,
                    "total_marked": 0,
                    "rate": 0,
                    "has_data": False
                })

        # Compute Today vs Yesterday real-time snapshot
        def build_day_summary(target_d):
            d_str = target_d.isoformat()
            rec = agg_map.get(d_str)
            if not rec or rec["total_marked"] == 0:
                return {
                    "date": d_str,
                    "day": target_d.strftime("%A"),
                    "present": 0,
                    "total_marked": 0,
                    "rate": None
                }
            return {
                "date": d_str,
                "day": target_d.strftime("%A"),
                "present": rec["present"],
                "total_marked": rec["total_marked"],
                "rate": round((rec["present"] / rec["total_marked"]) * 100)
            }

        today_summary = build_day_summary(today_date)
        yesterday_summary = build_day_summary(yesterday_date)

        delta_vs_yesterday = None
        if today_summary["rate"] is not None and yesterday_summary["rate"] is not None:
            delta_vs_yesterday = today_summary["rate"] - yesterday_summary["rate"]

        period_avg = round(sum(active_rates) / len(active_rates)) if active_rates else 0

        # Fetch ONLY top 6 newest events per collection via index sort (ultra-fast)
        activities = []

        audit_docs = await db.audit_logs.find({}, {"_id": 0}).sort("timestamp", DESCENDING).limit(6).to_list(length=6)
        for log in audit_docs:
            activities.append({
                "type": "audit",
                "message": f"{log.get('actor_id')} -> {log.get('action')} ({log.get('target_roll_no')}: {log.get('new_value')})",
                "timestamp": str(log.get("timestamp", ""))
            })

        att_docs = await db.attendance.find({}, {"_id": 0}).sort("updated_at", DESCENDING).limit(6).to_list(length=6)
        for att in att_docs:
            activities.append({
                "type": "attendance",
                "message": f"{att.get('marked_by')} marked {att.get('student_roll_no')} {att.get('status')} ({att.get('room_id')})",
                "timestamp": str(att.get("updated_at") or att.get("date", ""))
            })

        txn_docs = await db.fee_transactions.find({}, {"_id": 0}).sort("paid_at", DESCENDING).limit(6).to_list(length=6)
        for txn in txn_docs:
            activities.append({
                "type": "fee",
                "message": f"Fee Rs.{txn.get('amount', 0):,} {txn.get('status')} - {txn.get('student_roll_no')} ({txn.get('payment_mode')})",
                "timestamp": str(txn.get("paid_at", ""))
            })

        activities.sort(key=lambda x: x["timestamp"], reverse=True)

        return {
            "window_days": days,
            "server_date_ist": today_date.isoformat(),
            "period_avg": period_avg,
            "today": today_summary,
            "yesterday": yesterday_summary,
            "delta_vs_yesterday": delta_vs_yesterday,
            "timeline": timeline,
            "recent_activities": activities[:6]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 2. WARDEN / ADMIN LIVE ATTENDANCE WRITE ====================
@app.post("/api/attendance/mark")
async def mark_student_attendance(payload: WardenAttendancePayload):
    """Real-world endpoint for Admin/Warden page: mutates MongoDB & logs audit trail live."""
    try:
        student = await db.students.find_one({"roll_no": payload.student_roll_no})
        if not student:
            raise HTTPException(status_code=404, detail="Student not found in database")

        now_ist = datetime.now(IST)
        target_date = payload.date or now_ist.date().isoformat()
        now_iso = now_ist.isoformat()

        existing = await db.attendance.find_one({
            "student_roll_no": payload.student_roll_no,
            "date": target_date
        })
        old_status = existing["status"] if existing else None

        # Atomic upsert into attendance collection
        await db.attendance.update_one(
            {"student_roll_no": payload.student_roll_no, "date": target_date},
            {
                "$set": {
                    "hostel_id": student.get("hostel_id"),
                    "room_id": student.get("room_id"),
                    "status": payload.status,
                    "marked_by": payload.marked_by,
                    "updated_at": now_iso
                }
            },
            upsert=True
        )

        # Immutable audit trail automatically feeds Recent Activities
        await db.audit_logs.insert_one({
            "actor_id": payload.marked_by,
            "actor_role": payload.actor_role,
            "action": "MARK_ATTENDANCE",
            "target_roll_no": payload.student_roll_no,
            "old_value": old_status,
            "new_value": payload.status,
            "timestamp": now_iso
        })

        return {
            "message": "Attendance saved and stats updated",
            "date": target_date,
            "student_roll_no": payload.student_roll_no,
            "status": payload.status
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/attendance/roster")
async def get_attendance_roster(
    date: str | None = Query(default=None, pattern="^\\d{4}-\\d{2}-\\d{2}$"),
    hostel_id: str = Query(default="ALL")
):
    try:
        target_date = date or datetime.now(IST).date().isoformat()

        # Fetch hostels and staff dynamically for dropdown filters
        hostels = await db.hostels.find({}, {"_id": 0, "hostel_id": 1, "name": 1}).sort("hostel_id", ASCENDING).to_list(length=50)
        staff_list = await db.staff_users.find(
            {}, {"_id": 0, "staff_id": 1, "name": 1, "role": 1, "assigned_hostel_id": 1}
        ).sort("staff_id", ASCENDING).to_list(length=50)

        # Filter students by hostel if selected
        student_query = {}
        if hostel_id != "ALL":
            student_query["hostel_id"] = hostel_id

        students = await db.students.find(
            student_query,
            {"_id": 0, "roll_no": 1, "name": 1, "branch": 1, "hostel_id": 1, "room_id": 1}
        ).sort([("hostel_id", ASCENDING), ("room_id", ASCENDING), ("roll_no", ASCENDING)]).to_list(length=1000)

        # Fetch existing attendance for target_date
        att_query = {"date": target_date}
        if hostel_id != "ALL":
            att_query["hostel_id"] = hostel_id

        att_docs = await db.attendance.find(att_query, {"_id": 0, "student_roll_no": 1, "status": 1}).to_list(length=1000)
        att_map = {doc["student_roll_no"]: doc["status"] for doc in att_docs}

        for s in students:
            s["status"] = att_map.get(s["roll_no"], "unmarked")

        return {
            "date": target_date,
            "hostels": hostels,
            "staff": staff_list,
            "students": students
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


import re
from pydantic import field_validator

COURSE_BRANCHES = {
    "BTECH": ["CSE (AI/ML)", "CSE", "ECE", "Electrical", "Mechanical", "Civil"],
    "BCA": ["General", "Data Science", "Cloud Computing", "Cyber Security"],
    "MCA": ["General", "AI & ML", "Software Engineering", "Cyber Security"],
}

COURSE_MAX_SEM = {"BTECH": 8, "BCA": 6, "MCA": 4}

HOSTEL_ROOM_PREFIX = {
    "HST-01": "SB",
    "HST-02": "VV",
    "HST-03": "RM",
    "HST-04": "KL",
    "HST-05": "KP",
}


def normalize_indian_phone(raw: str) -> str:
    """Accepts 10-digit, 0XXXXXXXXXX, 91XXXXXXXXXX, +91XXXXXXXXXX (with spaces/hyphens)."""
    digits = re.sub(r"\D", "", raw.strip())
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not re.match(r"^[6-9]\d{9}$", digits):
        raise ValueError("Invalid Indian phone number. Use 10 digits, 0-prefix, 91, or +91.")
    return f"+91{digits}"


class StudentRegisterPayload(BaseModel):
    name: str = Field(..., min_length=2, max_length=80)
    phone: str
    guardian_phone: str
    room_id: str = Field(..., min_length=2, max_length=15)
    roll_no: str = Field(..., min_length=4, max_length=20)
    semester: int = Field(..., ge=1, le=8)
    hostel_id: str
    email: str
    gender: str = Field(..., pattern="^(male|female)$")
    course: str
    branch: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        cleaned = " ".join(v.strip().split())
        if not re.match(r"^[A-Za-z.\s]{2,80}$", cleaned):
            raise ValueError("Name must contain only letters and spaces.")
        return cleaned

    @field_validator("phone", "guardian_phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        return normalize_indian_phone(v)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if not re.match(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", cleaned):
            raise ValueError("Invalid email address format.")
        return cleaned

    @field_validator("roll_no")
    @classmethod
    def validate_roll_no(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if not re.match(r"^[A-Z0-9-]{4,20}$", cleaned):
            raise ValueError("Roll number must be alphanumeric (e.g., 2026AI009).")
        return cleaned

    @field_validator("course")
    @classmethod
    def validate_course(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if cleaned not in COURSE_BRANCHES:
            raise ValueError("Invalid course selected.")
        return cleaned


@app.get("/api/students/meta")
async def get_student_form_meta():
    try:
        hostels = await db.hostels.find(
            {},
            {"_id": 0, "hostel_id": 1, "name": 1, "allowed_gender": 1, "allowed_years": 1}
        ).sort("hostel_id", ASCENDING).to_list(length=50)
        return {
            "hostels": hostels,
            "course_branches": COURSE_BRANCHES,
            "course_max_sem": COURSE_MAX_SEM
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/students/register", status_code=201)
async def register_student(payload: StudentRegisterPayload):
    try:
        if payload.phone == payload.guardian_phone:
            raise HTTPException(status_code=400, detail="Student phone and Guardian phone cannot be identical.")

        if payload.branch not in COURSE_BRANCHES[payload.course]:
            raise HTTPException(status_code=400, detail=f"Invalid branch '{payload.branch}' for {payload.course}.")

        max_sem = COURSE_MAX_SEM[payload.course]
        if payload.semester > max_sem:
            raise HTTPException(status_code=400, detail=f"{payload.course} allows semesters 1 to {max_sem} only.")

        academic_year = (payload.semester + 1) // 2

        # Duplicate check
        existing_student = await db.students.find_one(
            {"$or": [{"roll_no": payload.roll_no}, {"email": payload.email}]},
            {"_id": 1, "roll_no": 1, "email": 1}
        )
        if existing_student:
            if existing_student.get("roll_no") == payload.roll_no:
                raise HTTPException(status_code=409, detail=f"Roll number {payload.roll_no} already exists.")
            raise HTTPException(status_code=409, detail=f"Email {payload.email} is already registered.")

        # Hostel rules enforcement
        hostel = await db.hostels.find_one({"hostel_id": payload.hostel_id})
        if not hostel:
            raise HTTPException(status_code=404, detail="Selected hostel does not exist.")

        if hostel.get("allowed_gender") != payload.gender:
            raise HTTPException(
                status_code=400,
                detail=f"{hostel['name']} is restricted to {hostel['allowed_gender']} students."
            )

        if academic_year not in hostel.get("allowed_years", []):
            raise HTTPException(
                status_code=400,
                detail=f"{hostel['name']} accepts Year {hostel['allowed_years']} only (Sem {payload.semester} = Year {academic_year})."
            )

        # Normalize Room ID ('102' -> 'SB-102' or 'SB-102')
        raw_room = payload.room_id.strip().upper()
        prefix = HOSTEL_ROOM_PREFIX.get(payload.hostel_id, payload.hostel_id[:2])
        normalized_room_id = f"{prefix}-{raw_room}" if raw_room.isdigit() else raw_room

        if not re.match(r"^[A-Z]{2,4}-\d{2,4}$", normalized_room_id):
            raise HTTPException(status_code=400, detail=f"Invalid room format. Use e.g. {prefix}-102 or 102.")

        # Atomic Room Allocation (3-student cap)
        max_cap = hostel.get("max_students_per_room", 3)
        existing_room = await db.rooms.find_one({"room_id": normalized_room_id})
        room_created = False

        if existing_room:
            if existing_room.get("hostel_id") != payload.hostel_id:
                raise HTTPException(status_code=400, detail=f"Room {normalized_room_id} belongs to another hostel.")

            updated_room = await db.rooms.find_one_and_update(
                {
                    "room_id": normalized_room_id,
                    "current_occupancy": {"$lt": max_cap},
                    "occupants": {"$ne": payload.roll_no}
                },
                {
                    "$inc": {"current_occupancy": 1},
                    "$push": {"occupants": payload.roll_no}
                },
                return_document=ReturnDocument.AFTER
            )
            if not updated_room:
                raise HTTPException(status_code=400, detail=f"Room {normalized_room_id} is full ({max_cap}/{max_cap}).")

            if updated_room["current_occupancy"] >= max_cap:
                await db.rooms.update_one({"room_id": normalized_room_id}, {"$set": {"status": "full"}})
        else:
            room_digits = re.search(r"\d+", normalized_room_id).group()
            floor_num = int(room_digits[0]) if len(room_digits) >= 3 else 1
            await db.rooms.insert_one({
                "room_id": normalized_room_id,
                "hostel_id": payload.hostel_id,
                "floor": floor_num,
                "max_capacity": max_cap,
                "current_occupancy": 1,
                "occupants": [payload.roll_no],
                "status": "available"
            })
            room_created = True

        # Insert Student Document into MongoDB
        now_iso = datetime.now(IST).isoformat()
        student_doc = {
            "roll_no": payload.roll_no,
            "name": payload.name,
            "email": payload.email,
            "password_hash": "$2b$12$replace_with_bcrypt_hash",
            "role": "student",
            "gender": payload.gender,
            "year": academic_year,
            "semester": payload.semester,
            "course": payload.course,
            "branch": payload.branch,
            "phone": payload.phone,
            "guardian_phone": payload.guardian_phone,
            "hostel_id": payload.hostel_id,
            "room_id": normalized_room_id,
            "fee_details": {
                "total_fee": 85000,
                "paid_fee": 0,
                "due_fee": 85000,
                "status": "unpaid",
                "last_payment_date": None
            },
            "profile_completed": True,
            "is_active": True,
            "created_at": now_iso
        }

        try:
            await db.students.insert_one(student_doc)
        except Exception:
            if room_created:
                await db.rooms.delete_one({"room_id": normalized_room_id})
            else:
                await db.rooms.update_one(
                    {"room_id": normalized_room_id},
                    {
                        "$inc": {"current_occupancy": -1},
                        "$pull": {"occupants": payload.roll_no},
                        "$set": {"status": "available"}
                    }
                )
            raise

        await db.audit_logs.insert_one({
            "actor_id": "SELF-ENTRY",
            "actor_role": "student",
            "action": "STUDENT_REGISTERED",
            "target_roll_no": payload.roll_no,
            "old_value": None,
            "new_value": f"{payload.name} ({normalized_room_id})",
            "timestamp": now_iso
        })

        return {
            "message": f"{payload.name} added to {hostel['name']} ({normalized_room_id})",
            "roll_no": payload.roll_no,
            "room_id": normalized_room_id,
            "hostel_id": payload.hostel_id
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/warden/students")
async def search_students_for_warden(
    q: str = Query(default=""),
    hostel_id: str = Query(default="ALL")
):
    try:
        query_filter = {"is_active": True}
        if hostel_id != "ALL":
            query_filter["hostel_id"] = hostel_id

        search_term = q.strip()
        if search_term:
            regex = {"$regex": re.escape(search_term), "$options": "i"}
            query_filter["$or"] = [
                {"name": regex},
                {"roll_no": regex},
                {"room_id": regex},
                {"branch": regex},
                {"email": regex},
                {"phone": regex}
            ]

        # Build hostel_id -> hostel_name lookup map
        hostels = await db.hostels.find(
            {}, {"_id": 0, "hostel_id": 1, "name": 1}
        ).sort("hostel_id", ASCENDING).to_list(length=50)
        hostel_map = {h["hostel_id"]: h["name"] for h in hostels}

        students = await db.students.find(
            query_filter,
            {
                "_id": 0,
                "roll_no": 1,
                "name": 1,
                "email": 1,
                "phone": 1,
                "guardian_phone": 1,
                "semester": 1,
                "year": 1,
                "branch": 1,
                "hostel_id": 1,
                "room_id": 1
            }
        ).sort("created_at", DESCENDING).to_list(length=500)

        for s in students:
            s["hostel_name"] = hostel_map.get(s.get("hostel_id"), s.get("hostel_id", "Unassigned"))

        return {
            "count": len(students),
            "hostels": hostels,
            "students": students
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/students/{roll_no}")
async def delete_student_permanently(roll_no: str):
    """Permanently removes a student, releases their hostel room slot, and purges orphan records."""
    try:
        clean_roll = roll_no.strip().upper()
        student = await db.students.find_one({"roll_no": clean_roll})
        if not student:
            raise HTTPException(status_code=404, detail=f"Student {clean_roll} not found.")

        student_name = student.get("name", clean_roll)
        room_id = student.get("room_id")

        # 1. Atomically release hostel room occupancy (guards against negative occupancy)
        if room_id:
            await db.rooms.update_one(
                {
                    "room_id": room_id,
                    "occupants": clean_roll,
                    "current_occupancy": {"$gt": 0}
                },
                {
                    "$inc": {"current_occupancy": -1},
                    "$pull": {"occupants": clean_roll},
                    "$set": {"status": "available"}
                }
            )

        # 2. Purge student document and all related records (zero orphan data)
        await db.students.delete_one({"roll_no": clean_roll})
        await db.attendance.delete_many({"student_roll_no": clean_roll})
        await db.fee_transactions.delete_many({"student_roll_no": clean_roll})

        # 3. Record immutable deletion event in audit_logs for Recent Activities
        now_iso = datetime.now(IST).isoformat()
        await db.audit_logs.insert_one({
            "actor_id": "ADMIN",
            "actor_role": "super_admin",
            "action": "STUDENT_DELETED",
            "target_roll_no": clean_roll,
            "old_value": f"{student_name} ({room_id or 'No Room'})",
            "new_value": "PERMANENTLY_REMOVED",
            "timestamp": now_iso
        })

        return {
            "message": f"{student_name} ({clean_roll}) permanently removed and room slot released.",
            "roll_no": clean_roll
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
# ====================
# -----Dashboard------
# ====================
@app.get("/api/dashboard")
async def dashboard():
    try:
        today_str = datetime.now(IST).date().isoformat()

        # Count active students directly from the students collection
        total_students = await db.students.count_documents({"is_active": True})

        # Count only today's present entries using the indexed 'date' field
        present_today = await db.attendance.count_documents({
            "date": today_str,
            "status": "present"
        })

        return {
            "status": "success",
            "data": {
                "date": today_str,
                "total_students": total_students,
                "present_today": present_today
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))