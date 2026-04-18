// 1. Search Bar Logic
function filterTable() {
    const input = document.getElementById("locationSearch");
    const filter = input.value.toUpperCase();
    const rows = document.querySelectorAll("tbody tr");

    rows.forEach(row => {
        const locationCell = row.getElementsByTagName("td")[0];
        if (locationCell) {
            const text = locationCell.textContent || locationCell.innerText;
            row.style.display = text.toUpperCase().includes(filter) ? "" : "none";
        }
    });
}

// 2. Relative Time Logic
function updateTime() {
    const elements = document.querySelectorAll('.time-ago');
    const now = new Date();

    elements.forEach(el => {
        const timestampStr = el.getAttribute('data-timestamp');
        if (!timestampStr) return;

        const recordTime = new Date(timestampStr);
        const diffInSeconds = Math.floor((now - recordTime) / 1000);

        let timeText = "";
        if (diffInSeconds < 60) timeText = "Just now";
        else if (diffInSeconds < 3600) timeText = Math.floor(diffInSeconds / 60) + "m ago";
        else if (diffInSeconds < 86400) timeText = Math.floor(diffInSeconds / 3600) + "h ago";
        else timeText = Math.floor(diffInSeconds / 86400) + "d ago";

        el.innerText = timeText;
    });
}

// Initial run and every minute update
document.addEventListener('DOMContentLoaded', () => {
    updateTime();
    setInterval(updateTime, 60000);
});