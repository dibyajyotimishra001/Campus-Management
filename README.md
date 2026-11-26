# Campus Management System

A full-stack, real-time campus and hostel operations platform built with **FastAPI**, **MongoDB Atlas (Async Motor)**, and a **Modular SPA Frontend**. Engineered to replace fragmented spreadsheets and manual registers with atomic database operations, live analytics, and an immutable audit trail.

**Live Demo:** [campus-management-system.netlify.app](https://campus-management-system.netlify.app/)

## Problem Solved

Colleges and hostels typically manage student onboarding, room occupancy, daily roll-call attendance, and fee ledgers across disconnected files. This causes:

* **Overbooked rooms** and stale occupancy counts when students leave.
* **Fee discrepancies**, duplicate UTR/transaction entries, and accidental overpayments.
* **Zero real-time visibility** into daily attendance trends or staff actions.

This system unifies all four workflows into a single responsive dashboard backed by strict server-side validation, atomic MongoDB updates, and automated rollback safeguards.

## Tech Stack & Architecture

| Layer | Technologies Used |
| :--- | :--- |
| **Backend** | Python, FastAPI (Async), Uvicorn, Pydantic v2 (`field_validator`) |
| **Database** | MongoDB Atlas, Motor (Async Driver), Native Aggregation Pipelines, Compound & Partial Unique Indexes |
| **Frontend** | Vanilla JavaScript (ES6+ SPA), Modular HTML5 & CSS3 (Dark/Light Theme, Mobile Card Views), Custom SVG + Bar Charts |
| **Deployment** | Render (FastAPI Backend), Netlify (Frontend), MongoDB Atlas (Cloud DB) |

## Key Features & Engineering Highlights

### 1. Real-Time Dashboard & Analytics Engine
* Live IST-synced KPIs (total active students, daily attendance percentage, live progress bars).
* **7-Day & 30-Day Attendance Tracker** powered by MongoDB `$match` / `$group` aggregation pipelines (zero in-memory bloat) rendered as a dual Bar + SVG trendline chart.
* **Immutable Audit Feed** streaming live attendance, registration, deletion, and fee events.

### 2. Student Onboarding & Atomic Room Allocation
* Enforces Indian phone normalization, course-branch-semester rules, and hostel gender/year restrictions via Pydantic & FastAPI.
* **Atomic Room Cap (`find_one_and_update`)** prevents race-condition overbooking (3-student max per room) with automatic rollback if student insertion fails.

### 3. Attendance Roll-Call System
* Date, hostel, and warden/admin-scoped attendance marking with live client-side search.
* Compound unique index on `(student_roll_no, date)` guarantees zero duplicate daily entries.

### 4. Fee & Payment Ledger (Zero-Compromise Logic)
* **Atomic Anti-Overpayment Guard:** Verifies `due_fee >= amount` and updates `paid_fee`, `due_fee`, and `status` (`unpaid` / `partial` / `paid`) in a single atomic pipeline operation.
* **Duplicate UTR Blocker:** Partial unique index on `reference_id` prevents reusing UPI/Bank transaction IDs.
* Automatic balance rollback on ledger failure, complete student payment history, and **1-click printable official receipts**.

### 5. Student Directory & Cascade Delete
* Instant search and hostel filtering with automated `hostel_id` to Hostel Name resolution and tap-to-call/email links.
* Permanent deletion atomically releases the student's hostel room slot, purges orphan attendance/payment records, and logs the event.

## Project Structure

```text
Campus Management/
├── Backend/
│   ├── main.py              # Core FastAPI app, lifespan indexes, dashboard, attendance, student CRUD
│   ├── payments.py          # Modular APIRouter for atomic fee ledger, KPIs & receipts
│   └── requirements.txt     # Production Python dependencies
├── scripts/
│   ├── script.js            # SPA router, live stats, attendance, student directory & API switch
│   └── payments.js          # Fee ledger controller, modals & receipt generator
├── stylesheets/
│   ├── style.css            # Base layout & Carbon-Slate Dark/Light theme variables
│   ├── dashboard.css        # KPI cards & live status styling
│   ├── attendance.css       # Roll-call table & status pills
│   ├── payment.css          # Fee ledger, modals & responsive cards
│   ├── student-info.css     # Student directory cards & delete controls
│   ├── stats.css            # 7D/30D dual chart & activity feed
│   └── responsive.css       # Tablet & mobile breakpoints
├── index.html               # Single-Page Application entry point
└── favicon.ico              # Custom brand favicon
```

## Local Setup

1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd "Campus Management/Backend"
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables** (`Backend/.env`)
   ```env
   MONGO_URI=your_mongodb_atlas_connection_string
   DB_NAME=campusAutomation
   ```

4. **Run the FastAPI server**
   ```bash
   uvicorn main:app --reload
   ```

5. **Launch Frontend**
   Set `API_BASE_URL` in `scripts/script.js` to `http://127.0.0.1:8000` and open `index.html` with Live Server.

## License

Licensed under the [GNU AGPL-3.0](LICENSE).