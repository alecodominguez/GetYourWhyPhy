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
        const timestamp = el.getAttribute('data-timestamp');
        // checks the timestamp is parsed correctly
        const recordDate = new Date(timestamp);
        const diff = Math.floor((now - recordDate) / 1000);

        if (isNaN(diff)) return; // is skipped if date is invalid

        if (diff < 60) el.innerText = "Just now";
        else if (diff < 3600) el.innerText = Math.floor(diff / 60) + "m ago";
        else if (diff < 86400) el.innerText = Math.floor(diff / 3600) + "h ago";
        else el.innerText = Math.floor(diff / 86400) + "d ago";
    });
}

// 1st run and every minute update
document.addEventListener('DOMContentLoaded', () => {
    updateTime();
    setInterval(updateTime, 60000);
});