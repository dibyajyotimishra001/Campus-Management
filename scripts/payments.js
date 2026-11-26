// ==================== PRODUCTION FEE & PAYMENT ENGINE ====================
const PAYMENT_API_BASE = `${remort_url}/api/payments`;

let payCurrentStatus = "ALL";
let payMetaInitialized = false;
let paySearchTimer = null;

function formatINR(amount) {
    return `₹${Number(amount || 0).toLocaleString("en-IN")}`;
}

async function loadPaymentOverview() {
    const kpiGrid = document.getElementById("pay-kpi-grid");
    const tbody = document.getElementById("pay-roster-tbody");
    const hostelSelect = document.getElementById("pay-hostel-filter");
    const actorSelect = document.getElementById("pay-actor-select");
    const searchInput = document.getElementById("pay-search-input");

    if (!tbody || !kpiGrid) return;

    const params = new URLSearchParams({
        status: payCurrentStatus,
        hostel_id: hostelSelect?.value || "ALL",
        q: searchInput?.value.trim() || ""
    });

    try {
        const res = await fetch(`${PAYMENT_API_BASE}/overview?${params.toString()}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        // Populate Hostel & Staff dropdowns once from MongoDB
        if (!payMetaInitialized) {
            if (hostelSelect && data.hostels) {
                hostelSelect.innerHTML = `<option value="ALL">All Hostels</option>` +
                    data.hostels.map(h => `<option value="${h.hostel_id}">${h.name}</option>`).join("");
            }
            if (actorSelect && data.staff) {
                actorSelect.innerHTML = data.staff.length
                    ? data.staff.map(st =>
                        `<option value="${st.staff_id}" data-role="${st.role}">
                            ${st.staff_id} - ${st.name} (${st.role})
                        </option>`
                    ).join("")
                    : `<option value="ADMIN-01" data-role="super_admin">ADMIN-01 (super_admin)</option>`;
            }
            payMetaInitialized = true;
        }

        renderPaymentKPIs(data.kpis);
        renderPaymentRoster(data.students);
    } catch (err) {
        console.error("Payment overview fetch error:", err);
        tbody.innerHTML = `<tr><td colspan="7" style="color: var(--error-crimson);">Failed to load fee data from FastAPI.</td></tr>`;
    }
}

function renderPaymentKPIs(k) {
    const kpiGrid = document.getElementById("pay-kpi-grid");
    if (!kpiGrid || !k) return;

    const defaulters = (k.unpaid_count || 0) + (k.partial_count || 0);

    kpiGrid.innerHTML = `
        <div class="pay-kpi-card" style="--kpi-accent: var(--accent-blue);">
            <span class="pay-kpi-label">Total Expected</span>
            <div class="pay-kpi-value">${formatINR(k.total_expected)}</div>
            <span class="pay-kpi-sub">${k.total_students} active students</span>
        </div>
        <div class="pay-kpi-card" style="--kpi-accent: var(--accent-green);">
            <span class="pay-kpi-label">Total Collected</span>
            <div class="pay-kpi-value">${formatINR(k.total_collected)}</div>
            <div class="pay-progress-track">
                <div class="pay-progress-fill" style="width: ${k.collection_pct}%;"></div>
            </div>
            <span class="pay-kpi-sub">${k.collection_pct}% collected (${k.paid_count} fully paid)</span>
        </div>
        <div class="pay-kpi-card" style="--kpi-accent: var(--error-crimson);">
            <span class="pay-kpi-label">Pending Dues</span>
            <div class="pay-kpi-value">${formatINR(k.total_due)}</div>
            <span class="pay-kpi-sub">Outstanding hostel & tuition balance</span>
        </div>
        <div class="pay-kpi-card" style="--kpi-accent: var(--warning-amber);">
            <span class="pay-kpi-label">Pending Students</span>
            <div class="pay-kpi-value">${defaulters}</div>
            <span class="pay-kpi-sub">${k.unpaid_count} Unpaid • ${k.partial_count} Partial</span>
        </div>
    `;
}

function renderPaymentRoster(students) {
    const tbody = document.getElementById("pay-roster-tbody");
    if (!tbody) return;

    if (!students || students.length === 0) {
        tbody.innerHTML = `<tr><td colspan="7">No matching student fee records found.</td></tr>`;
        return;
    }

    tbody.innerHTML = students.map(s => {
        const fee = s.fee_details || { total_fee: 0, paid_fee: 0, due_fee: 0, status: "unpaid" };
        const isCleared = fee.due_fee <= 0;
        const safeName = s.name.replace(/'/g, "\\'");

        return `
            <tr>
                <td><strong>${s.roll_no}</strong></td>
                <td>
                    ${s.name}
                    <span class="pay-student-meta">${s.course || ""} ${s.branch || ""} • ${s.room_id || "No Room"}</span>
                </td>
                <td>${formatINR(fee.total_fee)}</td>
                <td>${formatINR(fee.paid_fee)}</td>
                <td class="${isCleared ? "pay-money-clear" : "pay-money-due"}">${formatINR(fee.due_fee)}</td>
                <td><span class="pay-badge ${fee.status}">${fee.status}</span></td>
                <td>
                    <div class="pay-action-group">
                        <button type="button" class="pay-action-btn collect"
                            ${isCleared ? "disabled" : ""}
                            onclick="openRecordPaymentModal('${s.roll_no}', '${safeName}', ${fee.due_fee})">
                            ${isCleared ? "Cleared" : "+ Pay"}
                        </button>
                        <button type="button" class="pay-action-btn history"
                            onclick="openPaymentLedgerModal('${s.roll_no}')">
                            Ledger
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join("");
}

function openRecordPaymentModal(rollNo, studentName, dueFee) {
    document.getElementById("pay-roll-no").value = rollNo;
    document.getElementById("pay-max-due").value = dueFee;
    document.getElementById("pay-modal-student-info").textContent =
        `${studentName} (${rollNo}) • Pending Due: ${formatINR(dueFee)}`;

    const amountInput = document.getElementById("pay-amount");
    amountInput.value = "";
    amountInput.max = dueFee;

    document.getElementById("pay-mode").value = "UPI";
    document.getElementById("pay-ref-id").value = "";
    document.getElementById("pay-remarks").value = "";
    document.getElementById("pay-form-msg").textContent = "";
    togglePaymentRefRequirement();

    document.getElementById("pay-record-modal").classList.add("open");
}

function togglePaymentRefRequirement() {
    const mode = document.getElementById("pay-mode")?.value;
    const refGroup = document.getElementById("pay-ref-group");
    const refInput = document.getElementById("pay-ref-id");
    if (!refGroup || !refInput) return;

    if (mode === "Cash") {
        refGroup.style.display = "none";
        refInput.required = false;
        refInput.value = "";
    } else {
        refGroup.style.display = "flex";
        refInput.required = true;
    }
}

document.getElementById("pay-mode")?.addEventListener("change", togglePaymentRefRequirement);

document.getElementById("pay-quick-full")?.addEventListener("click", () => {
    const maxDue = Number(document.getElementById("pay-max-due").value || 0);
    document.getElementById("pay-amount").value = maxDue;
});

document.getElementById("pay-quick-half")?.addEventListener("click", () => {
    const maxDue = Number(document.getElementById("pay-max-due").value || 0);
    document.getElementById("pay-amount").value = Math.ceil(maxDue / 2);
});

document.getElementById("pay-record-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();

    const rollNo = document.getElementById("pay-roll-no").value;
    const maxDue = Number(document.getElementById("pay-max-due").value);
    const amount = Number(document.getElementById("pay-amount").value);
    const paymentMode = document.getElementById("pay-mode").value;
    const referenceId = document.getElementById("pay-ref-id").value.trim();
    const remarks = document.getElementById("pay-remarks").value.trim();
    const actorSelect = document.getElementById("pay-actor-select");
    const selectedActor = actorSelect?.options[actorSelect.selectedIndex];

    const msgEl = document.getElementById("pay-form-msg");
    const submitBtn = document.getElementById("pay-submit-btn");

    const setMsg = (txt, isErr = false) => {
        msgEl.textContent = txt;
        msgEl.style.color = isErr ? "var(--error-crimson)" : "var(--accent-green)";
    };

    if (!Number.isInteger(amount) || amount <= 0) {
        return setMsg("Enter a valid positive amount in whole rupees.", true);
    }
    if (amount > maxDue) {
        return setMsg(`Amount exceeds pending due (${formatINR(maxDue)}).`, true);
    }
    if (paymentMode !== "Cash" && !referenceId) {
        return setMsg(`Reference / UTR ID is mandatory for ${paymentMode}.`, true);
    }

    submitBtn.disabled = true;
    setMsg("Recording atomic transaction in MongoDB...", false);

    try {
        const res = await fetch(`${PAYMENT_API_BASE}/record`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                student_roll_no: rollNo,
                amount,
                payment_mode: paymentMode,
                reference_id: paymentMode === "Cash" ? null : referenceId,
                recorded_by: selectedActor?.value || "ADMIN-01",
                actor_role: selectedActor?.dataset?.role || "super_admin",
                remarks
            })
        });

        const result = await res.json();
        if (!res.ok) {
            const errText = Array.isArray(result.detail)
                ? result.detail[0]?.msg || "Validation failed"
                : result.detail || "Payment recording failed";
            throw new Error(errText);
        }

        setMsg(result.message, false);

        // Refresh Payment KPIs/Table + Right-side Live Recent Activities immediately
        await loadPaymentOverview();
        if (typeof window.refreshLiveStats === "function") {
            window.refreshLiveStats();
        }

        setTimeout(() => {
            document.getElementById("pay-record-modal").classList.remove("open");
        }, 700);
    } catch (err) {
        setMsg(err.message, true);
    } finally {
        submitBtn.disabled = false;
    }
});

async function openPaymentLedgerModal(rollNo) {
    const modal = document.getElementById("pay-ledger-modal");
    const headerInfo = document.getElementById("pay-ledger-header-info");
    const summaryBox = document.getElementById("pay-ledger-summary");
    const listBox = document.getElementById("pay-ledger-list");

    headerInfo.textContent = `Fetching ledger for ${rollNo}...`;
    summaryBox.innerHTML = "";
    listBox.innerHTML = `<p style="font-size: 13px; color: var(--text-secondary);">Loading transactions...</p>`;
    modal.classList.add("open");

    try {
        const res = await fetch(`${PAYMENT_API_BASE}/history/${encodeURIComponent(rollNo)}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const { student, transactions } = await res.json();
        const fee = student.fee_details || { total_fee: 0, paid_fee: 0, due_fee: 0, status: "unpaid" };

        headerInfo.textContent = `${student.name} (${student.roll_no}) • ${student.room_id || "No Room"} • ${student.phone || ""}`;

        summaryBox.innerHTML = `
            <div class="pay-ledger-box">
                <span>Total Fee</span>
                <strong>${formatINR(fee.total_fee)}</strong>
            </div>
            <div class="pay-ledger-box">
                <span>Paid</span>
                <strong style="color: var(--accent-green);">${formatINR(fee.paid_fee)}</strong>
            </div>
            <div class="pay-ledger-box">
                <span>Pending Due</span>
                <strong style="color: ${fee.due_fee > 0 ? "var(--error-crimson)" : "var(--accent-green)"};">
                    ${formatINR(fee.due_fee)}
                </strong>
            </div>
        `;

        if (!transactions || transactions.length === 0) {
            listBox.innerHTML = `<p style="font-size: 13px; color: var(--text-secondary); text-align: center; padding: 16px;">No payments recorded yet for this student.</p>`;
            return;
        }

        listBox.innerHTML = transactions.map(tx => `
            <div class="pay-receipt-item">
                <div class="pay-receipt-left">
                    <p>${tx.receipt_no} • ${tx.payment_mode}</p>
                    <div class="pay-receipt-meta">
                        ${new Date(tx.paid_at).toLocaleString("en-IN")}
                        ${tx.reference_id ? ` | Ref: ${tx.reference_id}` : ""}
                        ${tx.remarks ? ` | ${tx.remarks}` : ""}
                        <br>Recorded by: ${tx.recorded_by} | Balance after: ${formatINR(tx.balance_after)}
                    </div>
                </div>
                <div class="pay-receipt-right">
                    <span class="pay-receipt-amt">+ ${formatINR(tx.amount)}</span>
                    <button type="button" class="pay-chip-btn"
                        onclick='printFeeReceipt(${JSON.stringify(tx).replace(/'/g, "&#39;")})'>
                        Print Receipt
                    </button>
                </div>
            </div>
        `).join("");
    } catch (err) {
        listBox.innerHTML = `<p style="color: var(--error-crimson); font-size: 13px;">Failed to load ledger: ${err.message}</p>`;
    }
}

function printFeeReceipt(tx) {
    const win = window.open("", "_blank", "width=620,height=680");
    if (!win) return;
    win.document.write(`
        <html>
        <head>
            <title>Receipt - ${tx.receipt_no}</title>
            <style>
                body { font-family: Arial, sans-serif; padding: 28px; color: #111; }
                .box { border: 2px solid #111; border-radius: 8px; padding: 22px; }
                h2 { margin: 0 0 4px; }
                .meta { color: #555; font-size: 13px; margin-bottom: 18px; }
                table { width: 100%; border-collapse: collapse; margin-top: 12px; }
                td { padding: 9px 6px; border-bottom: 1px solid #ddd; font-size: 14px; }
                td:first-child { font-weight: bold; width: 42%; color: #333; }
                .amt { font-size: 20px; font-weight: bold; color: #0d9488; }
            </style>
        </head>
        <body>
            <div class="box">
                <h2>Campus Management System</h2>
                <div class="meta">Official Fee Payment Receipt • ${tx.receipt_no}</div>
                <table>
                    <tr><td>Student Name</td><td>${tx.student_name}</td></tr>
                    <tr><td>Roll Number</td><td>${tx.student_roll_no}</td></tr>
                    <tr><td>Room / Hostel</td><td>${tx.room_id || "-"} (${tx.hostel_id || "-"})</td></tr>
                    <tr><td>Date & Time (IST)</td><td>${new Date(tx.paid_at).toLocaleString("en-IN")}</td></tr>
                    <tr><td>Payment Mode</td><td>${tx.payment_mode}</td></tr>
                    <tr><td>Reference / UTR ID</td><td>${tx.reference_id || "N/A (Cash)"}</td></tr>
                    <tr><td>Amount Paid</td><td class="amt">${formatINR(tx.amount)}</td></tr>
                    <tr><td>Remaining Balance</td><td>${formatINR(tx.balance_after)}</td></tr>
                    <tr><td>Recorded By</td><td>${tx.recorded_by}</td></tr>
                    <tr><td>Remarks</td><td>${tx.remarks || "-"}</td></tr>
                </table>
            </div>
            <script>window.onload = () => { window.print(); };<\/script>
        </body>
        </html>
    `);
    win.document.close();
}

// Close modal buttons & backdrop click
document.querySelectorAll("[data-close-modal]").forEach(btn => {
    btn.addEventListener("click", () => {
        document.getElementById(btn.dataset.closeModal)?.classList.remove("open");
    });
});

document.querySelectorAll(".pay-modal-overlay").forEach(overlay => {
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) overlay.classList.remove("open");
    });
});

// Status Filter Pills
document.querySelectorAll("#pay-status-filters .pay-filter-pill").forEach(btn => {
    btn.addEventListener("click", () => {
        document.querySelectorAll("#pay-status-filters .pay-filter-pill").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        payCurrentStatus = btn.dataset.status;
        loadPaymentOverview();
    });
});

// Debounced live search & hostel filter
document.getElementById("pay-search-input")?.addEventListener("input", () => {
    clearTimeout(paySearchTimer);
    paySearchTimer = setTimeout(loadPaymentOverview, 250);
});

document.getElementById("pay-hostel-filter")?.addEventListener("change", loadPaymentOverview);
document.getElementById("pay-refresh-btn")?.addEventListener("click", loadPaymentOverview);

// Auto-load whenever Fee & Payment tab is clicked
document.querySelector('[data-target="payment-page"]')?.addEventListener("click", loadPaymentOverview);