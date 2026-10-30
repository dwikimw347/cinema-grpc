"use strict";

let SCH = [], curSch = "SCH001", seats = {}, selSeat = null, sse = null;
let pendingBooking = null, bookingInFlight = false, streamReady = false, toolBusy = false;
const artwork = {
  SCH001: "/static/images/poster_nusantara.jpg",
  SCH002: "/static/images/poster_misteri.jpg",
  SCH003: "/static/images/poster_senja.jpg",
};
const $ = id => document.getElementById(id);

function escapeText(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);
}

function sessionTime(schedule) {
  const date = new Date(schedule.show_time);
  if (Number.isNaN(date.getTime())) return {time: schedule.show_time, day: ""};
  return {
    time: new Intl.DateTimeFormat("id-ID", {hour: "2-digit", minute: "2-digit", timeZone: "Asia/Jakarta", hourCycle: "h23"}).format(date).replace(".", ":") + " WIB",
    day: new Intl.DateTimeFormat("id-ID", {day: "numeric", month: "long", year: "numeric", timeZone: "Asia/Jakarta"}).format(date),
  };
}

function log(message, type = "i") {
  const line = document.createElement("div");
  line.className = `log-line ${{o: "success", w: "warning", e: "error"}[type] || ""}`;
  const time = new Date().toLocaleTimeString("id-ID", {hour12: false});
  line.innerHTML = `<span class="log-time">${escapeText(time)}</span><span class="log-message">${escapeText(message)}</span>`;
  $("con").appendChild(line);
  while ($("con").children.length > 200) $("con").firstElementChild.remove();
  $("con").scrollTop = $("con").scrollHeight;
}

function clrLog() {
  $("con").innerHTML = "";
  log("Log dibersihkan.");
}

function toast(title, description, type = "ok") {
  const item = document.createElement("div");
  item.className = `toast ${type === "err" ? "error" : type === "warn" ? "warning" : ""}`;
  item.innerHTML = `<strong>${escapeText(title)}</strong><span>${escapeText(description)}</span>`;
  $("toastBox").appendChild(item);
  while ($("toastBox").children.length > 3) $("toastBox").firstElementChild.remove();
  setTimeout(() => item.remove(), 6500);
}

function uuid() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, char => {
    const n = Math.random() * 16 | 0;
    return (char === "x" ? n : (n & 3 | 8)).toString(16);
  });
}

function newUUID() {
  if (pendingBooking) {
    toast("Hasil belum diketahui", "Periksa pemesanan yang sama sebelum membuat ID baru.", "warn");
    return;
  }
  $("inpUUID").value = uuid();
  log(`ID permintaan baru: ${$("inpUUID").value}`);
}

function openTools() { $("systemTools").open = true; }

function connectionState(state, text) {
  $("connection").dataset.state = state;
  $("streamLabel").textContent = text;
}

function updateBookingButton() {
  const hasSelection = !!selSeat && seats[selSeat]?.status === "AVAILABLE" && streamReady;
  $("btnBook").disabled = bookingInFlight || (!pendingBooking && !hasSelection);
  $("btnBook").textContent = bookingInFlight ? "Memproses pemesanan..." : pendingBooking ? "Periksa / ulangi pemesanan" : "Pesan kursi";
  $("inpName").disabled = !!pendingBooking;
  $("bookingHelp").textContent = pendingBooking
    ? "ID dan data yang sama dipertahankan sampai hasilnya pasti."
    : hasSelection ? `Kursi ${selSeat} dipilih. Siap dipesan.` : "Pilih kursi untuk melanjutkan.";
}

async function init() {
  if (!pendingBooking) newUUID();
  $("movieStrip").setAttribute("aria-busy", "true");
  try {
    log("GetSchedules: mengambil jadwal film.");
    const response = await fetch("/api/schedules");
    const result = await response.json();
    if (!result.success) throw new Error(result.error || "Server belum dapat dihubungi.");
    SCH = result.schedules;
    $("scheduleCount").textContent = `${SCH.length} film`;
    if (!SCH.length) {
      $("movieStrip").innerHTML = '<p class="empty-state">Belum ada jadwal tayang.</p>';
      $("selectedTitle").textContent = "Belum ada jadwal";
      $("seatNotice").textContent = "Coba lagi setelah jadwal ditambahkan.";
      connectionState("error", "Jadwal belum tersedia");
      return;
    }
    const days = [...new Set(SCH.map(schedule => sessionTime(schedule).day))];
    $("programmeDate").textContent = days.length === 1 ? days[0] : "Pilih tanggal dan jam pada jadwal film.";
    renderStrip();
    pickMovie(SCH.some(s => s.schedule_id === curSch) ? curSch : SCH[0].schedule_id);
    log(`${SCH.length} jadwal berhasil dimuat.`, "o");
  } catch (error) {
    $("scheduleCount").textContent = "Tidak terhubung";
    $("movieStrip").innerHTML = '<p class="empty-state">Jadwal belum dapat dimuat. Pastikan server aktif.</p><button class="secondary-button retry-load" onclick="init()">Coba lagi</button>';
    $("selectedTitle").textContent = "Studionya belum terhubung";
    $("selectedMeta").textContent = "Jadwal dan kursi akan tampil setelah koneksi pulih.";
    $("seatNotice").textContent = "Periksa server, lalu pilih Coba lagi.";
    connectionState("error", "Koneksi terputus");
    log(error.message, "e");
  } finally {
    $("movieStrip").setAttribute("aria-busy", "false");
  }
}

function renderStrip() {
  $("movieStrip").innerHTML = "";
  SCH.forEach(schedule => {
    const button = document.createElement("button");
    const selected = schedule.schedule_id === curSch;
    const session = sessionTime(schedule);
    button.type = "button";
    button.className = "movie-option";
    button.id = `mc-${schedule.schedule_id}`;
    button.setAttribute("aria-pressed", String(selected));
    const image = artwork[schedule.schedule_id]
      ? `<img class="poster" src="${artwork[schedule.schedule_id]}" alt="" width="52" height="72">`
      : '<span class="poster-fallback" aria-hidden="true">C</span>';
    button.innerHTML = `${image}<span class="movie-copy"><span class="movie-title">${escapeText(schedule.movie_title)}</span><span class="movie-meta">${escapeText(session.time)} / ${escapeText(schedule.studio)}</span><span class="movie-state">${selected ? "✓ Dipilih" : "Lihat kursi"}</span></span>`;
    button.onclick = () => pickMovie(schedule.schedule_id);
    $("movieStrip").appendChild(button);
  });
}

function pickMovie(id) {
  if (pendingBooking) {
    toast("Pemesanan belum selesai", "Pastikan hasil pemesanan sebelum mengganti jadwal.", "warn");
    return;
  }
  const schedule = SCH.find(item => item.schedule_id === id);
  if (!schedule) return;
  const retainFocus = document.activeElement?.id === `mc-${id}`;
  curSch = id;
  selSeat = null;
  seats = {};
  streamReady = false;
  $("bookingResult").hidden = true;
  $("toolResult").hidden = true;
  renderStrip();
  if (retainFocus) $(`mc-${id}`)?.focus();
  const session = sessionTime(schedule);
  $("selectedTitle").textContent = schedule.movie_title;
  $("selectedMeta").textContent = `${session.day} / ${session.time}`;
  $("studioLabel").textContent = schedule.studio;
  $("tkMovie").textContent = schedule.movie_title;
  $("tkStudio").textContent = schedule.studio;
  $("tkTime").textContent = session.time;
  $("tkDate").textContent = session.day;
  $("tkSeat").textContent = "-";
  $("ticketHint").innerHTML = "Belum<br>dipilih";
  $("ticketHint").dataset.state = "empty";
  $("seatsArea").innerHTML = "";
  $("cntA").textContent = "-";
  $("cntB").textContent = "-";
  updateBookingButton();
  startSSE(id);
}

function startSSE(id) {
  if (sse) sse.close();
  streamReady = false;
  connectionState("connecting", "Menghubungkan");
  $("seatNotice").textContent = "Memuat status kursi...";
  $("seatsArea").setAttribute("aria-busy", "true");
  log(`StreamSeatStatus: mengikuti jadwal ${id}.`);
  const source = new EventSource(`/api/stream?schedule_id=${encodeURIComponent(id)}`);
  sse = source;
  source.onmessage = event => {
    if (source !== sse || curSch !== id) return;
    try {
      const data = JSON.parse(event.data);
      if (data.schedule_id !== curSch) return;
      if (data.event_type === "SNAPSHOT") seats = {};
      data.seats.forEach(seat => {
        seats[seat.seat_number] = seat;
        if (seat.seat_number === selSeat && seat.status === "BOOKED" && !pendingBooking) {
          selSeat = null;
          $("tkSeat").textContent = "-";
          $("ticketHint").innerHTML = "Pilih kursi<br>lain";
          $("ticketHint").dataset.state = "empty";
          toast("Kursi sudah dipesan", "Pilih kursi lain yang masih tersedia.", "warn");
        }
      });
      streamReady = true;
      connectionState("connected", "Pembaruan langsung");
      $("seatNotice").textContent = "";
      $("seatsArea").setAttribute("aria-busy", "false");
      log(data.event_type === "SNAPSHOT" ? `Snapshot: ${data.seats.length} kursi diterima.` : `Update: ${data.seats.map(s => `${s.seat_number} ${s.status}`).join(", ")}.`, "o");
      renderSeats();
      updateBookingButton();
    } catch (error) { log(`Pesan streaming tidak dapat dibaca: ${error.message}`, "e"); }
  };
  const disconnected = () => {
    if (source !== sse) return;
    streamReady = false;
    connectionState("error", "Menghubungkan ulang");
    $("seatNotice").textContent = "Koneksi terputus. Menunggu status kursi terbaru...";
    $("seatsArea").setAttribute("aria-busy", "true");
    renderSeats();
    updateBookingButton();
  };
  source.onerror = disconnected;
  source.addEventListener("stream_error", () => {
    disconnected();
    source.close();
    setTimeout(() => { if (sse === source && curSch === id) startSSE(id); }, 3000);
  });
}

function renderSeats() {
  const focused = document.activeElement?.dataset?.seat;
  const container = $("seatsArea");
  container.innerHTML = "";
  let available = 0, booked = 0;
  for (const letter of ["A", "B", "C", "D"]) {
    const row = document.createElement("div");
    row.className = "seat-row";
    row.innerHTML = `<span class="row-label" aria-hidden="true">${letter}</span>`;
    for (let column = 1; column <= 5; column++) {
      if (column === 4) {
        const aisle = document.createElement("span");
        aisle.className = "aisle";
        aisle.setAttribute("aria-hidden", "true");
        row.appendChild(aisle);
      }
      const number = `${letter}${column}`, seat = seats[number];
      if (!seat) continue;
      const isBooked = seat.status === "BOOKED", selected = selSeat === number && !isBooked;
      isBooked ? booked++ : available++;
      const button = document.createElement("button");
      button.type = "button";
      button.dataset.seat = number;
      button.className = `seat ${isBooked ? "booked" : "available"}${selected ? " selected" : ""}${!streamReady ? " unavailable" : ""}`;
      button.disabled = isBooked || !streamReady || !!pendingBooking;
      button.setAttribute("aria-label", `Kursi ${number}, ${isBooked ? "sudah dipesan" : selected ? "dipilih" : "tersedia"}`);
      button.setAttribute("aria-pressed", String(selected));
      button.innerHTML = `${selected ? '<span class="seat-check" aria-hidden="true">✓</span>' : isBooked ? '<span aria-hidden="true">×</span>' : ""}${number}`;
      button.onclick = () => {
        if (pendingBooking || !streamReady) return;
        selSeat = number;
        $("tkSeat").textContent = number;
        $("ticketHint").innerHTML = "Kursi<br>pilihanmu";
        $("ticketHint").dataset.state = "chosen";
        $("bookingResult").hidden = true;
        renderSeats();
        document.querySelector(`[data-seat="${number}"]`)?.focus();
        updateBookingButton();
        log(`Kursi ${number} dipilih.`);
      };
      row.appendChild(button);
    }
    container.appendChild(row);
  }
  $("cntA").textContent = available;
  $("cntB").textContent = booked;
  $("seatInstruction").textContent = selSeat ? `Kursi ${selSeat} pilihanmu. Lengkapi nama untuk memesan.` : available ? "Pilih satu kursi yang tersedia." : streamReady ? "Semua kursi sudah dipesan. Pilih jadwal lain." : "Menunggu status kursi terbaru.";
  if (focused) document.querySelector(`[data-seat="${focused}"]`)?.focus();
}

function bookingFeedback(title, message, type = "success") {
  const result = $("bookingResult");
  result.hidden = false;
  result.className = `booking-result ${type}`;
  result.innerHTML = `<strong>${escapeText(title)}</strong><span>${escapeText(message)}</span>`;
}

async function doBook() {
  if (bookingInFlight || (!selSeat && !pendingBooking)) return;
  bookingInFlight = true;
  // Payload tetap disimpan sampai respons final; koneksi putus bukan penolakan.
  if (!pendingBooking) pendingBooking = {
    schedule_id: curSch, seat_id: `${curSch}-${selSeat}`,
    customer_name: $("inpName").value.trim() || "User", request_id: $("inpUUID").value,
  };
  const operation = pendingBooking;
  updateBookingButton();
  log(`BookSeat: ${operation.seat_id}, ID ${operation.request_id}.`);
  try {
    const response = await fetch("/api/book", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({...operation, timeout: 3, max_retries: 3})});
    const result = await response.json();
    if (result.success) {
      const number = operation.seat_id.split("-").pop();
      bookingFeedback("Reservasimu berhasil", `Kursi ${number} atas nama ${operation.customer_name}. Booking ID: ${result.booking_id}`);
      toast("Kursimu sudah dipesan", `${number} / ${operation.customer_name}`);
      selSeat = null;
      $("tkSeat").textContent = number;
      $("ticketHint").innerHTML = "Berhasil<br>dipesan";
      $("ticketHint").dataset.state = "chosen";
      log(`CONFIRMED: booking ${result.booking_id}.`, "o");
    } else {
      const unknown = result.status_unknown || (!result.booking_status && !result.final);
      const title = unknown ? "Hasil belum diketahui" : "Pemesanan ditolak";
      bookingFeedback(title, unknown ? "Server mungkin sudah menyimpan pemesanan. Pilih Periksa / ulangi pemesanan untuk memastikan hasilnya." : result.message || result.error || "Pilih kursi lain atau periksa data pemesanan.", unknown ? "warning" : "error");
      log(result.message || result.error || title, "e");
      toast(title, result.message || result.error || "Periksa pemesanan yang sama.", unknown ? "warn" : "err");
    }
    if (result.success || result.booking_status === "REJECTED" || result.final) {
      pendingBooking = null;
      newUUID();
    }
  } catch (error) {
    bookingFeedback("Hasil belum diketahui", "Koneksi terputus. Gunakan tombol Periksa / ulangi pemesanan. ID dan data yang sama akan dikirim kembali.", "warning");
    toast("Koneksi terputus", "Periksa kembali hasil pemesanan yang sama.", "warn");
    log(error.message, "e");
  } finally {
    bookingInFlight = false;
    renderSeats();
    updateBookingButton();
  }
}

async function runTool(buttonId, title, action) {
  openTools();
  if (toolBusy || pendingBooking) {
    toast("Selesaikan proses sebelumnya", "Tunggu pengujian atau pemesanan sebelumnya selesai.", "warn");
    return;
  }
  toolBusy = true;
  const label = $(buttonId).textContent;
  for (const id of ["btnIdemp", "btnRace", "btnBench"]) $(id).disabled = true;
  $(buttonId).textContent = "Sedang menguji...";
  $("toolResult").hidden = false;
  $("toolResult").className = "tool-result";
  $("toolResult").textContent = `${title}. Menunggu hasil server...`;
  try { await action(); }
  catch (error) {
    $("toolResult").className = "tool-result error";
    $("toolResult").textContent = `Pengujian belum berhasil: ${error.message}`;
    log(error.message, "e");
  } finally {
    toolBusy = false;
    $(buttonId).textContent = label;
    for (const id of ["btnIdemp", "btnRace", "btnBench"]) $(id).disabled = false;
  }
}

function availableSeat() {
  const seat = streamReady && Object.values(seats).find(item => item.status === "AVAILABLE");
  if (!seat) throw new Error("Pilih jadwal terhubung dengan kursi yang masih tersedia.");
  return seat;
}

async function postJSON(path, body) {
  const response = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, ...(body ? {body: JSON.stringify(body)} : {})});
  return response.json();
}

async function testIdemp() {
  return runTool("btnIdemp", "Menguji deduplikasi", async () => {
    const seat = availableSeat();
    const request = {schedule_id: curSch, seat_id: seat.seat_id, customer_name: "Tester Idemp", request_id: uuid()};
    const first = await postJSON("/api/book", request);
    const second = await postJSON("/api/book", request);
    if (!first.success || !second.success || !first.booking_id || first.booking_id !== second.booking_id || first.request_id !== second.request_id) throw new Error(first.message || second.error || "Respons booking tidak identik.");
    $("toolResult").textContent = `Deduplikasi lulus. Dua request dengan ID sama mengembalikan booking ${first.booking_id} untuk kursi ${seat.seat_number}.`;
    log(`Deduplikasi lulus: ${first.booking_id}.`, "o");
  });
}

async function testRace() {
  return runTool("btnRace", "Menguji lima pemesan bersamaan", async () => {
    const seat = availableSeat();
    const result = await postJSON("/api/concurrency_test", {schedule_id: curSch, seat_id: seat.seat_id});
    (result.results || []).forEach(row => log(`${row.client}: ${row.status}, ${row.latency_ms} ms.`, row.success ? "o" : "w"));
    if (!result.success || result.confirmed_count !== 1 || result.rejected_count !== 4) throw new Error("Hasil tidak memenuhi satu berhasil dan empat ditolak. Kursi mungkin sudah dipesan pengguna lain.");
    $("toolResult").textContent = `Uji konkurensi lulus. Kursi ${seat.seat_number}: satu pemesan berhasil, empat lainnya ditolak.`;
    log("Konkurensi: 1 CONFIRMED, 4 REJECTED.", "o");
  });
}

async function testBench() {
  return runTool("btnBench", "Mengukur kinerja", async () => {
    const result = await postJSON("/api/benchmark");
    if (!result.success || !result.results?.length) throw new Error(result.error || "Belum ada hasil pengukuran.");
    $("toolResult").innerHTML = '<strong>Hasil pengukuran saat ini</strong><table><caption class="sr-only">Latensi dan throughput berdasarkan jumlah client bersamaan</caption><thead><tr><th scope="col">Client</th><th scope="col">Durasi (s)</th><th scope="col">Req/s</th><th scope="col">Latensi (ms)</th></tr></thead><tbody>' + result.results.map(row => `<tr><th scope="row">${escapeText(row.concurrency)}</th><td>${escapeText(row.total_time)}</td><td>${escapeText(row.throughput)}</td><td>${escapeText(row.avg_latency)}</td></tr>`).join("") + '</tbody></table>';
    result.results.forEach(row => log(`${row.concurrency} client: ${row.throughput} req/s, ${row.avg_latency} ms.`, "o"));
  });
}

window.addEventListener("pagehide", () => { if (sse) sse.close(); });
window.onload = () => {
  init();
  if (window.location?.hash === "#systemTools") openTools();
};
