/*
 * lite.js - WhyPhy Web Lite
 *
 * Runs entirely in the browser: no install, no OS security warning, but no
 * access to anything outside the browser sandbox. That means no SSID/BSSID
 * (the user picks a building) and no raw ICMP: latency, jitter and packet
 * loss are approximated from repeated fetch() round-trips against /ping.
 *
 * Scoring mirrors WhyPhy.py's WEIGHTS/THRESHOLDS/score_metric so a Lite score
 * and a native score mean the same thing. If those constants change in
 * WhyPhy.py, update them here too. This is plain JS rather than compiled
 * WebAssembly on purpose: the math is a dozen lines of arithmetic, and a
 * Rust/Emscripten build pipeline would add complexity for no benefit.
 */

const WEIGHTS = {download: 35, upload: 20, latency: 30, jitter: 10, packet_loss: 5};
const THRESHOLDS = {
    download: [0, 200],
    upload: [0, 100],
    latency: [500, 5],
    jitter: [100, 0],
    packet_loss: [100, 0],
};

function clamp(val, lo, hi) {
    return Math.max(lo, Math.min(hi, val));
}

function scoreMetric(value, [lo, hi]) {
    if (hi === lo) return 0;
    const ratio = (value - lo) / (hi - lo);
    return Math.round(clamp(ratio * 100, 0, 100) * 10) / 10;
}

function setProgress(text) {
    document.getElementById("progress").innerText = text;
}

async function measureLatencyAndJitter(samples = 8) {
    const rtts = [];
    let failures = 0;
    for (let i = 0; i < samples; i++) {
        const start = performance.now();
        try {
            await fetch("/ping?_=" + Date.now(), {cache: "no-store"});
            rtts.push(performance.now() - start);
        } catch (e) {
            failures++;
        }
        setProgress(`Measuring latency... (${i + 1}/${samples})`);
    }
    const packetLoss = (failures / samples) * 100;
    if (rtts.length < 2) {
        return {latency: rtts[0] || 0, jitter: 0, packetLoss};
    }
    const mean = rtts.reduce((a, b) => a + b, 0) / rtts.length;
    const variance = rtts.reduce((a, b) => a + (b - mean) ** 2, 0) / rtts.length;
    return {
        latency: Math.round(mean * 10) / 10,
        jitter: Math.round(Math.sqrt(variance) * 10) / 10,
        packetLoss,
    };
}

async function measureDownload() {
    setProgress("Measuring download speed...");
    const start = performance.now();
    const resp = await fetch("/speedtest-download?_=" + Date.now(), {cache: "no-store"});
    const blob = await resp.blob();
    const seconds = (performance.now() - start) / 1000;
    return Math.round(((blob.size * 8) / (seconds * 1_000_000)) * 100) / 100;
}

async function measureUpload() {
    setProgress("Measuring upload speed...");
    const size = 2 * 1024 * 1024;
    const data = new Uint8Array(size);
    // crypto.getRandomValues is capped at 65536 bytes per call.
    const CHUNK = 65536;
    for (let offset = 0; offset < size; offset += CHUNK) {
        crypto.getRandomValues(data.subarray(offset, Math.min(offset + CHUNK, size)));
    }
    const start = performance.now();
    await fetch("/speedtest-upload", {method: "POST", body: data, cache: "no-store"});
    const seconds = (performance.now() - start) / 1000;
    return Math.round(((size * 8) / (seconds * 1_000_000)) * 100) / 100;
}

async function runLiteTest() {
    const building = document.getElementById("building").value;
    if (!building) {
        alert("Pick your building first so the report can be counted toward the leaderboard.");
        return;
    }

    const btn = document.getElementById("runBtn");
    btn.disabled = true;
    document.getElementById("results").style.display = "none";

    try {
        const download = await measureDownload();
        const upload = await measureUpload();
        const {latency, jitter, packetLoss} = await measureLatencyAndJitter();

        const raw = {download, upload, latency, jitter, packet_loss: packetLoss};
        let total = 0;
        for (const metric in WEIGHTS) {
            total += scoreMetric(raw[metric], THRESHOLDS[metric]) * WEIGHTS[metric] / 100;
        }
        total = Math.round(total * 10) / 10;

        document.getElementById("scoreValue").innerText = total;
        document.getElementById("dlResult").innerText = `${download} Mbps`;
        document.getElementById("ulResult").innerText = `${upload} Mbps`;
        document.getElementById("latResult").innerText = `${latency} ms`;
        document.getElementById("jitterResult").innerText = `${jitter} ms (approx.)`;
        document.getElementById("results").style.display = "block";

        setProgress("");
        document.getElementById("submitStatus").innerText = "Submitting to the leaderboard...";

        const resp = await fetch("/log-wifi", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                location: building,
                download, upload, latency,
                jitter, packet_loss: packetLoss,
                score: total,
                source: "web_lite",
            }),
        });

        if (resp.ok) {
            document.getElementById("submitStatus").innerText = "Report submitted - thanks for contributing!";
        } else {
            const err = await resp.json().catch(() => ({}));
            document.getElementById("submitStatus").innerText =
                `Couldn't submit: ${err.detail || resp.statusText}`;
        }
    } catch (e) {
        setProgress("");
        alert("Something went wrong running the test: " + e.message);
    } finally {
        btn.disabled = false;
    }
}
