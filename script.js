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