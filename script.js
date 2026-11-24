const search = document.getElementById("search")

search.addEventListener("focus", () => {
    search.parentElement.classList.toggle("active");
})
search.addEventListener("blur", () => {
    search.parentElement.classList.remove("active");
});

const lightIcon = document.querySelector(".lightIcon");

let savedTheme = localStorage.getItem("currentTheme");

if (savedTheme === "enabled") {
    document.body.classList.add("light");
    }

lightIcon.addEventListener("click", function () {
    document.body.classList.toggle("light");

    if (document.body.classList.contains("light")) {
        localStorage.setItem("currentTheme", "enabled");
    } else {
        localStorage.setItem("currentTheme", "disabled");
    }
});

// SPA Navigation Logic
const sidebarItems = document.querySelectorAll('.sidebar ul li');
const pageViews = document.querySelectorAll('.page-view');

const initialActiveTarget = document.querySelector('.page-view.active-page').id;
const initialActiveTab = document.querySelector(`[data-target="${initialActiveTarget}"]`);
if (initialActiveTab) {
    initialActiveTab.classList.add('active-tab');
}

sidebarItems.forEach(item => {
    item.addEventListener('click', () => {
        const targetId = item.getAttribute('data-target');
        if (!targetId) return;

        pageViews.forEach(page => {
            page.classList.remove('active-page');
        });

        sidebarItems.forEach(li => {
            li.classList.remove('active-tab');
        });

        const targetPage = document.getElementById(targetId);
        if (targetPage) {
            targetPage.classList.add('active-page');
        }

        item.classList.add('active-tab');
    });
});

const STATS_API_BASE = "http://127.0.0.1:8000/api/stats/live";
let currentStatsWindow = 7;

async function loadLiveStats(days = currentStatsWindow) {
    currentStatsWindow = days;
    try {
        const res = await fetch(`${STATS_API_BASE}?days=${days}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        // 1. Live Today vs Yesterday header status
        const metaEl = document.getElementById("day-delta-meta");
        const todayText = data.today.rate !== null
            ? `${data.today.day}: ${data.today.rate}% (${data.today.present}/${data.today.total_marked})`
            : `${data.today.day}: Unmarked`;

        let deltaText = "";
        if (data.delta_vs_yesterday !== null) {
            const sign = data.delta_vs_yesterday >= 0 ? "+" : "";
            deltaText = ` | vs Yest: ${sign}${data.delta_vs_yesterday}%`;
        } else if (data.yesterday.rate === null) {
            deltaText = ` | Yest: No record`;
        }
        metaEl.textContent = `${todayText}${deltaText}`;

        // 2. Render dynamic rolling 7-day or 30-day chart
        renderAttendanceTimeline(data.timeline, data.period_avg, days);

        // 3. Render live Recent Activities
        const list = document.getElementById("recent-activity-list");
        list.innerHTML = data.recent_activities.length
            ? data.recent_activities.map(item => `
                <li class="activity-item">
                    <span class="activity-dot"></span>
                    <div class="activity-content">
                        <p>${item.message}</p>
                        <span>${item.timestamp ? new Date(item.timestamp).toLocaleString("en-IN") : ""}</span>
                    </div>
                </li>
            `).join("")
            : `<li class="activity-item"><span>No live events recorded yet.</span></li>`;
    } catch (err) {
        console.error("Live stats fetch error:", err);
        document.getElementById("recent-activity-list").innerHTML =
            `<li class="activity-item"><span style="color: var(--error-crimson);">Stats API offline.</span></li>`;
    }
}

function renderAttendanceTimeline(timeline, periodAvg, days) {
    const barBox = document.getElementById("weekly-bar-chart");
    const labelBox = document.getElementById("weekly-chart-labels");
    const svg = document.getElementById("attendance-trend-svg");
    const avgBadge = document.getElementById("weekly-avg-badge");

    barBox.innerHTML = "";
    labelBox.innerHTML = "";
    barBox.classList.toggle("mode-30d", days > 7);
    avgBadge.textContent = `${periodAvg}% Avg`;

    if (!timeline || timeline.length === 0) {
        svg.innerHTML = "";
        return;
    }

    const svgW = 280;
    const svgH = 110;
    const stepX = svgW / timeline.length;
    const activePoints = [];

    timeline.forEach((item, i) => {
        const tooltip = item.has_data
            ? `${item.short_date} (${item.day}): ${item.present}/${item.total_marked} Present (${item.rate}%)`
            : `${item.short_date} (${item.day}): No roll-call recorded`;

        const col = document.createElement("div");
        col.className = "bar-col";
        col.innerHTML = `
            <div class="bar-fill ${item.has_data ? "" : "no-data"}"
                 style="height: ${item.has_data ? item.rate : 0}%;"
                 title="${tooltip}">
            </div>`;
        barBox.appendChild(col);

        // Clean X-axis labels: all 7 days in 7D mode, smart milestones in 30D mode
        const lbl = document.createElement("span");
        if (days <= 7) {
            lbl.textContent = item.day;
        } else if (i === 0 || i === timeline.length - 1 || i % 7 === 0) {
            lbl.textContent = item.short_date.split(" ")[0];
        } else {
            lbl.textContent = "";
        }
        labelBox.appendChild(lbl);

        // Plot SVG trend line only across days with real DB records
        if (item.has_data) {
            const x = (i * stepX) + (stepX / 2);
            const y = svgH - (item.rate / 100) * (svgH - 8) - 4;
            activePoints.push(`${x},${y}`);
        }
    });

    svg.innerHTML = activePoints.length >= 2
        ? `<polyline fill="none" stroke="var(--accent-green)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" points="${activePoints.join(" ")}" />`
        : "";
}

// Wire 7D / 30D range switch buttons
document.querySelectorAll(".range-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".range-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        loadLiveStats(Number(btn.dataset.days));
    });
});

// Expose global refresh hook for future Warden/Admin toggle actions
window.refreshLiveStats = () => loadLiveStats(currentStatsWindow);

document.addEventListener("DOMContentLoaded", () => loadLiveStats(7));

// ==================== WARDEN / ADMIN LIVE ATTENDANCE ENGINE ====================
const ATT_API_BASE = "http://127.0.0.1:8000/api/attendance";
let attDropdownsInitialized = false;

async function loadAttendanceRoster() {
    const dateInput = document.getElementById("att-date-picker");
    const hostelSelect = document.getElementById("att-hostel-filter");
    const actorSelect = document.getElementById("att-actor-select");
    const tbody = document.getElementById("att-roster-tbody");

    if (!tbody) return;

    const params = new URLSearchParams();
    if (dateInput.value) params.append("date", dateInput.value);
    if (hostelSelect.value) params.append("hostel_id", hostelSelect.value);

    try {
        const res = await fetch(`${ATT_API_BASE}/roster?${params.toString()}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        // Sync date picker with server IST date on first load
        if (!dateInput.value) {
            dateInput.value = data.date;
        }

        // Populate Hostel & Warden/Admin dropdowns dynamically from MongoDB once
        if (!attDropdownsInitialized) {
            hostelSelect.innerHTML = `<option value="ALL">All Hostels</option>` +
                data.hostels.map(h => `<option value="${h.hostel_id}">${h.name}</option>`).join("");

            actorSelect.innerHTML = data.staff.map(st =>
                `<option value="${st.staff_id}" data-role="${st.role}" data-hostel="${st.assigned_hostel_id}">
                    ${st.staff_id} - ${st.name} (${st.role})
                </option>`
            ).join("");

            attDropdownsInitialized = true;
        }

        if (data.students.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5">No students found for this filter.</td></tr>`;
            return;
        }

        tbody.innerHTML = data.students.map(s => `
            <tr data-roll="${s.roll_no}">
                <td>${s.roll_no}</td>
                <td>${s.name}</td>
                <td>${s.room_id || "Unassigned"}</td>
                <td><span class="att-pill ${s.status}" id="pill-${s.roll_no}">${s.status}</span></td>
                <td>
                    <div class="att-btn-group">
                        <button class="att-btn present ${s.status === "present" ? "active" : ""}"
                                onclick="submitAttendance('${s.roll_no}', 'present', this)">Present</button>
                        <button class="att-btn absent ${s.status === "absent" ? "active" : ""}"
                                onclick="submitAttendance('${s.roll_no}', 'absent', this)">Absent</button>
                    </div>
                </td>
            </tr>
        `).join("");
        filterAttendanceTable();
    } catch (err) {
        console.error("Roster fetch error:", err);
        tbody.innerHTML = `<tr><td colspan="5" style="color: var(--error-crimson);">Failed to load roster from FastAPI.</td></tr>`;
    }
}

async function submitAttendance(rollNo, status, clickedBtn) {
    const dateInput = document.getElementById("att-date-picker");
    const actorSelect = document.getElementById("att-actor-select");
    const selectedActor = actorSelect.options[actorSelect.selectedIndex];

    if (!selectedActor) return;

    const btnGroup = clickedBtn.parentElement;
    const buttons = btnGroup.querySelectorAll("button");
    buttons.forEach(b => b.disabled = true);

    try {
        const res = await fetch(`${ATT_API_BASE}/mark`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                student_roll_no: rollNo,
                status: status,
                marked_by: selectedActor.value,
                actor_role: selectedActor.dataset.role || "warden",
                date: dateInput.value
            })
        });

        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Failed to mark attendance");

        // Update UI row instantly
        const pill = document.getElementById(`pill-${rollNo}`);
        pill.textContent = status;
        pill.className = `att-pill ${status}`;

        buttons.forEach(b => b.classList.remove("active"));
        clickedBtn.classList.add("active");

        // Trigger real-time refresh on Right-Side Stats Bar (Graph + Recent Activities)
        if (typeof window.refreshLiveStats === "function") {
            window.refreshLiveStats();
        }
    } catch (err) {
        console.error("Attendance mark error:", err);
        alert(`Error: ${err.message}`);
    } finally {
        buttons.forEach(b => b.disabled = false);
    }
}

// Auto-filter hostel when switching Warden/Admin actor (super_admin sees ALL)
document.getElementById("att-actor-select")?.addEventListener("change", (e) => {
    const opt = e.target.options[e.target.selectedIndex];
    const assignedHostel = opt?.dataset?.hostel || "ALL";
    const hostelSelect = document.getElementById("att-hostel-filter");
    if (hostelSelect) {
        hostelSelect.value = assignedHostel;
        loadAttendanceRoster();
    }
});

document.getElementById("att-date-picker")?.addEventListener("change", loadAttendanceRoster);
document.getElementById("att-hostel-filter")?.addEventListener("change", loadAttendanceRoster);

// Load fresh roster whenever Attendance tab is clicked in sidebar
document.querySelector('[data-target="attendance-page"]')?.addEventListener("click", loadAttendanceRoster);

// Live client-side search for Attendance Roster (Roll No, Name, Room No)
function filterAttendanceTable() {
    const query = document.querySelector(".studentSearch")?.value.trim().toLowerCase() || "";
    const rows = document.querySelectorAll("#att-roster-tbody tr[data-roll]");

    rows.forEach(row => {
        const roll = row.cells[0]?.textContent.toLowerCase() || "";
        const name = row.cells[1]?.textContent.toLowerCase() || "";
        const room = row.cells[2]?.textContent.toLowerCase() || "";

        row.style.display = (roll.includes(query) || name.includes(query) || room.includes(query)) ? "" : "none";
    });
}

document.querySelector(".studentSearch")?.addEventListener("input", filterAttendanceTable);
const studentSearch = document.querySelector(".studentSearch")

studentSearch.addEventListener("focus", ()=>{
    studentSearch.classList.add("active");
});
studentSearch.addEventListener("blur", ()=>{
    studentSearch.classList.remove("active");
});

// ==================== DYNAMIC STUDENT REGISTRATION ENGINE ====================
const STUDENT_API_BASE = "http://127.0.0.1:8000/api";

let courseBranchesMap = {
    BTECH: ["CSE (AI/ML)", "CSE", "ECE", "Electrical", "Mechanical", "Civil"],
    BCA: ["General", "Data Science", "Cloud Computing", "Cyber Security"],
    MCA: ["General", "AI & ML", "Software Engineering", "Cyber Security"]
};
let courseMaxSemMap = { BTECH: 8, BCA: 6, MCA: 4 };
let studentMetaLoaded = false;

async function initStudentEntryMeta() {
    if (studentMetaLoaded) return;
    const hostelSelect = document.getElementById("hostle");
    if (!hostelSelect) return;

    try {
        const res = await fetch(`${STUDENT_API_BASE}/students/meta`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        if (data.course_branches) courseBranchesMap = data.course_branches;
        if (data.course_max_sem) courseMaxSemMap = data.course_max_sem;

        hostelSelect.innerHTML = `<option value="" disabled selected>Select your hostel</option>` +
            data.hostels.map(h =>
                `<option value="${h.hostel_id}">
                    ${h.name} (${h.allowed_gender}, Yr ${h.allowed_years.join("/")})
                </option>`
            ).join("");

        studentMetaLoaded = true;
    } catch (err) {
        console.error("Failed to load student form metadata:", err);
    }
}

// Dynamic Branch dropdown triggered on Course change
document.getElementById("course")?.addEventListener("change", (e) => {
    const selectedCourse = e.target.value;
    const branchWrapper = document.getElementById("branch-wrapper");
    const branchSelect = document.getElementById("branch");
    if (!branchWrapper || !branchSelect) return;

    const branches = courseBranchesMap[selectedCourse] || [];
    if (branches.length > 0) {
        branchSelect.innerHTML = `<option value="" disabled selected>Select your branch</option>` +
            branches.map(b => `<option value="${b}">${b}</option>`).join("");
        branchWrapper.style.display = "block";
    } else {
        branchWrapper.style.display = "none";
        branchSelect.innerHTML = "";
    }
});

// Helper to display inline validation / API feedback
function setStudentFormMsg(text, isError = false) {
    const msgEl = document.getElementById("student-form-msg");
    if (!msgEl) return;
    msgEl.textContent = text;
    msgEl.style.color = isError ? "var(--error-crimson)" : "var(--accent-green)";
}

// Validate & Submit Student to FastAPI -> MongoDB
document.getElementById("student-entry-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();

    const name = document.getElementById("name").value.trim();
    const phone = document.getElementById("phNo").value.trim();
    const guardianPhone = document.getElementById("parentphNo").value.trim();
    const roomId = document.getElementById("roomNO").value.trim();
    const rollNo = document.getElementById("rollNumber").value.trim();
    const semester = Number(document.getElementById("semester").value);
    const hostelId = document.getElementById("hostle").value;
    const email = document.getElementById("email").value.trim();
    const genderInput = document.querySelector('input[name="gender"]:checked');
    const course = document.getElementById("course").value;
    const branch = document.getElementById("branch")?.value || "";

    // Client-side validation before hitting FastAPI
    if (!/^[A-Za-z.\s]{2,80}$/.test(name)) {
        return setStudentFormMsg("Enter a valid full name (letters only).", true);
    }
    if (!/^[6-9]\d{9}$/.test(phone) || !/^[6-9]\d{9}$/.test(guardianPhone)) {
        return setStudentFormMsg("Both phone numbers must be valid 10-digit Indian numbers.", true);
    }
    if (phone === guardianPhone) {
        return setStudentFormMsg("Student phone and Guardian phone cannot be the same.", true);
    }
    if (!/^[A-Za-z0-9-]{4,20}$/.test(rollNo)) {
        return setStudentFormMsg("Roll number must be alphanumeric (e.g., 2026AI009).", true);
    }
    if (!roomId) {
        return setStudentFormMsg("Enter room number (e.g., SB-102 or 102).", true);
    }
    if (!hostelId) {
        return setStudentFormMsg("Please select a hostel.", true);
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        return setStudentFormMsg("Enter a valid email address.", true);
    }
    if (!genderInput) {
        return setStudentFormMsg("Please select Male or Female.", true);
    }
    if (!course || !branch) {
        return setStudentFormMsg("Please select both Course and Branch.", true);
    }
    const maxSem = courseMaxSemMap[course] || 8;
    if (!Number.isInteger(semester) || semester < 1 || semester > maxSem) {
        return setStudentFormMsg(`Semester for ${course} must be between 1 and ${maxSem}.`, true);
    }

    const submitBtn = e.target.querySelector(".sub_btn");
    submitBtn.disabled = true;
    setStudentFormMsg("Registering student in MongoDB...", false);

    try {
        const res = await fetch(`${STUDENT_API_BASE}/students/register`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                name,
                phone,
                guardian_phone: guardianPhone,
                room_id: roomId,
                roll_no: rollNo,
                semester,
                hostel_id: hostelId,
                email,
                gender: genderInput.value,
                course,
                branch
            })
        });

        const result = await res.json();
        if (!res.ok) {
            const errMsg = Array.isArray(result.detail)
                ? result.detail[0]?.msg || "Validation failed"
                : result.detail || "Registration failed";
            throw new Error(errMsg);
        }

        setStudentFormMsg(result.message, false);
        e.target.reset();
        document.getElementById("branch-wrapper").style.display = "none";

        // Live sync Attendance Roster & Right-Side Stats Bar immediately
        if (typeof loadAttendanceRoster === "function") loadAttendanceRoster();
        if (typeof window.refreshLiveStats === "function") window.refreshLiveStats();
    } catch (err) {
        setStudentFormMsg(err.message, true);
    } finally {
        submitBtn.disabled = false;
    }
});

// Live search helper ready for Warden page (returns newly added students instantly)
async function searchWardenStudents(query = "", hostelId = "ALL") {
    const params = new URLSearchParams({ q: query, hostel_id: hostelId });
    const res = await fetch(`${STUDENT_API_BASE}/warden/students?${params.toString()}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
}

// Load hostel options automatically when Students tab opens or page loads
document.addEventListener("DOMContentLoaded", initStudentEntryMeta);
document.querySelector('[data-target="student-page"]')?.addEventListener("click", initStudentEntryMeta);